"""Exercise the candidate store contract through PG, not the bounded example reader."""

from pathlib import Path
import shutil

import pytest
import yapyon

from persona_grata import AgentStore, make_agent
from persona_grata.composition import compose, read_document
from persona_grata.schema import FORMAT


BARE = '''agent:
  format: "agents-yapyon-v0.6.5"
  name: "authored"
  secret: None
  persona:
    name: "local"
    mind:
      endpoint: "http://127.0.0.1:8000"
      dialects:
        chat:
          name: "chat"
          api_uri: "http://127.0.0.1:8000"
  harness:
    name: "client"
    supported_dialects: ["chat"]
'''


def seed(tmp_path, source=BARE, *, defaults=None, paths=None):
    store = AgentStore(tmp_path / "agents")
    path = tmp_path / "outside" / "original_directory"
    path.mkdir(parents=True)
    (path / "agent.cfg").write_text(source)
    store.root.mkdir()
    registry = {"format": FORMAT, "agents": {"scout": {"path": str(path)}},
                "env_vars": {"from_host": [], "additions": []}}
    if defaults is not None:
        registry["defaults"] = defaults
    if paths is not None:
        registry["paths"] = paths
    store.registry_path.write_text(yapyon.dumps(registry))
    return store, path


def put(store, label, source):
    path = store.component_path(tuple(label.split(".")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def test_minimal_definition_gets_only_specified_fallbacks(tmp_path):
    store, path = seed(tmp_path)
    result = store.load("scout")
    agent = result["agent"]
    assert agent["name"] == "scout"
    assert agent["harness"]["path"] == str(path / "harness")
    assert agent["harness"]["protocol"] == "chat"
    assert agent["persona"]["mind"]["model_list"] == []
    assert agent["persona"]["mind"]["model_active"] is None
    assert agent["persona"]["mind"]["model_subagent"] is None
    assert "context_size" not in agent["persona"]["mind"]
    assert "auth_var" not in agent["harness"]
    assert "desc" not in agent


def test_recursive_defaults_bind_missing_fields_before_expressions(tmp_path):
    defaults = {"persona": {"mind": {
                    "context_size": 200000, "model_list": ["first", "second"],
                    "dialects": {"chat": {"verify": {"headers": ["X-Test: one"]}}}}},
                "harness": {"auth_var": "EXTERNAL_KEY", "wrapper_env": {"EXTERNAL_KEY": ""}}}
    store, _ = seed(tmp_path, defaults=defaults)
    agent = store.load("scout")["agent"]
    assert agent["persona"]["mind"]["context_size"] == 200000
    assert agent["persona"]["mind"]["model_active"] == "first"
    assert agent["persona"]["mind"]["model_subagent"] == "first"
    assert agent["persona"]["mind"]["dialects"]["chat"]["verify"] == {"headers": ["X-Test: one"]}
    assert agent["harness"]["auth_var"] == "EXTERNAL_KEY"
    assert agent["harness"]["wrapper_env"] == {"EXTERNAL_KEY": ""}


def test_explicit_none_empty_lists_and_native_data_block_defaults(tmp_path):
    source = yapyon.merge(yapyon.parse(BARE), yapyon.parse('''agent:
  persona:
    mind:
      context_size: None
      model_list:= []
  harness:
    config_store:= {}
    wrapper_env:= {}
''')).to_source()
    defaults = {"persona": {"mind": {"context_size": 200000, "model_list": ["default"]}},
                "harness": {"config_store": {"native": True}, "wrapper_env": {"LANG": "C"}}}
    store, _ = seed(tmp_path, source, defaults=defaults)
    agent = store.load("scout")["agent"]
    assert agent["persona"]["mind"]["context_size"] is None
    assert agent["persona"]["mind"]["model_list"] == []
    assert agent["harness"]["config_store"] == {}
    assert agent["harness"]["wrapper_env"] == {}


def test_source_record_reset_does_not_resurrect_defaults(tmp_path):
    source = yapyon.merge(yapyon.parse(BARE), yapyon.parse("agent: {persona: {mind:= {}}}")).to_source()
    store, _ = seed(tmp_path, source, defaults={"persona": {"mind": {"endpoint": "http://default.example"}}})
    with pytest.raises((ValueError, yapyon.AkanError), match="endpoint|dialects"):
        store.load("scout")


def test_per_protocol_defaults_do_not_implicitly_broadcast_generic_values(tmp_path):
    source = yapyon.merge(yapyon.parse(BARE), yapyon.parse('''agent:
  persona:
    mind:
      dialects:
        chat:
          verify: {key_header: "X-Auth:"}
        responses: None
''')).to_source()
    defaults = {"dialect": {"verify": {"headers": ["X-All: yes"], "body": "generic"}},
                "persona": {"mind": {"dialects": {"chat": {"verify": {"body": "specific"}},
                                                   "responses": {"api_uri": "http://unused"}}}}}
    store, _ = seed(tmp_path, source, defaults=defaults)
    dialects = store.load("scout")["agent"]["persona"]["mind"]["dialects"]
    assert dialects["chat"]["verify"] == {"body": "specific", "key_header": "X-Auth:"}
    assert dialects["responses"] is None


def test_file_values_from_reused_source_are_above_store_defaults(tmp_path):
    source = 'agent<agent.reused>: {secret: None}'
    store, _ = seed(tmp_path, source, defaults={"persona": {"mind": {"context_size": 200000}}})
    reused = yapyon.merge(yapyon.parse(BARE), yapyon.parse(
        "agent: {persona: {mind: {context_size: 300000}}}")).to_source()
    put(store, "agent.reused", reused)
    assert store.load("scout")["agent"]["persona"]["mind"]["context_size"] == 300000


@pytest.mark.parametrize("shared", ["includes", "nested/shared"])
def test_configured_include_root_is_global_for_nested_includes(tmp_path, shared):
    store, _ = seed(tmp_path, 'agent<agent.root>: {secret: None}', paths={"shared": shared})
    base = put(store, "agent.root", BARE.replace('  secret: None', '  secret<secret.alternate>: {}'))
    put(store, "secret.example", 'secret: {name: "example", fetch: y"read {entry}"}')
    put(store, "secret.alternate", 'secret<secret.example>: {entry: "shared/team"}')
    decoy = store.component_path(("secret", "secret", "example"))
    decoy.parent.mkdir(parents=True)
    decoy.write_text('secret: {name: "wrong", entry: "wrong", fetch: "wrong"}')
    source = read_document(base)
    base.write_text(source.to_source())
    config = store.locate("scout") / "agent.cfg"
    config.write_text('agent<agent.root>: {secret<secret.alternate>: {}}')
    secret = store.load("scout")["agent"]["secret"]
    assert secret["name"] == "example"
    assert secret["entry"] == "shared/team"
    assert secret["fetch"] == "read shared/team"


@pytest.mark.parametrize("label", ["one", "site.team.local.profile"])
def test_arbitrary_include_depth_uses_single_branch_value(tmp_path, label):
    store, _ = seed(tmp_path, f'agent<{label}>: {{secret: None}}')
    put(store, label, BARE.replace("agent:", "definition:", 1))
    assert store.load("scout")["agent"]["name"] == "scout"


def test_discarded_include_is_not_loaded_before_slot_overrides(tmp_path):
    source = 'agent<one>: {secret: None, persona: {unused:= None}}'
    store, _ = seed(tmp_path, source)
    put(store, "one", BARE.replace('    name: "local"', '    name: "local"\n    unused<missing.dependency>: {}'))
    assert store.load("scout")["agent"]["persona"]["unused"] is None


def test_computed_include_branch_is_not_silently_discarded(tmp_path):
    store, _ = seed(tmp_path, 'agent<one>: {secret: None}')
    put(store, "one", BARE.replace("agent:", 'y"{$MISSING_BRANCH}":', 1))
    with pytest.raises(ValueError, match="literal.*branch"):
        store.load("scout")


def test_absolute_shared_root_and_repeated_dependency_are_supported(tmp_path):
    store, _ = seed(tmp_path, 'agent<one>: {secret: None}', paths={"shared": str(tmp_path / "includes")})
    source = BARE.replace('    name: "local"', '''    name: "local"
    first<shared.reused>: {}
    second<shared.reused>: {}''')
    put(store, "one", source)
    put(store, "shared.reused", 'data: {number: 1}')
    persona = store.load("scout")["agent"]["persona"]
    assert persona["first"] == persona["second"] == {"number": 1}
    assert not (store.root / "shared").exists()


def test_missing_include_never_uses_private_packaged_catalog(tmp_path):
    source = BARE.replace('  persona:\n    name: "local"', '  persona<persona.ollama>:\n    name: "local"')
    store, _ = seed(tmp_path, source)
    with pytest.raises(ValueError, match="not found"):
        store.load("scout")
    assert not (store.root / "shared").exists()


def test_changed_entry_convention_does_not_redirect_registration(tmp_path):
    store, path = seed(tmp_path, paths={"entries": "somewhere_else"})
    assert store.locate("scout") == path
    assert store.load("scout")["runtime"]["agent_path"] == str(path)
    assert not (store.root / "somewhere_else").exists()


def test_new_nonempty_registry_identifies_its_profile(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.create("scout", yapyon.parse(BARE))
    assert yapyon.loads_record(store.registry_path.read_text())["format"] == FORMAT


def test_explicit_profile_rejects_old_markers_without_relabeling(tmp_path):
    store, path = seed(tmp_path)
    original = store.registry_path.read_text().replace(FORMAT, "agents-yapyon-v0.1")
    store.registry_path.write_text(original)
    with pytest.raises(ValueError, match="unsupported"):
        store.load("scout")
    assert store.registry_path.read_text() == original
    assert (path / "agent.cfg").read_text() == BARE


def test_default_backend_uses_the_same_single_branch_include_adapter(tmp_path):
    store, _ = seed(tmp_path, BARE.replace('  secret: None', '  secret<>: {}'),
                    defaults={"secret": {"backend": "custom"}})
    put(store, "secret.custom", 'backend: {name: "custom", fetch: y"fake-reader {entry}"}')
    secret = store.load("scout")["agent"]["secret"]
    assert secret["name"] == "custom"
    assert secret["entry"] == "agents/scout"
    assert secret["fetch"] == "fake-reader agents/scout"


def test_finite_reuse_can_cut_off_an_included_recursive_field():
    included = yapyon.parse('data: {number: 1, child<one>: {}}')
    result = compose(yapyon.parse('value<one>: {child<one>: {child:= None}}'),
                     lookup=lambda label: included).resolve()
    assert result["value"]["child"] == {"number": 1, "child": None}


def test_create_retains_dependencies_and_changed_defaults_remain_live(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"harness": {"wrapper_env": {"BEFORE": "one"}}})
    path = store.create("scout", make_agent("ollama", "codex"))
    source = (path / "agent.cfg").read_text()
    assert "persona.ollama" in source and "harness.codex" in source
    assert path == store.root / "entries" / "scout"
    assert not (store.root / "shared" / "agent" / "base.tmpl").exists()
    assert (store.root / "shared" / "persona" / "ollama.tmpl").is_file()
    before = store.load("scout")["agent"]
    assert before["harness"]["auth_var"] == "API_KEY"
    store.configure(defaults={"persona": {"mind": {"context_size": 200000}}})
    after = store.load("scout")["agent"]
    assert before["persona"]["mind"]["context_size"] is None
    assert after["persona"]["mind"]["context_size"] == 200000
    assert (path / "agent.cfg").read_text() == source


def test_prepared_candidate_detects_semantically_equal_include_edit(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.create("existing", make_agent("ollama", "codex"))
    prepared = store.prepare_create("next", make_agent("ollama", "codex"))
    included = store.component_path(("persona", "ollama"))
    included.write_text(included.read_text() + "\n# intervening edit\n")
    with pytest.raises(ValueError, match="changed during preflight"):
        store.commit(prepared)
    assert "next" not in store.agents()


def test_atomic_native_default_data_keeps_exact_keys_and_values(tmp_path):
    native = {"é": 1.5, "é": 2, "name": "native", "false": False,
              "zero": 0, "empty": [], "none": None}
    store, _ = seed(tmp_path, defaults={"harness": {"config_store": native}})
    assert store.load("scout")["agent"]["harness"]["config_store"] == native


def test_saved_generic_defaults_remain_late_bound_after_registry_changes(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"persona": {"mind": {"context_size": 100000, "model_list": ["one"]}}})
    path = store.create("scout", yapyon.parse(BARE))
    saved = (path / "agent.cfg").read_bytes()
    assert "context_size" not in saved.decode()
    assert store.load("scout")["agent"]["persona"]["mind"]["model_active"] == "one"
    store.configure(defaults={"persona": {"mind": {"context_size": 200000, "model_list": ["two"]}}})
    mind = store.load("scout")["agent"]["persona"]["mind"]
    assert mind["context_size"] == 200000
    assert mind["model_active"] == mind["model_subagent"] == "two"
    assert (path / "agent.cfg").read_bytes() == saved


def test_required_fields_omitted_in_source_can_receive_spec_defined_defaults(tmp_path):
    source = BARE.replace('      endpoint: "http://127.0.0.1:8000"\n', '').replace(
        '    supported_dialects: ["chat"]\n', '')
    store, _ = seed(tmp_path, source, defaults={
        "persona": {"mind": {"endpoint": "https://default.example"}},
        "harness": {"supported_dialects": ["chat"]}})
    agent = store.load("scout")["agent"]
    assert agent["persona"]["mind"]["endpoint"] == "https://default.example"
    assert agent["harness"]["protocol"] == "chat"


@pytest.mark.parametrize("keyed", [False, True])
def test_store_preserves_authored_native_authentication_for_both_selections(tmp_path, keyed):
    source = BARE.replace('  secret: None', '''  secret:
    name: "inline"
    entry: "shared/key"
    fetch: "fake-reader shared/key"''') if keyed else BARE
    source = yapyon.merge(yapyon.parse(source), yapyon.parse('''agent:
  harness:
    auth_var: "EXTERNAL_KEY"
    wrapper_env: {EXTERNAL_KEY: ""}
    config_store:
      env: {EXTERNAL_KEY: "authored-placeholder", OTHER: None}
      env_key: "EXTERNAL_KEY"
''')).to_source()
    store, _ = seed(tmp_path, source)
    harness = store.load("scout")["agent"]["harness"]
    assert harness["auth_var"] == "EXTERNAL_KEY"
    assert harness["wrapper_env"] == {"EXTERNAL_KEY": ""}
    assert harness["config_store"] == {
        "env": {"EXTERNAL_KEY": "authored-placeholder", "OTHER": None}, "env_key": "EXTERNAL_KEY"}


def test_source_only_resolution_has_no_executable_side_effects(tmp_path, monkeypatch):
    import subprocess
    import urllib.request
    from persona_grata import installation

    def forbidden(*args, **kwargs):
        pytest.fail("information-only resolution crossed an execution boundary")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    for operation in ("fetch_secret", "setup_store", "prepare_installation", "apply_installation"):
        monkeypatch.setattr(installation, operation, forbidden)
    store, path = seed(tmp_path)
    before = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    assert store.load("scout")["agent"]["secret"] is None
    assert before == {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    assert not (path / "harness").exists()


def test_published_example_sources_resolve_through_pg(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parents[2] / "agent_store" / "examples"
    if not fixture.is_dir():
        pytest.skip("standalone standard examples are a sibling artifact")
    store = AgentStore(tmp_path / "agents")
    shutil.copytree(fixture, store.root)
    registry = yapyon.loads_record(store.registry_path.read_text())
    for name, record in registry["agents"].items():
        record["path"] = str(store.root / "entries" / name)
    store.registry_path.write_text(yapyon.dumps(registry))
    monkeypatch.setenv("AGENT_STORE_EXAMPLE_ENDPOINT", "http://127.0.0.1:8000")
    results = {name: store.load(name)["agent"] for name in registry["agents"]}
    assert results["scout"]["secret"] is None
    assert results["unprefixed"]["secret"]["entry"] == "unprefixed"
    assert results["shared"]["secret"]["entry"] == "shared/team"
    assert results["scout"]["harness"]["auth_var"] == "EXAMPLE_API_KEY"
    store.configure(defaults={"secret": {"backend": "alternate_secret"}})
    assert store.load("shared")["agent"]["secret"]["name"] == "alternate_secret"
    assert store.load("unprefixed")["agent"]["secret"]["name"] == "example_secret"

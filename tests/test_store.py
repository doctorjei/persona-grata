"""Agent-first persistence, identity, runtime ownership, and native rendering."""

import json
from pathlib import Path
import tomllib

import pytest
import yapyon

from persona_grata import AgentStore, make_agent
from persona_grata.composition import DATA_DIR, load_preset, read_document
from persona_grata.store import default_store
from persona_grata import yapyon_io as yio


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents")


def test_default_store_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_STORE_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert default_store() == tmp_path / ".config/agents"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert default_store() == tmp_path / "config/agents"
    monkeypatch.setenv("AGENT_STORE_DIR", str(tmp_path / "chosen"))
    assert default_store() == tmp_path / "chosen"


def test_read_empty_store_does_not_create_it(store):
    assert store.agents() == {}
    assert store.settings["defaults"]["secret"]["backend"] is None
    assert not store.root.exists()


def test_registry_keeps_environment_policy_after_agents(store):
    expected_allow = store.settings["env_allow"]
    store.configure(env_add=["MY_ENDPOINT"], env_allow=expected_allow)
    store.create("scout", make_agent("ollama", "codex"))
    saved = yapyon.loads_record(store.registry_path.read_text())
    assert saved["env_add"] == ["MY_ENDPOINT"]
    assert saved["env_allow"] == expected_allow
    assert list(saved)[-2:] == ["env_add", "env_allow"]
    assert list(saved).index("agents") < list(saved).index("env_add")
    store.configure(defaults={"secret": {"root": "custom"}})
    updated = yapyon.loads_record(store.registry_path.read_text())
    assert updated == {**saved, "defaults": {"secret": {"root": "custom"}}}
    assert list(updated)[-2:] == ["env_add", "env_allow"]


def test_create_materializes_only_used_composed_categories(store):
    definition = load_preset(("minimax_claude", "agent"))
    path = store.create("scout", definition)
    assert path == store.root / "store/scout"
    assert store.agents() == {"scout": path}
    saved = yapyon.parse_file(path / "agent.cfg")
    assert saved["agent"].kind == "mapping"
    assert saved["agent"]["name"].value == "scout"
    assert saved["agent"]["secret"].value is None
    assert {slot.label for slot in saved.slots()} == {("minimax", "persona"), ("claude", "harness")}
    assert sorted(p.name for p in (store.root / "personas").iterdir()) == ["minimax.persona.tmpl"]
    assert sorted(p.name for p in (store.root / "harnesses").iterdir()) == ["claude.harness.tmpl"]
    assert {p.name for p in (store.root / "dialects").iterdir()} == {
        "chat.dialect.tmpl", "responses.dialect.tmpl", "anthropic.dialect.tmpl"}
    assert not (store.root / "store/__templates__").exists()
    assert not (store.root / "agents").exists()
    assert not list(store.root.rglob("base.*.tmpl"))
    persona = yapyon.parse_file(store.root / "personas/minimax.persona.tmpl")
    assert persona["persona"].kind == "mapping"
    assert persona["persona"]["desc"].value == "Minimax"


@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize("keyed", [False, True])
def test_runtime_agent_path_is_invocation_owned_scalar(store, tmp_path, harness, keyed):
    if keyed:
        store.configure(defaults={"secret": {"backend": "pass"}})
    destination = tmp_path / "selected agent"
    candidate = store.prepare_create("scout", make_agent("ollama", harness), path=destination)
    assert not destination.exists()
    assert candidate.resolved["runtime"]["agent_path"] == str(destination)
    assert candidate.resolved["agent"]["harness"]["path"] == str(destination / "harness")
    assert store.commit(candidate) == destination
    assert store.load("scout")["runtime"]["agent_path"] == str(destination)


def test_reload_without_shipped_template_library(store, monkeypatch):
    store.create("scout", make_agent("minimax", "claude"))
    monkeypatch.setattr("persona_grata.store.load_preset", lambda *a: pytest.fail("bundled preset used"))
    resolved = store.load("scout")
    harness = resolved["agent"]["harness"]
    native = json.loads(harness["content"])
    assert native["env"]["ANTHROPIC_BASE_URL"] == "https://api.minimax.io/anthropic"
    assert native["env"]["ANTHROPIC_MODEL"] == "MiniMax-M3.1-Flash"
    assert native["env"]["ANTHROPIC_AUTH_TOKEN"] == "no-token-required"
    assert harness["auth_var"] == ""
    assert harness["path"] == str(store.locate("scout") / "harness")
    assert not (store.locate("scout") / "harness").exists()


def test_same_pair_can_have_distinct_names_and_paths(store, tmp_path):
    one = store.create("one", make_agent("kimi", "codex"))
    two = store.create("two", make_agent("kimi", "codex"), path=tmp_path / "separate")
    assert one != two
    assert store.load("two")["runtime"]["agent_path"] == str(two)
    with pytest.raises(FileExistsError, match="occupied"):
        store.create("one", make_agent("minimax", "claude"))
    with pytest.raises(FileExistsError, match="already exists"):
        store.create("three", make_agent("kimi", "codex"), path=two)


def test_rename_keeps_directory_but_name_based_default_entry_follows_name(store):
    store.configure(defaults={"secret": {"backend": "pass", "root": "personal/agents"}})
    path = store.create("before", make_agent("minimax", "claude"))
    store.rename("before", "after")
    assert store.agents() == {"after": path}
    resolved = store.load("after")
    assert resolved["agent"]["name"] == "after"
    assert resolved["agent"]["secret"]["entry"] == "personal/agents/after"
    assert resolved["runtime"]["secret_backend"]["entry"] == "personal/agents/after"
    native = json.loads(resolved["agent"]["harness"]["content"])
    assert "ANTHROPIC_AUTH_TOKEN" not in native["env"]
    assert resolved["agent"]["harness"]["auth_var"] == "ANTHROPIC_AUTH_TOKEN"


@pytest.mark.parametrize("backend", ["pass", "gopass", "prs"])
def test_default_secret_root_is_inherited_and_applied(store, backend):
    store.configure(defaults={"secret": {"backend": backend}})
    assert yapyon.loads_record(store.registry_path.read_text()) == {
        "defaults": {"secret": {"backend": backend}}}
    store = AgentStore(store.root)
    store.create("scout", make_agent("minimax", "claude"))
    resolved = store.load("scout")
    assert resolved["agent"]["secret"]["entry"] == "agents/scout"
    assert resolved["runtime"]["secret_backend"]["entry"] == "agents/scout"
    assert resolved["runtime"]["secret_backend"]["fetch"].endswith(" agents/scout")
    assert resolved["runtime"]["secret_backend"]["store"].endswith(" agents/scout")


@pytest.mark.parametrize("prefix", ["team", "personal/agents", "literal{agent.name}"])
def test_explicit_secret_root_is_preserved_on_reload(store, prefix):
    store.configure(defaults={"secret": {"backend": "pass", "root": prefix}})
    store.create("scout", make_agent("minimax", "claude"))
    before = store.registry_path.read_bytes()
    store = AgentStore(store.root)
    assert store.settings["defaults"]["secret"]["root"] == prefix
    expected = prefix + "/scout" if prefix else "scout"
    manager = store.load("scout")["runtime"]["secret_backend"]
    assert manager["entry"] == expected
    assert manager["fetch"] == "pass show " + expected
    assert store.registry_path.read_bytes() == before


def test_shared_explicit_secret_entry(store):
    store.configure(defaults={"secret": {"backend": "pass"}})
    for name in ("one", "two"):
        definition = make_agent("minimax", "claude")
        definition["agent"].overrides  # Reading overrides must not mutate the source.
        definition = yapyon.merge(definition, yapyon.parse('agent:\n  secret<>: {entry: "shared/key"}'))
        store.create(name, definition)
        assert store.load(name)["agent"]["secret"]["entry"] == "shared/key"


def test_keyless_stays_keyless_after_backend_configured(store):
    store.create("local", make_agent("ollama", "codex"))
    store.configure(defaults={"secret": {"backend": "pass"}})
    resolved = store.load("local")
    assert resolved["agent"]["secret"] is None
    assert resolved["runtime"]["secret_backend"]["name"] == "pass"
    assert tomllib.loads(resolved["agent"]["harness"]["content"])[
        "model_providers"]["ollama"]["env_key"] == ""


@pytest.mark.parametrize("definition", [
    'agent: {path: "/tmp/elsewhere"}',
    'runtime: {}\nagent: {}',
])
def test_invocation_owned_fields_cannot_be_supplied(store, definition):
    with pytest.raises(ValueError, match="path|runtime"):
        store.create("bad", yapyon.parse(definition))
    assert store.agents() == {}


def test_required_slot_refuses_creation(store):
    with pytest.raises((ValueError, yapyon.AkanError), match="required slot|abstract slot"):
        store.create("missing", load_preset(("base", "agent")))
    assert store.agents() == {}


def test_mapping_secret_without_backend_is_not_silently_keyless(store):
    definition = yapyon.merge(make_agent("minimax", "claude"),
                              yapyon.parse('agent: {secret: {entry: "x"}}'))
    with pytest.raises(ValueError, match="agent.secret"):
        store.create("keyed", definition)
    assert store.agents() == {}


@pytest.mark.parametrize("entry", ["../outside", "/absolute", "-option", "a/./b", "line\nbreak", "nul\x00byte"])
def test_unsafe_secret_entries(store, entry):
    store.configure(defaults={"secret": {"backend": "pass"}})
    definition = yapyon.merge(make_agent("minimax", "claude"),
                              yio.value_tree({"agent": {"secret": {"entry": entry}}}))
    with pytest.raises(ValueError, match="secret entry"):
        store.create("bad", definition)
    assert store.agents() == {}


def test_create_uses_existing_composed_component_without_overwrite(store):
    store.create("first", make_agent("minimax", "claude"))
    component = store.root / "personas/minimax.persona.tmpl"
    tree = yapyon.parse_file(component)
    tree["persona"]["desc"].replace(yio.value_tree("Locally edited"))
    component.write_text(tree.to_source())
    store.create("second", make_agent("minimax", "claude"))
    assert store.load("second")["agent"]["persona"]["desc"] == "Locally edited"
    assert yapyon.parse_file(component)["persona"]["desc"].value == "Locally edited"


def test_update_preserves_location_and_registry_identity(store):
    path = store.create("one", make_agent("minimax", "claude"))
    store.update("one", make_agent("kimi", "codex", name="not_the_registry_name"))
    assert store.locate("one") == path
    resolved = store.load("one")
    assert resolved["agent"]["name"] == "one"
    assert resolved["agent"]["persona"]["name"] == "kimi"
    assert resolved["agent"]["harness"]["name"] == "codex"


def test_invalid_update_preserves_previous_definition(store):
    path = store.create("one", make_agent("minimax", "claude"))
    before = (path / "agent.cfg").read_bytes()
    with pytest.raises(ValueError):
        store.update("one", yapyon.parse('agent: {path: "no"}'))
    assert (path / "agent.cfg").read_bytes() == before


def test_registry_write_failure_rolls_back_new_agent(store, monkeypatch):
    monkeypatch.setattr(store, "_write", lambda data: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.create("one", make_agent("minimax", "claude"))
    assert not (store.root / "store/one").exists()
    assert store.agents() == {}


def test_overlapping_agents_are_refused(store):
    path = store.create("one", make_agent("minimax", "claude"))
    with pytest.raises(ValueError, match="overlap"):
        store.create("two", make_agent("minimax", "claude"), path=path / "nested")


def test_symlinked_agent_directory_loads_and_updates_target(store, tmp_path):
    path = store.create("one", make_agent("minimax", "claude"))
    target = tmp_path / "target"
    path.rename(target)
    path.symlink_to(target, target_is_directory=True)
    store.update("one", make_agent("kimi", "codex"))
    assert path.is_symlink()
    assert store.load("one")["agent"]["persona"]["name"] == "kimi"
    assert yapyon.parse_file(target / "agent.cfg")["agent"]["persona"].label == ("kimi", "persona")


def test_symlinked_registry_is_read_and_updated_at_its_target(store, tmp_path):
    store.create("one", make_agent("minimax", "claude"))
    target = tmp_path / "registry.yap"
    store.registry_path.rename(target)
    store.registry_path.symlink_to(target)
    store.configure(env_add=["PG_ENDPOINT"])
    assert store.registry_path.is_symlink()
    assert yapyon.loads_record(target.read_text())["env_add"] == ["PG_ENDPOINT"]


def test_symlinked_component_is_reused(store, tmp_path):
    store.create("first", make_agent("minimax", "claude"))
    component = store.root / "personas/minimax.persona.tmpl"
    target = tmp_path / "minimax.persona.tmpl"
    component.rename(target)
    component.symlink_to(target)
    store.create("second", make_agent("minimax", "claude"))
    assert component.is_symlink()
    assert store.load("second")["agent"]["persona"]["name"] == "minimax"


def test_load_uses_new_environment_allowlist(store, monkeypatch):
    monkeypatch.setenv("PG_PRIVATE_ENDPOINT", "https://example.invalid")
    store.create("one", make_agent("minimax", "claude"))
    path = store.root / "personas/minimax.persona.tmpl"
    tree = yapyon.parse_file(path)
    tree["persona"]["mind"]["endpoint"].replace(yapyon.parse('y"{$PG_PRIVATE_ENDPOINT}"'))
    path.write_text(tree.to_source())
    with pytest.raises(yapyon.AkanError):
        store.load("one")
    store.configure(env_add=["PG_PRIVATE_ENDPOINT"])
    assert store.load("one")["agent"]["persona"]["mind"]["endpoint"] == "https://example.invalid"


@pytest.mark.parametrize("changes", [
    {"defaults": {"secret": {"backend": []}}}, {"defaults": {"secret": {"root": "../bad"}}},
    {"env_add": "NO"}, {"env_allow": [""]}, {"env_add": ["9BAD"]},
    {"agents": {}},
])
def test_invalid_settings_are_not_written(store, changes):
    with pytest.raises(ValueError):
        store.configure(**changes)
    assert not store.registry_path.exists()


@pytest.mark.parametrize("persona", [path.name.removesuffix(".persona.tmpl")
                                    for path in sorted((DATA_DIR / "personas").glob("*.persona.tmpl"))
                                    if not path.name.startswith("base.")])
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_every_supported_pair_renders_without_pg_functions(store, persona, harness):
    name = f"{persona}_{harness}"
    store.create(name, make_agent(persona, harness))
    resolved = store.load(name)
    content = resolved["agent"]["harness"]["content"]
    native = json.loads(content) if harness == "claude" else tomllib.loads(content)
    assert native
    mind = resolved["agent"]["persona"]["mind"]
    if mind["model_list"]:
        assert mind["model_active"] == mind["model_list"][0]
        assert mind["model_subagent"] == mind["model_active"]
        assert native["model"] == mind["model_active"]
    else:
        assert mind["model_active"] is None
        if harness == "claude":
            assert "model" not in native
            assert all(value is not None for value in native["env"].values())
        else:
            assert "model" not in native
            assert native["@_lexemes"]["model"] == "None"
            assert native["models"] == {}


def test_claude_none_filter_cleans_native_copy_only(store):
    definition = yapyon.merge(make_agent("ollama", "claude"), yapyon.parse('''agent:
  harness:
    config_store:
      env: {CUSTOM_NULL: None, CUSTOM_EMPTY: "", CUSTOM_ZERO: 0, CUSTOM_FALSE: False}
'''))
    store.create("custom", definition)
    harness = store.load("custom")["agent"]["harness"]
    native = json.loads(harness["content"])
    assert "model" not in native
    optional = {"ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION",
                "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_AUTO_COMPACT_WINDOW"}
    assert optional.isdisjoint(native["env"])
    assert all(harness["config_store"]["env"][key] is None for key in optional)
    assert harness["config_store"]["model"] is None
    assert harness["config_store"]["env"]["CUSTOM_NULL"] is None
    assert "CUSTOM_NULL" not in native["env"]
    assert native["env"]["CUSTOM_EMPTY"] == ""
    assert native["env"]["CUSTOM_ZERO"] == 0
    assert native["env"]["CUSTOM_FALSE"] is False
    source = read_document(store.root / "harnesses/claude.harness.tmpl").to_source()
    assert "__not_none__().__as_json__()" in source
    assert "for name, value" not in source
    assert yapyon.parse(source).to_source() == source


def test_claude_optional_defaults_keep_available_model_values(store):
    definition = yapyon.merge(make_agent("ollama", "claude"), yapyon.parse('''agent:
  persona:
    mind:
      model_list:= ["m1"]
'''))
    store.create("single", definition)
    native = json.loads(store.load("single")["agent"]["harness"]["content"])
    assert native["model"] == "m1"
    assert native["env"]["ANTHROPIC_MODEL"] == "m1"
    assert native["env"]["ANTHROPIC_DEFAULT_FABLE_MODEL"] == "m1"
    assert native["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "m1"
    assert {"ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW"}.isdisjoint(native["env"])


@pytest.mark.parametrize("definition", [
    'agent: {}', 'agent: {persona: {}}',
    'agent: {persona: {}, harness: {}}',
    'agent<base.agent>: {format: "future", persona<minimax.persona>: {}, harness<claude.harness>: {}}',
])
def test_malformed_agent_never_registers(store, definition):
    with pytest.raises((ValueError, yapyon.AkanError)):
        store.create("bad", yapyon.parse(definition))
    assert store.agents() == {}


@pytest.mark.parametrize("backend", [[], {}, 12, True])
def test_invalid_backend_types_are_clear_errors(store, backend):
    with pytest.raises(ValueError, match="backend"):
        store.configure(defaults={"secret": {"backend": backend}})
    store.root.mkdir(exist_ok=True)
    store.registry_path.write_text(yapyon.dumps({"defaults": {"secret": {"backend": backend}}}))
    with pytest.raises(ValueError, match="backend"):
        store.agents()


@pytest.mark.parametrize("prefix", ["bad//prefix", "trailing/", "/leading"])
def test_secret_root_uses_the_same_entry_grammar_as_installer(store, prefix):
    with pytest.raises(ValueError, match="secret entry"):
        store.configure(defaults={"secret": {"root": prefix}})


def test_symlink_ancestor_redirects_update_to_its_target(store, tmp_path):
    path = store.create("one", make_agent("minimax", "claude"))
    before = (path / "agent.cfg").read_bytes()
    foreign = tmp_path / "foreign"
    (foreign / "one").mkdir(parents=True)
    (foreign / "one/agent.cfg").write_text("untouched")
    (store.root / "store").rename(store.root / "old-store")
    old_path = store.root / "old-store" / "one"
    (store.root / "store").symlink_to(foreign, target_is_directory=True)
    store.update("one", make_agent("kimi", "codex"))
    assert store.load("one")["agent"]["persona"]["name"] == "kimi"
    assert (old_path / "agent.cfg").read_bytes() == before
    assert (foreign / "one/agent.cfg").read_text() != "untouched"


def test_registry_fifo_is_rejected_without_blocking(store):
    import os

    store.root.mkdir()
    os.mkfifo(store.registry_path)
    with pytest.raises(ValueError, match="regular file"):
        store.agents()


def test_installed_config_fifo_is_rejected_without_blocking(store):
    import os

    path = store.create("one", make_agent("minimax", "claude"))
    (path / "agent.cfg").unlink()
    os.mkfifo(path / "agent.cfg")
    with pytest.raises(ValueError, match="regular file"):
        store.load("one")


def test_unknown_or_computed_reserved_agent_fields_are_rejected(store):
    path = store.create("one", make_agent("minimax", "claude"))
    config = path / "agent.cfg"
    config.write_text(config.read_text() + '  |\n  field: "path"\n  y"{field}": "/elsewhere"\n')
    with pytest.raises(ValueError):
        store.load("one")


def test_disabling_dialects_changes_negotiation(store):
    definition = yapyon.merge(make_agent("minimax", "codex"), yapyon.parse('''agent:
  persona<minimax.persona>:
    mind:
      dialects:
        responses:= None
'''))
    store.create("chat_only", definition)
    resolved = store.load("chat_only")
    assert resolved["agent"]["harness"]["protocol"] == "chat"
    assert tomllib.loads(resolved["agent"]["harness"]["content"])[
        "model_providers"]["minimax"]["wire_api"] == "chat"


def test_loading_from_inside_agent_directory_is_allowed(store, monkeypatch):
    path = store.create("one", make_agent("minimax", "claude"))
    monkeypatch.chdir(path)
    assert store.load("one")["agent"]["name"] == "one"


def test_oversized_registry_is_rejected(store):
    store.root.mkdir()
    with store.registry_path.open("wb") as stream:
        stream.truncate(8 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="8 MiB"):
        store.agents()


def test_failed_validation_does_not_leave_unused_components(store):
    definition = yapyon.merge(make_agent("minimax", "claude"), yapyon.parse('''agent:
  persona:
    mind:
      endpoint: "not-a-url"
'''))
    with pytest.raises(ValueError, match="endpoint"):
        store.create("bad", definition)
    assert not list(store.root.rglob("*.tmpl"))
    assert store.agents() == {}


def test_registry_failure_rolls_back_new_components(store, monkeypatch):
    def fail(data):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(OSError, match="disk full"):
        store.create("bad", make_agent("minimax", "claude"))
    assert not list(store.root.rglob("*.tmpl"))
    assert not (store.root / "store/bad").exists()


@pytest.mark.parametrize("supported,protocol", [([], "chat"), (["chat"], "responses")])
def test_explicit_protocol_cannot_bypass_required_dialect_declarations(store, supported, protocol):
    definition = yapyon.merge(make_agent("minimax", "codex"), yapyon.parse(
        "agent:\n  harness:\n"
        f"    supported_dialects:= {supported!r}\n    protocol: {protocol!r}\n"))
    with pytest.raises(ValueError, match="dialect"):
        store.create("invalid_protocol", definition)
    assert store.agents() == {}


def test_failed_setup_does_not_leave_unregistered_backend(store, monkeypatch):
    def fail(data):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(OSError, match="disk full"):
        store.configure(defaults={"secret": {"backend": "pass"}})
    assert not list(store.root.rglob("*.tmpl"))
    assert not store.registry_path.exists()

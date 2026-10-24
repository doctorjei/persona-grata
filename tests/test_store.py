"""Agent-first persistence, identity, runtime ownership, and native rendering."""

import json
from pathlib import Path
import tomllib

import pytest
import yapyon

from persona_grata import AgentStore, make_agent
from persona_grata.composition import DATA_DIR, load_preset
from persona_grata.rendering import prune_config
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
    assert store.settings["secret_backend"] is None
    assert not store.root.exists()


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


def test_rename_does_not_move_directory_or_default_secret_entry(store):
    store.configure(secret_backend="pass", secret_root="personal/agents")
    path = store.create("before", make_agent("minimax", "claude"))
    store.rename("before", "after")
    assert store.agents() == {"after": path}
    resolved = store.load("after")
    assert resolved["agent"]["name"] == "after"
    assert resolved["agent"]["secret"]["entry"] == "before"
    assert resolved["runtime"]["secret_backend"]["entry"] == "personal/agents/before"
    native = json.loads(resolved["agent"]["harness"]["content"])
    assert "ANTHROPIC_AUTH_TOKEN" not in native["env"]
    assert resolved["agent"]["harness"]["auth_var"] == "ANTHROPIC_AUTH_TOKEN"


def test_shared_explicit_secret_entry(store):
    store.configure(secret_backend="pass")
    for name in ("one", "two"):
        definition = make_agent("minimax", "claude")
        definition["agent"].overrides  # Reading overrides must not mutate the source.
        definition = yapyon.merge(definition, yapyon.parse('agent:\n  secret: {entry: "shared/key"}'))
        store.create(name, definition)
        assert store.load(name)["runtime"]["secret_backend"]["entry"] == "shared/key"


def test_keyless_stays_keyless_after_backend_configured(store):
    store.create("local", make_agent("ollama", "codex"))
    store.configure(secret_backend="pass")
    resolved = store.load("local")
    assert resolved["agent"]["secret"] is None
    assert resolved["runtime"]["secret_backend"] == {}
    assert "env_key" not in tomllib.loads(resolved["agent"]["harness"]["content"])[
        "model_providers"]["ollama"]


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
    with pytest.raises(ValueError, match="secret backend"):
        store.create("keyed", definition)
    assert store.agents() == {}


@pytest.mark.parametrize("entry", ["../outside", "/absolute", "-option", "a/./b", "line\nbreak", "nul\x00byte"])
def test_unsafe_secret_entries(store, entry):
    store.configure(secret_backend="pass")
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


def test_symlink_agent_directory_refused(store, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        store.create("bad", make_agent("minimax", "claude"), path=link)


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
    {"secret_backend": "unknown"}, {"secret_root": "../bad"},
    {"env_add": "NO"}, {"env_allow": [""]}, {"env_add": ["9BAD"]},
    {"agents": {}},
])
def test_invalid_settings_are_not_written(store, changes):
    with pytest.raises(ValueError):
        store.configure(**changes)
    assert not store.registry_path.exists()


def test_pruning_keeps_zero_false_and_does_not_mutate_input():
    source = {"none": None, "empty": "", "list": [None, 0, False, ""],
              "dict": {"gone": None, "keep": False}, "zero": 0, "false": False}
    assert prune_config(source) == {"list": [0, False], "dict": {"keep": False}, "zero": 0, "false": False}
    assert source["list"] == [None, 0, False, ""]


def test_pruning_through_contract_before_native_serialization():
    result = yio.resolve_document(yapyon.parse('''config:
  omitted: None
  false: False
  zero: 0
  nested: {empty: "", keep: "yes"}
  items: [None, False, 0]
content: y"{PRUNE_CONFIG(config).__as_json__()}"
'''))
    assert json.loads(result["content"]) == {
        "false": False, "zero": 0, "nested": {"keep": "yes"}, "items": [False, 0]}
    assert result["config"]["omitted"] is None


@pytest.mark.parametrize("persona", [path.name.removesuffix(".persona.tmpl")
                                    for path in sorted((DATA_DIR / "personas").glob("*.persona.tmpl"))
                                    if not path.name.startswith("base.")])
@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_every_supported_pair_renders_without_freezing_none(store, persona, harness):
    name = f"{persona}_{harness}"
    store.create(name, make_agent(persona, harness))
    resolved = store.load(name)
    content = resolved["agent"]["harness"]["content"]
    native = json.loads(content) if harness == "claude" else tomllib.loads(content)
    assert native
    assert '"None"' not in content
    mind = resolved["agent"]["persona"]["mind"]
    if mind["model_list"]:
        assert mind["model_active"] == mind["model_list"][0]
        assert mind["model_subagent"] == mind["model_active"]
    else:
        assert mind["model_active"] is None
        assert "model" not in native


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
    with pytest.raises(ValueError, match="secret_backend"):
        store.configure(secret_backend=backend)
    store.root.mkdir(exist_ok=True)
    store.registry_path.write_text('secret_backend: ' + repr(backend))
    with pytest.raises(ValueError, match="secret_backend"):
        store.agents()


@pytest.mark.parametrize("prefix", ["bad//prefix", "trailing/", "/leading"])
def test_secret_root_uses_the_same_entry_grammar_as_installer(store, prefix):
    with pytest.raises(ValueError, match="secret entry"):
        store.configure(secret_root=prefix)


def test_symlink_ancestor_never_redirects_update(store, tmp_path):
    path = store.create("one", make_agent("minimax", "claude"))
    foreign = tmp_path / "foreign"
    (foreign / "one").mkdir(parents=True)
    (foreign / "one/agent.cfg").write_text("untouched")
    (store.root / "store").rename(store.root / "old-store")
    (store.root / "store").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        store.update("one", make_agent("kimi", "codex"))
    assert (foreign / "one/agent.cfg").read_text() == "untouched"


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
    config.write_text(config.read_text() + '  field: "path"\n  y"{field}": "/elsewhere"\n')
    with pytest.raises(ValueError):
        store.load("one")


def test_disabling_dialects_changes_negotiation(store):
    definition = yapyon.merge(make_agent("minimax", "codex"), yapyon.parse('''agent:
  persona<minimax.persona>:
    mind:
      dialects:
        responses: None
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
    definition = yapyon.merge(make_agent("minimax", "codex"), yio.value_tree({
        "agent": {"harness": {"supported_dialects": supported, "protocol": protocol}}}))
    with pytest.raises(ValueError, match="dialect"):
        store.create("invalid_protocol", definition)
    assert store.agents() == {}


def test_failed_setup_does_not_leave_unregistered_backend(store, monkeypatch):
    def fail(data):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(OSError, match="disk full"):
        store.configure(secret_backend="pass")
    assert not list(store.root.rglob("*.tmpl"))
    assert not store.registry_path.exists()

"""Each agent selects a backend before its inherited commands are resolved."""

import pytest
import yapyon

from persona_grata import AgentStore, make_agent
from persona_grata import yapyon_io as yio


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents")


def agent_with_secret(source):
    definition = make_agent("minimax", "claude")
    definition["agent"].merge(yapyon.parse(source))
    return definition


@pytest.mark.parametrize("backend", ["pass", "gopass", "prs"])
def test_default_backend_and_full_entry_are_inherited_by_agent(store, backend, tmp_path):
    store.configure(defaults={"secret": {"backend": backend, "root": "team/keys"}})
    destination = tmp_path / "different directory"
    store.create("scout", make_agent("minimax", "claude"), path=destination)
    resolved = store.load("scout")
    assert resolved["runtime"]["defaults"]["secret"] == {"backend": backend, "root": "team/keys"}
    secret = resolved["agent"]["secret"]
    assert secret["name"] == backend
    assert secret["entry"] == "team/keys/scout"
    assert "team/keys/scout" in secret["fetch"]
    assert "team/keys/scout" in secret["store"]
    saved = yapyon.parse_file(destination / "agent.cfg")
    assert saved["agent"]["secret"].kind == "slot"
    assert saved["agent"]["secret"].label == ()


@pytest.mark.parametrize("backend", ["pass", "gopass", "prs"])
def test_none_default_root_uses_agent_name_without_becoming_keyless(store, backend):
    store.configure(defaults={"secret": {"backend": backend, "root": None}})
    store.create("scout", make_agent("minimax", "claude"))
    resolved = AgentStore(store.root).load("scout")
    assert resolved["runtime"]["defaults"]["secret"]["root"] is None
    for secret in (resolved["agent"]["secret"], resolved["runtime"]["secret_backend"]):
        assert secret["name"] == backend
        assert secret["root"] is None
        assert secret["entry"] == "scout"
        assert secret["fetch"].endswith(" scout")
        assert secret["store"].endswith(" scout")
    assert resolved["agent"]["harness"]["auth_var"] == "ANTHROPIC_AUTH_TOKEN"


@pytest.mark.parametrize("selection", ["secret<>", "secret<pass.secret>"])
def test_none_agent_root_overrides_nonempty_default(store, selection):
    store.configure(defaults={"secret": {"backend": "pass", "root": "team/keys"}})
    path = store.create("scout", agent_with_secret(f"{selection}: {{root: None}}"))
    source = yapyon.parse_file(path / "agent.cfg")
    assert source["agent"]["secret"].overrides["root"].value is None
    resolved = AgentStore(store.root).load("scout")
    secret = resolved["agent"]["secret"]
    assert secret["root"] is None
    assert secret["entry"] == "scout"
    assert secret["fetch"] == "pass show scout"
    assert secret["store"] == "pass insert -m -f scout"
    assert resolved["runtime"]["secret_backend"]["entry"] == "team/keys/scout"


def test_explicit_backend_overrides_runtime_default(store):
    store.configure(defaults={"secret": {"backend": "pass"}})
    store.create("scout", agent_with_secret('secret<gopass.secret>: {root: "other/keys"}'))
    resolved = store.load("scout")
    assert resolved["runtime"]["secret_backend"]["name"] == "pass"
    assert resolved["agent"]["secret"]["name"] == "gopass"
    assert resolved["agent"]["secret"]["entry"] == "other/keys/scout"
    assert resolved["agent"]["secret"]["fetch"] == "gopass show -o other/keys/scout"


def test_explicit_backend_does_not_require_a_store_default(store):
    store.create("scout", agent_with_secret('secret<pass.secret>: {}'))
    resolved = store.load("scout")
    assert resolved["runtime"]["secret_backend"] == {}
    assert resolved["agent"]["secret"]["name"] == "pass"
    assert resolved["agent"]["secret"]["entry"] == "agents/scout"


@pytest.mark.parametrize("root", ["unused/prefix", None])
def test_complete_entry_override_is_not_prefixed_again(store, root):
    store.configure(defaults={"secret": {"backend": "pass", "root": root}})
    for name in ("one", "two"):
        store.create(name, agent_with_secret('secret<>: {entry: "shared/team-key"}'))
        secret = store.load(name)["agent"]["secret"]
        assert secret["entry"] == "shared/team-key"
        assert secret["fetch"] == "pass show shared/team-key"
        assert secret["store"] == "pass insert -m -f shared/team-key"
        expected = f"{root}/{name}" if root is not None else name
        assert store.load(name)["runtime"]["secret_backend"]["entry"] == expected


@pytest.mark.parametrize("root", ["agents", None])
def test_root_override_rebinds_inherited_entry_and_commands(store, root):
    store.configure(defaults={"secret": {"backend": "pass", "root": root}})
    store.create("scout", agent_with_secret('secret<>: {root: "per-agent"}'))
    secret = store.load("scout")["agent"]["secret"]
    assert secret["entry"] == "per-agent/scout"
    assert secret["fetch"] == "pass show per-agent/scout"


@pytest.mark.parametrize("root", ["agents", None])
def test_explicit_keyless_stays_keyless_despite_default_backend(store, root):
    store.configure(defaults={"secret": {"backend": "pass", "root": root}})
    store.create("local", agent_with_secret('secret: None'))
    resolved = store.load("local")
    assert resolved["agent"]["secret"] is None
    assert resolved["runtime"]["secret_backend"]["name"] == "pass"
    assert resolved["agent"]["harness"]["auth_var"] == ""


def test_omitted_saved_secret_uses_default_without_rewriting_source(store):
    store.configure(defaults={"secret": {"backend": "pass"}})
    path = store.create("scout", make_agent("minimax", "claude"))
    config = path / "agent.cfg"
    saved = yapyon.parse_file(config)
    saved["agent"]["secret"].remove()
    config.write_text(saved.to_source())
    before = config.read_bytes()
    resolved = store.load("scout")
    assert resolved["agent"]["secret"]["name"] == "pass"
    assert resolved["agent"]["secret"]["entry"] == "agents/scout"
    assert resolved["agent"]["harness"]["auth_var"] == "ANTHROPIC_AUTH_TOKEN"
    assert config.read_bytes() == before


def test_implicit_keyless_is_persisted_before_later_setup(store):
    path = store.create("local", make_agent("minimax", "claude"))
    assert yapyon.parse_file(path / "agent.cfg")["agent"]["secret"].value is None
    store.configure(defaults={"secret": {"backend": "pass"}})
    assert store.load("local")["agent"]["secret"] is None


def test_custom_named_backend_is_materialized_and_reusable(store, monkeypatch):
    component = store.root / "secrets/custom.secret.tmpl"
    component.parent.mkdir(parents=True)
    component.write_text('''secret<base.secret>:
  name: "custom"
  fetch: y"custom-read {entry}"
  store: y"custom-write {entry}"
  probe: "custom-read"
  check: "custom-ready"
''')
    store.create("scout", agent_with_secret('secret<custom.secret>: {}'))
    monkeypatch.setattr("persona_grata.store.load_preset", lambda *args: pytest.fail("bundled lookup"))
    resolved = store.load("scout")
    assert resolved["agent"]["secret"]["name"] == "custom"
    assert resolved["agent"]["secret"]["fetch"] == "custom-read agents/scout"
    assert resolved["agent"]["secret"]["check"] == "custom-ready"
    assert resolved["runtime"]["secret_backend"] == {}


def test_inline_backend_can_be_independent_of_default(store):
    store.configure(defaults={"secret": {"backend": "pass"}})
    store.create("scout", agent_with_secret('''secret:
  name: "private"
  entry: "shared/private-key"
  fetch: y"private-read {entry}"
  store: y"private-write {entry}"
  probe: "private-read"
'''))
    secret = store.load("scout")["agent"]["secret"]
    assert secret == {"name": "private", "entry": "shared/private-key",
                      "fetch": "private-read shared/private-key",
                      "store": "private-write shared/private-key", "probe": "private-read"}


def test_read_only_inline_backend_keeps_explicit_optional_none_values(store):
    store.create("scout", agent_with_secret('''secret:
  name: "read_only"
  entry: "shared/key"
  fetch: y"custom-read {entry}"
  store: None
  probe: None
  check: None
'''))
    secret = store.load("scout")["agent"]["secret"]
    assert secret["fetch"] == "custom-read shared/key"
    assert all(secret[key] is None for key in ("store", "probe", "check"))


def test_default_backend_changes_affect_inherited_agents_not_explicit_ones(store):
    store.configure(defaults={"secret": {"backend": "pass"}})
    store.create("inherited", make_agent("minimax", "claude"))
    store.create("fixed", agent_with_secret('secret<pass.secret>: {}'))
    store.configure(defaults={"secret": {"backend": "gopass"}})
    assert store.load("inherited")["agent"]["secret"]["name"] == "gopass"
    assert store.load("fixed")["agent"]["secret"]["name"] == "pass"


@pytest.mark.parametrize("root", ["agents", None])
def test_name_default_follows_rename_but_explicit_entry_does_not(store, root):
    store.configure(defaults={"secret": {"backend": "pass", "root": root}})
    directory = store.create("before", make_agent("minimax", "claude"))
    store.rename("before", "after")
    assert store.locate("after") == directory
    expected = f"{root}/after" if root is not None else "after"
    assert store.load("after")["agent"]["secret"]["entry"] == expected
    store.update("after", agent_with_secret('secret<>: {entry: "agents/before"}'))
    store.rename("after", "final")
    assert store.load("final")["agent"]["secret"]["entry"] == "agents/before"


@pytest.mark.parametrize("entry", ["../outside", "/absolute", "-option", "a//b", "line\nbreak", ""])
def test_complete_entry_validation_is_still_applied(store, entry):
    secret = yapyon.parse('secret<pass.secret>: {}')
    secret["secret"].merge(yio.value_tree({"entry": entry}))
    definition = agent_with_secret(secret.to_source())
    with pytest.raises(ValueError, match="secret entry"):
        store.prepare_create("bad", definition)
    assert not store.registry_path.exists()


def test_sparse_registry_is_preserved_through_create_rename_and_unregister(store):
    store.configure(defaults={"secret": {"root": "team"}})
    store.create("before", make_agent("minimax", "claude"))
    store.rename("before", "after")
    store.unregister("after")
    saved = yapyon.loads_record(store.registry_path.read_text())
    assert saved == {"defaults": {"secret": {"root": "team"}}, "agents": {}}

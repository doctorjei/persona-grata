"""Regression checks for source freshness and release-blocking integration boundaries."""

from unittest.mock import Mock

import pytest
import yapyon

from persona_grata import AgentStore, make_agent
from persona_grata import installation as inst
from persona_grata.composition import read_document
from persona_grata.schema import FORMAT


BARE = f'''agent:
  format: {FORMAT!r}
  name: "authored"
  secret: None
  persona:
    name: "local"
    mind:
      endpoint: "http://127.0.0.1:8000"
      dialects: {{chat: {{name: "chat", api_uri: "http://127.0.0.1:8000"}}}}
  harness:
    name: "client"
    supported_dialects: ["chat"]
'''


def test_agent_location_cannot_contain_the_configured_shared_root(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.configure(paths={"shared": "entries/scout/shared"})
    with pytest.raises(ValueError, match="unsafe agent directory"):
        store.prepare_create("scout", make_agent("ollama", "codex"))
    assert not (store.root / "entries").exists()


def test_rename_never_overwrites_an_edit_between_source_read_and_preparation(tmp_path, monkeypatch):
    store = AgentStore(tmp_path / "agents")
    path = store.create("before", yapyon.merge(yapyon.parse(BARE), yapyon.parse('agent: {desc: "old"}')))
    real_prepare = store.prepare_update

    def intervening_edit(*args, **kwargs):
        edited = yapyon.merge(read_document(path / "agent.cfg"), yapyon.parse('agent: {desc: "new"}'))
        (path / "agent.cfg").write_text(edited.to_source())
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(store, "prepare_update", intervening_edit)
    store.rename("before", "after")
    assert store.load("after")["agent"]["desc"] == "new"


def test_forwarded_configured_model_list_is_not_duplicated(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"persona": {"mind": {"model_list": ["one", "one", "two"]}}})
    store.create("scout", make_agent("ollama", "claude"))
    assert store.load("scout")["agent"]["persona"]["mind"]["model_list"] == ["one", "one", "two"]


@pytest.mark.parametrize("changed", ["agent.base", "persona.base"])
def test_prepared_candidate_tracks_sources_collapsed_before_final_assembly(tmp_path, changed):
    store = AgentStore(tmp_path / "agents")
    agent_base = store.component_path(("agent", "base"))
    persona_base = store.component_path(("persona", "base"))
    agent_base.parent.mkdir(parents=True)
    persona_base.parent.mkdir(parents=True)
    agent_base.write_text(BARE.replace("  persona:", "  persona<persona.base>:"))
    persona_base.write_text('persona: {desc: "before"}')
    prepared = store.prepare_create("scout", yapyon.parse("agent<agent.base>: {}"))
    selected = store.component_path(tuple(changed.split(".")))
    selected.write_text(selected.read_text() + "\n# intervening source edit\n")
    with pytest.raises(ValueError, match="changed during preflight"):
        store.commit(prepared)
    assert store.agents() == {}


@pytest.mark.parametrize("filename", [
    ".persona-grata-installation", "./.persona-grata-installation",
    ".persona-grata-installation/config.json",
])
def test_native_output_cannot_overwrite_or_contain_the_ownership_marker(tmp_path, filename):
    path = tmp_path / "scout"
    harness = {"path": str(path / "harness"), "config_file": filename, "content": "{}"}
    resolved = {"runtime": {"agent_path": str(path)},
                "agent": {"name": "scout", "harness": harness, "secret": None}}
    with pytest.raises(ValueError, match="ownership marker"):
        inst.prepare_installation(resolved, install_wrapper=False)
    assert not path.exists()


@pytest.mark.parametrize("key", ["persona", "harness", "secret", "name", "format"])
def test_computed_agent_schema_keys_are_validated_after_resolution(tmp_path, key):
    store = AgentStore(tmp_path / "agents")
    source = BARE.replace(f"  {key}:", f'  y"{key}":', 1)
    store.create("scout", yapyon.parse(source))
    agent = store.load("scout")["agent"]
    assert agent["name"] == "scout"
    assert agent["secret"] is None
    assert agent["format"] == FORMAT


@pytest.mark.parametrize("extra", ['y"path": "/outside"', 'y"unexpected": 1', 'y"format": "wrong"'])
def test_computed_agent_keys_cannot_bypass_closed_schema_or_format(tmp_path, extra):
    store = AgentStore(tmp_path / "agents")
    source = BARE.replace(f"  format: {FORMAT!r}\n", "") if "format" in extra else BARE
    source += "  " + extra + "\n"
    with pytest.raises(ValueError, match="unknown agent fields|format"):
        store.prepare_create("scout", yapyon.parse(source))
    assert not store.root.exists()


@pytest.mark.parametrize("protocol", ["config_store", "wrapper_env"])
def test_protocol_names_do_not_select_native_data_overlay_rules(tmp_path, protocol):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"persona": {"mind": {"dialects": {
        protocol: {"api_uri": "http://example.invalid", "verify": {"headers": ["X-One: 1"]}}
    }}}})
    store.configure(defaults={"persona": {"mind": {"dialects": {
        protocol: {"verify": {"body": "second"}}
    }}}})
    assert store.settings["defaults"]["persona"]["mind"]["dialects"][protocol] == {
        "api_uri": "http://example.invalid", "verify": {"headers": ["X-One: 1"], "body": "second"}}


@pytest.mark.parametrize("store_command", [None, "", "missing-writer entry"])
def test_existing_credential_does_not_require_optional_store_command(tmp_path, monkeypatch, store_command):
    path = tmp_path / "scout"
    manager = {"name": "readonly", "entry": "entry", "fetch": "fake-read entry", "store": store_command}
    harness = {"name": "client", "path": str(path / "harness"), "config_file": "config.json", "content": "{}"}
    resolved = {"runtime": {"agent_path": str(path)},
                "agent": {"name": "scout", "harness": harness, "secret": manager}}
    monkeypatch.setattr(inst, "_secret_probe", Mock())
    monkeypatch.setattr(inst.shutil, "which", lambda name: "/fake-bin/" + name if name == "fake-read" else None)
    monkeypatch.setattr(inst, "_check_secret_store", Mock(return_value=True))
    monkeypatch.setattr(inst, "_fetch_secret", Mock(return_value="SYNTHETIC_TOKEN"))
    writer = Mock(side_effect=AssertionError("read-only installation wrote a credential"))
    monkeypatch.setattr(inst, "_store_secret", writer)
    plan = inst.prepare_installation(resolved, install_wrapper=False)
    assert plan.fresh is False
    inst.commit_credential(plan)
    inst.apply_installation(plan)
    writer.assert_not_called()
    assert (path / "harness/config.json").read_text() == "{}"


@pytest.mark.parametrize("token", [None, "SYNTHETIC_IMPORT"])
def test_readonly_backend_missing_credential_refuses_before_prompt_or_write(tmp_path, monkeypatch, token):
    path = tmp_path / "scout"
    manager = {"name": "readonly", "entry": "entry", "fetch": "fake-read entry"}
    resolved = {"runtime": {"agent_path": str(path)}, "agent": {
        "name": "scout", "secret": manager,
        "harness": {"path": str(path / "harness"), "config_file": "config.json", "content": "{}"}}}
    monkeypatch.setattr(inst, "_secret_probe", Mock())
    monkeypatch.setattr(inst.shutil, "which", lambda name: "/fake-bin/" + name)
    monkeypatch.setattr(inst, "_check_secret_store", Mock(return_value=False))
    prompt = Mock(side_effect=AssertionError("unusable read-only backend prompted for a credential"))
    monkeypatch.setattr(inst, "_prompt_token", prompt)
    with pytest.raises(ValueError, match="store"):
        inst.prepare_installation(resolved, token=token, install_wrapper=False)
    prompt.assert_not_called()
    assert not path.exists()

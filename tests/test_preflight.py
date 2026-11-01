"""CLI preparation must not strand agents on predictable installation failures."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yapyon

from persona_grata import AgentStore, cli, make_agent
from persona_grata import installation as inst
from persona_grata.composition import read_document, write_document


TOKEN = "PREFLIGHT_TEST_TOKEN_DO_NOT_PRINT"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("AGENT_STORE_DIR", str(home / "agents"))
    monkeypatch.setattr(inst, "_is_interactive", lambda: False)
    store = AgentStore()
    key = home / "input-key"
    key.write_text(TOKEN + "\n")
    return SimpleNamespace(home=home, store=store, key=key)


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def backend(environment, monkeypatch):
    environment.store.configure(secret_backend="pass")
    state = SimpleNamespace(readiness=Mock(return_value=False), fetch=Mock(return_value=TOKEN), store=Mock())
    monkeypatch.setattr(inst, "_secret_probe", Mock())
    monkeypatch.setattr(inst.shutil, "which", lambda name: "/test-bin/" + name)
    monkeypatch.setattr(inst, "_check_secret_store", state.readiness)
    monkeypatch.setattr(inst, "_fetch_secret", state.fetch)
    monkeypatch.setattr(inst, "_store_secret", state.store)
    return state


def create(environment, *options):
    return cli.main(["--create", "ollama", "claude", "--name", "test_agent", *options])


def test_keyless_with_token_leaves_even_fresh_store_absent(environment, monkeypatch, capsys):
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: pytest.fail("keyless preflight ran a manager"))
    assert create(environment, "--token", str(environment.key)) == 1
    assert not environment.store.root.exists()
    assert not (environment.home / ".bashrc").exists()
    output = capsys.readouterr()
    assert "nowhere to store" in output.err and "configuration was saved" not in output.err
    assert TOKEN not in output.out + output.err


@pytest.mark.parametrize("reason", ["not installed", "store is uninitialized", "store is inaccessible"])
def test_backend_readiness_failure_preserves_store_and_components(environment, backend, reason, capsys):
    before = snapshot(environment.home)
    backend.readiness.side_effect = ValueError(reason)
    assert create(environment, "--token", str(environment.key)) == 1
    assert snapshot(environment.home) == before
    assert environment.store.agents() == {}
    backend.store.assert_not_called()
    backend.fetch.assert_not_called()
    assert reason in capsys.readouterr().err


def test_actual_missing_executable_fails_before_persistence(environment, backend, monkeypatch):
    monkeypatch.setattr(inst, "_secret_probe", lambda manager: (_ for _ in ()).throw(ValueError("manager missing")))
    before = snapshot(environment.home)
    assert create(environment, "--token", str(environment.key)) == 1
    assert snapshot(environment.home) == before
    backend.readiness.assert_not_called()
    backend.store.assert_not_called()


def test_inaccessible_existing_entry_is_not_treated_as_missing(environment, backend, monkeypatch, capsys):
    before = snapshot(environment.home)
    backend.readiness.return_value = True
    backend.fetch.side_effect = ValueError("credential cannot be decrypted")
    monkeypatch.setattr(inst, "_prompt_token", lambda *a: pytest.fail("inaccessible credential was replaced by a prompt"))
    assert create(environment) == 1
    assert snapshot(environment.home) == before
    backend.store.assert_not_called()
    assert "cannot be decrypted" in capsys.readouterr().err
    assert backend.fetch.call_args.kwargs == {}


def test_known_missing_entry_without_terminal_fails_before_persistence(environment, backend, capsys):
    before = snapshot(environment.home)
    assert create(environment) == 1
    assert snapshot(environment.home) == before
    backend.fetch.assert_not_called()
    backend.store.assert_not_called()
    assert "supply a token" in capsys.readouterr().err


def test_unknown_entry_state_never_swallows_failed_fetch(environment, backend, monkeypatch):
    backend.readiness.return_value = None
    backend.fetch.side_effect = ValueError("custom fetch failed")
    monkeypatch.setattr(inst, "_prompt_token", lambda *a: pytest.fail("ambiguous fetch failure prompted"))
    before = snapshot(environment.home)
    assert create(environment) == 1
    assert snapshot(environment.home) == before
    backend.fetch.assert_called_once()
    backend.store.assert_not_called()


def test_prompted_token_is_validated_before_any_pg_write(environment, backend, monkeypatch):
    before = snapshot(environment.home)
    def prompt(description):
        assert snapshot(environment.home) == before
        return TOKEN
    monkeypatch.setattr(inst, "_prompt_token", prompt)
    assert create(environment) == 0
    assert [call.kwargs["writing"] for call in backend.readiness.call_args_list] == [False, True]
    backend.store.assert_called_once()


def test_optional_verification_rejects_before_any_write(environment, backend, monkeypatch, capsys):
    before = snapshot(environment.home)
    monkeypatch.setattr(inst, "_verify_token", Mock(side_effect=ValueError("Credential rejected")))
    assert create(environment, "--token", str(environment.key), "--verify") == 1
    assert snapshot(environment.home) == before
    backend.store.assert_not_called()
    assert "Credential rejected" in capsys.readouterr().err


@pytest.mark.parametrize("action", ["fetch", "store"])
def test_malformed_manager_command_is_rejected_before_persistence(environment, backend, action):
    path = environment.store.component_path(("pass", "secret"))
    tree = read_document(path)
    tree["secret"][action].replace(yapyon.parse('""'))
    write_document(tree, path, overwrite=True)
    before = snapshot(environment.home)
    assert create(environment, "--token", str(environment.key)) == 1
    assert snapshot(environment.home) == before
    backend.store.assert_not_called()


@pytest.mark.parametrize("override", [
    "content:= None",
    'wrapper_env: {"not-valid": "value"}',
    'config_file: "/outside/settings.json"',
])
def test_native_and_wrapper_validation_precedes_registration(environment, backend, override):
    source = environment.home / "invalid.cfg"
    source.write_text('agent<base.agent>:\n  name: "test_agent"\n  persona<ollama.persona>: {}\n'
                      f'  harness<claude.harness>:\n    {override}\n')
    before = snapshot(environment.home)
    assert cli.main(["--create", str(source), "--token", str(environment.key)]) == 1
    assert snapshot(environment.home) == before
    backend.store.assert_not_called()
    backend.readiness.assert_not_called()


def test_bad_shell_startup_target_precedes_credential_handling(environment, backend):
    (environment.home / ".bashrc").mkdir()
    before = snapshot(environment.home)
    assert create(environment, "--token", str(environment.key)) == 1
    assert snapshot(environment.home) == before
    assert not (environment.store.root / "store").exists()
    backend.store.assert_not_called()
    backend.readiness.assert_not_called()


@pytest.mark.parametrize("stage", ["store", "readback"])
def test_runtime_credential_failure_still_precedes_pg_registration(environment, backend, stage, capsys):
    before = snapshot(environment.home)
    getattr(backend, "store" if stage == "store" else "fetch").side_effect = ValueError("backend failed")
    assert create(environment, "--token", str(environment.key)) == 1
    assert snapshot(environment.home) == before
    assert environment.store.agents() == {}
    assert "configuration was saved" not in capsys.readouterr().err


def test_successful_import_and_readback_happen_before_registration(environment, backend, capsys):
    before = snapshot(environment.home)
    def imported(manager, token):
        assert token == TOKEN
        assert snapshot(environment.home) == before
        assert environment.store.agents() == {}
    def readback(manager):
        assert environment.store.agents() == {}
        return TOKEN
    backend.store.side_effect = imported
    backend.fetch.side_effect = readback
    assert create(environment, "--token", str(environment.key)) == 0
    backend.store.assert_called_once()
    backend.fetch.assert_called_once()
    path = environment.store.locate("test_agent")
    settings = json.loads((path / "harness" / "settings.json").read_text())
    assert "ANTHROPIC_AUTH_TOKEN" not in settings["env"]
    assert "test_agent()" in (environment.home / ".bashrc").read_text()
    assert all(TOKEN.encode() not in value for key, value in snapshot(environment.home).items() if key != "input-key")
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err


def test_failed_create_does_not_occupy_name_before_backend_setup(environment, backend):
    backend.readiness.side_effect = ValueError("store is uninitialized")
    assert create(environment, "--token", str(environment.key)) == 1
    assert environment.store.agents() == {}
    backend.readiness.side_effect = None
    assert create(environment, "--token", str(environment.key)) == 0
    assert list(environment.store.agents()) == ["test_agent"]


def test_update_and_rename_preflight_failure_preserves_previous_installation(environment):
    assert create(environment) == 0
    source = environment.home / "bad-update.cfg"
    definition = make_agent("ollama", "claude")
    definition.merge(yapyon.parse('agent:\n  harness:\n    wrapper_env: {"bad-name": "value"}\n'))
    write_document(definition, source)
    before = snapshot(environment.home)
    assert cli.main(["--update", "test_agent", str(source), "--name", "renamed"]) == 1
    assert snapshot(environment.home) == before
    assert list(environment.store.agents()) == ["test_agent"]


def test_shell_change_during_preflight_stops_before_credential_write(environment, backend):
    candidate = environment.store.prepare_create("candidate", make_agent("ollama", "claude"))
    plan = inst.prepare_installation(candidate.resolved, token=TOKEN)
    rc = environment.home / ".bashrc"
    rc.write_text("new unrelated shell settings\n")
    with pytest.raises(ValueError, match="changed during preflight"):
        inst.commit_credential(plan)
    backend.store.assert_not_called()
    assert environment.store.agents() == {}
    assert rc.read_text() == "new unrelated shell settings\n"
    assert TOKEN not in repr(plan)


def test_prepare_create_is_read_only_and_commit_is_source_only(environment, monkeypatch):
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: pytest.fail("source preparation contacted a manager"))
    candidate = environment.store.prepare_create("candidate", make_agent("ollama", "claude"))
    assert not environment.store.root.exists()
    assert candidate.resolved["agent"]["name"] == "candidate"
    path = environment.store.commit(candidate)
    assert environment.store.locate("candidate") == path
    assert (path / "agent.cfg").is_file() and not (path / "harness").exists()


def test_stale_registry_candidate_is_refused(environment):
    candidate = environment.store.prepare_create("candidate", make_agent("ollama", "claude"))
    environment.store.create("other", make_agent("ollama", "claude"))
    before = snapshot(environment.home)
    with pytest.raises(ValueError, match="store changed during preflight"):
        environment.store.commit(candidate)
    assert snapshot(environment.home) == before


def test_stale_source_update_is_refused(environment):
    path = environment.store.create("old", make_agent("ollama", "claude"))
    candidate = environment.store.prepare_update("old", make_agent("kimi", "claude"), new_name="new")
    assert list(environment.store.agents()) == ["old"]
    (path / "agent.cfg").write_text((path / "agent.cfg").read_text() + "\n")
    before = snapshot(environment.home)
    with pytest.raises(ValueError, match="source changed during preflight"):
        environment.store.commit(candidate)
    assert snapshot(environment.home) == before
    assert list(environment.store.agents()) == ["old"]


def test_stale_shared_component_is_refused(environment):
    environment.store.create("other", make_agent("ollama", "claude"))
    candidate = environment.store.prepare_create("candidate", make_agent("ollama", "claude"))
    path = environment.store.component_path(("ollama", "persona"))
    tree = read_document(path)
    tree["persona"]["desc"].replace(yapyon.parse('"changed after preflight"'))
    write_document(tree, path, overwrite=True)
    before = snapshot(environment.home)
    with pytest.raises(ValueError, match="configuration changed during preflight"):
        environment.store.commit(candidate)
    assert snapshot(environment.home) == before


def test_prepare_update_commit_combines_rename_and_source_replacement(environment):
    path = environment.store.create("old", make_agent("ollama", "claude"))
    candidate = environment.store.prepare_update("old", make_agent("kimi", "claude"), new_name="new")
    assert environment.store.locate("old") == path
    assert environment.store.commit(candidate) == path
    assert environment.store.agents() == {"new": path}
    assert environment.store.load("new")["agent"]["persona"]["name"] == "kimi"


def test_parent_segments_in_agent_path_fail_before_credential_import(environment, backend):
    before = snapshot(environment.home)
    target = environment.home / "intermediate" / ".." / "selected"
    assert create(environment, "--path", str(target), "--token", str(environment.key)) == 1
    assert snapshot(environment.home) == before
    assert environment.store.agents() == {}
    backend.store.assert_not_called()
    backend.readiness.assert_not_called()


def test_readonly_target_is_caught_without_mkdir(environment, monkeypatch):
    monkeypatch.setattr("persona_grata.composition.os.access", lambda *a: False)
    with pytest.raises(ValueError, match="not writable"):
        environment.store.prepare_create("candidate", make_agent("ollama", "claude"))
    assert not environment.store.root.exists()

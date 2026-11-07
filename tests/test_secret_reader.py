"""Credential reads use the saved backend without installation or secret-bearing diagnostics."""

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
import yapyon

from persona_grata import AgentStore, cli, installation, make_agent
from persona_grata import yapyon_io as yio


TOKEN = "FAKE_PG_READER_TEST_VALUE"


def snapshot(root):
    return {str(path.relative_to(root)): (path.stat().st_mode, path.read_bytes())
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def reader_store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENT_STORE_DIR", str(tmp_path / "agents"))
    store = AgentStore()
    store.configure(secret_backend="pass", secret_root="team/agents")
    definition = yapyon.merge(make_agent("minimax", "claude"), yio.value_tree({
        "agent": {"secret": {"entry": "folder/key with spaces;not-a-command"}}}))
    store.create("scout", definition)

    def forbidden(*args, **kwargs):
        pytest.fail("credential reader attempted installation, prompting or network access")

    for name in ("setup_store", "prepare_installation", "commit_credential", "apply_installation"):
        monkeypatch.setattr(installation, name, forbidden)
    monkeypatch.setattr(installation.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)
    return store


@pytest.mark.parametrize("explicit_store", [False, True])
def test_get_secret_prints_only_credential_without_writes(reader_store, monkeypatch, capsys, explicit_store):
    before = snapshot(reader_store.root)
    fetch = Mock(return_value=TOKEN)
    monkeypatch.setattr(installation, "fetch_secret", fetch)
    arguments = ["--get-secret", "scout"]
    if explicit_store:
        monkeypatch.setenv("AGENT_STORE_DIR", str(reader_store.root / "unused"))
        arguments += ["--store", str(reader_store.root)]
    assert cli.main(arguments) == 0
    output = capsys.readouterr()
    assert output.out == TOKEN + "\n"
    assert output.err == ""
    fetch.assert_called_once()
    assert fetch.call_args.args[0]["entry"] == "team/agents/folder/key with spaces;not-a-command"
    assert snapshot(reader_store.root) == before
    assert list(Path.home().iterdir()) == []


def test_get_secret_keyless_fails_without_fetch(reader_store, monkeypatch, capsys):
    reader_store.create("public", yapyon.merge(make_agent("ollama", "codex"),
                                               yapyon.parse("agent:\n  secret:= None\n")))
    fetch = Mock(side_effect=AssertionError("keyless agent tried a credential fetch"))
    monkeypatch.setattr(installation, "fetch_secret", fetch)
    before = snapshot(reader_store.root)
    assert cli.main(["--get-secret", "public"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "keyless" in output.err
    fetch.assert_not_called()
    assert snapshot(reader_store.root) == before


@pytest.mark.parametrize("missing_store", [False, True])
def test_get_secret_unknown_agent_never_creates_store(reader_store, tmp_path, monkeypatch, capsys, missing_store):
    fetch = Mock()
    monkeypatch.setattr(installation, "fetch_secret", fetch)
    root = tmp_path / "missing" if missing_store else reader_store.root
    before = snapshot(root)
    assert cli.main(["--store", str(root), "--get-secret", "unknown"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "unknown agent" in output.err
    fetch.assert_not_called()
    assert snapshot(root) == before
    if missing_store:
        assert not root.exists()


@pytest.mark.parametrize("extra", [
    ["extra"], ["--create"], ["--update"], ["--remove"], ["--export", "out.cfg"], ["--setup"],
    ["--name", "new"], ["--path", "elsewhere"], ["--token", "missing"], ["--no-token"],
    ["--verify"], ["--no-wrapper"], ["--yes"], ["--force"], ["--backend", "pass"],
    ["--secret-root", "other"],
])
def test_get_secret_rejects_conflicts_before_store_or_token_read(monkeypatch, capsys, extra):
    constructor = Mock(side_effect=AssertionError("invalid options constructed a store"))
    monkeypatch.setattr("persona_grata.store.AgentStore", constructor)
    monkeypatch.setattr(cli, "_read_token", Mock(side_effect=AssertionError("read token input")))
    assert cli.main(["--get-secret", "scout", *extra]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "error:" in output.err
    constructor.assert_not_called()


@pytest.mark.parametrize("name", ["../escape", "bad-name", "9agent", "café", ""])
def test_get_secret_validates_name_before_store(monkeypatch, capsys, name):
    constructor = Mock(side_effect=AssertionError("invalid name constructed a store"))
    monkeypatch.setattr("persona_grata.store.AgentStore", constructor)
    assert cli.main(["--get-secret", name]) == 1
    assert capsys.readouterr().out == ""
    constructor.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "exit", "empty", "unicode", "multiline", "timeout"])
def test_get_secret_backend_failures_have_no_secret_output(reader_store, monkeypatch, capsys, failure):
    monkeypatch.setattr(installation.shutil, "which", lambda name: None if failure == "missing" else "/fake/pass")

    def failed(argv, **kwargs):
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.DEVNULL
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 120, output=TOKEN.encode(), stderr=TOKEN.encode())
        body = {"exit": TOKEN.encode(), "empty": b"", "unicode": b"\xff" + TOKEN.encode(),
                "multiline": (TOKEN + "\nsecond line").encode()}[failure]
        return subprocess.CompletedProcess(argv, 1 if failure == "exit" else 0, stdout=body)

    monkeypatch.setattr(installation.subprocess, "run", failed)
    before = snapshot(reader_store.root)
    assert cli.main(["--get-secret", "scout"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Error:" in output.err
    assert TOKEN not in output.err
    assert "Traceback" not in output.err
    assert snapshot(reader_store.root) == before


def test_get_secret_interrupt_has_no_stdout(reader_store, monkeypatch, capsys):
    monkeypatch.setattr(installation, "fetch_secret", Mock(side_effect=KeyboardInterrupt))
    assert cli.main(["--get-secret", "scout"]) == 130
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Cancelled.\n"


@pytest.mark.parametrize("fail", [False, True])
def test_get_secret_real_subprocess_suppresses_manager_diagnostics(reader_store, tmp_path, fail):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    manager = bin_dir / "pass"
    manager.write_text('''#!/bin/sh
[ "$#" -eq 2 ] && [ "$1" = show ] && [ "$2" = "$EXPECTED_ENTRY" ] || exit 9
printf '%s\\n' "$FAKE_READER_TOKEN" >&2
printf '%s\\n' "$FAKE_READER_TOKEN"
exit "$FAKE_READER_EXIT"
''')
    manager.chmod(0o700)
    environment = {
        "HOME": str(Path.home()), "PATH": str(bin_dir) + os.pathsep + os.defpath,
        "AGENT_STORE_DIR": str(reader_store.root), "FAKE_READER_TOKEN": TOKEN,
        "EXPECTED_ENTRY": "team/agents/folder/key with spaces;not-a-command",
        "FAKE_READER_EXIT": "7" if fail else "0",
    }
    before = snapshot(reader_store.root)
    result = subprocess.run([sys.executable, "-m", "persona_grata", "--get-secret", "scout"],
                            env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == (1 if fail else 0)
    assert result.stdout == ("" if fail else TOKEN + "\n")
    if fail:
        assert "Error:" in result.stderr
    else:
        assert result.stderr == ""
    assert TOKEN not in result.stderr
    assert snapshot(reader_store.root) == before
    assert list(Path.home().iterdir()) == []

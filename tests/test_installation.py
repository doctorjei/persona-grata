"""Agent-first installation safety; fake managers never touch real credentials."""

import copy
from email.message import Message
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import urllib.error
import urllib.response
from unittest.mock import Mock

import pytest

from persona_grata import installation as inst
from persona_grata.identifiers import validate_id


SHELL_WORDS = ("if", "then", "else", "elif", "fi", "do", "done", "case", "esac",
               "while", "until", "for", "in", "function", "select", "time", "coproc")


class Store:
    """The AgentStore protocol, without depending on concurrent store development."""

    def __init__(self, root):
        self.root = root
        self.root.mkdir()
        self.registry = {}
        self.definitions = {}
        self.settings = {"defaults": {"secret": {"backend": None, "root": "agents"}}}

    def add(self, name="orion", *, path=None, manager=None, default_manager=None, keyless=True):
        path = path or self.root / name
        path.mkdir(parents=True)
        (path / "agent.cfg").write_text("agent: {}\n")
        self.registry[name] = path
        self.definitions[name] = {
            "agent": {
                "name": name,
                "persona": {"mind": {"model_active": "orion"}, "dialects": {}},
                "secret": None if keyless else copy.deepcopy(manager or {}),
                "harness": {
                    "name": "fake-harness", "path": str(path / "harness"),
                    "config_file": "config.json",
                    "content": '{"model": "orion"}\n',
                    "path_var": "HARNESS_HOME", "auth_var": "API_KEY",
                    "wrapper_env": {},
                },
            },
            "runtime": {"agent_path": str(path), "secret_backend": copy.deepcopy(default_manager or {})},
        }
        return path

    def agents(self):
        return dict(self.registry)

    def locate(self, name):
        return self.registry[name]

    def load(self, name):
        return copy.deepcopy(self.definitions[name])

    def unregister(self, name):
        del self.registry[name]

    def configure(self, **changes):
        if "defaults" in changes:
            self.settings["defaults"]["secret"].update(changes["defaults"]["secret"])
        return copy.deepcopy(self.settings)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(inst, "_is_interactive", lambda: False)
    return home


@pytest.fixture
def store(home):
    return Store(home / "agents")


@pytest.fixture
def fake_manager(home, monkeypatch):
    """An argv-logging manager with stdin writes and selectable broken fetches."""
    bin_dir = home / "bin"
    bin_dir.mkdir()
    data = home / "encrypted-manager-data"
    data.mkdir()
    manager = bin_dir / "fake-manager"
    manager.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "root = pathlib.Path(os.environ['FAKE_MANAGER_DATA'])\n"
        "with (root / 'calls').open('a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "action = sys.argv[1]\n"
        "if action in ('fetch', 'show'):\n"
        "    if os.environ.get('FAKE_MANAGER_READ_STDIN'): (root / 'stdin').write_text(sys.stdin.read())\n"
        "    if (root / 'fail').exists():\n"
        "        print('diagnostic-containing-SECRET', file=sys.stderr); sys.exit(9)\n"
        "    if not (root / 'value').exists(): sys.exit(1)\n"
        "    print((root / 'value').read_text(), end='')\n"
        "elif action in ('store', 'insert', 'add'): (root / 'value').write_text(sys.stdin.read())\n"
        "else: sys.exit(2)\n"
    )
    manager.chmod(0o700)
    monkeypatch.setenv("FAKE_MANAGER_DATA", str(data))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    entry = "team/my agent's key;$(touch PWNED)"
    resolved = {
        "name": "test-manager", "entry": entry, "probe": str(manager),
        "fetch": f"{manager} fetch {entry}", "store": f"{manager} store {entry}",
    }
    monkeypatch.setattr(inst, "_check_secret_store", lambda *a, **k:
                        (data / "value").exists() or (data / "fail").exists())
    return resolved, data


def calls(data):
    path = data / "calls"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_keyless_install_is_private_and_never_makes_a_token_file(store, capsys):
    path = store.add()
    assert inst.install_agent(store, "orion") == path
    native = path / "harness" / "config.json"
    assert json.loads(native.read_text()) == {"model": "orion"}
    assert stat.S_IMODE(native.stat().st_mode) == 0o600
    assert stat.S_IMODE(native.parent.stat().st_mode) == 0o700
    assert not (path / "token").exists()
    assert not (path / ".secret_path").exists()
    wrapper = (path.parents[1] / ".bashrc").read_text()
    assert "API_KEY=" not in wrapper
    assert "credential" not in wrapper
    assert "keyless" in capsys.readouterr().out


def test_keyless_refuses_supplied_token_before_any_side_effects(store):
    path = store.add()
    with pytest.raises(ValueError, match="keyless"):
        inst.install_agent(store, "orion", token="SECRET")
    assert not (path / "harness").exists()


def test_auth_requires_manager(store):
    store.add(keyless=False)
    with pytest.raises(ValueError, match="no secret backend"):
        inst.install_agent(store, "orion")


def test_new_credential_uses_stdin_and_manager_entry_is_single_argv(store, fake_manager, home, capsys):
    manager, data = fake_manager
    path = store.add(manager=manager, keyless=False)
    inst.install_agent(store, "orion", token="SECRET with $'punctuation")
    assert calls(data) == [["store", manager["entry"]], ["fetch", manager["entry"]]]
    assert data.joinpath("value").read_text() == "SECRET with $'punctuation\n"
    assert "SECRET" not in capsys.readouterr().out
    for file in path.rglob("*"):
        if file.is_file():
            assert "SECRET" not in file.read_text()
    assert "SECRET" not in (home / ".bashrc").read_text()
    assert not (home / "PWNED").exists()


def test_agent_selected_backend_drives_preflight_import_readback_and_wrapper(store, fake_manager, home, monkeypatch):
    manager, data = fake_manager
    default = {
        "name": "unused_default", "entry": "default/key", "probe": "unused-default",
        "fetch": "unused-default fetch default/key", "store": "unused-default store default/key",
    }
    path = store.add(manager=manager, default_manager=default, keyless=False)
    readiness = Mock(return_value=False)
    probe = Mock(wraps=inst._secret_probe)
    monkeypatch.setattr(inst, "_check_secret_store", readiness)
    monkeypatch.setattr(inst, "_secret_probe", probe)
    plan = inst.prepare_installation(store.load("orion"), token="SELECTED_SECRET")
    assert plan.manager == manager
    probe.assert_called_once_with(manager)
    readiness.assert_called_once_with(manager, writing=True)
    assert not (path / "harness").exists()
    inst.commit_credential(plan)
    assert calls(data) == [["store", manager["entry"]], ["fetch", manager["entry"]]]
    inst.apply_installation(plan)
    wrapper = (home / ".bashrc").read_text()
    assert shlex.join(inst._command_argv(manager, "fetch")) in wrapper
    assert "unused-default" not in wrapper
    assert "SELECTED_SECRET" not in wrapper


@pytest.mark.parametrize("token", [None, "SECRET"])
def test_keyless_agent_ignores_runtime_default_backend(store, fake_manager, token):
    manager, data = fake_manager
    path = store.add(default_manager=manager)
    if token is None:
        assert inst.install_agent(store, "orion") == path
    else:
        with pytest.raises(ValueError, match="keyless"):
            inst.install_agent(store, "orion", token=token)
        assert not (path / "harness").exists()
    assert calls(data) == []


def test_existing_credential_is_reused_without_a_write(store, fake_manager):
    manager, data = fake_manager
    data.joinpath("value").write_text("already-saved\n")
    store.add(manager=manager, keyless=False)
    inst.install_agent(store, "orion", install_wrapper=False)
    assert calls(data) == [["fetch", manager["entry"]]]


def test_missing_entry_without_tty_fails_before_install(store, fake_manager, home):
    manager, data = fake_manager
    path = store.add(manager=manager, keyless=False)
    with pytest.raises(ValueError, match="supply a token|terminal"):
        inst.install_agent(store, "orion")
    assert calls(data) == []
    assert not (path / "harness").exists()
    assert not (home / ".bashrc").exists()


def test_missing_entry_can_be_prompted_at_a_terminal(store, fake_manager, monkeypatch):
    manager, data = fake_manager
    store.add(manager=manager, keyless=False)
    monkeypatch.setattr(inst, "_is_interactive", lambda: True)
    monkeypatch.setattr(inst.getpass, "getpass", lambda prompt: "prompted-key")
    inst.install_agent(store, "orion", install_wrapper=False)
    assert data.joinpath("value").read_text() == "prompted-key\n"


@pytest.mark.parametrize("entry", ["", "../token", "a/../token", "/token", "-clip", "a//b", "a/./b", "a\nb", "a\x00b", "a\rb"])
def test_bad_entries_are_rejected(entry):
    with pytest.raises(ValueError, match="store-relative"):
        inst._command_argv({"entry": entry, "fetch": "pass show " + entry}, "fetch")


@pytest.mark.parametrize("prefix", ["pass show", "gopass show -o", "prs show --no-interact --first"])
def test_builtin_resolved_fetch_commands_keep_quoted_entry_whole(prefix):
    entry = "team/a b'\";$HOME$(touch never)"
    manager = {"entry": entry, "fetch": prefix + " " + entry}
    assert inst._command_argv(manager, "fetch") == shlex.split(prefix) + [entry]


def test_custom_explicitly_quoted_commands_are_split_not_shell_executed():
    manager = {"entry": "team/key with space", "fetch": "custom --entry 'team/key with space' --quiet"}
    assert inst._command_argv(manager, "fetch") == ["custom", "--entry", "team/key with space", "--quiet"]


@pytest.mark.parametrize("command", ["", "'unclosed", "\x00"])
def test_invalid_commands_fail_cleanly(command):
    with pytest.raises(ValueError):
        inst._command_argv({"entry": "x", "fetch": command}, "fetch")


def test_manager_errors_and_timeouts_never_echo_output(fake_manager, monkeypatch):
    manager, data = fake_manager
    data.joinpath("fail").touch()
    with pytest.raises(ValueError) as error:
        inst.fetch_secret(manager)
    assert "SECRET" not in str(error.value)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("SECRET", 120, output=b"SECRET", stderr=b"SECRET")
    monkeypatch.setattr(inst.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="gpg-agent") as error:
        inst.fetch_secret(manager)
    assert "SECRET" not in str(error.value)


def run_wrapper(home, wrapper, *, shell="/bin/sh"):
    file = home / "wrapper.sh"
    file.write_text(wrapper + '\norion "argument with spaces"\n')
    return subprocess.run([shell, str(file)], cwd=home, capture_output=True, text=True)


def harness_probe(home, monkeypatch):
    executable = home / "fake harness's command"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({'key': os.environ.get('API_KEY'), 'path': os.environ.get('HARNESS_HOME'), 'extra': os.environ.get('EXTRA'), 'args': sys.argv[1:]}))\n"
    )
    executable.chmod(0o700)
    return {"name": str(executable), "path": str(home / "a b'$(touch PWNED)"),
            "path_var": "HARNESS_HOME", "auth_var": "API_KEY",
            "wrapper_env": {"EXTRA": "literal ' \" $HOME `touch PWNED`\nsecond line"}}


def test_wrapper_executes_with_literal_values_and_forwards_arguments(home, fake_manager, monkeypatch):
    manager, data = fake_manager
    data.joinpath("value").write_text("secret' $literal\n")
    harness = harness_probe(home, monkeypatch)
    wrapper = inst.render_wrapper("orion", harness, manager)
    result = run_wrapper(home, wrapper)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"key": "secret' $literal", "path": harness["path"],
                                        "extra": harness["wrapper_env"]["EXTRA"], "args": ["argument with spaces"]}
    assert calls(data) == [["fetch", manager["entry"]]]
    assert not (home / "PWNED").exists()


@pytest.mark.parametrize("configured_auth", [None, "", "wrapper-auth"])
def test_keyless_wrapper_preserves_inherited_or_explicit_auth(home, monkeypatch, configured_auth):
    monkeypatch.setenv("API_KEY", "inherited-auth")
    harness = harness_probe(home, monkeypatch)
    if configured_auth is not None:
        harness["wrapper_env"]["API_KEY"] = configured_auth
    before = copy.deepcopy(harness)
    wrapper = inst.render_wrapper("orion", harness)
    result = run_wrapper(home, wrapper)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["key"] == ("inherited-auth" if configured_auth is None else configured_auth)
    assert harness == before
    assert "_pg_token" not in wrapper


@pytest.mark.parametrize("value", [None, False, 0, {}, []])
def test_wrapper_refuses_nonstring_environment_values(value):
    with pytest.raises(ValueError, match="must be strings"):
        inst.render_wrapper("orion", {"name": "true", "wrapper_env": {"EXTRA": value}})


@pytest.mark.parametrize("mode", ["error", "empty"])
def test_wrapper_does_not_launch_on_failed_or_empty_fetch(home, fake_manager, monkeypatch, mode):
    manager, data = fake_manager
    data.joinpath("value").write_text("")
    if mode == "error":
        data.joinpath("fail").touch()
    result = run_wrapper(home, inst.render_wrapper("orion", harness_probe(home, monkeypatch), manager))
    assert result.returncode != 0
    assert not result.stdout
    assert "harness not started" in result.stderr
    assert "SECRET" not in result.stderr


@pytest.mark.parametrize("field,value", [("path_var", "BAD;touch X"), ("auth_var", "A=1"), ("wrapper_env", {"A-B": "x"})])
def test_wrapper_rejects_bad_variable_names(field, value):
    harness = {"name": "true", "path": "/tmp", field: value}
    with pytest.raises(ValueError, match="POSIX"):
        inst.render_wrapper("orion", harness)


def test_wrapper_refuses_auth_shadowing(fake_manager):
    manager, _ = fake_manager
    with pytest.raises(ValueError, match="shadow"):
        inst.render_wrapper("orion", {"name": "true", "auth_var": "KEY", "wrapper_env": {"KEY": "wrong"}}, manager)


@pytest.mark.parametrize("name", SHELL_WORDS)
def test_shell_words_are_storage_names_but_wrapper_install_refuses_before_writes(store, home, monkeypatch, name):
    assert validate_id(name) == name
    assert validate_id(name, "persona") == name
    path = store.add(name=name, manager={"name": "must-not-run"}, keyless=False)
    probe = Mock(side_effect=AssertionError("manager must not run"))
    monkeypatch.setattr(inst, "_secret_probe", probe)
    with pytest.raises(ValueError, match="reserved shell word"):
        inst.install_agent(store, name, token="SECRET")
    with pytest.raises(ValueError, match="reserved shell word"):
        inst.install_shell_wrapper(name, {"name": "true"})
    probe.assert_not_called()
    assert not (path / "harness").exists()
    assert not (home / ".bashrc").exists()


@pytest.mark.parametrize("name", SHELL_WORDS)
def test_shell_word_can_install_native_snapshot_without_a_wrapper(store, home, name):
    path = store.add(name=name)
    assert inst.install_agent(store, name, install_wrapper=False) == path
    assert (path / "harness" / "config.json").is_file()
    assert not (home / ".bashrc").exists()


@pytest.mark.parametrize("name", ["", "default", "9agent", "café", "bad-name", "bad/name", "a" * 65])
def test_storage_name_validation_keeps_ascii_length_and_exact_default_reservation(name):
    with pytest.raises(ValueError):
        validate_id(name)
    with pytest.raises(ValueError):
        inst.render_wrapper(name, {"name": "true"})


@pytest.mark.parametrize("name", ["Default", "DEFAULT", "If", "_", "a" * 64])
def test_case_distinct_and_maximum_length_names_remain_valid(name):
    assert validate_id(name) == name
    assert f"{name}() (" in inst.render_wrapper(name, {"name": "true"})


def test_internal_wrapper_variable_collisions_are_refused():
    with pytest.raises(ValueError, match="internal"):
        inst.render_wrapper("orion", {"name": "true", "wrapper_env": {"_pg_token": "clobber"}})


def test_wrapper_disables_inherited_xtrace_before_fetch(home, fake_manager):
    manager, data = fake_manager
    data.joinpath("value").write_text("NEVER-TRACE-THIS-SECRET\n")
    wrapper = inst.render_wrapper("orion", {"name": "true", "auth_var": "API_KEY"}, manager)
    file = home / "trace.sh"
    file.write_text(wrapper + "\norion\n")
    result = subprocess.run(["/bin/sh", "-x", str(file)], capture_output=True, text=True)
    assert result.returncode == 0
    assert "NEVER-TRACE-THIS-SECRET" not in result.stdout + result.stderr
    assert "_pg_token=" not in result.stderr


@pytest.mark.parametrize("filename", [None, ""])
def test_wrapper_only_harness_needs_no_native_file(store, filename):
    path = store.add()
    harness = store.definitions["orion"]["agent"]["harness"]
    harness["config_file"] = filename
    harness["content"] = ""
    inst.install_agent(store, "orion")
    assert list((path / "harness").iterdir()) == [path / "harness" / inst._OWNER_FILE]


def test_missing_manager_executable_fails_without_installing(store):
    path = store.add(manager={"name": "absent", "probe": "not-a-real-installed-manager-12345",
                              "entry": "team/key", "fetch": "absent show team/key", "store": "absent put team/key"}, keyless=False)
    with pytest.raises(ValueError, match="not installed"):
        inst.install_agent(store, "orion", token="SECRET")
    assert not (path / "harness").exists()


def test_store_failure_does_not_log_token_or_install(store, fake_manager):
    manager, data = fake_manager
    manager["store"] = "/bin/false " + manager["entry"]
    path = store.add(manager=manager, keyless=False)
    with pytest.raises(ValueError, match="could not store") as error:
        inst.install_agent(store, "orion", token="SECRET")
    assert "SECRET" not in str(error.value)
    assert not (path / "harness").exists()
    assert not calls(data)


def test_store_readback_mismatch_fails_before_native_writes(store, fake_manager):
    manager, data = fake_manager
    manager["store"] = "/bin/true " + manager["entry"]
    data.joinpath("value").write_text("different credential\n")
    path = store.add(manager=manager, keyless=False)
    with pytest.raises(ValueError, match="just stored"):
        inst.install_agent(store, "orion", token="SECRET")
    assert not (path / "harness").exists()


def test_read_token_strips_file_newline_but_rejects_empty_and_multiline(home):
    file = home / "input-token"
    file.write_text("provided-token\n")
    assert inst.read_token(file) == "provided-token"
    for content in ("\n", "first\nsecond", "nul\x00token"):
        file.write_text(content)
        with pytest.raises(ValueError):
            inst.read_token(file)


def _assert_nonblocking_open(monkeypatch):
    original = inst.os.open
    def checked(path, flags, *args, **kwargs):
        # Assert before opening: a regression fails rather than hanging this test.
        assert flags & os.O_NONBLOCK
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(inst.os, "open", checked)


def test_public_token_reader_rejects_fifo_without_blocking(home, monkeypatch):
    fifo = home / "token.fifo"
    os.mkfifo(fifo)
    _assert_nonblocking_open(monkeypatch)
    with pytest.raises(ValueError, match="regular file"):
        inst.read_token(fifo)


def test_public_token_reader_rejects_oversized_nonregular_and_invalid_utf8(home):
    file = home / "token"
    file.write_bytes(b"x" * (inst._TOKEN_LIMIT + 1))
    with pytest.raises(ValueError, match="too large"):
        inst.read_token(file)
    file.write_bytes(b"SECRET" + bytes([255]))
    with pytest.raises(ValueError, match="UTF-8") as error:
        inst.read_token(file)
    assert "SECRET" not in str(error.value)
    with pytest.raises(ValueError):
        inst.read_token(home)


def test_fifo_ownership_marker_is_an_occupied_dir_not_a_hang(store, monkeypatch):
    path = store.add()
    (path / "harness").mkdir()
    os.mkfifo(path / "harness" / inst._OWNER_FILE)
    _assert_nonblocking_open(monkeypatch)
    with pytest.raises(ValueError, match="occupied"):
        inst.install_agent(store, "orion", install_wrapper=False)


def test_fifo_rc_is_rejected_before_credential_write(store, fake_manager, home, monkeypatch):
    manager, data = fake_manager
    path = store.add(manager=manager, keyless=False)
    os.mkfifo(home / ".bashrc")
    _assert_nonblocking_open(monkeypatch)
    with pytest.raises(ValueError, match="regular file"):
        inst.install_agent(store, "orion", token="SECRET")
    assert not calls(data)
    assert not (path / "harness").exists()


def test_oversized_ownership_marker_and_rc_fail_before_writes(store, home, monkeypatch):
    path = store.add()
    native = path / "harness"
    native.mkdir()
    marker = native / inst._OWNER_FILE
    marker.write_text("x" * (inst._OWNER_LIMIT + 1))
    with pytest.raises(ValueError, match="occupied"):
        inst.install_agent(store, "orion", install_wrapper=False)
    marker.unlink()
    monkeypatch.setattr(inst, "_RC_LIMIT", 32)
    (home / ".bashrc").write_text("x" * 33)
    with pytest.raises(ValueError, match="too large"):
        inst.install_agent(store, "orion")
    assert not (native / "config.json").exists()


def test_verification_does_not_forward_credentials_on_redirect(monkeypatch, capsys):
    opened = []
    class Transport(inst.urllib.request.BaseHandler):
        handler_order = 100
        def https_open(self, request):
            opened.append((request.full_url, request.get_header("Authorization")))
            headers = Message()
            source = request.full_url == "https://original.invalid/check"
            if source:
                headers["Location"] = "https://other.invalid/steal"
            response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302 if source else 200)
            response.msg = "Found" if source else "OK"
            return response
    original = inst.urllib.request.build_opener
    def build(*handlers):
        # Preserve production redirect handlers: dropping the no-redirect handler
        # would make this test observe a second request carrying the credential.
        return original(Transport(), *handlers)
    monkeypatch.setattr(inst.urllib.request, "build_opener", build)
    agent = {"harness": {"protocol": "chat"}, "persona": {
        "mind": {"model_active": "active-model", "dialects": {
            "chat": {"verify": {"check_uri": "https://original.invalid/check", "key_header": "Authorization: Bearer"}}
        }}}}
    inst._verify_token(agent, "SECRET")
    assert opened == [("https://original.invalid/check", "Bearer SECRET")]
    assert "SECRET" not in capsys.readouterr().err


def test_manager_fetch_cannot_consume_harness_stdin(home, fake_manager, monkeypatch):
    manager, data = fake_manager
    data.joinpath("value").write_text("SECRET\n")
    monkeypatch.setenv("FAKE_MANAGER_READ_STDIN", "1")
    assert inst.fetch_secret(manager) == "SECRET"
    assert data.joinpath("stdin").read_text() == ""
    wrapper = inst.render_wrapper("orion", {"name": "/bin/cat", "auth_var": "API_KEY"}, manager)
    file = home / "stdin.sh"
    file.write_text(wrapper + "\norion\n")
    result = subprocess.run(["/bin/sh", str(file)], input="original harness input\n", capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout == "original harness input\n"
    assert data.joinpath("stdin").read_text() == ""
    assert "SECRET" not in result.stderr


def test_removal_rejects_noncanonical_registered_path(store, home):
    path = store.add()
    external = home / "external"
    external.mkdir()
    (store.root / "link").symlink_to(external, target_is_directory=True)
    store.registry["orion"] = store.root / "link" / ".." / "orion"
    with pytest.raises(ValueError, match="must not contain"):
        inst.remove_agent(store, "orion", confirm=True)
    assert path.is_dir()


def test_public_wrapper_refuses_nul_path_and_nonmapping_environment():
    with pytest.raises(ValueError, match="NUL"):
        inst.render_wrapper("orion", {"name": "true", "path_var": "CONFIG_HOME", "path": "bad\x00path"})
    with pytest.raises(ValueError, match="mapping"):
        inst.render_wrapper("orion", {"name": "true", "wrapper_env": ["not a mapping"]})


def test_reinstall_replaces_same_path_marker_even_after_rename(store, home):
    store.add()
    inst.install_agent(store, "orion")
    store.registry["renamed"] = store.registry.pop("orion")
    store.definitions["renamed"] = store.definitions.pop("orion")
    inst.install_agent(store, "renamed")
    rc = (home / ".bashrc").read_text()
    assert "orion()" not in rc
    assert rc.count("renamed()") == 1
    subprocess.run(["/bin/sh", "-n", str(home / ".bashrc")], check=True)


@pytest.mark.parametrize("shell,file", [("bash", ".bashrc"), ("zsh", ".zshrc"), ("ash", ".profile"), ("dash", ".profile"), ("busybox", ".profile"), ("sh", ".bashrc")])
def test_shell_startup_conventions(store, home, monkeypatch, shell, file):
    store.add()
    monkeypatch.setenv("SHELL", "/usr/bin/" + shell)
    inst.install_agent(store, "orion")
    assert (home / file).exists()


def test_unknown_shell_warns(store, monkeypatch, capsys):
    store.add()
    monkeypatch.setenv("SHELL", "/bin/fish")
    inst.install_agent(store, "orion")
    assert "/bin/fish" in capsys.readouterr().out


@pytest.mark.parametrize("occupied", ["directory", "expected_file", "file"])
def test_occupied_native_directory_is_not_overwritten(store, occupied):
    path = store.add()
    native = path / "harness"
    if occupied == "file":
        native.write_text("unrelated")
    else:
        native.mkdir()
        (native / ("config.json" if occupied == "expected_file" else "unrelated")).write_text("original")
    with pytest.raises(ValueError, match="occupied"):
        inst.install_agent(store, "orion")
    if occupied == "file":
        assert native.read_text() == "unrelated"
    else:
        assert next(native.iterdir()).read_text() == "original"


def test_private_write_tightens_mode_and_follows_user_selected_symlink(home):
    file = home / "private"
    file.write_text("old")
    file.chmod(0o666)
    inst._write_private(file, "new")
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    link = home / "link"
    link.symlink_to(file)
    inst._write_private(link, "through-link")
    assert link.is_symlink()
    assert file.read_text() == "through-link"


@pytest.mark.parametrize("filename", ["../outside.json", "nested/../config.json", "/outside.json", "bad\x00name"])
def test_native_config_refuses_absolute_traversal_and_nul_before_writes(store, home, filename):
    path = store.add()
    harness = store.definitions["orion"]["agent"]["harness"]
    harness["config_file"] = filename
    with pytest.raises(ValueError, match="relative"):
        inst.install_agent(store, "orion")
    assert not (path / "harness").exists()
    assert not (home / ".bashrc").exists()


def test_absolute_native_config_is_refused_even_inside_harness_dir(store):
    path = store.add()
    store.definitions["orion"]["agent"]["harness"]["config_file"] = str(path / "harness" / "config.json")
    with pytest.raises(ValueError, match="relative"):
        inst.install_agent(store, "orion")
    assert not (path / "harness").exists()


@pytest.mark.parametrize("filename", [False, 0, [], {}])
def test_native_config_refuses_nonstring_filenames(store, filename):
    path = store.add()
    store.definitions["orion"]["agent"]["harness"]["config_file"] = filename
    with pytest.raises(ValueError, match="relative filename"):
        inst.install_agent(store, "orion")
    assert not (path / "harness").exists()


def test_native_config_joins_relative_subdirectories_to_harness_not_cwd(store, home, monkeypatch):
    path = store.add()
    cwd = home / "working"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    harness = store.definitions["orion"]["agent"]["harness"]
    harness["config_file"] = "nested/settings.json"
    plan = inst.prepare_installation(store.load("orion"), install_wrapper=False)
    assert plan.native_file == path / "harness" / "nested" / "settings.json"
    assert not (path / "harness").exists()
    inst.apply_installation(plan)
    assert plan.native_file.read_text() == harness["content"]
    assert not (cwd / "nested").exists()


def test_native_config_directory_must_stay_at_registered_harness_dir(store, home):
    store.add()
    harness = store.definitions["orion"]["agent"]["harness"]
    harness["path"] = str(home / "outside")
    with pytest.raises(ValueError, match="harness/"):
        inst.install_agent(store, "orion")


def test_native_directory_symlink_receives_installation(store, home):
    path = store.add()
    external = home / "external"
    external.mkdir()
    (path / "harness").symlink_to(external, target_is_directory=True)
    inst.install_agent(store, "orion")
    assert (external / "config.json").is_file()
    assert (external / inst._OWNER_FILE).is_file()


@pytest.mark.parametrize("link_location", ["file", "parent"])
def test_relative_native_filename_preserves_user_selected_links(store, home, link_location):
    path = store.add()
    native = path / "harness"
    native.mkdir()
    (native / inst._OWNER_FILE).write_text(json.dumps({"agent_path": str(path)}))
    external = home / "external-config"
    external.mkdir()
    target = external / "config.json"
    target.write_text("existing")
    harness = store.definitions["orion"]["agent"]["harness"]
    if link_location == "file":
        link = native / "config.json"
        link.symlink_to(target)
    else:
        link = native / "nested"
        link.symlink_to(external, target_is_directory=True)
        harness["config_file"] = "nested/config.json"
    inst.install_agent(store, "orion", install_wrapper=False)
    assert link.is_symlink()
    assert target.read_text() == harness["content"]


def test_removal_defaults_to_no_and_retains_registration(store):
    path = store.add()
    assert inst.remove_agent(store, "orion") is False
    assert path.is_dir() and "orion" in store.agents()


def test_confirmed_removal_retains_manager_entry_and_unrelated_rc(store, fake_manager, home, monkeypatch):
    manager, data = fake_manager
    path = store.add(manager=manager, keyless=False)
    (home / ".bashrc").write_text("export UNRELATED=1\n")
    inst.install_agent(store, "orion", token="saved")
    before = calls(data)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    assert inst.remove_agent(store, "orion", confirm=lambda q: str(path) in q) is True
    assert not path.exists() and "orion" not in store.agents()
    assert calls(data) == before
    assert data.joinpath("value").read_text() == "saved\n"
    assert (home / ".bashrc").read_text() == "export UNRELATED=1\n"


@pytest.mark.parametrize("which", ["root", "home", "cwd", "store"])
def test_removal_refuses_dangerous_registered_directories(store, home, monkeypatch, which):
    cwd = home / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    target = {"root": Path("/"), "home": home, "cwd": cwd, "store": store.root}[which]
    store.registry["orion"] = target
    with pytest.raises(ValueError, match="unsafe"):
        inst.remove_agent(store, "orion", confirm=True)
    assert target.is_dir()


def test_removal_refuses_ancestor_of_current_directory(store, monkeypatch):
    path = store.add()
    child = path / "working"
    child.mkdir()
    monkeypatch.chdir(child)
    with pytest.raises(ValueError, match="unsafe"):
        inst.remove_agent(store, "orion", confirm=True)


def test_removal_requires_registry_and_regular_agent_config(store):
    with pytest.raises(ValueError, match="registered"):
        inst.remove_agent(store, "missing", confirm=True)
    path = store.add()
    (path / "agent.cfg").unlink()
    with pytest.raises(ValueError, match="agent.cfg"):
        inst.remove_agent(store, "orion", confirm=True)


def test_removal_refuses_shared_or_overlapping_agent_directories(store):
    path = store.add()
    store.registry["other"] = path / "nested"
    with pytest.raises(ValueError, match="overlaps"):
        inst.remove_agent(store, "orion", confirm=True)


def test_removal_unlinks_registered_directory_symlink_only(store, home):
    path = store.add()
    linked = home / "linked"
    linked.symlink_to(path, target_is_directory=True)
    store.registry["orion"] = linked
    assert inst.remove_agent(store, "orion", confirm=True)
    assert not linked.exists()
    assert path.exists()
    assert "orion" not in store.registry


def test_removal_unlinks_nested_symlink_without_deleting_target(store, home):
    path = store.add()
    outside = home / "valuable"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (path / "link").symlink_to(outside, target_is_directory=True)
    inst.remove_agent(store, "orion", confirm=True)
    assert (outside / "keep").read_text() == "keep"


def test_confirmation_rechecks_path_before_deletion(store, home):
    original = store.add()
    def change_registry(question):
        store.registry["orion"] = home
        return True
    with pytest.raises(ValueError, match="unsafe"):
        inst.remove_agent(store, "orion", confirm=change_registry)
    assert original.is_dir()


def test_setup_without_manager_is_keyless(store, capsys):
    assert inst.setup_store(store, secret_root="team/keys")["defaults"]["secret"] == {
        "backend": None, "root": "team/keys"}
    assert "No credential backend" in capsys.readouterr().out


def test_setup_persists_only_after_manager_initialization(store, monkeypatch):
    initialized = []
    monkeypatch.setattr(inst, "_ensure_secret_store", initialized.append)
    assert inst.setup_store(store, backend="pass", secret_root="team")["defaults"]["secret"] == {
        "backend": "pass", "root": "team"}
    assert initialized == ["pass"]
    def fail(backend):
        raise ValueError("manager not ready")
    monkeypatch.setattr(inst, "_ensure_secret_store", fail)
    with pytest.raises(ValueError):
        inst.setup_store(store, backend="prs", secret_root="changed")
    assert store.settings["defaults"]["secret"] == {"backend": "pass", "root": "team"}


def test_setup_uses_nested_backend_default_and_preserves_root(store, monkeypatch):
    store.settings["defaults"]["secret"] = {"backend": "pass", "root": "retained/root"}
    initialized = []
    monkeypatch.setattr(inst, "_ensure_secret_store", initialized.append)
    result = inst.setup_store(store)
    assert initialized == ["pass"]
    assert result["defaults"]["secret"] == {"backend": "pass", "root": "retained/root"}


@pytest.mark.parametrize("root", ["", "../bad", "/absolute"])
def test_setup_rejects_invalid_root_before_manager_initialization(store, monkeypatch, root):
    initialized = Mock()
    monkeypatch.setattr(inst, "_ensure_secret_store", initialized)
    before = copy.deepcopy(store.settings)
    with pytest.raises(ValueError, match="store-relative"):
        inst.setup_store(store, backend="pass", secret_root=root)
    initialized.assert_not_called()
    assert store.settings == before


def test_setup_real_store_persists_only_nested_overrides(home, monkeypatch):
    import yapyon
    from persona_grata import AgentStore

    store = AgentStore(home / "configured-store")
    monkeypatch.setattr(inst, "_ensure_secret_store", lambda backend: None)
    result = inst.setup_store(store, backend="pass", secret_root="team/keys")
    assert result["defaults"]["secret"] == {"backend": "pass", "root": "team/keys"}
    assert yapyon.loads_record(store.registry_path.read_text()) == {
        "format": "agents-yapyon-v0.6.5",
        "defaults": {"secret": {"backend": "pass", "root": "team/keys"}}}


def test_wizard_missing_store_without_tty_fails_fast(monkeypatch):
    monkeypatch.setattr(inst.shutil, "which", lambda executable: "/bin/" + executable)
    monkeypatch.setattr(inst, "_store_missing", lambda backend: True)
    monkeypatch.setattr(inst, "_is_interactive", lambda: False)
    with pytest.raises(ValueError, match="terminal"):
        inst._ensure_secret_store("pass")


def test_existing_pass_store_is_never_reinitialized(home, monkeypatch):
    directory = home / ".password-store"
    directory.mkdir()
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(directory))
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: pytest.fail("existing store must not be probed or reset"))
    assert inst._store_missing("pass") is False


def test_pass_wizard_preserves_gpg_vocabulary_and_safe_argv(home, monkeypatch, capsys):
    key_id = "key with spaces; $(not a shell)"
    responses = iter(["i", key_id])
    monkeypatch.setattr("builtins.input", lambda question: next(responses))
    launched = []
    monkeypatch.setattr(inst, "_run_manager", lambda argv, what: launched.append(argv))
    inst._wizard_pass()
    assert launched == [["pass", "init", key_id]]
    assert "GPG key" in capsys.readouterr().out


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_actual_store_keyless_create_install_rename_remove(home, harness):
    import yapyon
    from persona_grata.store import AgentStore

    store = AgentStore(home / "real-store")
    definition = yapyon.parse(
        'agent<agent._DEFAULT_>:\n  persona<persona.ollama>:\n'
        '    mind:\n      model_list: ["example-model"]\n'
        f'  harness<harness.{harness}>: {{}}\n  secret: None\n')
    path = store.create("orion", definition)
    assert inst.install_agent(store, "orion") == path
    assert (path / "harness").is_dir()
    store.rename("orion", "renamed")
    assert inst.install_agent(store, "renamed") == path
    assert "orion()" not in (home / ".bashrc").read_text()
    assert "renamed()" in (home / ".bashrc").read_text()
    assert inst.remove_agent(store, "renamed", confirm=True)
    assert not path.exists()
    assert store.agents() == {}
    assert (store.root / "shared" / "persona" / "ollama.tmpl").is_file()


def test_actual_store_removal_unlinks_registered_agent_symlink_only(home):
    import yapyon
    from persona_grata.store import AgentStore

    store = AgentStore(home / "real-store")
    definition = yapyon.parse(
        'agent<agent._DEFAULT_>:\n  persona<persona.ollama>: {}\n'
        '  harness<harness.claude>: {}\n  secret: None\n')
    path = store.create("orion", definition)
    target = home / "preserved-agent"
    path.rename(target)
    path.symlink_to(target, target_is_directory=True)
    assert inst.remove_agent(store, "orion", confirm=True)
    assert not path.exists()
    assert target.is_dir()
    assert store.agents() == {}


@pytest.mark.parametrize("backend,store_args,fetch_args", [
    ("pass", ["insert", "-m", "-f"], ["show"]),
    ("gopass", ["insert"], ["show", "-o"]),
    ("prs", ["add", "--no-interact", "--stdin"], ["show", "--no-interact", "--first"]),
])
def test_actual_store_complete_secret_uses_presets_without_shell_splitting(home, fake_manager, backend, store_args, fetch_args):
    import yapyon
    from persona_grata.store import AgentStore

    manager, data = fake_manager
    executable = home / "bin" / backend
    executable.write_text(Path(manager["probe"]).read_text())
    executable.chmod(0o700)
    store = AgentStore(home / "real-store")
    store.configure(defaults={"secret": {"backend": backend, "root": "team root's"}})
    entry = "custom key's name;$(touch PWNED)"
    definition = yapyon.parse(
        'agent<agent._DEFAULT_>:\n  persona<persona.ollama>:\n'
        '    mind:\n      model_list: ["example-model"]\n'
        '  harness<harness.codex>: {}\n  secret<>:\n    entry: ' + json.dumps(entry) + '\n')
    path = store.create("orion", definition)
    inst.install_agent(store, "orion", token="SECRET", install_wrapper=False)
    expected = entry
    assert calls(data) == [[*store_args, expected], [*fetch_args, expected]]
    assert not (home / "PWNED").exists()
    assert not (path / "token").exists()
    assert not (path / ".secret_path").exists()


def test_setup_materializes_keyless_registry(home):
    from persona_grata.store import AgentStore

    store = AgentStore(home / "keyless-store")
    assert not store.registry_path.exists()
    inst.setup_store(store)
    assert store.registry_path.exists()
    assert store.settings["defaults"]["secret"]["backend"] is None


def test_verification_rejection_happens_before_storing(store, fake_manager, monkeypatch):
    manager, data = fake_manager
    path = store.add(manager=manager, keyless=False)
    agent = store.definitions["orion"]["agent"]
    agent["harness"]["protocol"] = "chat"
    agent["persona"]["dialects"] = {"chat": {"verify": {"check_uri": "https://example.invalid/check", "key_header": "Authorization: Bearer"}}}
    def reject(request, timeout):
        assert request.get_header("Authorization") == "Bearer SECRET"
        raise urllib.error.HTTPError(request.full_url, 401, "SECRET", {}, None)
    monkeypatch.setattr(inst, "_open_verification", reject)
    with pytest.raises(ValueError, match="rejected") as error:
        inst.install_agent(store, "orion", token="SECRET", verify=True)
    assert "SECRET" not in str(error.value)
    assert not calls(data)
    assert not (path / "harness").exists()

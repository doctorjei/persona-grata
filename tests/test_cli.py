"""CLI contract tests isolate lifecycle dispatch from manager/network side effects."""

import builtins
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
import yapyon

from persona_grata import cli


@pytest.fixture
def lifecycle(monkeypatch, tmp_path):
    state = SimpleNamespace(root=tmp_path / "store", entries={}, calls=[], constructed=[], prepared=[])

    def normalized(definition, name):
        tree = yapyon.merge(definition)
        tree.merge(yapyon.parse(f'agent:\n  name: "{name}"\n'))
        return tree

    class Store:
        def __init__(self, root=None):
            self.root = Path(root or os.environ.get("AGENT_STORE_DIR") or state.root)
            self.settings = {}
            state.constructed.append(self.root)

        def agents(self):
            return dict(state.entries)

        def locate(self, name):
            if name not in state.entries:
                raise ValueError(f"unknown agent {name!r}")
            return state.entries[name]

        def create(self, name, definition, *, path=None):
            if name in state.entries:
                raise ValueError(f"agent {name!r} already exists")
            directory = Path(path) if path is not None else self.root / name
            directory.mkdir(parents=True)
            tree = normalized(definition, name)
            (directory / "agent.cfg").write_text(tree.to_source())
            state.entries[name] = directory
            state.calls.append(("create", name, tree, path))
            return directory

        def update(self, name, definition):
            directory = self.locate(name)
            tree = normalized(definition, name)
            (directory / "agent.cfg").write_text(tree.to_source())
            state.calls.append(("update", name, tree))
            return directory

        def rename(self, old, new):
            if new in state.entries:
                raise ValueError("agent already exists")
            directory = state.entries.pop(old)
            state.entries[new] = directory
            tree = normalized(yapyon.parse_file(directory / "agent.cfg"), new)
            (directory / "agent.cfg").write_text(tree.to_source())
            state.calls.append(("rename", old, new))

        def prepare_create(self, name, definition, *, path=None):
            if name in state.entries:
                raise ValueError(f"agent {name!r} already exists")
            directory = Path(path) if path is not None else self.root / name
            candidate = SimpleNamespace(name=name, path=directory, definition=normalized(definition, name),
                                        previous_name=None,
                                        resolved={"agent": {"name": name}, "runtime": {"agent_path": str(directory)}})
            state.prepared.append(candidate)
            return candidate

        def prepare_update(self, name, definition, *, new_name=None):
            directory = self.locate(name)
            selected = new_name or name
            candidate = SimpleNamespace(name=selected, path=directory, definition=normalized(definition, selected),
                                        previous_name=name,
                                        resolved={"agent": {"name": selected}, "runtime": {"agent_path": str(directory)}})
            state.prepared.append(candidate)
            return candidate

        def commit(self, candidate):
            if candidate.previous_name is None:
                return self.create(candidate.name, candidate.definition, path=candidate.path)
            path = self.update(candidate.previous_name, candidate.definition)
            if candidate.name != candidate.previous_name:
                self.rename(candidate.previous_name, candidate.name)
            return path

        def load(self, name):
            pytest.fail("CLI must use its prepared candidate instead of reloading it")

    def make_agent(persona, harness, *, name=None):
        state.calls.append(("make", persona, harness, name))
        return yapyon.parse(
            f'agent:\n  name: "{name or persona + "_" + harness}"\n'
            f"  persona<{persona}.persona>: {{}}\n"
            f"  harness<{harness}.harness>: {{}}\n"
            "  secret:\n    entry: y\"{name}\"\n"
        )

    state.install = Mock()
    state.preflight = Mock(side_effect=lambda resolved, **kwargs: SimpleNamespace(
        name=resolved["agent"]["name"], resolved=resolved, **kwargs))
    state.credentials = Mock()
    state.remove = Mock()
    state.setup = Mock()
    store_module = ModuleType("persona_grata.store")
    store_module.AgentStore = Store
    store_module.make_agent = make_agent
    installation = ModuleType("persona_grata.installation")
    installation.prepare_installation = state.preflight
    installation.commit_credential = state.credentials
    installation.apply_installation = state.install
    installation.remove_agent = state.remove
    installation.setup_store = state.setup
    monkeypatch.setitem(sys.modules, "persona_grata.store", store_module)
    monkeypatch.setitem(sys.modules, "persona_grata.installation", installation)

    def seed(name="existing", body=None):
        directory = state.root / name
        directory.mkdir(parents=True)
        (directory / "agent.cfg").write_text(
            body or f'agent:\n  name: "{name}"\n  desc: y"{{$UNSET}}"\n'
        )
        state.entries[name] = directory
        return directory

    state.seed = seed
    return state


@pytest.mark.parametrize("argv", [["--help"], ["-h"], ["--version"]])
def test_help_version_need_no_lifecycle_imports(monkeypatch, capsys, argv):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[-1] in {"store", "installation", "composition", "identifiers"}:
            pytest.fail(f"help/version imported {name}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert cli.main(argv) == 0
    output = capsys.readouterr()
    assert not output.err
    assert "persona-grata" in output.out


@pytest.mark.parametrize("argv", [
    ["--create", "--update", "local", "shell"],
    ["--create"], ["--create", "local"], ["--create", "local", "shell", "other"],
    ["--create", "source.cfg", "shell"],
    ["--update"], ["--update", "existing", "not_a_source"],
    ["--remove"], ["--remove", "a", "b"], ["--export", "out.cfg"],
    ["--setup", "existing"], ["local", "shell"],
    ["--name", "new", "existing"], ["--path", "elsewhere", "--update", "existing"],
    ["--token", "missing", "existing"], ["--no-token", "--remove", "existing"],
    ["--verify", "--export", "out.cfg", "existing"],
    ["--no-wrapper", "existing"], ["--yes", "existing"], ["--force", "existing"],
    ["--backend", "pass", "existing"], ["--secret-root", "agents", "existing"],
    ["--create", "local", "shell", "--token", "missing", "--no-token"],
    ["--endpoint", "https://example.invalid", "--create", "local", "shell"],
])
def test_invalid_mode_options_fail_before_store_or_token_read(lifecycle, capsys, argv):
    assert cli.main(argv) == 2
    assert "error:" in capsys.readouterr().err
    assert lifecycle.constructed == []
    assert lifecycle.calls == []
    lifecycle.install.assert_not_called()


@pytest.mark.parametrize("name", ["../escape", "bad-name", "9agent", "café", "a+b", "a.b", "a" * 65, ""])
def test_invalid_agent_names_fail_before_store(lifecycle, capsys, name):
    assert cli.main(["--remove", "--yes", "--", name]) == 1
    assert "Error:" in capsys.readouterr().err
    assert lifecycle.constructed == []


@pytest.mark.parametrize("selection", [["bad-name", "shell"], ["local", "bad-name"]])
def test_invalid_persona_harness_fail_before_store(lifecycle, selection):
    assert cli.main(["--create", *selection]) == 1
    assert lifecycle.constructed == []


def test_no_args_list_without_loading_or_installing(lifecycle, capsys):
    b = lifecycle.seed("bravo")
    a = lifecycle.seed("alpha")
    assert cli.main([]) == 0
    text = capsys.readouterr().out
    assert text.index(f"alpha\t{a}") < text.index(f"bravo\t{b}")
    assert "--create" in text
    assert not lifecycle.calls
    lifecycle.install.assert_not_called()


def test_no_args_empty_store_is_not_created(lifecycle, capsys):
    assert cli.main([]) == 0
    assert "No registered agents" in capsys.readouterr().out
    assert not lifecycle.root.exists()


def test_name_alone_prints_saved_source_without_resolving(lifecycle, capsys):
    source = 'agent:\n  name: "existing"\n  secret<pass.secret>: {}\n  desc: y"{$MISSING}"\n'
    lifecycle.seed(body=source)
    assert cli.main(["existing"]) == 0
    assert capsys.readouterr().out == source
    assert lifecycle.calls == []
    lifecycle.install.assert_not_called()


def test_create_pair_defaults_and_no_paid_verification(lifecycle, capsys):
    assert cli.main(["--create", "local", "shell"]) == 0
    assert lifecycle.calls[0] == ("make", "local", "shell", "local_shell")
    assert lifecycle.calls[1][:2] == ("create", "local_shell")
    lifecycle.install.assert_called_once()
    assert lifecycle.install.call_args.args[0].name == "local_shell"
    assert lifecycle.preflight.call_args.kwargs == {"token": None, "verify": False, "install_wrapper": True}
    lifecycle.credentials.assert_called_once_with(lifecycle.install.call_args.args[0])
    assert "Created agent 'local_shell'" in capsys.readouterr().out


def test_create_named_path_and_keyless(lifecycle, tmp_path):
    target = tmp_path / "independent directory"
    assert cli.main(["--create", "local", "shell", "--name", "chosen", "--path", str(target),
                     "--no-token", "--no-wrapper"]) == 0
    assert lifecycle.entries["chosen"] == target
    tree = lifecycle.calls[1][2]
    assert "secret:= None" in tree.to_source()
    assert lifecycle.preflight.call_args.kwargs["install_wrapper"] is False


def test_store_flag_overrides_environment(lifecycle, monkeypatch, tmp_path):
    environment = tmp_path / "environment"
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("AGENT_STORE_DIR", str(environment))
    assert cli.main([]) == 0
    assert lifecycle.constructed[-1] == environment
    assert cli.main(["--store", str(explicit)]) == 0
    assert lifecycle.constructed[-1] == explicit


def test_create_source_does_not_resolve_custom_values(lifecycle, tmp_path):
    source = tmp_path / "custom.tmpl"
    source.write_text('agent:\n  name: "custom"\n  desc: y"{$UNSET}"\n  harness<>: {}\n')
    assert cli.main(["--create", str(source), "--no-wrapper"]) == 0
    assert "custom" in lifecycle.entries
    assert lifecycle.calls[0][:2] == ("create", "custom")
    assert lifecycle.calls[0][2]["agent"]["desc"].kind == "ystring"


def test_create_source_name_override_does_not_need_literal(lifecycle, tmp_path):
    source = tmp_path / "unnamed.cfg"
    source.write_text('agent:\n  name: y"{$MISSING}"\n  desc: "custom"\n')
    assert cli.main(["--create", str(source), "--name", "chosen"]) == 0
    assert yapyon.parse_file(lifecycle.entries["chosen"] / "agent.cfg")["agent"]["name"].value == "chosen"


@pytest.mark.parametrize("body", ['agent: {}\n', 'agent:\n  name: y"{$MISSING}"\n', 'not_agent: {}\n'])
def test_invalid_source_is_not_silently_created(lifecycle, tmp_path, body):
    source = tmp_path / "invalid.cfg"
    source.write_text(body)
    assert cli.main(["--create", str(source)]) == 1
    assert not lifecycle.calls
    lifecycle.install.assert_not_called()


def test_source_parse_error_has_no_traceback(lifecycle, tmp_path, capsys):
    source = tmp_path / "invalid.cfg"
    source.write_text('agent:\n  name: "unterminated\n')
    assert cli.main(["--create", str(source)]) == 1
    diagnostic = capsys.readouterr().err
    assert "Error:" in diagnostic
    assert str(source) in diagnostic
    assert "Traceback" not in diagnostic
    assert not lifecycle.calls


def test_duplicate_create_never_updates_existing(lifecycle):
    path = lifecycle.seed("local_shell")
    original = (path / "agent.cfg").read_bytes()
    assert cli.main(["--create", "local", "shell"]) == 1
    assert (path / "agent.cfg").read_bytes() == original
    assert not any(call[0] == "update" for call in lifecycle.calls)
    lifecycle.install.assert_not_called()


def test_update_unknown_never_creates(lifecycle):
    assert cli.main(["--update", "unknown"]) == 1
    assert lifecycle.calls == []
    lifecycle.install.assert_not_called()


def test_update_saved_definition_and_rename_preserve_directory(lifecycle):
    path = lifecycle.seed()
    assert cli.main(["--update", "existing", "--name", "renamed", "--no-token"]) == 0
    assert lifecycle.entries == {"renamed": path}
    assert [call[0] for call in lifecycle.calls] == ["update", "rename"]
    tree = yapyon.parse_file(path / "agent.cfg")
    assert tree["agent"]["name"].value == "renamed"
    assert tree["agent"]["secret"].value is None
    assert lifecycle.install.call_args.args[0].name == "renamed"


def test_update_source_uses_existing_registry_name(lifecycle, tmp_path):
    path = lifecycle.seed()
    source = tmp_path / "replacement.cfg"
    source.write_text('agent:\n  name: "elsewhere"\n  desc: "replacement"\n')
    assert cli.main(["--update", "existing", str(source)]) == 0
    tree = yapyon.parse_file(path / "agent.cfg")
    assert tree["agent"]["name"].value == "existing"
    assert tree["agent"]["desc"].value == "replacement"
    assert "elsewhere" not in lifecycle.entries


def test_rename_collision_rejected_before_update(lifecycle):
    path = lifecycle.seed()
    lifecycle.seed("taken")
    original = (path / "agent.cfg").read_bytes()
    assert cli.main(["--update", "existing", "--name", "taken", "--no-token"]) == 1
    assert (path / "agent.cfg").read_bytes() == original
    assert lifecycle.calls == []
    lifecycle.install.assert_not_called()


def test_token_preflight_forwarded_without_printing(lifecycle, tmp_path, capsys):
    token = "SENSITIVE_CLI_TEST_TOKEN"
    source = tmp_path / "key"
    source.write_text(f"  {token}\n")
    assert cli.main(["--create", "local", "shell", "--token", str(source), "--verify"]) == 0
    kwargs = lifecycle.preflight.call_args.kwargs
    assert kwargs["token"] == token
    assert kwargs["verify"] is True
    output = capsys.readouterr()
    assert token not in output.out + output.err
    assert token not in (lifecycle.entries["local_shell"] / "agent.cfg").read_text()
    assert source.read_text() == f"  {token}\n"


@pytest.mark.parametrize("body", [b"", b" \n", b"one\ntwo", b"one\rtwo", b"one\0two", b"\xffPRIVATE", b"x" * 65537])
def test_bad_token_file_fails_before_store_without_contents(lifecycle, tmp_path, capsys, body):
    source = tmp_path / "key"
    source.write_bytes(body)
    assert cli.main(["--create", "local", "shell", "--token", str(source)]) == 1
    diagnostic = capsys.readouterr().err
    assert "Error:" in diagnostic
    assert "PRIVATE" not in diagnostic
    assert lifecycle.constructed == []
    assert lifecycle.calls == []


def test_missing_token_file_fails_before_store(lifecycle, tmp_path):
    assert cli.main(["--create", "local", "shell", "--token", str(tmp_path / "missing")]) == 1
    assert lifecycle.constructed == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO safety")
def test_token_fifo_rejected_without_blocking(lifecycle, tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert cli.main(["--create", "local", "shell", "--token", str(fifo)]) == 1
    assert lifecycle.constructed == []


def test_install_failure_keeps_config_and_redacts_token(lifecycle, tmp_path, capsys):
    source = tmp_path / "key"
    source.write_text("SENSITIVE_CLI_TEST_TOKEN")
    lifecycle.install.side_effect = ValueError("rejected SENSITIVE_CLI_TEST_TOKEN")
    assert cli.main(["--create", "local", "shell", "--token", str(source)]) == 1
    output = capsys.readouterr()
    assert "Created agent" not in output.out
    assert "installation failed" in output.err
    assert "--update local_shell" in output.err
    assert "[redacted]" in output.err
    assert "SENSITIVE_CLI_TEST_TOKEN" not in output.err
    assert (lifecycle.entries["local_shell"] / "agent.cfg").exists()


def test_update_install_failure_reports_recoverable_rename(lifecycle, capsys):
    path = lifecycle.seed()
    lifecycle.install.side_effect = OSError(13, "permission denied")
    assert cli.main(["--update", "existing", "--name", "renamed"]) == 1
    assert lifecycle.entries == {"renamed": path}
    output = capsys.readouterr()
    assert "Updated agent" not in output.out
    assert "--update renamed" in output.err


@pytest.mark.parametrize("answer", ["", "n", "no", "anything"])
def test_removal_defaults_to_no(lifecycle, monkeypatch, answer):
    lifecycle.seed()
    monkeypatch.setattr(builtins, "input", lambda prompt: answer)
    assert cli.main(["--remove", "existing"]) == 0
    lifecycle.remove.assert_not_called()


def test_removal_eof_is_cancelled(lifecycle, monkeypatch):
    lifecycle.seed()

    def eof(prompt):
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)
    assert cli.main(["--remove", "existing"]) == 0
    lifecycle.remove.assert_not_called()


@pytest.mark.parametrize("flags", [["--yes"], []])
def test_confirmed_removal(lifecycle, monkeypatch, flags):
    lifecycle.seed()
    monkeypatch.setattr(builtins, "input", lambda prompt: "yes")
    assert cli.main(["--remove", "existing", *flags]) == 0
    lifecycle.remove.assert_called_once()
    assert lifecycle.remove.call_args.args[1] == "existing"
    assert lifecycle.remove.call_args.kwargs == {"confirm": True}
    lifecycle.install.assert_not_called()


def test_remove_unknown_does_not_prompt(lifecycle, monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt: pytest.fail("unknown agent prompted"))
    assert cli.main(["--remove", "unknown"]) == 1
    lifecycle.remove.assert_not_called()


def test_export_unfinished_source_does_not_resolve(lifecycle, tmp_path):
    path = lifecycle.seed(body='agent:\n  name: "existing"\n  secret<pass.secret>: {}\n  desc: y"{$UNSET}"\n')
    destination = tmp_path / "export.cfg"
    assert cli.main(["--export", str(destination), "existing"]) == 0
    assert yapyon.parse_file(destination).to_source() == yapyon.parse_file(path / "agent.cfg").to_source()
    assert lifecycle.calls == []
    lifecycle.install.assert_not_called()


def test_export_overwrite_requires_force(lifecycle, tmp_path):
    lifecycle.seed()
    destination = tmp_path / "export.cfg"
    destination.write_text("user draft")
    assert cli.main(["--export", str(destination), "existing"]) == 1
    assert destination.read_text() == "user draft"
    assert cli.main(["--export", str(destination), "existing", "--force"]) == 0
    assert "agent:" in destination.read_text()


def test_export_never_overwrites_installed_definition(lifecycle):
    path = lifecycle.seed()
    source = path / "agent.cfg"
    original = source.read_bytes()
    assert cli.main(["--export", str(source), "existing", "--force"]) == 1
    assert source.read_bytes() == original


def test_setup_options_only_forwarded(lifecycle, capsys):
    assert cli.main(["--setup", "--backend", "pass", "--secret-root", "work/agents"]) == 0
    lifecycle.setup.assert_called_once()
    assert lifecycle.setup.call_args.kwargs == {"backend": "pass", "secret_root": "work/agents"}
    assert "Configured agent store" in capsys.readouterr().out
    lifecycle.install.assert_not_called()


def test_keyboard_interrupt_returns_130(lifecycle, capsys):
    lifecycle.install.side_effect = KeyboardInterrupt
    assert cli.main(["--create", "local", "shell"]) == 130
    assert "Cancelled." in capsys.readouterr().err


@pytest.fixture
def hermetic_store(monkeypatch, tmp_path):
    from persona_grata import installation
    from persona_grata.store import AgentStore

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("AGENT_STORE_DIR", str(tmp_path / "actual_store"))
    monkeypatch.setenv("SHELL", "/bin/bash")

    def forbidden(*args, **kwargs):
        pytest.fail("keyless CLI contacted a network endpoint or spawned a credential manager")

    monkeypatch.setattr(installation.subprocess, "run", forbidden)
    monkeypatch.setattr(installation.urllib.request, "urlopen", forbidden)
    return AgentStore()


def test_real_keyless_lifecycle_create_export_update_remove(hermetic_store, tmp_path):
    store = hermetic_store
    assert cli.main(["--create", "ollama", "claude", "--no-token", "--no-wrapper"]) == 0
    directory = store.locate("ollama_claude")
    assert (directory / "agent.cfg").is_file()
    assert store.load("ollama_claude")["agent"]["secret"] is None
    assert not (Path.home() / ".bashrc").exists()

    exported = tmp_path / "export.cfg"
    assert cli.main(["--export", str(exported), "ollama_claude"]) == 0
    tree = yapyon.parse_file(exported)
    assert "runtime" not in tree
    assert "path" not in tree["agent"]
    assert tree.slots(), "export should retain deferred category slots"

    assert cli.main(["--update", "ollama_claude", "--name", "renamed", "--no-wrapper"]) == 0
    assert store.agents() == {"renamed": directory}
    assert yapyon.parse_file(directory / "agent.cfg")["agent"]["name"].value == "renamed"
    assert cli.main(["--remove", "renamed", "--yes"]) == 0
    assert not directory.exists()
    assert store.agents() == {}
    assert (store.root / "personas" / "ollama.persona.tmpl").exists()


def test_real_source_create_and_external_agent_path_rejection(hermetic_store, tmp_path):
    source = tmp_path / "custom.cfg"
    source.write_text(
        'agent<base.agent>:\n  name: "custom"\n'
        '  persona<ollama.persona>: {}\n  harness<claude.harness>: {}\n'
    )
    directory = tmp_path / "custom directory"
    assert cli.main(["--create", str(source), "--path", str(directory), "--no-token", "--no-wrapper"]) == 0
    assert hermetic_store.locate("custom") == directory

    source.write_text(
        'agent<base.agent>:\n  name: "bad"\n  path: "/forbidden"\n'
        '  persona<ollama.persona>: {}\n  harness<claude.harness>: {}\n'
    )
    assert cli.main(["--create", str(source), "--no-token", "--no-wrapper"]) == 1
    assert "bad" not in hermetic_store.agents()


def test_real_duplicate_create_reports_explicit_collision(hermetic_store, capsys):
    assert cli.main(["--create", "ollama", "claude", "--no-token", "--no-wrapper"]) == 0
    capsys.readouterr()
    assert cli.main(["--create", "ollama", "claude", "--no-token", "--no-wrapper"]) == 1
    assert "already occupied" in capsys.readouterr().err


def test_real_wrapper_rename_replaces_command_not_directory(hermetic_store):
    assert cli.main(["--create", "ollama", "claude", "--no-token"]) == 0
    directory = hermetic_store.locate("ollama_claude")
    rc_file = Path.home() / ".bashrc"
    assert "ollama_claude()" in rc_file.read_text()
    assert cli.main(["--update", "ollama_claude", "--name", "renamed"]) == 0
    assert "ollama_claude()" not in rc_file.read_text()
    assert "renamed()" in rc_file.read_text()
    assert hermetic_store.locate("renamed") == directory
    assert cli.main(["--remove", "renamed", "--yes"]) == 0
    assert "renamed()" not in rc_file.read_text()


def test_main_defaults_to_process_argv(lifecycle, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pg"])
    assert cli.main() == 0
    assert "No registered agents" in capsys.readouterr().out


def test_yes_removal_never_prompts(lifecycle, monkeypatch):
    lifecycle.seed()
    monkeypatch.setattr(builtins, "input", lambda prompt: pytest.fail("--yes prompted"))
    assert cli.main(["--remove", "existing", "--yes"]) == 0
    lifecycle.remove.assert_called_once()


@pytest.mark.parametrize("mode", ["display", "export", "update"])
def test_saved_config_symlink_is_followed(lifecycle, tmp_path, capsys, mode):
    directory = lifecycle.seed()
    target = tmp_path / "target.cfg"
    target.write_text('agent:\n  name: "linked"\n')
    saved = directory / "agent.cfg"
    saved.unlink()
    saved.symlink_to(target)
    arguments = {"display": ["existing"],
                 "export": ["--export", str(tmp_path / "export.cfg"), "existing"],
                 "update": ["--update", "existing"]}[mode]
    assert cli.main(arguments) == 0
    output = capsys.readouterr()
    assert saved.is_symlink()
    if mode == "display":
        assert output.out == target.read_text()
    elif mode == "export":
        assert "linked" in (tmp_path / "export.cfg").read_text()
    else:
        assert lifecycle.calls[0][0] == "update"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO safety")
def test_source_fifo_rejected_without_blocking(lifecycle, tmp_path):
    fifo = tmp_path / "source.cfg"
    os.mkfifo(fifo)
    assert cli.main(["--create", str(fifo)]) == 1
    assert not lifecycle.calls


@pytest.mark.parametrize("body", [
    'agent: {name: "bad"}\n',
    'agent:\n  name: "bad"\n  persona:\n    mind:\n      endpoint: "http://localhost:11434"\n',
])
def test_malformed_source_shape_returns_error_without_registration(hermetic_store, tmp_path, body):
    source = tmp_path / "malformed.cfg"
    source.write_text(body)
    assert cli.main(["--create", str(source), "--no-wrapper"]) == 1
    assert hermetic_store.agents() == {}


def test_malformed_backend_registry_returns_error_not_traceback(hermetic_store):
    from persona_grata.composition import write_document
    from persona_grata.yapyon_io import value_tree

    write_document(value_tree({"defaults": {"secret": {"backend": []}}}), hermetic_store.registry_path)
    assert cli.main([]) == 1


def test_explicit_source_symlink_to_regular_file_is_supported(lifecycle, tmp_path):
    target = tmp_path / "actual.tmpl"
    target.write_text('agent:\n  name: "custom"\n')
    source = tmp_path / "selected.cfg"
    source.symlink_to(target)
    assert cli.main(["--create", str(source), "--no-wrapper"]) == 0
    assert "custom" in lifecycle.entries


def test_update_follows_symlink_ancestor_to_foreign_target(hermetic_store, tmp_path):
    assert cli.main(["--create", "ollama", "claude", "--no-token", "--no-wrapper"]) == 0
    directory = hermetic_store.locate("ollama_claude")
    original = (directory / "agent.cfg").read_bytes()
    (hermetic_store.root / "store").rename(hermetic_store.root / "saved_store")
    foreign = tmp_path / "foreign" / "ollama_claude"
    foreign.mkdir(parents=True)
    (foreign / "agent.cfg").write_bytes(original)
    (hermetic_store.root / "store").symlink_to(foreign.parent, target_is_directory=True)
    source = tmp_path / "replacement.cfg"
    source.write_text(
        'agent<base.agent>:\n  name: "replacement"\n  desc: "FOREIGN CHANGED"\n'
        '  persona<ollama.persona>: {}\n  harness<claude.harness>: {}\n  secret: None\n'
    )
    assert cli.main(["--update", "ollama_claude", str(source), "--no-wrapper"]) == 0
    assert (foreign / "agent.cfg").read_bytes() != original
    assert b"FOREIGN CHANGED" in (foreign / "agent.cfg").read_bytes()

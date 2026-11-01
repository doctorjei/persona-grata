"""Native metadata-only checks: isolated fixtures, no real managers, keys or providers."""

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from persona_grata import secret_preflight as pre


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("GOPASS_", "PASSWORD_STORE_", "XDG_", "GNUPG")):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GNUPGHOME", str(tmp_path / "gnupg"))
    for key in ("CONFIG", "DATA", "CACHE", "STATE"):
        monkeypatch.setenv(f"XDG_{key}_HOME", str(tmp_path / key.lower()))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("GOPASS_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(pre.shutil, "which", lambda name: "/fake/" + name)
    def forbidden(*args, **kwargs):
        pytest.fail("Metadata checks must not invoke any manager or crypto command")
    monkeypatch.setattr(pre.subprocess, "run", forbidden)
    return tmp_path


def manager(name="pass", entry="team/key"):
    fetch = {"pass": "pass show", "gopass": "gopass show -o",
             "prs": "prs show --no-interact --first"}.get(name, "custom fetch")
    store = {"pass": "pass insert -m -f", "gopass": "gopass insert",
             "prs": "prs add --no-interact --stdin"}.get(name, "custom store")
    return {"name": name, "probe": name, "entry": entry,
            "fetch": fetch + " " + entry, "store": store + " " + entry}


def initialize(marker=".gpg-id", content="fixture-recipient\n", path=None):
    root = Path(path or os.environ["PASSWORD_STORE_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    (root / marker).write_text(content)
    return root


@pytest.mark.parametrize("name", ["pass", "gopass", "prs"])
def test_missing_store_fails_without_creating_any_paths(name, isolated):
    before = set(isolated.rglob("*"))
    with pytest.raises(ValueError, match="missing|uninitialized"):
        pre.check_store(manager(name), writing=True)
    assert set(isolated.rglob("*")) == before


@pytest.mark.parametrize("name", ["pass", "gopass", "prs"])
def test_empty_directory_is_not_an_initialized_store(name):
    Path(os.environ["PASSWORD_STORE_DIR"]).mkdir()
    with pytest.raises(ValueError, match="uninitialized"):
        pre.check_store(manager(name))


@pytest.mark.parametrize("name", ["pass", "gopass", "prs"])
def test_definitely_missing_entry_does_not_create_parent_or_write(name):
    root = initialize()
    before = {p: p.read_bytes() for p in root.iterdir()}
    assert pre.check_store(manager(name), writing=True) is False
    assert list(root.iterdir()) == list(before)
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize("name", ["pass", "gopass", "prs"])
def test_existing_unreadable_entry_is_true_not_missing_and_not_read(name):
    root = initialize()
    (root / "team").mkdir()
    entry = root / "team/key.gpg"
    entry.write_bytes(b"synthetic ciphertext, never decrypted")
    entry.chmod(0)
    try:
        assert pre.check_store(manager(name)) is True
    finally:
        entry.chmod(0o600)


def test_stat_permission_failure_is_not_an_absent_entry(monkeypatch):
    root = initialize()
    target = root / "team/key.gpg"
    original = Path.stat
    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError("SENSITIVE diagnostic")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(ValueError, match="Cannot access") as exc:
        pre.check_store(manager())
    assert "SENSITIVE" not in str(exc.value)


def test_unreadable_metadata_is_a_safe_error(monkeypatch):
    root = initialize()
    original = pre.os.open
    def denied(path, *args, **kwargs):
        if Path(path) == root / ".gpg-id":
            raise PermissionError("SENSITIVE metadata")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(pre.os, "open", denied)
    with pytest.raises(ValueError, match="Cannot read") as exc:
        pre.check_store(manager())
    assert "SENSITIVE" not in str(exc.value)


@pytest.mark.parametrize("body", ["", "\n# comment only\n", "  \n"])
def test_empty_recipient_metadata_fails(body):
    initialize(content=body)
    with pytest.raises(ValueError, match="empty"):
        pre.check_store(manager(), writing=True)


def test_metadata_fifo_is_rejected_without_blocking():
    root = Path(os.environ["PASSWORD_STORE_DIR"])
    root.mkdir()
    os.mkfifo(root / ".gpg-id")
    with pytest.raises(ValueError, match="regular file"):
        pre.check_store(manager())


def test_pass_nearest_recipients_and_password_store_key_override():
    root = initialize()
    (root / "team").mkdir()
    (root / "team/.gpg-id").write_text("# deliberately empty\n")
    with pytest.raises(ValueError, match="empty"):
        pre.check_store(manager(), writing=True)


def test_pass_password_store_key_does_not_require_gpg_id(monkeypatch):
    root = Path(os.environ["PASSWORD_STORE_DIR"])
    root.mkdir()
    monkeypatch.setenv("PASSWORD_STORE_KEY", "fixture-one fixture-two")
    assert pre.check_store(manager(), writing=True) is False


def test_prs_uses_root_recipients_not_nested_recipient_file():
    root = initialize()
    (root / "team").mkdir()
    (root / "team/.gpg-id").write_text("")
    assert pre.check_store(manager("prs"), writing=True) is False
    (root / ".gpg-id").write_text("")
    (root / "team/.gpg-id").write_text("fixture-recipient\n")
    with pytest.raises(ValueError, match="empty"):
        pre.check_store(manager("prs"), writing=True)


@pytest.mark.parametrize("name", ["pass", "gopass", "prs"])
def test_symlinked_root_metadata_and_entry_are_preserved(name, isolated, monkeypatch):
    real = initialize(path=isolated / "real")
    external = isolated / "external-metadata"
    external.write_text("fixture-recipient\n")
    (real / ".gpg-id").unlink()
    (real / ".gpg-id").symlink_to(external)
    (real / "team").mkdir()
    target = isolated / "external-ciphertext"
    target.write_bytes(b"synthetic encrypted fixture")
    (real / "team/key.gpg").symlink_to(target)
    root = isolated / "linked-store"
    root.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(root))
    assert pre.check_store(manager(name), writing=True) is True
    assert root.is_symlink() and (real / "team/key.gpg").is_symlink()
    assert target.read_bytes() == b"synthetic encrypted fixture"


def test_dangling_entry_link_is_not_a_missing_credential():
    root = initialize()
    (root / "team").mkdir()
    (root / "team/key.gpg").symlink_to(root / "unavailable-target")
    assert pre.check_store(manager()) is True


@pytest.mark.parametrize("marker,suffix,body", [
    (".gpg-id", ".gpg", "fixture-gpg-recipient\n"),
    (".age-recipients", ".age", "age1fixture-recipient\n"),
    (".plain-id", ".txt", ""),
])
def test_gopass_crypto_layouts_do_not_require_external_crypto_tools(marker, suffix, body, monkeypatch):
    root = initialize(marker, body)
    monkeypatch.setattr(pre.shutil, "which", lambda name: "/fake/gopass" if name == "gopass" else None)
    assert pre.check_store(manager("gopass"), writing=True) is False
    (root / "team").mkdir()
    (root / ("team/key" + suffix)).write_bytes(b"synthetic fixture")
    assert pre.check_store(manager("gopass")) is True


def test_gopass_always_appends_the_native_suffix():
    root = initialize()
    (root / "key.gpg").write_bytes(b"different entry")
    assert pre.check_store(manager("gopass", "key.gpg")) is False
    (root / "key.gpg.gpg").write_bytes(b"exact entry")
    assert pre.check_store(manager("gopass", "key.gpg")) is True


def write_config(body, path=None):
    path = Path(path or Path(os.environ["XDG_CONFIG_HOME"]) / "gopass/config")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_gopass_longest_mount_and_nearest_recipients(isolated):
    initialize()
    first = initialize(path=isolated / "first")
    second = initialize(".age-recipients", "age1fixture\n", path=isolated / "second")
    write_config(f'[mounts "team"]\npath = "{first}"\n[mounts "team/sub"]\npath = "{second}"\n')
    (second / "key.age").write_bytes(b"synthetic fixture")
    assert pre.check_store(manager("gopass", "team/sub/key")) is True
    assert pre.check_store(manager("gopass", "team/other")) is False
    (second / ".age-recipients").write_text("")
    with pytest.raises(ValueError, match="empty"):
        pre.check_store(manager("gopass", "team/sub/key"), writing=True)


@pytest.mark.parametrize("source", ["configured", "xdg", "legacy"])
def test_gopass_store_discovery_without_invoking_manager(source, isolated, monkeypatch):
    monkeypatch.delenv("PASSWORD_STORE_DIR")
    if source == "configured":
        root = initialize(path=isolated / "configured store")
        path = write_config(f'[mounts]\npath = "{root}"\n', isolated / "custom-config")
        monkeypatch.setenv("GOPASS_CONFIG", str(path))
    elif source == "xdg":
        root = initialize(path=Path(os.environ["XDG_DATA_HOME"]) / "gopass/stores/root")
    else:
        root = initialize(path=Path.home() / ".password-store")
    assert pre.check_store(manager("gopass")) is False
    assert (root / ".gpg-id").is_file()


def test_gopass_local_readonly_and_environment_precedence(monkeypatch):
    root = initialize()
    write_config("[core]\nreadonly = true\n", root / "config.worktree")
    assert pre.check_store(manager("gopass")) is False
    with pytest.raises(ValueError, match="read-only"):
        pre.check_store(manager("gopass"), writing=True)
    monkeypatch.setenv("GOPASS_CONFIG_COUNT", "1")
    monkeypatch.setenv("GOPASS_CONFIG_KEY_0", "core.readonly")
    monkeypatch.setenv("GOPASS_CONFIG_VALUE_0", "false")
    assert pre.check_store(manager("gopass"), writing=True) is False


@pytest.mark.parametrize("body", ["root:\n  path: /uninterpreted\n", "[include]\npath = /uninterpreted\n"])
def test_unknown_gopass_config_is_indeterminate_not_missing(body):
    write_config(body)
    assert pre.check_store(manager("gopass")) is None


def test_unknown_native_command_variant_is_indeterminate():
    custom = manager("pass")
    custom["fetch"] = "pass show --custom-layout team/key"
    assert pre.check_store(custom) is None


def test_generic_backend_without_check_stays_compatible():
    assert pre.check_store(manager("custom"), writing=True) is None


@pytest.mark.parametrize("command", ["custom check --quiet", ["custom", "check", "--quiet"]])
def test_optional_check_hook_is_argv_only_and_output_is_discarded(command, monkeypatch):
    custom = manager("custom")
    custom["check"] = command
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pre.subprocess, "run", run)
    assert pre.check_store(custom) is None
    assert calls[0][0] == ["custom", "check", "--quiet"]
    assert calls[0][1] == {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                           "stderr": subprocess.DEVNULL, "timeout": 120}


@pytest.mark.parametrize("failure", ["status", "os", "timeout"])
def test_check_errors_never_expose_command_output(failure, monkeypatch):
    custom = manager("custom")
    custom["check"] = ["custom", "check"]
    def run(*args, **kwargs):
        if failure == "os":
            raise OSError("SENSITIVE")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("SENSITIVE", 120, output=b"SENSITIVE")
        return SimpleNamespace(returncode=5, stderr="SENSITIVE")
    monkeypatch.setattr(pre.subprocess, "run", run)
    with pytest.raises(ValueError, match="readiness check") as exc:
        pre.check_store(custom)
    assert "SENSITIVE" not in str(exc.value)


@pytest.mark.parametrize("entry", ["", "../key", "/key", "-key", "a//key", "a/./key", "a\x00key"])
def test_bad_entries_fail_before_any_store_access(entry):
    with pytest.raises(ValueError, match="store-relative"):
        pre.check_store(manager(entry=entry))


def test_missing_executable_is_a_safe_error(monkeypatch):
    monkeypatch.setattr(pre.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="not installed"):
        pre.check_store(manager())


def test_readonly_parent_fails_only_for_writing(monkeypatch):
    initialize()
    original = os.access
    monkeypatch.setattr(pre.os, "access", lambda path, mode: False if mode & os.W_OK else original(path, mode))
    assert pre.check_store(manager()) is False
    with pytest.raises(ValueError, match="not writable"):
        pre.check_store(manager(), writing=True)


def test_gopass_mount_inherits_root_local_readonly(isolated):
    root = initialize()
    mounted = initialize(path=isolated / "mounted")
    write_config(f'[mounts "team"]\npath = "{mounted}"\n')
    write_config("[core]\nreadonly = true\n", root / "config")
    with pytest.raises(ValueError, match="read-only"):
        pre.check_store(manager("gopass"), writing=True)


@pytest.mark.parametrize("body", ["[core]\ncasefold = true\n", "[recipients]\ncheck = true\n",
                                  "[DEFAULT]\npath = /not-native-defaults\n[mounts]\n"])
def test_uncertain_native_metadata_never_reports_missing(body):
    initialize()
    write_config(body)
    assert pre.check_store(manager("gopass")) is None


def test_legacy_age_marker_does_not_get_misclassified_as_gpg():
    root = initialize()
    (root / ".age-ids").write_text("legacy-age-recipient\n")
    assert pre.check_store(manager("gopass")) is None


def test_prs_raw_exact_filename_fallback_is_supported():
    root = initialize()
    (root / "key.gpg").write_bytes(b"synthetic fixture")
    assert pre.check_store(manager("prs", "key.gpg")) is True
    assert pre.check_store(manager("prs", "key.gpg"), writing=True) is None


@pytest.mark.parametrize("name", ["pass", "gopass", "prs"])
def test_existing_file_write_semantics_are_indeterminate_not_restricted(name, monkeypatch):
    root = initialize()
    target = root / "key.gpg"
    target.write_bytes(b"synthetic fixture")
    original = os.access
    monkeypatch.setattr(pre.os, "access", lambda path, mode:
                        False if Path(path) == target and mode & os.W_OK else original(path, mode))
    assert pre.check_store(manager(name, "key")) is True
    assert pre.check_store(manager(name, "key"), writing=True) is None


@pytest.mark.parametrize("check", [None, ""])
def test_empty_optional_check_is_disabled(check):
    custom = manager("custom")
    custom["check"] = check
    assert pre.check_store(custom) is None


def test_dangling_write_link_is_indeterminate_without_restricting_symlinks():
    root = initialize()
    (root / "key.gpg").symlink_to(root / "unavailable")
    assert pre.check_store(manager("pass", "key")) is True
    assert pre.check_store(manager("pass", "key"), writing=True) is None
    assert (root / "key.gpg").is_symlink()

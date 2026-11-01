"""Read-only native store metadata checks, not a guarantee of a future import.

No manager discovery commands are run: even gopass ls can create directories.
Unknown command/configuration variants return None so callers fetch strictly.
"""

from __future__ import annotations

from collections.abc import Mapping
import configparser
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess


_LIMIT = 64 * 1024
_TIMEOUT = 120


def _fail(message):
    raise ValueError(message)


def _stat(path):
    try:
        return path.stat()
    except FileNotFoundError:
        # A dangling entry link is not an absent credential to replace/prompt for.
        try:
            return path.lstat() if path.is_symlink() else None
        except OSError:
            _fail("Cannot access secret store metadata or destination.")
    except OSError:
        _fail("Cannot access secret store metadata or destination.")


def _text(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail("Secret store metadata must be a regular file.")
            body = stream.read(_LIMIT + 1)
    except OSError:
        _fail("Cannot read secret store metadata.")
    if len(body) > _LIMIT:
        _fail("Secret store metadata is too large.")
    try:
        return body.decode("utf-8")
    except UnicodeError:
        _fail("Secret store metadata is not UTF-8 text.")


def _entry(manager):
    entry = manager.get("entry")
    if (not isinstance(entry, str) or not entry or entry.startswith(("/", "-"))
            or any(c in entry for c in "\x00\r\n")
            or any(p in ("", ".", "..") for p in entry.split("/"))):
        _fail("Secret entry must be a nonempty store-relative path without traversal.")
    return entry


def _argv(manager, action, entry):
    command = manager.get(action)
    try:
        if isinstance(command, str):
            if command.endswith(entry) and command[:-len(entry)].endswith((" ", "\t")):
                argv = shlex.split(command[:-len(entry)]) + [entry]
            else:
                argv = shlex.split(command)
        elif isinstance(command, (list, tuple)):
            argv = list(command)
        else:
            argv = []
    except ValueError:
        _fail(f"Secret backend '{action}' command has invalid quoting.")
    if (not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
            or not argv[0]):
        _fail(f"Secret backend has no valid '{action}' command.")
    return argv


def _hook(argv):
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        _fail("Secret backend readiness check timed out.")
    except OSError:
        _fail("Secret backend readiness check could not run.")
    if proc.returncode:
        _fail("Secret backend readiness check failed.")


def _directory(path, *, writing=False):
    info = _stat(path)
    if info is None:
        _fail("Secret store is missing or uninitialized; initialize it explicitly first.")
    if not stat.S_ISDIR(info.st_mode) or not os.access(path, os.R_OK | os.X_OK):
        _fail("Secret store or destination directory is inaccessible.")
    if writing and not os.access(path, os.W_OK | os.X_OK):
        _fail("Secret store destination is not writable.")


def _config(path):
    """Read the simple native git-style format; do not guess include/legacy semantics."""
    if _stat(path) is None:
        return {}
    parser = configparser.RawConfigParser(strict=False, allow_no_value=True)
    try:
        parser.read_string(_text(path))
    except configparser.Error:
        return None
    if parser.defaults():
        return None
    values = {}
    for section in parser.sections():
        if section.lower().startswith(("include", "default")):
            return None
        match = re.fullmatch(r'([^\s]+)(?: "([^"\\]+)")?', section)
        if not match:
            return None
        prefix = match[1].lower() + ("." + match[2] if match[2] else "")
        for key, value in parser.items(section):
            value = value or "true"
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    return None
            elif any(c in value for c in '#;"\\'):
                return None
            if not isinstance(value, str):
                return None
            values[prefix + "." + key] = value
    return values


def _gopass(entry):
    home = Path(os.environ.get("GOPASS_HOMEDIR") or Path.home())
    override = os.environ.get("GOPASS_HOMEDIR")
    config_home = home / ".config" if override else Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    data_home = home / ".local/share" if override else Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    paths = [] if os.environ.get("GOPASS_CONFIG_NOSYSTEM") else [Path("/etc/gopass/config")]
    paths.append(Path(os.environ.get("GOPASS_CONFIG") or config_home / "gopass/config").expanduser())
    values = {}
    for path in paths:
        parsed = _config(path)
        if parsed is None:
            return None
        values.update(parsed)
    env_values = {}
    try:
        count = int(os.environ.get("GOPASS_CONFIG_COUNT", "0"))
        if not 0 <= count <= 1024:
            return None
        for index in range(count):
            env_values[os.environ[f"GOPASS_CONFIG_KEY_{index}"]] = os.environ[f"GOPASS_CONFIG_VALUE_{index}"]
    except (ValueError, KeyError):
        return None
    values.update(env_values)
    legacy = home / ".password-store"
    default = legacy if _stat(legacy) is not None else data_home / "gopass/stores/root"
    root = os.environ.get("PASSWORD_STORE_DIR") or values.get("mounts.path") or default
    mounts = {key[len("mounts."):-len(".path")]: value for key, value in values.items()
              if key.startswith("mounts.") and key.endswith(".path") and key != "mounts.path"}
    # Root-local defaults are inherited by mounted stores; local mounts are not.
    for filename in ("config", "config.worktree"):
        parsed = _config(Path(root).expanduser() / filename)
        if parsed is None or "mounts.path" in parsed:
            return None
        values.update(parsed)
    for alias in sorted(mounts, key=len, reverse=True):
        if entry == alias:
            return None  # A mount directory is not an exact credential entry.
        if entry.startswith(alias + "/"):
            # The native CLI requires its root store to be initialized as well.
            base = Path(root).expanduser()
            _directory(base)
            if not any(_stat(base / marker) is not None for marker in
                       (".age-recipients", ".gpg-id", ".plain-id")):
                _fail("Secret root store is uninitialized.")
            root, entry = mounts[alias], entry[len(alias) + 1:]
            break
    root = Path(root).expanduser()
    for filename in ("config", "config.worktree"):
        parsed = _config(root / filename)
        if parsed is None:
            return None
        # A local path override or non-filesystem backend needs native resolution.
        if "mounts.path" in parsed or parsed.get("storage.backend", "fs") not in ("fs", "gitfs", "jjfs", "fossilfs"):
            return None
        values.update(parsed)
    values.update(env_values)
    if values.get("storage.backend", "fs") not in ("fs", "gitfs", "jjfs", "fossilfs"):
        return None
    if any(values.get(key, "false").lower() in ("true", "yes", "on", "1")
           for key in ("core.casefold", "recipients.check")):
        return None  # Do not guess native casefold or recipient-hash semantics.
    return root, entry, values


def _native_args(argv, name, entry, action):
    """Only inspect metadata for understood native invocations, never arbitrary wrappers."""
    if Path(argv[0]).name != name or argv[-1] != entry:
        return False
    verb = "show" if action == "fetch" else ("add" if name == "prs" else "insert")
    allowed = {"--", "-o", "--password", "--first", "--no-interact", "-I", "--quiet", "-q",
               "-m", "--multiline", "-f", "--force", "--stdin", "-S", "--nofuzzysearch", "--nosync"}
    return len(argv) >= 3 and argv[1] == verb and all(arg in allowed for arg in argv[2:-1])


def check_store(manager, *, writing=False) -> bool | None:
    """Return exact entry existence, or None when native metadata cannot determine it.

    True does not promise decryption: callers must fetch strictly and propagate
    read failures. Recipient metadata checks do not prove crypto/key readiness;
    the real import and readback must precede pg registration.
    """
    if not isinstance(manager, Mapping):
        _fail("Secret backend must be a mapping.")
    entry = _entry(manager)
    fetch = _argv(manager, "fetch", entry)
    store = _argv(manager, "store", entry) if writing else None
    probe = manager.get("probe") or fetch[0]
    if not isinstance(probe, str) or not probe or "\x00" in probe or shutil.which(probe) is None:
        _fail("Secret backend is not installed.")
    if shutil.which(fetch[0]) is None or (store and shutil.which(store[0]) is None):
        _fail("Secret backend command is not installed.")
    if manager.get("check") not in (None, ""):
        _hook(_argv(manager, "check", entry))
    name = manager.get("name")
    if name not in ("pass", "gopass", "prs") or not _native_args(fetch, name, entry, "fetch"):
        return None
    if store and not _native_args(store, name, entry, "store"):
        return None
    values = {}
    if name == "gopass":
        resolved = _gopass(entry)
        if resolved is None:
            return None
        root, entry, values = resolved
        if writing and values.get("core.readonly", "false").lower() in ("true", "yes", "on", "1"):
            _fail("Secret store is configured read-only.")
    else:
        root = Path(os.environ.get("PASSWORD_STORE_DIR") or Path.home() / ".password-store").expanduser()
    _directory(root)
    suffix, metadata = ".gpg", ".gpg-id"
    if name == "gopass":
        if _stat(root / ".age-ids") is not None:
            return None  # Native detection may migrate this, even alongside .gpg-id.
        for marker, extension in ((".age-recipients", ".age"), (".gpg-id", ".gpg"), (".plain-id", ".txt")):
            if _stat(root / marker) is not None:
                suffix, metadata = extension, marker
                break
        else:
            _fail("Secret store is uninitialized; initialize it explicitly first.")
    if name == "prs" and writing and entry.endswith(suffix):
        return None  # prs show and add normalize an explicit .gpg suffix differently.
    destination = root / (entry + suffix)
    info = _stat(destination)
    # prs also accepts an exact existing file without appending .gpg.
    if name == "prs" and info is None:
        alternative = root / entry
        if _stat(alternative) is not None:
            destination, info = alternative, _stat(alternative)
    if info is not None and not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        _fail("Secret credential destination is not a regular file.")
    parent = destination.parent
    while _stat(parent) is None:
        parent = parent.parent
    _directory(parent, writing=writing)
    # Some writers replace an entry atomically, others follow and overwrite it.
    # Do not turn uncertain file/link write semantics into a new link restriction.
    uncertain_write = writing and info is not None and (
        stat.S_ISLNK(info.st_mode) or not os.access(destination, os.W_OK))
    if name == "pass" and os.environ.get("PASSWORD_STORE_KEY", "").strip():
        return None if uncertain_write else info is not None
    recipient_dir = root if name == "prs" else destination.parent
    while _stat(recipient_dir / metadata) is None and recipient_dir != root:
        recipient_dir = recipient_dir.parent
    recipient_path = recipient_dir / metadata
    if _stat(recipient_path) is None:
        _fail("Secret store is uninitialized; no recipient metadata is available.")
    body = _text(recipient_path)
    if metadata != ".plain-id" and not any(line.split("#", 1)[0].strip() for line in body.splitlines()):
        _fail("Secret store recipient metadata is empty.")
    return None if uncertain_write else info is not None

"""Install registered agents and obtain credentials without plaintext token files.

Manager command strings are split into argv, never run through a shell.  The
shell wrapper contains only a safely quoted fetch command and fetches afresh at
launch; a failed or empty fetch prevents the harness from starting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from .composition import check_write_path


_SECRET_TIMEOUT = 120
_TOKEN_LIMIT = 64 * 1024
_RC_LIMIT = 8 * 1024 * 1024
_OWNER_LIMIT = 4096
_VERIFY_TIMEOUT = 15
_RC_FILES = {"zsh": ".zshrc", "ash": ".profile", "dash": ".profile", "busybox": ".profile"}
_KNOWN_SHELLS = frozenset({"bash", "sh", *_RC_FILES})
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SHELL_RESERVED = frozenset({"if", "then", "else", "elif", "fi", "do", "done", "case", "esac",
                             "while", "until", "for", "in", "function", "select", "time", "coproc"})
_OWNER_FILE = ".persona-grata-installation"


def _fail(message):
    raise ValueError(message)


def _is_interactive():
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _confirm(question, default=False):
    if not _is_interactive():
        return False
    try:
        answer = input(question + (" [Y/n]: " if default else " [y/N]: ")).strip().lower()
    except EOFError:
        return default
    return answer in ("y", "yes") or (not answer and default)


def _ask(question, default=None):
    while True:
        try:
            answer = input(question + (f" [{default}]: " if default else ": ")).strip()
        except EOFError:
            _fail("No input received.")
        if answer:
            return answer
        if default is not None:
            return default
        print("Nothing entered — try again.", file=sys.stderr)


def _read_regular_text(path, *, limit, description):
    """Bound reads before decoding; O_NONBLOCK prevents opening a FIFO from hanging."""
    try:
        descriptor = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail(f"The {description} must be a regular file.")
            body = stream.read(limit + 1)
    except OSError as error:
        _fail(f"Cannot read the {description}: {error.strerror}.")
    if len(body) > limit:
        _fail(f"The {description} is too large (limit {limit} bytes).")
    try:
        return body.decode("utf-8")
    except UnicodeError:
        _fail(f"The {description} must contain UTF-8 text.")


def read_token(path):
    """Read a bounded regular token file; never copy it into the agent store."""
    return _validate_token(_read_regular_text(path, limit=_TOKEN_LIMIT, description="token file").strip())


def _validate_token(token):
    if not isinstance(token, str) or not token.strip():
        _fail("The authorization token is empty.")
    if "\x00" in token or "\n" in token or "\r" in token:
        _fail("The authorization token must be a single line without NUL characters.")
    return token.strip()


def _prompt_token(description):
    if not _is_interactive():
        _fail("No stored credential is available; supply a token or run installation at a terminal.")
    while True:
        try:
            token = getpass.getpass(f"Paste your {description} API key here: ")
        except EOFError:
            _fail("No input received.")
        if token:
            return _validate_token(token)
        print("Nothing entered — try again.", file=sys.stderr)


def validate_entry(entry):
    """Validate a store-relative manager entry, without forbidding safe punctuation."""
    if (not isinstance(entry, str) or not entry or entry.startswith(("/", "-"))
            or any(char in entry for char in ("\n", "\r", "\x00"))
            or any(part in ("", ".", "..") for part in entry.split("/"))):
        _fail("Secret entry must be a nonempty store-relative path without traversal or control characters.")
    return entry


def _command_argv(manager, action):
    entry = validate_entry(manager.get("entry"))
    command = manager.get(action)
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        _fail(f"Secret backend has no valid '{action}' command.")
    try:
        # Built-in templates end in an unquoted {entry}.  Split the command
        # prefix, not the resolved entry: spaces, quotes, and shell metacharacters
        # in that final value must remain ONE manager argument.
        if command.endswith(entry) and command[:-len(entry)].endswith((" ", "\t")):
            argv = shlex.split(command[:-len(entry)]) + [entry]
        else:
            argv = shlex.split(command)
    except ValueError:
        _fail(f"Secret backend '{action}' command has invalid quoting.")
    if not argv or not argv[0] or any("\x00" in arg for arg in argv):
        _fail(f"Secret backend has no valid '{action}' command.")
    return argv


def _secret_probe(manager):
    probe = manager.get("probe") or _command_argv(manager, "fetch")[0]
    if not isinstance(probe, str) or not probe or shutil.which(probe) is None:
        _fail(f"Secret backend '{manager.get('name', 'custom')}' is not installed (no '{probe}' on PATH).")


def _check_secret_store(manager, *, writing=False):
    from .secret_preflight import check_store

    return check_store(manager, writing=writing)


def _fetch_secret(manager, *, missing_ok=False):
    argv = _command_argv(manager, "fetch")
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=_SECRET_TIMEOUT)
    except subprocess.TimeoutExpired:
        _fail("Secret backend timed out reading the credential; it may be waiting for a passphrase (prime gpg-agent).")
    except OSError:
        _fail("Secret backend could not run its fetch command.")
    # Never include a manager's output or exception representation in errors:
    # even diagnostics from a broken manager can contain the credential.
    try:
        token = proc.stdout.decode().strip()
    except UnicodeError:
        _fail("Secret backend returned a credential that is not UTF-8 text.")
    if proc.returncode or not token:
        if missing_ok:
            return None
        _fail("Secret backend could not read the credential (entry missing, empty, or inaccessible).")
    return _validate_token(token)


def fetch_secret(manager):
    """Fetch a nonempty credential, suppressing possibly sensitive diagnostics."""
    _secret_probe(manager)
    return _fetch_secret(manager)


def _store_secret(manager, token):
    argv = _command_argv(manager, "store")
    try:
        proc = subprocess.run(argv, input=(_validate_token(token) + "\n").encode(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=_SECRET_TIMEOUT)
    except subprocess.TimeoutExpired:
        _fail("Secret backend timed out storing the credential (prime gpg-agent if needed).")
    except OSError:
        _fail("Secret backend could not run its store command.")
    if proc.returncode:
        _fail("Secret backend could not store the credential.")


def _run_manager(argv, what):
    """Run credential-free setup commands; the tool's diagnostics are useful here."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_SECRET_TIMEOUT)
    except subprocess.TimeoutExpired:
        _fail(f"{what} timed out; prime gpg-agent if needed.")
    except OSError:
        _fail(f"Could not run {argv[0]} for {what}.")
    if proc.returncode:
        _fail(f"{what} failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return proc


def _wizard_key_source(prog):
    print(f"The '{prog}' utility requires a GPG key.\n")
    print("[f]ile Path\n[i]nput stream (type it in)\n[C]reate a new key")
    choice = _ask("Where should the GPG key come from [f/i/C]?").lower()
    if choice == "f":
        path = Path(_ask("Path to the file holding the key id?")).expanduser()
        key_id = _read_regular_text(path, limit=_TOKEN_LIMIT, description="GPG key id file").strip()
        if not key_id:
            _fail(f"{path} holds no key id.")
        return key_id
    if choice == "i":
        return _ask("GPG key id?")
    if choice == "c":
        return _wizard_create_key()
    _fail("Answer 'f', 'i' or 'C'.")


def _wizard_create_key():
    identity = _ask("Name and email for the new key (e.g. Agent <agent@example.com>)?")
    print(" - Generating a key with no passphrase, so unattended fetch works.")
    _run_manager(["gpg", "--batch", "--passphrase", "", "--quick-generate-key",
                  identity, "default", "default", "never"], "key generation")
    proc = _run_manager(["gpg", "--list-keys", "--with-colons", identity], "key listing")
    fingerprints = [line.split(":")[9] for line in proc.stdout.splitlines()
                    if line.startswith("fpr:") and len(line.split(":")) > 9]
    if not fingerprints:
        _fail("Generated a GPG key but cannot list its fingerprint.")
    return fingerprints[-1]


def _wizard_choose(question, options, default):
    answer = _ask(question, default).lower()
    for letter, (_, value) in options.items():
        if answer == letter.lower():
            return value
    _fail(f"Answer one of {', '.join(options)}.")


def _wizard_pass():
    key_id = _wizard_key_source("pass")
    _run_manager(["pass", "init", key_id], "pass init")
    print(" - Password store initialized.")


def _wizard_gopass():
    crypto = {}
    if shutil.which("gpg"):
        crypto["G"] = ("GPG-CLI", "gpgcli")
    if shutil.which("age"):
        crypto["a"] = ("age", "age")
    crypto["p"] = ("plaintext", "plain")
    if len(crypto) == 1:
        print("WARNING: only plaintext storage is available; install 'gpg' or 'age' for encryption.")
        chosen_crypto = "plain"
    else:
        print("Cryptographic backend options:\n" + "\n".join(
            f"[{letter}] {name}" for letter, (name, _) in crypto.items()))
        print("WARNING: storing passwords / keys / tokens in plaintext is INSECURE.")
        chosen_crypto = _wizard_choose("Which backend should encrypt the store", crypto, next(iter(crypto)))
    storage = {"F": ("Filesystem", "fs")}
    if shutil.which("git"):
        storage["g"] = ("git", "gitfs")
    if len(storage) == 1:
        chosen_storage = "fs"
    else:
        print("Storage options:\n[F] Filesystem\n[g] git")
        chosen_storage = _wizard_choose("Which backend should store the data", storage, "F")
    argv = ["gopass", "init", "--crypto", chosen_crypto, "--storage", chosen_storage]
    if chosen_crypto != "plain":
        argv.append(_wizard_key_source("gopass"))
    _run_manager(argv, "gopass init")
    print(" - Password store initialized.")


def _wizard_prs():
    _run_manager(["prs", "init"], "prs init")
    print("The 'prs' utility requires a key.\n\n[f]ile Path\n[i]nput stream (type it in)\n[C]reate a new key")
    choice = _ask("Where should the key come from [f/i/C]?").lower()
    if choice in ("f", "i"):
        _run_manager(["prs", "recipients", "add", "--secret"], "adding the key")
    elif choice == "c":
        _run_manager(["prs", "recipients", "generate"], "generating a key")
    else:
        _fail("Answer 'f', 'i' or 'C'.")
    print(" - Password store initialized.")


def _store_missing(backend):
    if backend == "pass":
        path = Path(os.environ.get("PASSWORD_STORE_DIR") or Path.home() / ".password-store")
        # Never reinitialize an existing pass directory: init rewrites .gpg-id.
        if path.exists():
            return False
    try:
        proc = subprocess.run([backend, "list" if backend == "prs" else "ls"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=_SECRET_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        _fail(f"Could not check the '{backend}' password store (prime gpg-agent if needed).")
    return proc.returncode != 0


def _ensure_secret_store(backend):
    if backend not in ("pass", "gopass", "prs"):
        _fail(f"Secret backend '{backend}' has no guided setup; initialize its store by hand.")
    if shutil.which(backend) is None:
        _fail(f"Secret backend '{backend}' is not installed (no '{backend}' on PATH).")
    need = {"pass": "gpg", "gopass": None, "prs": "gpg"}[backend]
    if need and shutil.which(need) is None:
        _fail(f"Secret backend '{backend}' needs '{need}' on PATH; install it, then re-run.")
    if not _store_missing(backend):
        return
    if not _is_interactive():
        _fail(f"Secret backend '{backend}' has no store yet; run setup at a terminal to create one, or initialize it by hand.")
    {"pass": _wizard_pass, "gopass": _wizard_gopass, "prs": _wizard_prs}[backend]()


def setup_store(store, *, backend=None, secret_root=None):
    """Initialize a selected manager, then persist default credential settings.

    No backend (and none already configured) is explicitly keyless.  Custom
    managers must already be initialized. ``secret_root`` is an entry prefix,
    not the manager's on-disk store directory.
    """
    settings = store.settings["defaults"]["secret"]
    selected = backend if backend is not None else settings["backend"]
    changes = {}
    if secret_root is not None:
        validate_entry(secret_root)
        changes["root"] = secret_root
    if selected:
        name = selected.get("name") if isinstance(selected, dict) else selected
        if not isinstance(name, str) or not name:
            _fail("Secret backend must have a name.")
        if name in ("pass", "gopass", "prs"):
            _ensure_secret_store(name)
        elif isinstance(selected, dict):
            probe = selected.get("probe")
            if not probe or shutil.which(probe) is None:
                _fail(f"Custom secret backend '{name}' has no installed probe executable; initialize it by hand.")
        else:
            _fail(f"Secret backend '{name}' has no guided setup; configure and initialize it by hand.")
        if backend is not None:
            changes["backend"] = backend
    else:
        print("No credential backend configured; agents without secrets are keyless.")
    return store.configure(defaults={"secret": changes}) if changes else store.configure()


def _absolute(path):
    return Path(os.path.abspath(Path(path).expanduser()))


def _safe_agent_path(store, name):
    registered = store.agents()
    if name not in registered:
        _fail(f"Agent '{name}' is not registered.")
    if ".." in Path(registered[name]).expanduser().parts:
        _fail("Registered agent path must not contain '..'.")
    path = _absolute(store.locate(name))
    if path != _absolute(registered[name]):
        _fail("Registered agent path disagrees with the located directory.")
    protected = {Path.home().absolute(), Path.cwd().absolute(), Path(store.root).absolute(), Path(path.anchor)}
    if any(path == item or path in item.parents for item in protected):
        _fail(f"Refusing unsafe agent directory: {path} (root, home, cwd, store root, or their ancestor).")
    for other_name, other_path in registered.items():
        if other_name == name:
            continue
        other = _absolute(other_path)
        if path == other or path in other.parents or other in path.parents:
            _fail(f"Agent directory overlaps another registered agent ('{other_name}').")
    cfg = path / "agent.cfg"
    if not path.is_dir() or not cfg.is_file():
        _fail(f"Registered agent directory must contain a regular agent.cfg: {path}.")
    return path


def _native_paths(agent_path, harness):
    config_dir = _absolute(harness.get("path") or agent_path / "harness")
    expected = agent_path / "harness"
    if config_dir != expected:
        _fail("Harness config directory must be the registered agent's harness/ directory.")
    if config_dir.exists() and not config_dir.is_dir():
        _fail(f"Harness config directory is occupied by a file: {config_dir}.")
    config_file = harness.get("config_file")
    native_file = _absolute(config_file) if config_file else None
    if native_file is not None:
        if native_file == config_dir or config_dir not in native_file.parents:
            _fail("Native config file must be inside the agent's harness/ directory.")
        for parent in native_file.parents:
            if parent == config_dir:
                break
            if parent.exists() and not parent.is_dir():
                _fail(f"Native config parent is occupied by a file: {parent}.")
        if native_file.exists() and not native_file.is_file():
            _fail(f"Native config path is occupied by a directory: {native_file}.")
    owner = config_dir / _OWNER_FILE
    if config_dir.exists() and any(config_dir.iterdir()):
        try:
            valid = json.loads(_read_regular_text(
                owner, limit=_OWNER_LIMIT, description="installation ownership marker")) == {"agent_path": str(agent_path)}
        except (OSError, ValueError):
            valid = False
        if not valid:
            _fail(f"Harness config directory is occupied and is not managed by persona-grata: {config_dir}.")
    return config_dir, native_file


def _write_private(path, text):
    """Atomically replace private text, following user-selected links."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pg-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _rc_file():
    shell = os.environ.get("SHELL", "/bin/bash")
    return Path.home() / _RC_FILES.get(os.path.basename(shell), ".bashrc")


def _marker(name, agent_path=None):
    if agent_path is None:
        identity = f"name:{name}"
    else:
        identity = "path:" + str(_absolute(agent_path))
    return "persona-grata agent: " + hashlib.sha256(identity.encode()).hexdigest()


def _strip_wrapper(content, name, *, agent_path=None):
    marker = re.escape(_marker(name, agent_path))
    return re.sub(rf"(?m)^# >>> {marker} >>>\n.*?^# <<< {marker} <<<\n?", "", content, flags=re.DOTALL)


def _env_name(name):
    if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
        _fail("Wrapper environment variable names must be POSIX identifiers.")
    return name


def render_wrapper(name, harness, manager=None, *, agent_path=None):
    """Return a POSIX wrapper; all data is shell-quoted and fetch fails closed."""
    _env_name(name)
    if name in _SHELL_RESERVED:
        _fail("Agent command name is a reserved shell word.")
    executable = harness.get("command") or harness.get("name")
    if not isinstance(executable, str) or not executable or any(c in executable for c in ("\n", "\r", "\x00")):
        _fail("Harness must name an executable.")
    # `command --` is not POSIX; reject option-shaped executable names instead.
    if executable.startswith("-"):
        _fail("Harness executable cannot start with '-'.")
    assignments = {}
    if harness.get("path_var"):
        path_value = str(harness["path"])
        if "\x00" in path_value:
            _fail("Wrapper config path must not contain NUL characters.")
        assignments[_env_name(harness["path_var"])] = path_value
    auth_var = harness.get("auth_var")
    if auth_var:
        _env_name(auth_var)
    wrapper_env = harness.get("wrapper_env") or {}
    if not isinstance(wrapper_env, dict):
        _fail("wrapper_env must be a mapping of POSIX variable names to scalar values.")
    for key, value in wrapper_env.items():
        _env_name(key)
        if value not in (None, ""):
            if not isinstance(value, (str, int, float, bool)) or "\x00" in str(value):
                _fail("Wrapper environment values must be scalar values without NUL characters.")
            assignments[key] = str(value)
    if "_pg_token" in assignments:
        _fail("Wrapper configuration must not assign the reserved internal _pg_token variable.")
    if manager and auth_var and auth_var in assignments:
        _fail("wrapper_env must not shadow the manager-backed authorization variable.")
    marker = _marker(name, agent_path)
    lines = [f"# >>> {marker} >>>", f"{name}() ("]
    if manager:
        # A subshell avoids global/local-variable collisions and confines the
        # token to this invocation; command substitution strips trailing newlines.
        fetch = shlex.join(_command_argv(manager, "fetch"))
        lines.extend([
            "  set +x",  # An inherited xtrace must not print the fetched credential.
            f"  _pg_token=$({fetch} </dev/null 2>/dev/null) || {{",
            "    printf '%s\\n' 'persona-grata: credential fetch failed; harness not started.' >&2",
            "    exit 1",
            "  }",
            '  [ -n "$_pg_token" ] || {',
            "    printf '%s\\n' 'persona-grata: credential is empty; harness not started.' >&2",
            "    exit 1",
            "  }",
        ])
    for key, value in assignments.items():
        lines.append(f"  {key}={shlex.quote(value)}")
        lines.append(f"  export {key}")
    if manager and auth_var:
        lines.extend([f'  {auth_var}="$_pg_token"', f"  export {auth_var}"])
    if manager and auth_var != "_pg_token":
        lines.append("  unset _pg_token")
    lines.extend([f"  command {shlex.quote(executable)} \"$@\"", ")", f"# <<< {marker} <<<", ""])
    return "\n".join(lines)


def install_shell_wrapper(name, harness, manager=None, *, agent_path=None):
    wrapper = render_wrapper(name, harness, manager, agent_path=agent_path)
    rc_file = _rc_file()
    content = _read_regular_text(rc_file, limit=_RC_LIMIT, description="shell startup file") if rc_file.exists() else ""
    content = _strip_wrapper(content, name, agent_path=agent_path).rstrip()
    _write_private(rc_file, (content + "\n\n" if content else "") + wrapper)
    _report_shell_wrapper(name, rc_file)
    return rc_file


def _report_shell_wrapper(name, rc_file):
    shell = os.environ.get("SHELL", "")
    if shell and os.path.basename(shell) not in _KNOWN_SHELLS:
        print(f"Warning: $SHELL is {shell}; its startup conventions are unknown. POSIX wrapper written to {rc_file}.")
    elif rc_file.name == ".profile":
        print("Note: .profile is read by login shells; non-login ash/dash need ENV to source it.")
    print(f"Installed {name}; open a new terminal or source {rc_file}.")


def remove_shell_wrapper(name, *, agent_path=None):
    """Remove this agent's marker from known startup files, including after SHELL changes."""
    removed = False
    for basename in {".bashrc", *_RC_FILES.values()}:
        rc_file = Path.home() / basename
        if not rc_file.exists():
            continue
        content = _read_regular_text(rc_file, limit=_RC_LIMIT, description="shell startup file")
        after = _strip_wrapper(content, name, agent_path=agent_path)
        if after != content:
            _write_private(rc_file, after.rstrip() + "\n")
            removed = True
    return removed


class _NoVerificationRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # A credential probe must never forward authentication to another URL.
        return None


def _open_verification(request, *, timeout):
    return urllib.request.build_opener(_NoVerificationRedirect()).open(request, timeout=timeout)


def _verify_token(agent, token):
    harness = agent["harness"]
    persona = agent.get("persona") or {}
    dialects = persona.get("dialects") or (persona.get("mind") or {}).get("dialects") or {}
    verification = (dialects.get(harness.get("protocol")) or {}).get("verify")
    if not verification:
        print("Warning: no verify section for this dialect; skipping credential check.", file=sys.stderr)
        return
    model = (persona.get("mind") or {}).get("model_active") or ""
    body = verification.get("body") or json.dumps({"model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1})
    try:
        request = urllib.request.Request(verification["check_uri"], data=body.encode(), method="POST")
        for line in ["Content-Type: application/json", *(verification.get("headers") or [])]:
            key, _, value = line.partition(":")
            request.add_header(key.strip(), value.strip())
        if verification.get("key_header"):
            key, _, value = verification["key_header"].partition(":")
            request.add_header(key.strip(), f"{value.strip()} {token}".strip())
        with _open_verification(request, timeout=_VERIFY_TIMEOUT) as response:
            code = response.status
    except urllib.error.HTTPError as error:
        code = error.code
    except Exception:
        print("Warning: verification endpoint unreachable; skipping credential check.", file=sys.stderr)
        return
    if code in (401, 403):
        _fail(f"Credential rejected (HTTP {code}); check and re-run.")
    if code == 404:
        _fail("Verification endpoint not found (HTTP 404).")
    if code != 200:
        print(f"Warning: verification returned HTTP {code}; skipping credential check.", file=sys.stderr)


@dataclass(frozen=True)
class _Installation:
    name: str
    agent_path: Path
    harness: dict = field(repr=False)
    config_dir: Path
    native_file: Path | None
    content: str | None = field(repr=False)
    manager: dict | None = field(repr=False)
    token: str | None = field(repr=False)
    fresh: bool
    shell_file: Path | None
    shell_before: str | None = field(repr=False)
    shell_content: str | None = field(repr=False)


def prepare_installation(resolved, *, name=None, token=None, verify=False, install_wrapper=True):
    """Validate installation and obtain its credential without persisting anything."""
    agent_path = _absolute(resolved["runtime"]["agent_path"])
    protected = {Path.home().absolute(), Path.cwd().absolute(), Path(agent_path.anchor)}
    if any(agent_path == path or agent_path in path.parents for path in protected):
        _fail(f"Refusing unsafe agent directory: {agent_path}.")
    agent = resolved["agent"]
    name = agent["name"] if name is None else name
    harness = agent["harness"]
    config_dir, native_file = _native_paths(agent_path, harness)
    content = harness.get("content")
    if native_file is not None and not isinstance(content, str):
        _fail("Native harness content must be rendered text.")
    manager = agent.get("secret")
    if manager is None:
        if token is not None:
            _fail("This agent is keyless; there is nowhere to store the supplied token.")
    elif not isinstance(manager, dict) or not manager:
        _fail("Agent requests a credential but no secret backend is configured; run store setup first.")
    for target in (config_dir / _OWNER_FILE, *((native_file,) if native_file is not None else ())):
        check_write_path(target)
    shell_file = shell_before = shell_content = None
    if install_wrapper:
        wrapper = render_wrapper(name, harness, manager, agent_path=agent_path)
        shell_file = _rc_file()
        check_write_path(shell_file)
        shell_before = (_read_regular_text(shell_file, limit=_RC_LIMIT, description="shell startup file")
                        if shell_file.exists() else "")
        existing = _strip_wrapper(shell_before, name, agent_path=agent_path).rstrip()
        shell_content = (existing + "\n\n" if existing else "") + wrapper
    key, fresh = None, False
    if manager:
        _secret_probe(manager)
        for action in ("fetch", "store"):
            argv = _command_argv(manager, action)
            if shutil.which(argv[0]) is None:
                _fail(f"Secret backend cannot run its {action} command (executable missing).")
        exists = _check_secret_store(manager, writing=token is not None)
        if token is not None:
            key = _validate_token(token)
        else:
            key = None if exists is False else _fetch_secret(manager)
        fresh = token is not None or key is None
        if key is None:
            key = _prompt_token(agent.get("desc") or name)
            _check_secret_store(manager, writing=True)
        if verify:
            _verify_token(agent, key)
    return _Installation(name, agent_path, harness, config_dir, native_file, content,
                         manager, key, fresh, shell_file, shell_before, shell_content)


def _check_installation(plan):
    if _native_paths(plan.agent_path, plan.harness) != (plan.config_dir, plan.native_file):
        _fail("Installation paths changed during preflight; retry the operation.")
    if plan.shell_file is not None:
        current = (_read_regular_text(plan.shell_file, limit=_RC_LIMIT, description="shell startup file")
                   if plan.shell_file.exists() else "")
        if current != plan.shell_before:
            _fail("Shell startup file changed during preflight; retry the operation.")


def commit_credential(plan):
    """Import and read back a prepared credential before committing pg state."""
    _check_installation(plan)
    if plan.manager and plan.fresh:
        _store_secret(plan.manager, plan.token)
        if _fetch_secret(plan.manager) != plan.token:
            _fail("Secret backend did not return the credential it just stored.")


def apply_installation(plan):
    """Write only the native settings and wrapper validated during preparation."""
    _check_installation(plan)
    plan.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if plan.native_file is not None:
        _write_private(plan.native_file, plan.content)
    _write_private(plan.config_dir / _OWNER_FILE, json.dumps({"agent_path": str(plan.agent_path)}) + "\n")
    if plan.shell_file is not None:
        _write_private(plan.shell_file, plan.shell_content)
        _report_shell_wrapper(plan.name, plan.shell_file)
    if plan.manager is None:
        print(f"Agent '{plan.name}' is keyless; no credentials configured.")
    return plan.agent_path


def install_agent(store, name, *, token=None, verify=False, install_wrapper=True):
    """Prepare and install an already registered source definition."""
    agent_path = _safe_agent_path(store, name)
    resolved = store.load(name)
    if _absolute(resolved["runtime"]["agent_path"]) != agent_path:
        _fail("Resolved runtime agent path disagrees with the registry.")
    plan = prepare_installation(resolved, name=name, token=token, verify=verify, install_wrapper=install_wrapper)
    commit_credential(plan)
    return apply_installation(plan)


def remove_agent(store, name, *, confirm=None):
    """Confirm, remove a registered agent directory and wrapper, then unregister.

    ``confirm=None`` prompts with a safe default (non-TTY means no).
    ``confirm=True`` is an explicit caller confirmation, ``False`` cancels;
    a callable receives the confirmation question. Shared manager entries and
    composed store definitions are always retained.
    """
    agent_path = _safe_agent_path(store, name)
    question = f"Remove agent '{name}' and all files in {agent_path}?"
    if confirm is None:
        accepted = _confirm(question)
    elif callable(confirm):
        accepted = confirm(question)
    else:
        accepted = confirm is True
    if not accepted:
        return False
    # Confirm callbacks may execute arbitrary code; check registry and filesystem
    # again after consent, before touching wrappers or deleting anything.
    if _safe_agent_path(store, name) != agent_path:
        _fail("Agent directory changed during removal confirmation.")
    remove_shell_wrapper(name, agent_path=agent_path)
    if agent_path.is_symlink():
        agent_path.unlink()
    else:
        shutil.rmtree(agent_path)
    store.unregister(name)
    print(f"Removed agent '{name}'; password-manager entries were retained.")
    return True

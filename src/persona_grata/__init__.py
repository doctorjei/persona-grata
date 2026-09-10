"""Persona setup workflow: resolve agents.yaml, then wire up a harness.

All template resolution is delegated to :mod:`template_engine`; this module only
assembles the layered configuration and consumes the resolved values to obtain
and verify the API token, create directories, render the harness config file,
and install a shell wrapper.
"""

import os
import re
import sys
import copy
import shutil
import getpass
import urllib.request
import urllib.error
from pathlib import Path

import yaml
from . import template_engine as te

__version__ = "0.0.2"

# Default values for selected environment variables (rule 1e is "empty string";
# these are the caller-supplied defaults the engine falls back to when a var is
# absent from the real environment).
ENV_DEFAULTS = {
    "XDG_CONFIG_HOME": str(Path.home() / ".config"),
    "HOME": str(Path.home()),
}

# Shared verification bits live here, NOT in the schema (only per-harness values
# belong in agents.yaml). content-type is always sent; the ping body is the same
# for every harness and only injects the persona's model.
VERIFY_TIMEOUT = 10
DEFAULT_VERIFY_HEADERS = ["content-type: application/json"]
DEFAULT_BODY = """{"model": "%s", "max_tokens": 16, "messages": [{"role": "user", "content": "ping"}]}"""

DATA_DIR = Path(__file__).parent / "data"

# The schema files use `None` to mean "unset" -- readable in a comment-heavy
# reference file, but YAML 1.1 has no such token, so PyYAML hands it back as a
# plain string. Normalize it (and its YAML-ish spellings) on load.
_UNSET_TOKENS = {"none", "null"}

USAGE = """\
Usage:
  persona-grata [options] <persona> [harness ...]
  persona-grata [options] <agents.yaml> [persona] [harness ...]

Omit the harness names to act on every harness known to that persona; with a
configuration file, omit the persona too to act on every persona it declares.
The shipped presets are a library to choose from, so naming neither a persona
nor a file is an error rather than a request to install all of them.

  -r, --remove   Remove the shell wrapper and config directory instead of
                 installing them. Offers to delete the persona's token once
                 all of its harnesses are gone.
  -i, --interactive
                 Ask for a persona's settings rather than taking them as the
                 flags below, then offer to save the result. Anything already
                 given on the command line is not asked about again.
  --token FILE   Read the API key from FILE instead of prompting for it, for
                 unattended setup. The key is verified and stored exactly as a
                 typed one would be; FILE itself is only read. Contradicts
                 --no-token, which says there is no key at all.
  -h, --help     Show this message.

Defining a persona on the command line, instead of writing an agents.yaml.
These define the named persona if it is new, or override it if it exists:

  --endpoint URL   API endpoint                        (mind.endpoint)
  --model NAME     Primary model                       (mind.model)
  --desc TEXT      Short description                   (persona_desc)
  --no-token       Endpoint needs no key; skips the    (token)
                   key prompt and its verification
  --create         Write a new persona; fails if the name is taken. This is the
                   default, so it is only worth naming to make the intent
                   explicit -- a script that means "create" then cannot quietly
                   do something else.
  --update         Update an existing persona; fails if there is none by that
                   name.
  --export FILE    Write the persona's configuration to FILE and do nothing
                   else. The persona is loaded first, so the export is its
                   settings with any replacements above applied.

Which of the three a definition performs is explicit, so a name collision is
never resolved silently:

  --create         write a new persona -- fails if the name is taken (default)
  --update         change an existing one -- fails if the name is free
  --export FILE    write the configuration out; sets nothing up

Removing an agent set up this way works by name alone: everything --remove
needs comes from the persona store.

  # A local model needing no API key:
  persona-grata --endpoint http://localhost:11434 --model llama3 \\
                --no-token ollama claude

  # The same settings, asked for rather than typed:
  persona-grata -i

  # Unattended, with the key already on disk:
  persona-grata --token ~/keys/moonshot.key kimi claude

`pg` is a shorter alias for `persona-grata`."""

# Command-line flags that define a persona, mapped to their path in the schema.
_VALUE_FLAGS = {
    "--endpoint": ("mind", "endpoint"),
    "--model": ("mind", "model"),
    "--desc": ("persona_desc",),
}

# Flags that take a filename and act on it, rather than defining a persona.
_FILE_FLAGS = ("--export", "--token")


def deep_merge(source, destination):
    """Recursively merge source into destination."""
    for key, value in source.items():
        if isinstance(value, dict):
            node = destination.setdefault(key, {})
            if isinstance(node, dict):
                deep_merge(value, node)
            else:
                destination[key] = value
        else:
            destination[key] = value
    return destination


def _normalize_unset(value):
    """Rewrite the schema's ``None`` placeholder to a real ``None``."""
    if isinstance(value, dict):
        return {k: _normalize_unset(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_unset(v) for v in value]
    if isinstance(value, str) and value.strip().lower() in _UNSET_TOKENS:
        return None
    return value


def _ensure_dict(mapping, key):
    """``setdefault``, but an unset placeholder is replaced by a fresh dict."""
    value = mapping.get(key)
    if not isinstance(value, dict):
        value = {}
        mapping[key] = value
    return value


def load_yaml(path, env_defaults=None):
    """Read text -> substitute env vars -> parse YAML -> normalize placeholders."""
    if not Path(path).exists():
        return None
    raw = Path(path).read_text()
    substituted = te.substitute_env(raw, env_defaults)
    return _normalize_unset(yaml.safe_load(substituted))


def preset_names(kind):
    """Sorted ids of the ``<kind>.<id>.yaml`` presets shipped in ``data/``."""
    return sorted(f.stem.split(".", 1)[1] for f in DATA_DIR.glob(f"{kind}.*.yaml")
                  if f.stem != f"{kind}.default")


def _load_preset(kind, name, env_defaults, schema_keys):
    """Load ``data/<kind>.<name>.yaml``, unwrapping its self-named outer block.

    Presets wrap their body in their own id so that a block can be pasted
    straight into an ``agents.yaml``; the layering merges the *body*, so the
    wrapper is stripped here. A wrapper that disagrees with the filename is an
    authoring mistake and is reported rather than silently merged as a stray key.
    """
    data = load_yaml(DATA_DIR / f"{kind}.{name}.yaml", env_defaults)
    if not isinstance(data, dict):
        return {}
    if len(data) == 1:
        (key, body), = data.items()
        if key not in schema_keys:
            if key != name:
                sys.exit(f"Error: {kind} preset '{name}.yaml' wraps its settings in "
                         f"'{key}:', which does not match its id '{name}'.")
            return body if isinstance(body, dict) else {}
    return data


def _normalize_personas(user_cfg):
    """Accept the ``personas`` shorthands (a name, or a list of names)."""
    personas = user_cfg.get("personas")
    if isinstance(personas, str):
        user_cfg["personas"] = {personas: {}}
    elif isinstance(personas, list):
        user_cfg["personas"] = {name: {} for name in personas}
    elif not isinstance(personas, dict):
        user_cfg["personas"] = {}
    return user_cfg


def declared_personas(path, env_defaults=None):
    """Persona names the user's file asks for, in order (empty when there is none)."""
    if path is None:
        return []
    user_cfg = load_yaml(path, env_defaults if env_defaults is not None else ENV_DEFAULTS)
    if not isinstance(user_cfg, dict):
        return []
    return list(_normalize_personas(user_cfg)["personas"])


def _warn_unknown_keys(user_cfg, top_schema, persona_schema, harness_schema):
    """Warn about settings keys the schema does not define.

    A misspelled key is otherwise silent: it merges into the tree, nothing reads
    it, and setup quietly uses the default. Persona and harness *names* are the
    user's to invent, so only their settings are checked, and ``config_store``
    is free-form harness data that is deliberately not validated.
    """
    warnings = []

    def check(mapping, allowed, where):
        if not isinstance(mapping, dict):
            return
        for key in mapping:
            if key not in allowed:
                warnings.append(f"{where}{key}")

    check(user_cfg, top_schema, "")
    for pid, persona in (user_cfg.get("personas") or {}).items():
        if not isinstance(persona, dict):
            continue
        check(persona, persona_schema, f"personas.{pid}.")
        check(persona.get("mind"), persona_schema.get("mind") or {}, f"personas.{pid}.mind.")
        for hid, harness in (persona.get("harnesses") or {}).items():
            if not isinstance(harness, dict):
                continue
            base = f"personas.{pid}.harnesses.{hid}."
            check(harness, harness_schema, base)
            check(harness.get("verify"), harness_schema.get("verify") or {}, f"{base}verify.")

    for path in warnings:
        print(f"Warning: unknown setting '{path}' — ignored (check spelling).", file=sys.stderr)
    return warnings


def _drop_disabled(config):
    """Remove personas/harnesses the configuration switched off with ``None``."""
    personas = _ensure_dict(config, "personas")
    for pid, persona in list(personas.items()):
        if not isinstance(persona, dict):
            del personas[pid]
            continue
        harnesses = _ensure_dict(persona, "harnesses")
        for hid, harness in list(harnesses.items()):
            if not isinstance(harness, dict):
                del harnesses[hid]
    return config


def load_config(path=None, env_defaults=None, overrides=None):
    """Assemble and resolve the full configuration tree.

    Layered bottom-up, each stage overriding the last -- most specific wins::

        persona:  Global Default -> Persona Default -> Persona Preset -> User
        harness:  Harness Default -> Harness Preset
                                  -> Persona Preset's harnesses.<hid> -> User

    ``overrides`` is a user-config-shaped mapping layered *over* the file, for
    definitions supplied on the command line. It is applied before layering, so
    a persona that exists only in ``overrides`` is built like any other.

    Every persona -- the user's and the shipped presets alike -- is built, so
    that absolute references such as ``{{personas.orion.mind.model}}`` resolve
    from anywhere. Choosing *which* personas to actually set up is the caller's
    job (see :func:`declared_personas`).
    """
    if env_defaults is None:
        env_defaults = ENV_DEFAULTS

    # 1. User config -- optional; the shipped presets alone are a usable config.
    user_cfg = {}
    if path is not None:
        user_cfg = load_yaml(path, env_defaults)
        if user_cfg is None:
            sys.exit(f"Error: Configuration file {path} not found.")
        if not isinstance(user_cfg, dict):
            sys.exit(f"Error: Configuration file {path} is not a mapping.")
    user_cfg = _normalize_personas(user_cfg)
    # Command-line definitions are the most specific input there is, so they go
    # on top of the file before anything is layered.
    if overrides:
        deep_merge(_normalize_personas(copy.deepcopy(overrides)), user_cfg)
    users_personas = user_cfg["personas"]

    # 2. Global defaults.
    base_cfg = load_yaml(DATA_DIR / "agents.default.yaml", env_defaults) or {}
    base_personas = _ensure_dict(base_cfg, "personas")

    persona_default = load_yaml(DATA_DIR / "persona.default.yaml", env_defaults) or {}
    harness_default = load_yaml(DATA_DIR / "harness.default.yaml", env_defaults) or {}
    known_harnesses = preset_names("harness")

    # The .default.yaml files *are* the schema; anything else the user wrote is
    # a typo that would otherwise fail silently. pid/hid are injected, not authored.
    _warn_unknown_keys(user_cfg,
                       set(base_cfg) | {"personas"},
                       {**persona_default, "pid": None},
                       {**harness_default, "hid": None})

    # 3. Layer every persona: shipped presets plus whatever the user declared.
    for pid in dict.fromkeys(preset_names("persona") + list(users_personas)):
        preset = _load_preset("persona", pid, env_defaults, persona_default)
        # A persona preset may also carry per-harness overrides; those are more
        # specific than the harness presets, so they are layered separately below
        # rather than merged in with the rest of the persona.
        preset_harnesses = preset.pop("harnesses", None) or {}

        persona_layer = copy.deepcopy(persona_default)
        deep_merge(preset, persona_layer)
        persona_layer.pop("harnesses", None)
        persona_layer["pid"] = pid
        persona = deep_merge(persona_layer, _ensure_dict(base_personas, pid))

        # Harness expansion: every known harness, plus any this persona adds.
        declared = users_personas.get(pid)
        declared = declared.get("harnesses") if isinstance(declared, dict) else None
        harness_ids = dict.fromkeys(
            known_harnesses + list(preset_harnesses) + list(declared or {}))

        harness_map = _ensure_dict(persona, "harnesses")
        for hid in harness_ids:
            if hid in preset_harnesses and preset_harnesses[hid] is None:
                continue                              # preset switched it off
            harness_layer = copy.deepcopy(harness_default)
            deep_merge(_load_preset("harness", hid, env_defaults, harness_default), harness_layer)
            if isinstance(preset_harnesses.get(hid), dict):
                deep_merge(preset_harnesses[hid], harness_layer)
            harness_layer["hid"] = hid
            deep_merge(harness_layer, _ensure_dict(harness_map, hid))

    # 4. User overrides go on last, so they beat every default and preset.
    deep_merge(user_cfg, base_cfg)

    # 5. Drop anything switched off before resolving -- a disabled harness need
    #    not hold resolvable templates.
    _drop_disabled(base_cfg)

    # 6. Template resolution.
    return te.resolve_tree(base_cfg)


def _require(mapping, key, kind):
    if not isinstance(mapping, dict) or key not in mapping:
        sys.exit(f"Error: {kind} '{key}' not found in config.")
    return mapping[key]


def _path(value):
    """A configured path as a real one -- ``~`` is the user's, not a directory."""
    return Path(value).expanduser()


# --------------------------------------------------------------------------- #
# Token acquisition + verification
# --------------------------------------------------------------------------- #
def _prompt_key(persona_desc):
    """Prompt (hidden) until a non-empty key is entered; exit on EOF."""
    while True:
        try:
            key = getpass.getpass(f"Paste your {persona_desc} API key here: ")
        except EOFError:
            print("Error: No input received.", file=sys.stderr)
            sys.exit(1)
        if key:
            return key
        print("Nothing entered — try again.", file=sys.stderr)


def _read_key(path):
    """The API key held in a file, for supplying one without a prompt.

    Stripped, because a key file conventionally ends in a newline and a trailing
    one would otherwise be copied verbatim into the token store. Read up front
    so that an unusable file fails before any setup has begun.
    """
    try:
        key = _path(path).read_text().strip()
    except OSError as error:
        sys.exit(f"Error: cannot read the key file {path}: {error.strerror}.")
    if not key:
        sys.exit(f"Error: the key file {path} is empty.")
    return key


def _confirm(question, default=False):
    """Yes/No prompt. Blank input or EOF returns ``default``."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        ans = input(question + suffix).strip().lower()
    except EOFError:
        return default
    if ans in ("y", "yes"):
        return True
    elif ans in ("n", "no"):
        return False
    return default


def _ask(question, default=None):
    """Text prompt. Blank input takes ``default``; without one, it re-asks.

    EOF is fatal rather than silently defaulted -- unlike :func:`_confirm`,
    there is no safe answer to invent for an endpoint or a name.
    """
    suffix = f" [{default}]: " if default else ": "
    while True:
        try:
            answer = input(question + suffix).strip()
        except EOFError:
            print("\nError: No input received.", file=sys.stderr)
            sys.exit(1)
        if answer:
            return answer
        if default is not None:
            return default
        print("Nothing entered — try again.", file=sys.stderr)


def _add_header(req, line, key=None):
    """Split a ``Name: value`` header line and add it; if ``key`` is given the
    line is a key-header prefix and the key is appended (mirrors the shell's
    ``-H "$KEY_HEADER $KEY"``)."""
    name, _, value = line.partition(":")
    value = value.strip()
    if key is not None:
        value = f"{value} {key}".strip()
    req.add_header(name.strip(), value)


def _verify_key(verify, model, key):
    """Ping the API to validate the key before anything is written.

    Fatal (exit) on an authenticated rejection (401/403) or a missing endpoint
    (404). A missing ``verify`` block, an unreachable host, or any other status
    only warns and lets setup continue (matches the shell's tolerance).
    """
    if not verify:
        print("Warning: no 'verify' section for this harness — skipping key check.",
              file=sys.stderr)
        return

    url = verify["check_uri"]
    headers = DEFAULT_VERIFY_HEADERS + list(verify.get("headers") or [])
    body = verify.get("body") or DEFAULT_BODY % (model or "")

    print("Verifying key... ", end="", flush=True)
    # The request is built inside the try: a malformed url raises from
    # Request() rather than from urlopen(), and an unusable verify url should
    # degrade like any other failure to reach the endpoint, not traceback.
    try:
        req = urllib.request.Request(url, data=body.encode(), method="POST")
        for line in headers:
            _add_header(req, line)
        if verify.get("key_header"):
            _add_header(req, verify["key_header"], key=key)
        with urllib.request.urlopen(req, timeout=VERIFY_TIMEOUT) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:                     # any transport failure == shell's `|| code="000"`
        print("unreachable. Skipping key check.", file=sys.stderr)
        return

    if code == 200:
        print("success! Key verified.")
    elif code in (401, 403):
        print(f"\nError: key rejected (HTTP {code}). Check & re-run.", file=sys.stderr)
        sys.exit(1)
    elif code == 404:
        print("\nError: endpoint not found (HTTP 404). Check the verify url.", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"error (HTTP {code}). Skipping key check.", file=sys.stderr)


def _write_private(path, text):
    """Write ``text`` to ``path`` with 0600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(text)


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
def setup_harness(persona_id, harness_id, config, key=None):
    """Wire up one agent. ``key`` supplies the API key in place of a prompt."""
    personas = _require(config, "personas", "section")
    persona = _require(personas, persona_id, "persona")
    harnesses = _require(persona, "harnesses", "section")
    harness = _require(harnesses, harness_id, "harness")

    persona_desc = persona.get("persona_desc") or persona_id
    endpoint = (persona.get("mind") or {}).get("endpoint")
    if not endpoint:
        sys.exit(f"Error: persona '{persona_id}' has no 'mind.endpoint' — it is required.")
    # Everything downstream issues HTTP against this -- key verification, and
    # the harnesses themselves once installed. A bare host is therefore an
    # unusable agent, not merely an unverifiable one; say so here rather than
    # writing a config that cannot work.
    if not str(endpoint).startswith(("http://", "https://")):
        sys.exit(f"Error: persona '{persona_id}' has 'mind.endpoint: {endpoint}', which "
                 "has no scheme — write e.g. http://127.0.0.1:11434.")
    model = (persona.get("mind") or {}).get("model") or ""

    home = persona.get("path")
    if not home:
        sys.exit(f"Error: persona '{persona_id}' resolved an empty 'path'.")
    home = _path(home)
    token_path = _path(persona["token"]) if persona.get("token") else None

    harness_desc = harness.get("harness_desc") or harness_id
    agent_desc = harness.get("agent_desc") or f"{persona_id}-{harness_id}"
    config_dir = harness.get("path")
    if not config_dir:
        sys.exit(f"Error: harness '{harness_id}' resolved an empty 'path'.")
    config_dir = _path(config_dir)
    config_file = harness.get("config_file")
    content = harness.get("content")
    path_var = harness.get("path_var") or ""
    auth_var = harness.get("auth_var") or ""
    wrapper_env = harness.get("wrapper_env") or {}
    verify = harness.get("verify")

    print("\n===========================================================================")
    print(f"    {agent_desc} Setup Script  ({persona_desc} & {harness_desc})")
    print("===========================================================================\n")

    # 1. Token: keep an existing one on request, else prompt + verify + write.
    #    Verification runs before anything is written, so a bad key writes nothing.
    #    A persona with no token path talks to an endpoint that needs no key.
    #    A key from --token skips both prompts: naming a file is already an
    #    explicit instruction to set the token, so there is nothing to confirm.
    if token_path is None:
        if key is not None:
            sys.exit(f"Error: persona '{persona_id}' stores no token, so there is "
                     "nowhere to put the key from --token.")
        print(" - No token path configured; skipping key setup.")
    elif key is None and token_path.exists() and not _confirm(
            "Replace existing authorization token?"):
        print(f" - Keeping existing token at {token_path}.")
    else:
        key = _prompt_key(persona_desc) if key is None else key
        _verify_key(verify, model, key)
        _write_private(token_path, key)

    # 2. Directories
    home.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    # 3. Record the token location for downstream tooling (0600).
    if token_path is not None:
        _write_private(home / ".secret_path", str(token_path) + "\n")

    # 4. Render config file (content is already fully resolved by the engine).
    if config_file and content:
        config_file = _path(config_file)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(content)
        print(f" - Wrote {config_file}.")
    else:
        print(f" - No config file for {harness_desc}; skipping.")

    # 5. Shell wrapper
    _install_shell_wrapper(persona_id, persona_desc, harness_id, harness_desc,
      config_dir, token_path, path_var, auth_var, wrapper_env, agent_desc)


def _rc_file():
    """The shell rc file this user's login shell reads."""
    shell = os.environ.get("SHELL", "/bin/bash")
    return Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")


def _strip_wrapper(content, persona_id, harness_id, persona_desc=None, harness_desc=None):
    """Remove an agent's wrapper block, matched by its markers.

    Blocks are keyed on the stable ``<pid>-<hid>``; the description-keyed form
    is matched too so blocks written before that change are still cleaned up.
    """
    markers = [re.escape(f"persona-grata: {persona_id}-{harness_id}")]
    if persona_desc and harness_desc:
        markers.append(rf"{re.escape(persona_desc)} & {re.escape(harness_desc)}")
    for marker in markers:
        content = re.sub(rf"# >>> {marker} >>>.*?# <<< {marker} <<<", "",
                         content, flags=re.DOTALL)
    return content


def _install_shell_wrapper(persona_id, persona_desc, harness_id, harness_desc,
      config_dir, token_path, path_var, auth_var, wrapper_env=None, agent_desc=None):
    rc_file = _rc_file()
    cmd_name = f"{persona_id}-{harness_id}"
    agent_desc = agent_desc or cmd_name

    # Only emit assignments the harness actually uses; an empty name would
    # otherwise become a bare `="..."` word and break the function.
    assignments = []
    if path_var:
        assignments.append(f'{path_var}="{config_dir}"')
    if auth_var and token_path is not None:
        assignments.append(f'{auth_var}="$(cat {token_path})"')
    # Harnesses with no relocatable config file are configured entirely here.
    for name, value in (wrapper_env or {}).items():
        if name and value not in (None, ""):
            assignments.append(f'{name}="{value}"')
    env_lines = "".join(f"  {a} \\\n" for a in assignments)

    wrapper = f"""
# >>> persona-grata: {cmd_name} >>>
{cmd_name}() {{
{env_lines}  command {harness_id} "$@"
}}
# <<< persona-grata: {cmd_name} <<<
"""

    content = rc_file.read_text() if rc_file.exists() else ""
    content = _strip_wrapper(content, persona_id, harness_id,
                             persona_desc, harness_desc).strip()
    rc_file.write_text((content + "\n" if content else "") + wrapper)

    print("Setup complete.\n")
    print("Setup Notes")
    print("-----------")
    print(f"1. Before use, open a new terminal or run `source {rc_file}`.")
    print(f"2. Running {harness_id} still uses its native models (settings unchanged).\n")
    print(f"To run {agent_desc} ({persona_desc} with {harness_desc})")
    print("----------------------------------------------------------------------------")
    print(f"> {cmd_name}\n")


# --------------------------------------------------------------------------- #
# Removal
# --------------------------------------------------------------------------- #
def _too_dangerous_to_remove(target):
    """Refuse to delete a root, a home, or anything containing one.

    Directories come from resolved config, so a broken template must not be able
    to aim ``rmtree`` at something catastrophic.
    """
    resolved = target.resolve()
    home = Path.home().resolve()
    return (resolved == Path(resolved.anchor)
            or resolved == home
            or resolved in home.parents)


def _remove_tree(target, label):
    """Delete a configured directory, or explain why it was left alone."""
    if not target.is_dir():
        print(f" - No {label} at {target}; nothing to remove.")
        return False
    if _too_dangerous_to_remove(target):
        print(f"Error: refusing to remove {target} — that is a home or root directory.",
              file=sys.stderr)
        return False
    shutil.rmtree(target)
    print(f" - Removed {label} {target}.")
    return True


def remove_harness(persona_id, harness_id, config):
    """Undo :func:`setup_harness`: drop the shell wrapper and the config dir.

    The persona directory (which holds the API token) is deliberately left in
    place; :func:`main` offers it separately once every harness is gone.
    """
    personas = _require(config, "personas", "section")
    persona = _require(personas, persona_id, "persona")
    harnesses = _require(persona, "harnesses", "section")
    harness = _require(harnesses, harness_id, "harness")

    persona_desc = persona.get("persona_desc") or persona_id
    harness_desc = harness.get("harness_desc") or harness_id
    agent_desc = harness.get("agent_desc") or f"{persona_id}-{harness_id}"

    print(f"\nRemoving {agent_desc} ({persona_desc} & {harness_desc})")

    rc_file = _rc_file()
    if rc_file.exists():
        before = rc_file.read_text()
        after = _strip_wrapper(before, persona_id, harness_id, persona_desc, harness_desc)
        if after != before:
            rc_file.write_text(after.strip() + "\n")
            print(f" - Removed the shell wrapper from {rc_file}.")
        else:
            print(f" - No shell wrapper found in {rc_file}.")

    if harness.get("path"):
        _remove_tree(_path(harness["path"]), "config directory")


def remove_persona_store(persona_id, config):
    """Delete a persona's directory, including its API token."""
    persona = (config.get("personas") or {}).get(persona_id) or {}
    if not persona.get("path"):
        return False
    return _remove_tree(_path(persona["path"]), "persona directory (including token)")


# --------------------------------------------------------------------------- #
# Interactive definition
# --------------------------------------------------------------------------- #
def _ask_persona_name(known, updating):
    """Ask for a persona id, held to the same rule the chosen mode enforces.

    A name that fails is re-asked rather than fatal: the point of the interview
    is that someone is sitting there able to correct it.
    """
    while True:
        name = _ask("Persona name")
        if re.search(r"[\s/]", name):
            print("A persona name becomes a directory and a shell function name, "
                  "so it cannot contain spaces or '/'.", file=sys.stderr)
        elif updating and name not in known:
            print(f"No persona named '{name}'. Known: "
                  f"{', '.join(sorted(known)) or 'none'}.", file=sys.stderr)
        elif not updating and name in known:
            print(f"'{name}' already exists; --update changes an existing persona.",
                  file=sys.stderr)
        else:
            return name


def _ask_endpoint(default):
    """Ask for the endpoint, insisting on a scheme urllib can actually fetch.

    Everything downstream -- key verification and the harnesses themselves --
    issues HTTP against this, so a bare host is a mistake worth catching while
    it can still be retyped rather than at first use.
    """
    while True:
        answer = _ask("API endpoint", default)
        if answer.startswith(("http://", "https://")):
            return answer
        print("An endpoint needs a scheme, e.g. https://api.example.com or "
              "http://localhost:11434.", file=sys.stderr)


def _ask_harnesses(available):
    """Ask which harnesses to wire up; blank takes all of them."""
    listed = " ".join(available)
    while True:
        chosen = _ask(f"Harnesses ({listed})", listed).replace(",", " ").split()
        unknown = [hid for hid in chosen if hid not in available]
        if not unknown:
            return chosen
        print(f"Not available: {', '.join(unknown)}. Choose from: {listed}.",
              file=sys.stderr)


def _interview(persona, harnesses, definition, export_path, config_path, known,
               updating, key_file=None):
    """Ask for a persona definition instead of requiring flags or a config file.

    The interview and the defining flags are two front ends to one operation: it
    collects exactly what ``--endpoint`` / ``--model`` / ``--desc`` /
    ``--no-token`` carry, does not ask about anything already given on the
    command line, and hands back an ordinary definition for :func:`load_config`.

    Returns ``(persona, definition, harnesses, save_path)``, or ``None`` if the
    summary was declined.
    """
    definition = copy.deepcopy(definition)
    print("\nDefining a persona. Press Enter to accept a [default].\n")

    if persona is None:
        persona = _ask_persona_name(known, updating)
    current = known.get(persona) or {}
    current_mind = current.get("mind") or {}

    if "persona_desc" not in definition:
        definition["persona_desc"] = _ask("Description",
                                          current.get("persona_desc") or persona)
    mind = _ensure_dict(definition, "mind")
    if "endpoint" not in mind:
        mind["endpoint"] = _ask_endpoint(current_mind.get("endpoint"))
    if "model" not in mind:
        mind["model"] = _ask("Model", current_mind.get("model") or "")

    # An existing persona defaults to whatever it already does; a new one is
    # assumed to need a key, since most endpoints do. --token settles it without
    # asking: a key was supplied, so one is plainly needed.
    if "token" not in definition:
        keyed = current.get("token") is not None if current else True
        wanted = True if key_file else _confirm("Does this endpoint need an API key?",
                                                default=keyed)
        if not wanted:
            definition["token"] = None
        elif not keyed:
            # Switched off and being switched back on. Take the location from
            # the schema rather than keeping a second copy of it here.
            schema = load_yaml(DATA_DIR / "persona.default.yaml", ENV_DEFAULTS) or {}
            definition["token"] = schema.get("token")

    # Export sets nothing up, so in that mode there is no harness to choose.
    if not harnesses and not export_path:
        harnesses = _ask_harnesses(list(current.get("harnesses") or preset_names("harness")))

    if definition.get("token", "") is None:
        key_row = "not needed"
    else:
        key_row = f"read from {key_file}" if key_file else "required"
    rows = [("persona", persona),
            ("description", definition["persona_desc"]),
            ("endpoint", mind["endpoint"]),
            ("model", mind["model"] or "(harness default)"),
            ("API key", key_row)]
    if not export_path:
        rows.append(("harnesses", ", ".join(harnesses)))
    print()
    for label, value in rows:
        print(f"  {label:<14}{value}")
    print()

    # A definition is otherwise ephemeral, so offer the same thing --export does.
    save_path = None
    if not export_path and _confirm("Save this persona to a config file?"):
        save_path = _ask("File", config_path or "agents.yaml")

    action = (f"Write '{persona}' to {export_path}?" if export_path else
              "Set up " + ", ".join(f"{persona}-{hid}" for hid in harnesses) + "?")
    if not _confirm(action, default=True):
        return None
    return persona, definition, harnesses, save_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _looks_like_config(arg):
    """A leading argument is the config file when it is named like one.

    Keyed on the extension rather than on the file existing, so that a stray
    file in the working directory cannot turn ``pg kimi`` into a request to
    load a config named "kimi".
    """
    return arg.endswith((".yaml", ".yml"))


def _extract_options(args):
    """Split argv into positional arguments and the options that precede them.

    Returns ``(positionals, removing, creating, updating, interactive, key_file,
    export_path, definition)``, where *definition* is a persona-shaped mapping of
    whatever the defining flags set -- empty when none were given. Both
    ``--model NAME`` and ``--model=NAME`` are accepted.
    """
    positionals, definition = [], {}
    removing = updating = creating = interactive = False
    key_file = export_path = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-r", "--remove"):
            removing = True
        elif arg in ("-i", "--interactive"):
            interactive = True
        elif arg == "--update":
            updating = True
        elif arg == "--create":
            creating = True
        elif arg == "--no-token":
            definition["token"] = None
        elif not arg.startswith("-"):
            positionals.append(arg)
        else:
            name, joined, inline = arg.partition("=")
            if name not in _FILE_FLAGS and name not in _VALUE_FLAGS:
                sys.exit(f"Error: unknown option '{name}'.\n\n{USAGE}")
            if joined:
                value = inline
            else:
                index += 1
                if index >= len(args):
                    sys.exit(f"Error: option '{name}' needs a value.\n\n{USAGE}")
                value = args[index]
            if name == "--export":
                export_path = value
            elif name == "--token":
                key_file = value
            else:
                node = definition
                *branches, leaf = _VALUE_FLAGS[name]
                for key in branches:
                    node = node.setdefault(key, {})
                node[leaf] = value
        index += 1
    return (positionals, removing, creating, updating, interactive,
            key_file, export_path, definition)


_PERSONAS_LINE = re.compile(r"^personas:[ \t]*$", re.M)


def _for_config_file(definition):
    """A flag definition as it should read *in* an agents.yaml.

    Ordered to match the schema rather than the order the flags happened to be
    typed in, and unset written as the schema's ``None`` placeholder rather than
    YAML's ``null`` -- both spellings load back to the same thing, but only one
    matches every other file in the project.
    """
    def placeholders(value):
        if isinstance(value, dict):
            return {k: placeholders(v) for k, v in value.items()}
        return "None" if value is None else value

    order = ("persona_desc", "path", "token", "mind", "harnesses")
    ranked = sorted(definition, key=lambda k: (order.index(k) if k in order else len(order), k))
    return {key: placeholders(definition[key]) for key in ranked}


def _authored_definition(persona_id, config_path, definition):
    """The persona as *authored*, for export: preset, then user file, then flags.

    Deliberately not the resolved tree, which is mostly derived values -- store
    paths, rendered harness content, every default filled in. What belongs in a
    template is the handful of settings someone would have written by hand, so
    an existing agent exports as something recognisably like its own preset.
    """
    schema = load_yaml(DATA_DIR / "persona.default.yaml", ENV_DEFAULTS) or {}
    authored = {}
    deep_merge(_load_preset("persona", persona_id, ENV_DEFAULTS, schema), authored)
    if config_path:
        declared = _normalize_personas(load_yaml(config_path, ENV_DEFAULTS) or {})["personas"]
        if isinstance(declared.get(persona_id), dict):
            deep_merge(declared[persona_id], authored)
    deep_merge(definition, authored)          # the flags are the last word
    return authored


def _export_definition(path, persona_id, definition):
    """Write a persona definition out as an editable config template.

    Edited as text rather than re-serialized, because PyYAML cannot round-trip
    comments and a hand-written agents.yaml is mostly comments. The block goes
    directly under ``personas:`` -- appending to the end of the file would land
    outside that mapping whenever it is not the last thing in the file.

    Declines rather than guesses in the two cases it cannot place safely: a
    persona the file already defines (merging would mean a full rewrite) and a
    ``personas`` written in flow or shorthand form (no line to insert after).
    """
    body = Path(path).read_text() if Path(path).exists() else ""
    existing = load_yaml(path) or {}
    block = yaml.safe_dump({persona_id: _for_config_file(definition)},
                           default_flow_style=False, sort_keys=False,
                           allow_unicode=True, width=te._NO_WRAP)
    indented = "".join("  " + line if line.strip() else line
                       for line in block.splitlines(keepends=True))

    if isinstance(existing, dict) and persona_id in (existing.get("personas") or {}):
        print(f" - '{persona_id}' is already defined in {path}; left as written.")
        return

    if not isinstance(existing, dict) or "personas" not in existing:
        prefix = "" if not body or body.endswith("\n") else "\n"
        Path(path).write_text(body + prefix + "personas:\n" + indented)
    else:
        match = _PERSONAS_LINE.search(body)
        if not match:
            print(f" - Cannot place '{persona_id}' in {path} automatically; its "
                  f"'personas' is not a block mapping. Add this by hand:\n\n{indented}")
            return
        cut = match.end() + 1
        Path(path).write_text(body[:cut] + indented + body[cut:])
    print(f" - Exported persona '{persona_id}' to {path}.")


def _check_name(persona, personas, defining, creating, updating):
    """Hold a persona name to the mode that named it.

    Creating and updating are separate intents, and each says which one it is:
    --update requires the persona to exist, its absence requires that it does
    not. Either way a name collision is reported rather than silently resolved.
    """
    if updating and persona is not None and persona not in personas:
        sys.exit(f"Error: persona '{persona}' does not exist; omit --update to "
                 f"create it.\n\n{USAGE}")
    if (defining or creating) and persona in personas and not updating:
        sys.exit(f"Error: persona '{persona}' already exists; pass --update to "
                 f"change it.\n\n{USAGE}")


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in args):
        print(USAGE)
        return 0

    (args, removing, creating, updating, interactive,
     key_file, export_path, definition) = _extract_options(args)

    config_path = args.pop(0) if args and _looks_like_config(args[0]) else None
    persona = args.pop(0) if args else None
    chosen_harnesses = args

    # The interview supplies both, so under -i their absence is not yet an error.
    if definition and persona is None and not interactive:
        sys.exit("Error: a persona name is required when defining one on the "
                 f"command line.\n\n{USAGE}")
    if export_path and persona is None and not interactive:
        sys.exit(f"Error: --export needs a persona to export.\n\n{USAGE}")
    modes = [name for name, chosen in (("--create", creating), ("--update", updating),
                                       ("--export", export_path is not None),
                                       ("--remove", removing)) if chosen]
    if len(modes) > 1:
        sys.exit(f"Error: {' and '.join(modes)} are separate modes; use one."
                 f"\n\n{USAGE}")
    if interactive and removing:
        sys.exit("Error: --interactive defines a persona; --remove needs only a "
                 f"name.\n\n{USAGE}")
    # `token` is in the definition only when --no-token put it there.
    if key_file and "token" in definition:
        sys.exit("Error: --token supplies a key and --no-token says there is "
                 f"none; use one.\n\n{USAGE}")
    if key_file and (export_path or removing):
        sys.exit("Error: --token supplies a key for setup, and neither --export "
                 f"nor --remove stores one.\n\n{USAGE}")

    # Read up front, so an unusable key file fails before anything is asked or
    # written rather than partway through a multi-harness run.
    key = _read_key(key_file) if key_file else None

    # Not a mode of its own: the interview fills in the definition the flags
    # would have carried, so everything downstream treats it as one of those.
    # It runs ahead of the mode checks so that a name it asks for is held to the
    # same rule -- and rejected while it can still be retyped.
    save_path = None
    if interactive:
        known = load_config(config_path).get("personas") or {}
        _check_name(persona, known, True, creating, updating)
        interviewed = _interview(persona, chosen_harnesses, definition,
                                 export_path, config_path, known, updating, key_file)
        if interviewed is None:
            print("Cancelled; nothing was set up.")
            return 0
        persona, definition, chosen_harnesses, save_path = interviewed

    # --export is the whole operation, not an extra step: it writes the config
    # out and sets nothing up, so none of the guards below apply -- nothing is
    # being created or replaced to guard against.
    if export_path:
        authored = _authored_definition(persona, config_path, definition)
        if not authored:
            sys.exit(f"Error: unknown persona '{persona}'; nothing to export.\n\n{USAGE}")
        _export_definition(export_path, persona, authored)
        return 0

    config = load_config(config_path)
    personas = config.get("personas") or {}
    _check_name(persona, personas, definition, creating, updating)

    if definition:
        config = load_config(config_path, overrides={"personas": {persona: definition}})
        personas = config.get("personas") or {}

    # A persona set up from flags alone exists in the store but in no config, so
    # removing it by name would not find it. Everything removal needs -- the
    # wrapper markers and the directories -- derives from the id, so rebuild it
    # from the defaults once the store confirms it is really there. Setup does
    # *not* get this treatment: there, an unknown name is a typo, not a target.
    if removing and persona is not None and persona not in personas:
        if (_path(config.get("persona_store") or ".") / persona).is_dir():
            config = load_config(config_path,
                                 overrides={"personas": {persona: {}}})
            personas = config.get("personas") or {}

    if persona is not None:
        if persona not in personas:
            sys.exit(f"Error: unknown persona '{persona}'. "
                     f"Available: {', '.join(sorted(personas)) or 'none'}")
        targets = [persona]
    elif config_path is None:
        # Naming neither a persona nor a config file expresses no intent, and
        # the shipped presets are a library to choose from rather than a set to
        # install wholesale -- most of them are endpoints the user has no
        # account on, or local servers they are not running.
        sys.exit("Error: no persona named, and no configuration file to take one "
                 f"from. Available: {', '.join(sorted(personas)) or 'none'}")
    else:
        # A config file *is* an expressed intent: set up everything it declares.
        targets = declared_personas(config_path) or list(personas)

    if not targets:
        sys.exit("Error: no personas to set up.\n\n" + USAGE)

    for pid in targets:
        available = list((personas.get(pid) or {}).get("harnesses") or {})
        selected = chosen_harnesses or available
        for hid in selected:
            if hid not in available:
                sys.exit(f"Error: harness '{hid}' is not configured for persona '{pid}'. "
                         f"Available: {', '.join(available) or 'none'}")
            if removing:
                remove_harness(pid, hid, config)
            else:
                setup_harness(pid, hid, config, key)

        # Once nothing is left wired up, the token is the only thing still on
        # disk -- ask, since deleting it means pasting the key again.
        if removing and set(selected) >= set(available):
            if _confirm(f"Also remove {pid}'s stored API token?"):
                remove_persona_store(pid, config)

    # The interview's offer to keep the definition, written only once the setup
    # it describes has actually succeeded. (--export never reaches here; it is a
    # mode of its own and returned above.)
    if save_path:
        _export_definition(save_path, persona,
                           _authored_definition(persona, config_path, definition))
    return 0


if __name__ == "__main__":
    sys.exit(main())

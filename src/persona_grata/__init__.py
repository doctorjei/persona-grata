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
import getpass
import urllib.request
import urllib.error
from pathlib import Path

import yaml
from . import template_engine as te

__version__ = "0.0.1"

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
  persona-grata <persona> [harness ...]
  persona-grata <agents.yaml> [persona] [harness ...]

Omit the harness names to set up every harness known to that persona; omit the
persona too to set up every persona in the configuration."""


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


def load_config(path=None, env_defaults=None):
    """Assemble and resolve the full configuration tree.

    Layered bottom-up, each stage overriding the last -- most specific wins::

        persona:  Global Default -> Persona Default -> Persona Preset -> User
        harness:  Harness Default -> Harness Preset
                                  -> Persona Preset's harnesses.<hid> -> User

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
    users_personas = user_cfg["personas"]

    # 2. Global defaults.
    base_cfg = load_yaml(DATA_DIR / "agents.default.yaml", env_defaults) or {}
    base_personas = _ensure_dict(base_cfg, "personas")

    persona_default = load_yaml(DATA_DIR / "persona.default.yaml", env_defaults) or {}
    harness_default = load_yaml(DATA_DIR / "harness.default.yaml", env_defaults) or {}
    known_harnesses = preset_names("harness")

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

    url = verify["url"]
    headers = DEFAULT_VERIFY_HEADERS + list(verify.get("headers") or [])
    body = verify.get("body") or DEFAULT_BODY % (model or "")
    req = urllib.request.Request(url, data=body.encode(), method="POST")

    for line in headers:
        _add_header(req, line)
    if verify.get("key_header"):
        _add_header(req, verify["key_header"], key=key)

    print("Verifying key... ", end="", flush=True)
    try:
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
def setup_harness(persona_id, harness_id, config):
    personas = _require(config, "personas", "section")
    persona = _require(personas, persona_id, "persona")
    harnesses = _require(persona, "harnesses", "section")
    harness = _require(harnesses, harness_id, "harness")

    persona_desc = persona.get("persona_desc") or persona_id
    endpoint = (persona.get("mind") or {}).get("endpoint")
    if not endpoint:
        sys.exit(f"Error: persona '{persona_id}' has no 'mind.endpoint' — it is required.")
    model = (persona.get("mind") or {}).get("model") or ""

    home = persona.get("path")
    if not home:
        sys.exit(f"Error: persona '{persona_id}' resolved an empty 'path'.")
    home = _path(home)
    token_path = _path(persona["token"]) if persona.get("token") else None

    harness_desc = harness.get("harness_desc") or harness_id
    config_dir = harness.get("path")
    if not config_dir:
        sys.exit(f"Error: harness '{harness_id}' resolved an empty 'path'.")
    config_dir = _path(config_dir)
    config_file = harness.get("config_file")
    content = harness.get("content")
    path_var = harness.get("path_var") or ""
    auth_var = harness.get("auth_var") or ""
    verify = harness.get("verify")

    print("\n===========================================================================")
    print(f"    {persona_desc} & {harness_desc} Setup Script")
    print("===========================================================================\n")

    # 1. Token: keep an existing one on request, else prompt + verify + write.
    #    Verification runs before anything is written, so a bad key writes nothing.
    #    A persona with no token path talks to an endpoint that needs no key.
    if token_path is None:
        print(" - No token path configured; skipping key setup.")
    elif token_path.exists() and not _confirm("Replace existing authorization token?"):
        print(f" - Keeping existing token at {token_path}.")
    else:
        key = _prompt_key(persona_desc)
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
    _install_shell_wrapper(persona_id, persona_desc,
      harness_id, harness_desc, config_dir, token_path, path_var, auth_var)


def _install_shell_wrapper(persona_id, persona_desc,
      harness_id, harness_desc, config_dir, token_path, path_var, auth_var):
    shell = os.environ.get("SHELL", "/bin/bash")
    rc_file = Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")
    cmd_name = f"{persona_id}-{harness_id}"

    # Only emit assignments the harness actually uses; an empty name would
    # otherwise become a bare `="..."` word and break the function.
    assignments = []
    if path_var:
        assignments.append(f'{path_var}="{config_dir}"')
    if auth_var and token_path is not None:
        assignments.append(f'{auth_var}="$(cat {token_path})"')
    env_lines = "".join(f"  {a} \\\n" for a in assignments)

    wrapper = f"""
# >>> persona-grata: {cmd_name} >>>
{cmd_name}() {{
{env_lines}  command {harness_id} "$@"
}}
# <<< persona-grata: {cmd_name} <<<
"""

    content = rc_file.read_text() if rc_file.exists() else ""
    # Strip any previous block for this agent: the current id-keyed marker, and
    # the legacy description-keyed one so renamed descriptions don't orphan it.
    for marker in (re.escape(f"persona-grata: {cmd_name}"),
                   rf"{re.escape(persona_desc)} & {re.escape(harness_desc)}"):
        content = re.sub(rf"# >>> {marker} >>>.*?# <<< {marker} <<<", "",
                         content, flags=re.DOTALL)
    content = content.strip()
    rc_file.write_text((content + "\n" if content else "") + wrapper)

    print("Setup complete.\n")
    print("Setup Notes")
    print("-----------")
    print(f"1. Before use, open a new terminal or run `source {rc_file}`.")
    print(f"2. Running {harness_id} still uses its native models (settings unchanged).\n")
    print(f"To run {persona_desc} with {harness_desc}")
    print("----------------------------------------------------------------------------")
    print(f"> {cmd_name}\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _looks_like_config(arg):
    """A leading argument is the config file if it names one (or looks like it)."""
    return arg.endswith((".yaml", ".yml")) or Path(arg).is_file()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(USAGE)
        return 0

    config_path = args.pop(0) if args and _looks_like_config(args[0]) else None
    persona = args.pop(0) if args else None
    chosen_harnesses = args

    config = load_config(config_path)
    personas = config.get("personas") or {}

    if persona is not None:
        if persona not in personas:
            sys.exit(f"Error: unknown persona '{persona}'. "
                     f"Available: {', '.join(sorted(personas)) or 'none'}")
        targets = [persona]
    else:
        targets = declared_personas(config_path) or list(personas)

    if not targets:
        sys.exit("Error: no personas to set up.\n\n" + USAGE)

    for pid in targets:
        available = list((personas.get(pid) or {}).get("harnesses") or {})
        for hid in chosen_harnesses or available:
            if hid not in available:
                sys.exit(f"Error: harness '{hid}' is not configured for persona '{pid}'. "
                         f"Available: {', '.join(available) or 'none'}")
            setup_harness(pid, hid, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())

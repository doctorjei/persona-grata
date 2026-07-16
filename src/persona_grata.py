"""Persona setup workflow: resolve agents.yaml, then wire up a harness.

All template resolution is delegated to :mod:`template_engine`; this module only
consumes the resolved values to obtain/verify the API token, create directories,
render the harness config file, and install a shell wrapper.
"""

import os
import re
import sys
import json
import getpass
import urllib.request
import urllib.error
from pathlib import Path

import template_engine as te

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

def load_config(path="agents.yaml", env_defaults=None):
    """Read + fully resolve an agents.yaml file into a plain dict tree."""
    raw = Path(path).read_text()
    return te.render(raw, ENV_DEFAULTS if env_defaults is None else env_defaults)


def _require(mapping, key, kind):
    if not isinstance(mapping, dict) or key not in mapping:
        print(f"Error: {kind} '{key}' not found in config.")
        sys.exit(1)
    return mapping[key]


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
    headers = DEFAULT_VERIFY_HEADERS + list(verify.get("headers", []))
    body = verify.get("body") or DEFAULT_BODY % model
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


def _write_token(token_path, key):
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(key)


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
def setup_harness(persona_id, harness_id, config):
    personas = _require(config, "personas", "section")
    persona = _require(personas, persona_id, "persona")
    harnesses = _require(persona, "harnesses", "section")
    harness = _require(harnesses, harness_id, "harness")

    persona_desc = persona.get("persona_desc", persona_id)
    model = persona.get("mind", {}).get("model", "")
    home = Path(persona["path"])
    token_path = Path(persona["mind"]["token_path"])

    harness_desc = harness.get("harness_desc", "harness")
    config_dir = Path(harness["path"])
    config_file = Path(harness["config_file"])
    config_env = harness.get("config_env", "")
    auth_env = harness.get("auth_env", "")
    template = harness["template"]
    verify = harness.get("verify")

    print("\n===========================================================================")
    print(f"    {persona_desc} & {harness_desc} Setup Script")
    print("===========================================================================\n")

    # 1. Token: keep an existing one on request, else prompt + verify + write.
    #    Verification runs before anything is written, so a bad key writes nothing.
    if token_path.exists() and not _confirm("Replace existing authorization token?"):
        print(f" - Keeping existing token at {token_path}.")
    else:
        key = _prompt_key(persona_desc)
        _verify_key(verify, model, key)
        _write_token(token_path, key)

    # 2. Directories
    home.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    # 3. Record the token location for downstream tooling (0600).
    secret_path = home / ".secret_path"
    with os.fdopen(os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(str(token_path) + "\n")

    # 4. Render config file (template is already fully resolved by the engine).
    config_file.write_text(template)

    # 5. Shell wrapper
    _install_shell_wrapper(persona_id, persona_desc,
      harness_id, harness_desc, config_dir, token_path, config_env, auth_env)

def _install_shell_wrapper(persona_id, persona_desc,
      harness_id, harness_desc, config_dir, token_path, config_env, auth_env):
    shell = os.environ.get("SHELL", "/bin/bash")
    rc_file = Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")
    cmd_name = f"{persona_id}-{harness_id}"

    wrapper = f"""
# >>> {persona_desc} & {harness_desc} >>>
{cmd_name}() {{
  {config_env}="{config_dir}" \\
  {auth_env}="$(cat {token_path})" \\
  command {harness_id} "$@"
}}
# <<< {persona_desc} & {harness_desc} <<<
"""

    content = rc_file.read_text() if rc_file.exists() else ""
    pattern = re.compile(
        rf"# >>> {re.escape(persona_desc)} & {re.escape(harness_desc)} >>>.*?"
        rf"# <<< {re.escape(persona_desc)} & {re.escape(harness_desc)} <<<",
        re.DOTALL,
    )
    content = pattern.sub("", content).strip()
    rc_file.write_text(content + "\n\n" + wrapper)

    print("Setup complete.\n")
    print("Setup Notes")
    print("-----------")
    print(f"1. Before use, open a new terminal or run `source {rc_file}`.")
    print(f"2. Running {harness_id} still uses its native models (settings unchanged).\n")
    print(f"To run {persona_desc} with {harness_desc}")
    print("----------------------------------------------------------------------------")
    print(f"> {persona_id}-{harness_id}\n")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python persona_grata.py <schema-yaml> <persona> <harness>")
        sys.exit(1)
    setup_harness(sys.argv[2], sys.argv[3], load_config(sys.argv[1]))

"""Persona setup workflow: resolve agents.yaml, then wire up a harness.

All template resolution is delegated to :mod:`template_engine`; this module only
consumes the resolved values to create directories, store the API token, render
the harness config file, and install a shell wrapper.
"""

import os
import re
import sys
import getpass
from pathlib import Path

import template_engine as te

# Default values for selected environment variables (rule 1e is "empty string";
# these are the caller-supplied defaults the engine falls back to when a var is
# absent from the real environment).
ENV_DEFAULTS = {
    "XDG_CONFIG_HOME": str(Path.home() / ".config"),
    "HOME": str(Path.home()),
}


def load_config(path="agents.yaml", env_defaults=None):
    """Read + fully resolve an agents.yaml file into a plain dict tree."""
    raw = Path(path).read_text()
    return te.render(raw, ENV_DEFAULTS if env_defaults is None else env_defaults)


def _require(mapping, key, kind):
    if not isinstance(mapping, dict) or key not in mapping:
        print(f"Error: {kind} '{key}' not found in config.")
        sys.exit(1)
    return mapping[key]


def setup_harness(persona_name, harness_name, config):
    personas = _require(config, "personas", "section")
    persona = _require(personas, persona_name, "Persona")
    harnesses = _require(persona, "harnesses", "section")
    harness = _require(harnesses, harness_name, "Harness")

    home = Path(persona["path"])
    token_path = Path(persona["mind"]["token_path"])
    config_dir = Path(harness["path"])
    config_file = Path(harness["config_file"])
    config_env = harness.get("config_env", "")
    auth_env = harness.get("auth_env", "")
    template = harness["template"]

    # 1. Directories
    home.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    # 2. Record the token location for downstream tooling (0600).
    secret_path = home / ".secret_path"
    with os.fdopen(os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(str(token_path) + "\n")

    # 3. Token (prompt once, store 0600; reuse if present).
    if token_path.exists():
        print(f"Using existing token at {token_path}")
    else:
        key = getpass.getpass(f"Enter API key for {persona_name}: ")
        with os.fdopen(os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            f.write(key)

    # 4. Render config file (template is already fully resolved by the engine).
    config_file.write_text(template)

    # 5. Shell wrapper
    _install_shell_wrapper(persona_name, harness_name, config_dir, token_path,
                           config_env, auth_env)


def _install_shell_wrapper(persona_name, harness_name, config_dir, token_path,
                           config_env, auth_env):
    shell = os.environ.get("SHELL", "/bin/bash")
    rc_file = Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")
    cmd_name = f"{persona_name}-{harness_name}"

    wrapper = f"""
# >>> {persona_name} & {harness_name} >>>
{cmd_name}() {{
  {config_env}="{config_dir}" \\
  {auth_env}="$(cat {token_path})" \\
  command {harness_name} "$@"
}}
# <<< {persona_name} & {harness_name} <<<
"""

    content = rc_file.read_text() if rc_file.exists() else ""
    pattern = re.compile(
        rf"# >>> {re.escape(persona_name)} & {re.escape(harness_name)} >>>.*?"
        rf"# <<< {re.escape(persona_name)} & {re.escape(harness_name)} <<<",
        re.DOTALL,
    )
    content = pattern.sub("", content).strip()
    rc_file.write_text(content + "\n\n" + wrapper)

    print(f"Success! Run 'source {rc_file}' and then use the command: {cmd_name}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python persona_grata.py <persona> <harness>")
        sys.exit(1)
    setup_harness(sys.argv[1], sys.argv[2], load_config())

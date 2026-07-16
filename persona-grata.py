import os
import sys
import subprocess
import getpass
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install it with 'pip install pyyaml'.")
    sys.exit(1)

# Known defaults for common environment variables
ENV_DEFAULTS = {
    "XDG_CONFIG_HOME": str(Path.home() / ".config"),
    "HOME": str(Path.home()),
}

def load_config(path="agents.yaml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def resolve_env_vars_fixed(text):
    if not isinstance(text, str):
        return text

    def replace_match(match):
        # match.group(1) is the variable name for $VAR
        # match.group(2) is the variable name for ${VAR}
        var_name = match.group(1) or match.group(2)

        if not var_name:
            return match.group(0)

        val = os.environ.get(var_name)
        if val is not None:
            return val

        if var_name in ENV_DEFAULTS:
            val = ENV_DEFAULTS[var_name]
            os.environ[var_name] = val
            return val

        print(f"Warning: Environment variable '${var_name}' is not defined and has no default. Using empty string.")
        return ""

    # Use a raw string for the regex.
    # The pattern matches:
    # 1. A literal dollar sign followed by a valid identifier: $([a-zA-Z_][a-zA-Z0-9_]*)
    # 2. OR a literal dollar sign and curly braces: $\{([a-zA-Z_][a-zA-Z0-9_]*)\}
    return re.sub(r"\$([a-zA-Z_][a-zA-Z0-9_]*)|\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}", replace_match, text)

def resolve_template(template, context):
    """
    Recursively resolve {{variable}} placeholders in a string using the provided context.
    """
    if not isinstance(template, str):
        return template

    prev_template = None
    while prev_template != template:
        prev_template = template
        matches = re.findall(r"\{\{(.*?)\}\}", template)
        for var in matches:
            if var in context:
                template = template.replace(f"{{{{{var}}}}}", str(context[var]))
    return template

def setup_harness(persona_name, harness_name, config):
    if persona_name not in config['personas']:
        print(f"Error: Persona '{persona_name}' not found in config.")
        sys.exit(1)

    persona_cfg = config['personas'][persona_name]
    if harness_name not in persona_cfg['harnesses']:
        print(f"Error: Harness '{harness_name}' not supported for persona '{persona_name}'.")
        sys.exit(1)

    # Resolve global store
    raw_store = config.get('personal_store', "/personas")
    resolved_store = resolve_env_vars_fixed(resolve_template(raw_store, ctx))
    ctx = {"persona_store": resolved_store}

    if not resolved_store or resolved_store.strip() == "":
        print("Error: Resolved persona_store path is empty. Check your environment variables.")
        sys.exit(1)

    # Persona-level context
    pid = persona_cfg.get('pid', persona_name)
    ctx["pid"] = pid
    ctx["persona_name"] = persona_cfg.get('name', persona_name)
    ctx["name"] = ctx["persona_name"]

    # Resolve Persona Home
    raw_home = persona_cfg.get('home', "{{persona_store}}/{{pid}}")
    ctx["home"] = resolve_env_vars_fixed(resolve_template(raw_home, ctx))

    if not ctx["home"]:
        print("Error: Resolved persona home path is empty.")
        sys.exit(1)

    # Resolve Token Path
    mind = persona_cfg.get('mind', {})
    raw_token_path = mind.get('token_path', "{{home}}/token")
    ctx["token_path"] = resolve_env_vars_fixed(resolve_template(raw_token_path, ctx))

    if not ctx["token_path"]:
        print("Error: Resolved token path is empty.")
        sys.exit(1)

    # Harness-level context
    harness_cfg = persona_cfg['harnesses'][harness_name]
    hid = harness_cfg.get('hid', harness_name)
    ctx["hid"] = hid

    # Resolve Harness Config Path and File
    raw_config_path = harness_cfg.get('config_path', "{{home}}/{{hid}}")
    ctx["config_path"] = resolve_env_vars_fixed(resolve_template(raw_config_path, ctx))

    if not ctx["config_path"]:
        print("Error: Resolved config path is empty.")
        sys.exit(1)

    raw_config_file = harness_cfg.get('config_file', "{{config_path}}/config.toml")
    ctx["config_file"] = resolve_env_vars_fixed(resolve_template(raw_config_file, ctx))

    if not ctx["config_file"]:
        print("Error: Resolved config file path is empty.")
        sys.exit(1)

    # 1. Setup Directories
    token_file = Path(ctx["token_path"])
    token_file.parent.mkdir(parents=True, exist_ok=True)

    cfg_dir = Path(ctx["config_path"])
    cfg_dir.mkdir(parents=True, exist_ok=True)

    # Create .secret_path file in persona home
    home_dir = Path(ctx["home"])
    home_dir.mkdir(parents=True, exist_ok=True)
    secret_path = home_dir / ".secret_path"
    with os.fdopen(os.open(secret_path, os.O_WRONLY | os.O_CREAT, 0o600), 'w') as f:
        f.write(ctx["token_path"] + "\n")

    # 2. Securely Handle Token
    if not token_file.exists():
        key = getpass.getpass(f"Enter API key for {ctx['persona_name']}: ")
        with os.fdopen(os.open(token_file, os.O_WRONLY | os.O_CREAT, 0o600), 'w') as f:
            f.write(key)
    else:
        print(f"Using existing token at {token_file}")

    # 3. Render and Write Config
    ctx["endpoint"] = mind.get('endpoint', '')
    ctx["model"] = mind.get('model', '')
    ctx["auth_env"] = harness_cfg.get('auth_env', '')

    template = harness_cfg['template']
    rendered = resolve_template(template, ctx)

    with open(ctx["config_file"], 'w') as f:
        f.write(rendered)

    # 4. Shell Integration
    setup_shell_wrapper(persona_name, harness_name, harness_cfg, cfg_dir, token_file)

def setup_shell_wrapper(persona_name, harness_name, harness_cfg, cfg_dir, token_file):
    shell = os.environ.get("SHELL", "/bin/bash")
    rc_file = Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")

    cmd_name = f"{persona_name}-{harness_name}"

    config_env = harness_cfg.get('config_env', '')
    auth_env = harness_cfg.get('auth_env', '')

    wrapper = f"""
# >>> {persona_name} & {harness_name} >>>
{cmd_name}() {{
  {config_env}="{cfg_dir}" \
  {auth_env}="$(cat {token_file})" \
  command {harness_name} "$@"
}}
# <<< {persona_name} & {harness_name} <<<
"""

    content = rc_file.read_text() if rc_file.exists() else ""

    pattern = re.compile(rf"# >>> {persona_name} & {harness_name} >>>.*?# <<< {persona_name} & {harness_name} <<<", re.DOTALL)
    new_content = pattern.sub("", content)
    new_content = new_content.strip() + "\n\n" + wrapper

    with open(rc_file, 'w') as f:
        f.write(new_content)

    print(f"Success! Setup complete. Run 'source {rc_file}' and then use the command: {cmd_name}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python persona-grata.py <persona> <harness>")
        sys.exit(1)

    p_name = sys.argv[1]
    h_name = sys.argv[2]
    cfg = load_config()
    setup_harness(p_name, h_name, cfg)

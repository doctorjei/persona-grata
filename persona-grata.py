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

def load_config(path="agents.yaml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def resolve_template(template, context):
    """
    Recursively resolve {{variable}} placeholders in a string using the provided context.
    """
    if not isinstance(template, str):
        return template
    
    # Use a loop to handle nested templates (e.g., {{home}} where home is {{persona_store}}/{{pid}})
    prev_template = None
    while prev_template != template:
        prev_template = template
        # Find all matches of {{variable}}
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
    
    # Context for template resolution
    # Initial context starts with global and persona-level IDs
    ctx = {
        "XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")),
    }
    
    # Resolve global store
    raw_store = config.get('personal_store', "$XDG_CONFIG_HOME/personas")
    ctx["persona_store"] = os.path.expandvars(resolve_template(raw_store, ctx))
    
    # Persona-level context
    pid = persona_cfg.get('pid', persona_name)
    ctx["pid"] = pid
    ctx["persona_name"] = persona_cfg.get('name', persona_name)
    ctx["name"] = ctx["persona_name"] # Alias for template consistency
    
    # Resolve Persona Home
    raw_home = persona_cfg.get('home', "{{persona_store}}/{{pid}}")
    ctx["home"] = resolve_template(raw_home, ctx)
    
    # Resolve Token Path
    mind = persona_cfg.get('mind', {})
    raw_token_path = mind.get('token_path', "{{home}}/token")
    ctx["token_path"] = resolve_template(raw_token_path, ctx)
    
    # Harness-level context
    harness_cfg = persona_cfg['harnesses'][harness_name]
    hid = harness_cfg.get('hid', harness_name)
    ctx["hid"] = hid
    
    # Resolve Harness Config Path and File
    raw_config_path = harness_cfg.get('config_path', "{{home}}/{{hid}}")
    ctx["config_path"] = resolve_template(raw_config_path, ctx)
    
    raw_config_file = harness_cfg.get('config_file', "{{config_path}}/config.toml")
    ctx["config_file"] = resolve_template(raw_config_file, ctx)
    
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
    # Add a few more variables to context for the template itself
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
  {config_env}="{cfg_dir}" \\
  {auth_env}="\$(cat {token_file})" \\
  command {harness_name} "\$@"
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

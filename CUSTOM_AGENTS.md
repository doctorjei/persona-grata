# Persona-Grata: Custom Agent Guide

Persona-Grata combines one persona and one harness into a named agent. Variants are yapyon
configuration documents, not endpoint/model command-line flags. Start with an agent preset or a
`.cfg` / `.tmpl` file, then create the agent explicitly.

## A Custom Agent

Save this example as `<working-directory>/orion.cfg`:

```yapyon
agent<base.agent>:
  name: "orion"
  desc: "Orion Toolkit"
  secret: None
  persona<base.persona>:
    name: "orion"
    mind:
      endpoint: "https://api.cybertron.space"
      model_list: ["alpha-3-on"]
      model_active: "alpha-3-on"
  harness<claude.harness>: {}
```

```bash
pg --create orion.cfg
```

`secret: None` makes this example keyless; it does not prove the example endpoint accepts
unauthenticated requests. To use a real token, configure a manager with `pg --setup`, remove
`secret: None`, and supply the token when requested or use `--token FILE`.

[agents-example.yaml](agents-example.yaml) is another complete single-agent document. Its retained
filename does not change the grammar: it is yapyon, not YAML. Copy it to a `.cfg` or `.tmpl` file
for use with the CLI.

## Presets & Overrides

A named slot selects a preset; its contents override that preset. For example, a second agent can
use the same persona and harness with a different model:

```yapyon
agent<base.agent>:
  name: "local_review"
  secret: None
  persona<ollama.persona>:
    mind:
      model_list: ["review-model"]
      model_active: "review-model"
  harness<codex.harness>: {}
```

```bash
pg --create local_review.cfg --path ~/agents/review
```

Names and directories are independent. `--name` can override the document's name at creation, and
`pg --update local_review --name reviewer` renames it without moving the directory.
A registered name is the selector for later operations; a persona/harness pair does not uniquely
identify an agent.

The store saves composed components only when used. It does not install a second copy of the whole
preset catalog. Installed `<agent-directory>/agent.cfg` uses the same slot format as these examples, and stored
components allow reloading without the original inheritance files. Explicit source catalogs are a
Python composition API choice; the CLI does not implicitly search the current directory for
replacement component presets.

## Protocol Mounts

A persona declares where it serves each dialect; the harness lists which dialects it speaks,
best first. A shared dialect is required. Disable an unsupported dialect with `None`:

```yapyon
agent<base.agent>:
  name: "pax"
  secret: None
  persona<base.persona>:
    name: "pax"
    desc: "Pax Toolkit"
    mind:
      endpoint: "https://api.moonbase.space"
      model_list: ["pax-model"]
      model_active: "pax-model"
      dialects:
        anthropic<anthropic.dialect>:
          api_uri: y"{endpoint}/anthropic_api"
          verify:
            check_uri: y"{api_uri}/verify_me"
            key_header: "x-api-key:"
        responses: None
  harness<claude.harness>: {}
```

These mounts are declarations, not endpoint discovery. For keyed agents, optional `--verify`
requests a live credential check, potentially consuming provider tokens. Offline composition/rendering
tests do not establish compatibility with a live server or installed harness.

## Custom Harness Settings

Override a bundled harness's `config_store` inside its slot. To define a different harness, use
`harness<base.harness>` and supply its executable name and supported dialects. Put native settings
in `config_store`, then serialize them into `content`:

```yapyon
agent<base.agent>:
  name: "orion_apex"
  secret: None
  persona<base.persona>:
    name: "orion"
    mind:
      endpoint: "https://api.cybertron.space"
      model_list: ["alpha-3-on"]
      model_active: "alpha-3-on"
  harness<base.harness>:
    name: "apex"
    supported_dialects: ["chat"]
    path_var: "APEX_HOME"
    config_file: y"{path}/config.toml"
    config_store:
      model: (persona.mind.model_active)
      base_url: y"{persona.mind.dialects[protocol].api_uri}"
    content: y"{PRUNE_CONFIG(config_store).__as_toml__()}"
```

The `apex` executable in this example is hypothetical; install and validate your own harness.
Generated files must remain within `<agent-directory>/harness/`. For an environment-driven
harness, leave `config_file` / `content` unset and set `wrapper_env` instead. `auth_var` names the
variable that receives the manager-fetched key; do not put a real token into `config_store` or
`wrapper_env`.

## References & Whole Values

Yapyon strings are quoted. A y-string interpolates text, while a parenthesized reference copies
a value without converting lists, mappings, or `None` to text:

```yapyon
label: "Costs $5 per run"
config_dir: y"{$HOME}/agents"
model: y"{persona.mind.model_active}"
models: (persona.mind.model_list)
```

References use the surrounding scope; use full paths when crossing components. `{$NAME}` reads an
environment variable only if permitted by the store's `env_allow` / `env_add` policy. A plain
`"$HOME/agents"` is literal text, not a path expansion. Write `{{` or `}}` for literal braces
inside a y-string.

## Credentials, Updates, & Export

`agent.secret: None` is explicitly keyless. A secret mapping may set `entry` instead of the agent
directory's basename; the store's `secret_root` prefix applies to either. The store chooses one manager
(`pass`, `gopass`, or `prs`). It fetches credentials at launch and never creates a plaintext token
backend or `.secret_path` pointer. Keep the manager usable without an unattended passphrase prompt.

```bash
pg orion                          # Show the saved document; no changes
pg --update orion revised.cfg     # Replace its definition and reinstall
pg --export orion-copy.cfg orion  # Export an existing registered agent
pg --remove orion                 # Confirm removal; manager entries are retained
```

Removal deletes the entire registered agent directory, including any files you added, while
retaining shared components and password-manager entries.

Export produces a reusable partial agent document, not a resolved harness file. `--force` allows
replacing an export destination, but never the installed `agent.cfg` itself. Saved documents use
normalized yapyon: comments and original layout are not retained, and unsafe deferred merges are
refused. Keep hand-authored source files separately if their formatting matters.

Runtime owns `runtime.agent_path` and `runtime.secret_backend`; do not supply them in agent
files. `agent.path` is disallowed. Use `--path` at creation and the name-to-path registry afterward.
There are no legacy schema shims; former persona-matrix files must be rewritten.

See the [Config Reference Manual](CONFIG_REFERENCE.md) for the complete field list and store layout.

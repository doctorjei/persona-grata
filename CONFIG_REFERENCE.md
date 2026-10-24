# Configuration Reference Manual

Persona-Grata composes one agent from a persona, a harness, their dialects, and an optional
password-manager configuration. Configuration is [yapyon](https://pypi.org/project/yapyon/),
requiring yapyon 0.5.2 or newer; `.cfg` and `.tmpl` describe the same document format.

## Agent Store

The store root is an explicit `--store DIR`, otherwise `AGENT_STORE_DIR`, otherwise
`$XDG_CONFIG_HOME/agents`, otherwise `~/.config/agents`. Names are registry keys, not directory
names: renaming an agent does not move its files.

```text
~/.config/agents/
  agent_store.cfg
  personas/<id>.persona.tmpl
  harnesses/<id>.harness.tmpl
  dialects/<id>.dialect.tmpl
  secrets/<id>.secret.tmpl
  store/<creation-name>/
    agent.cfg
    harness/
```

`--path DIR` can place an agent elsewhere. Registry paths are absolute. There are no name symlinks,
no `__templates__` directory, and no copied library of every bundled preset. Component files are
composed, in-use definitions: missing bundled components are materialized when needed, while
existing stored definitions are reused rather than overwritten by unrelated agent creation.

### Store Settings (`<agent-store>/agent_store.cfg`)

This root mapping is separate from an agent document:

```yapyon
format: "agents-yapyon-v0.1"
secret_backend: None
secret_root: "persona-grata"
env_add: ["MY_ENDPOINT"]
agents:
  scout:
    path: "/home/you/.config/agents/store/scout"
```

| Setting | Type | Meaning |
|---------|------|---------|
| `format` | `str` | `"agents-yapyon-v0.1"`; identifies the registry schema |
| `secret_backend` | `str` or `None` | Store-wide manager: `"pass"`, `"gopass"`, or `"prs"`; `None` means none configured |
| `secret_root` | `str` | Prefix for manager entries, including explicit entries; default `""` |
| `env_allow` | `list[str]` | Replaces yapyon's standard environment allowlist when supplied |
| `env_add` | `list[str]` | Adds readable variables; default `[]` |
| `agents` | mapping | Agent name to `{path: "/absolute/directory"}` |

The standard environment allowlist includes `HOME`, `PATH`, `LANG`, and the `XDG_*` directories.
`HOME`, `XDG_CONFIG_HOME`, and `AGENT_STORE_DIR` remain readable for store location regardless of
these lists. Store-only environment policy cannot be overridden by an agent document. Environment values are
available only through permitted yapyon environment references; they are not implicitly expanded
inside plain strings.

## Agent Documents

An installed `<agent-directory>/agent.cfg` has the same partial slot format as an agent preset.
It does not flatten the entire component store or include invocation-owned runtime settings.

```yapyon
agent<base.agent>:
  name: "local_code"
  secret: None
  persona<ollama.persona>:
    mind:
      model_list: ["local-model"]
      model_active: "local-model"
  harness<codex.harness>: {}
```

Names of agents and component IDs use 1–64 ASCII letters, digits, and `_`, and do not begin with a
digit; `default` is reserved. A persona/harness pair is not a unique agent identifier. Default creation names are
`<persona>_<harness>`; `--name` chooses a different name.

### `agent.format`

String, `"agents-yapyon-v0.1"`, supplied by `base.agent`.

### `agent.name`

The registered agent's command name. Renaming changes this name and its wrapper, not its path.

### `agent.desc`

Short description; defaults to `y"{name}"`.

### `agent.persona`

Required persona slot, normally named as `persona<kimi.persona>`. Overrides within the slot are
applied to the selected persona. See [Persona Fields](#persona-fields).

### `agent.harness`

Required harness slot, normally named as `harness<claude.harness>`. One agent has one harness.
See [Harness Fields](#harness-fields).

### `agent.secret`

`None` explicitly means keyless. A mapping requests manager-backed credentials; its optional
`entry` replaces the default agent-directory basename. The store's `secret_root` prefix applies
to either choice (for example, prefix `"persona-grata"` plus entry `"work/scout"` yields
`"persona-grata/work/scout"`):

```yapyon
agent<base.agent>:
  name: "scout"
  secret:
    entry: "work/scout"
  persona<kimi.persona>: {}
  harness<claude.harness>: {}
```

The password manager is selected once for the store, not independently per agent. At creation,
omitting `agent.secret` saves `{}` if a manager is configured, otherwise saves `None` and setup
reports keyless operation. Enabling a manager later does not silently credential an existing keyless
agent. An explicit secret mapping without a configured backend is an error. Use `--no-token` or
`secret: None` when no key should be fetched or stored.

### Runtime-Owned Settings

`runtime.agent_path` and `runtime.secret_backend` are filled by the invocation. Do not put them in
agent documents or presets. `agent.path` is not a supported field; use `--path` / the registry.
Harness paths can refer to `runtime.agent_path` without storing an installation-specific location
in an exported agent document.

## Persona Fields

A component document has a `persona` branch. It may inherit a preset using
`persona<base.persona>`; inside an agent it occupies `agent.persona`.

### `persona.name`

Persona identifier; default `"base_persona"` in the abstract base.

### `persona.desc`

Short description; defaults to `y"{name}"`.

### `persona.mind`

Cognitive configuration mapping.

#### `persona.mind.endpoint`

Required nonempty connection endpoint, such as `"http://127.0.0.1:11434"`. The base leaves it `None`.

#### `persona.mind.context_size`

Optional context-window size, as an integer; default `None`.

#### `persona.mind.model_list`

Ordered list of model IDs; default `[]`. Harness presets may use this to offer model choices.

#### `persona.mind.model_active`

Active model ID; default `None`. An empty list does not invent a model the server supports.

#### `persona.mind.model_subagent`

Optional subagent model ID; default `None`.

#### `persona.mind.dialects`

Mapping of dialect names to dialect slots. The base supplies `chat`, `responses`, and `anthropic`;
this is configuration, not server discovery. Set an unsupported dialect to `None`, or override
its mount point:

```yapyon
agent<base.agent>:
  name: "mounted_api"
  secret: None
  persona<base.persona>:
    name: "mounted_api"
    mind:
      endpoint: "https://api.example.org"
      dialects:
        anthropic<anthropic.dialect>:
          api_uri: y"{endpoint}/anthropic"
        responses: None
  harness<claude.harness>: {}
```

## Harness Fields

A component document has a `harness` branch; inside an agent it occupies `agent.harness`.
Harness-native configuration under `config_store` is free-form data, not extra persona settings.

### `harness.name`

Harness executable / identifier; the base uses `"base_harness"`.

### `harness.desc`

Short description; defaults to `y"{name}"`.

### `harness.path`

Harness configuration directory; defaults to `y"{runtime.agent_path}/harness"`.

### `harness.auth_var`

Environment variable receiving the fetched token; base default `"API_KEY"`. Keyless setup clears
references to an unavailable key. A harness that requires a placeholder may declare one in its
preset; it must not replace a real manager-backed token.

### `harness.path_var`

Environment variable pointing to the harness configuration directory; default `""`.

### `harness.wrapper_env`

Mapping of additional environment variables exported by the shell wrapper; default `{}`.

### `harness.config_file`

Main harness settings filename. Empty/unset means no file is written.

### `harness.config_store`

Native configuration mapping, serialized to `content`; default `{}`. For example, the Codex preset
uses `GEN_CODEX_MODEL_TABLE(persona.name, persona.mind.model_list)` to generate its model table.

### `harness.content`

Settings file text, usually built from `config_store` with a serializer; default `""`.

### `harness.supported_dialects`

Required nonempty ordered list, best first. Setup refuses a persona/harness pair with no shared
dialect rather than claiming it is usable.

### `harness.protocol`

Selected dialect name, normally `MATCH_FIRST(supported_dialects, persona.mind.dialects)`.
The chosen dialect supplies both API mount points and verification settings.

## Dialect Fields

A component document has a `dialect` branch; its values are filled into
`agent.persona.mind.dialects.<name>`.

### `dialect.name`

Dialect identifier; the base uses `"base_dialect"`.

### `dialect.api_uri`

Protocol mount point; defaults to `y"{endpoint}"`.

### `dialect.msg_uri`

Completion/message URL. Defaults to `y"{api_uri}/v1/chat/completions"`; the `anthropic` and
`responses` presets select their respective operations.

### `dialect.model_uri`

Model-listing URL; defaults to `y"{api_uri}/v1/models"`.

### `dialect.emb_uri`

Embeddings URL; defaults to `y"{api_uri}/v1/embeddings"`, unset by dialects that do not supply it.

### `dialect.verify`

Optional credential-verification settings for keyed agents. Verification is requested with
`--verify`, not performed implicitly for every setup; requests may incur provider charges.
Warnings can indicate a skipped check, so a successful setup exit is not proof of endpoint or
harness compatibility.

#### `dialect.verify.check_uri`

Verification URL; defaults to `y"{msg_uri}"`.

#### `dialect.verify.key_header`

Authentication-header prefix; default `"Authorization: Bearer"`.

#### `dialect.verify.headers`

Additional header lines; default `[]`.

#### `dialect.verify.body`

Optional request body. Empty means use the installer's minimal probe with the active model.

## Secret Components & Installation

The store selects `pass`, `gopass`, or `prs`. Its composed backend is supplied through
`runtime.secret_backend`; `agent.secret.entry` selects the key within it. Persona-Grata invokes
the password manager, not `gpg` directly, and does not create a plaintext token backend or
`.secret_path` pointer.

### `secret.name`

Backend identifier.

### `secret.entry`

Manager entry name. An explicit `agent.secret.entry` overrides the generated default.

### `secret.fetch`

Command printing the key to standard output, used at setup and by the wrapper at launch.
The manager must be ready for unattended reads; a passphrase prompt can block a launch.

### `secret.store`

Command reading the key from standard input, used when importing a token during setup.
`--token FILE` reads a supplied token into the manager; it does not make that file the backend.

### `secret.probe`

Executable required by the backend. Install and initialize your chosen manager separately, or use
`pg --setup` for supported setup assistance. Removing an agent does not delete manager entries.

## Yapyon Composition & Persistence

- A named slot, `persona<kimi.persona>: {}`, fills a component from a preset. Overrides belong
  inside the slot. Abstract slots such as `persona<>: {}` must be filled before resolution.
- Strings are quoted. `None`, `True`, and `False` are values, not YAML's `null`, `true`, or `false`.
- `y"{reference}"` produces text; `(reference)` copies an entire value, retaining its type.
- `y"{$HOME}/agents"` reads an allowed environment variable; `"$HOME/agents"` is literal text.
- References resolve from their surrounding scope. Use full paths such as `persona.mind.endpoint`
  when crossing component boundaries.
- Harness templates call `PRUNE_CONFIG(config_store)` before a native serializer such as
  `__as_json__()`, `__as_toml__()`, `__as_yaml__()`, or `__as_yapyon__()`. For example:
  `y"{PRUNE_CONFIG(config_store).__as_json__()}"`. Pruning omits unset/empty values while retaining
  `0` and `False`; native serializers alone do not supply this persona-grata policy.

Composed documents are saved through yapyon's `Tree.to_source()` without resolving remaining slots
or expressions. This normalizes comments and layout. A deferred merge that cannot be represented
safely is refused, before an existing output is replaced. The installed agent document remains
partial; reloading uses the store's composed components rather than relying on original base files.

## Migration Boundary

This agent-first format does not read the former persona matrix,
`<old-persona-store>/persona_store.cfg`, or `<old-persona-store>/agent_names.cfg` as a compatibility schema. Recreate agents as single-agent slot documents and
register them by name. `PERSONA_STORE_DIR`, persona-level token paths, `.secret_path`, old designation
selectors, and endpoint/model override flags are not the new store API. Keep old data until you have
confirmed the new agents work; no automatic migration or live harness validation is implied.

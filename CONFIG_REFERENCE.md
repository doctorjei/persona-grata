# Configuration Reference Manual

Persona-Grata general, persona, and harness settings structure and default values can be found in
this manual. This document uses the following definitions throughout:

`type NestedDict = dict[str, NestedDict | list[str] | str]`

If a value is required, it _must be supplied_ in any custom configuration. Otherwise, a default
fallback value will be substituted for any absent keys in the configuration.

Two conventions apply throughout:

- **`None` means "unset".** Any setting's value may be cleared by assigning `None`.
- **Unset values are not emitted.** Keys with empty / unset values are omitted from config files.

## Primary Elements

Agents are configured via the definitions of three (3) primary elements:

- **Mind**: The `endpoint` and `model` that provide the cognitive function.
- **Persona**: Mind plus additional configuration variables / configuration (paths, context, etc.)
- **Harnesses**: Settings embodying persona in harness (e.g., via templates, environment variables)

Additionally, the communication must be in some **dialect**, which follows some **protocol**; the
dialects determine which **personas** and **harnesses** can be connected / matched.
---

## Top-Level Configuration

### `persona_store` -> `str`
- Required: **No**
- Default: `"$PERSONA_STORE_DIR"`, else `"$XDG_CONFIG_HOME/personas"`
  (`~/.config/personas` if `$XDG_CONFIG_HOME` unset/empty)

Persona config path (endpoint, harnesses, etc.)

### `personas` -> `NestedDict`
- Required: **Yes**

Maps persona names (keys) to settings (values): `{(<persona>: <settings>)*}`. May be reference(s):

```yapyon
personas: "kimi"                # one preset
```

```yapyon
personas: ["kimi", "minimax"]   # several
```

Such personas, loaded as presets, can be referenced from templates (e.g., `y"{personas.kimi.mind.model}"`).

### `secret_backend` -> `str`
- Required: **No**
- Default: `{}` (no backend)

Which password manager holds every persona's API key — `pass`, `gopass`, or `prs`. One store uses
one manager, so this is normally set once in `<persona_store>/persona_store.cfg` rather than in an
agents file. See [Secret Backend Configuration](#secret-backend-configuration-per-persona).

---

## Store Settings (`<persona_store>/persona_store.cfg`)

The store keeps settings that hold for every persona in it in `<persona_store>/persona_store.cfg`,
a yapyon file beside the persona directories. Its settings sit between what persona-grata ships
and your own agents file: they override the shipped presets, and your file overrides them. That
holds for `personas` entries too, so a store-wide tweak to a shipped persona takes effect.

Besides `secret_backend` and `personas`, two settings are **store-only**. An agents file that sets
either is refused. Between them they decide which environment variables a document may read as
`{$NAME}`:

### `env_allow` -> `list[str]`
- Required: **No**
- Default: yapyon's standard set (`HOME`, `PATH`, `LANG`, the `XDG_*` directories, and so on)

The variables documents may read, **replacing** the default list.

### `env_add` -> `list[str]`
- Required: **No**
- Default: `[]`

Variables to allow **in addition** to `env_allow`.

`HOME`, `XDG_CONFIG_HOME`, and `PERSONA_STORE_DIR` are always readable, whatever the lists say,
because persona-grata needs them to find the store at all. A variable that is not allowed is
refused by name rather than read, so a secret in your environment cannot end up in a harness
config by accident:

```yapyon
env_add: ["MY_ENDPOINT"]          # now y"{$MY_ENDPOINT}" works in any agents file
```

---

## Persona Configuration

These settings go within a single entry for a custom persona configuration:

```yapyon
personas:
  orion:
    <configuration>
  pax:
    <configuration>
```

### `persona_desc` -> `str`
- Required: **No**
- Default: `y"{__PARENT__.__KEY__}"`

Short-form description of the persona; defaults to string representation of persona root key.

### `pid` -> `str`
- Required: **No**
- Default: (Set automatically to the persona's root key)

Persona identifier, supplied for use in templates (e.g. `y"{persona_store}/{pid}"`).

### `path` -> `str`
- Required: **No**
- Default: `y"{persona_store}/{__PARENT__.__KEY__}"`

Persona settings path.

### `token` -> `str`
- Required: **No**
- Default: `y"{path}/token"`

Auth token/key path (not sent if empty/unset).

### `mind` -> `NestedDict`
- Required: **Yes**

Cognitive processing entity configuration.

#### `mind.endpoint` -> `str`
- Required: **Yes**

Connection endpoint (e.g., URL).

#### `mind.model` -> `str`
- Required: **No**
- Default: `""`

Primary model to use (not sent if empty/unset).

#### `mind.model_{n}` -> `str`, for n in [1:4]
- Required: **No**
- Default: `""`

Additional model options for harness configuration; not sent if empty/unset. 

#### `mind.dialects` -> `NestedDict`
- Required: **No**
- Default: (Filled in with known dialects, mounted at `mind.endpoint`)

Where the endpoint serves each wire protocol. Set one to `None` if it is not served:

```yapyon
mind:
  dialects:
    anthropic: {api_uri: y"{endpoint}/anthropic"}
    responses: None
```

### `harnesses` -> `NestedDict`
- Required: **No**
- Default: (Filled in with known harnesses)

Map/Dict: `{(<harness>: <harness settings>)*}`.

Known harnesses are pre-configured. To disable for a persona, set it to `None`:

```yapyon
harnesses:
  codex: None
```

### `secret` -> `NestedDict`
- Required: **No**
- Default: `None` (this persona has no key)

The password manager holding this persona's API key, and the entry within it. See
[Secret Backend Configuration](#secret-backend-configuration-per-persona) below:

```yapyon
secret:
  entry: "work/kimi"
```

---

## Harness Configuration (Per Persona)

These settings go in a single entry for a custom harness configuration _for a particular persona_:

```yapyon
personas:
  orion:
    # ...persona settings...
    harnesses:
      clod:
        <configuration>
      apex:
        <configuration>
```

### `harness_desc` -> `str`
- Required: **No**
- Default: `y"{__PARENT__.__KEY__}"`

Short-form description of the harness; defaults to string representation of harness root key.

### `agent_desc` -> `str`
- Required: **No**
- Default: `y"{pid}_{hid}"` (the command the agent is installed as)

Display name for the *agent* (persona bound to a harness). Set per harness to label agents:

```yapyon
personas:
  orion:
    harnesses:
      claude:
        agent_desc: "Orion (chat)"
```

### `hid` -> `str`
- Required: **No**
- Default: (Set automatically to the harness's root key)

Harness identifier, supplied for use in templates (e.g. `model_provider: y"{hid}"`).

### `path` -> `str`
- Required: **No**
- Default: `y"{path}/{__PARENT__.__KEY__}"`

Path to custom harness settings files.

### `auth_var` -> `str`
- Required: **No**
- Default: `"API_KEY"`

Env. variable holding key/token.

### `path_var` -> `str`
- Required: **No**
- Default: `""`

Store variable for harness config path.

### `wrapper_env` -> `NestedDict`
- Required: **No**
- Default: `{}`

Environment variables exported by shell wrapper in form `{(<NAME>: <value>)*}`

### `config_file` -> `str`
- Required: **No**
- Default: `""`

Main harness config file to write; no-op if this or `content` is empty/unset.

### `config_store` -> `NestedDict`
- Required: **No**
- Default: `{}`

Config data store used for `content` value. Nested maps become nested JSON, YAML, Yapyon, or TOML.

### `content` -> `str`
- Required: **No**
- Default: `""`

Content for harness config; often built from `config_store` via transformation calls:

| Call | Renders as |
|------|-----------|
| `y"{config_store.__AS_JSON__()}"`   | Indented JSON |
| `y"{config_store.__AS_TOML__()}"`   | TOML, nesting maps into tables |
| `y"{config_store.__AS_YAML__()}"`   | Block-style YAML, keys in declaration order |
| `y"{config_store.__AS_YAPYON__()}"` | Block-style YAPYON, keys in declaration order |

All four drop unset entries — `""`, `None`, and empty maps — before rendering, so a harness built
with any of them never sees a key the persona left empty. (Through `0.0.3` `__AS_YAPYON__` was the
exception and kept them; that asymmetry was closed under `yapyon 0.3.0`.)

### `supported_dialects` -> `list[str]`
- Required: **Yes**

Wire protocols this harness speaks, best first. No match with the persona, no agent.

### `protocol` -> `str`
- Required: **No**
- Default: `y"{match_first(supported_dialects, mind.dialects)}"`

The dialect negotiated for this pairing; indexes the persona's entry for it.

---

## Dialect Configuration (Per Persona)

A dialect is a wire protocol (`anthropic`, `chat`, `responses`) mounted under `mind.dialects`.

### `api_uri` -> `str`
- Required: **No**
- Default: `y"{endpoint}"`

Mount point for this protocol.

### `msg_uri` -> `str`
- Required: **No**
- Default: `y"{api_uri}/v1/chat/completions"` (`anthropic`: `/v1/messages`; `responses`: `/v1/responses`)

Completion/message operation.

### `model_uri` -> `str`
- Required: **No**
- Default: `y"{api_uri}/v1/models"`

Model listing operation.

### `emb_uri` -> `str`
- Required: **No**
- Default: `y"{api_uri}/v1/embeddings"` (unset for `anthropic`, `responses`)

Embeddings operation.

### `verify` -> `NestedDict`
- Required: **No**
- Default: N/A

Auth verification settings.

#### `verify.check_uri` -> `str`
- Required: **No**
- Default: `y"{msg_uri}"`

URL for auth verification (default OpenAI std).

#### `verify.key_header` -> `str`
- Required: **No**
- Default: `"Authorization: Bearer"`

Auth header variant (default OpenAI std).

#### `verify.headers` -> `list[str]`
- Required: **No**
- Default: `[]`

List of header lines; may be omitted. `content-type: application/json` is always sent.

#### `verify.body` -> `str`
- Required: **No**
- Default: `""` (minimal `"ping"` completion request using `mind.model`)

Body for verification call; only used if endpoint rejects standard single-message probe.

---

## Secret Backend Configuration (Per Persona)

A secret backend is the password manager holding a persona's API key. Persona-Grata does not
encrypt anything and never invokes `gpg` directly: it runs the manager you already use and reads
the key from that command's output.

Three backends ship — `pass`, `gopass`, and `prs` — and each supplies the command keys below, so a
persona normally sets only `entry`; which manager is used is `secret_backend`, set once for the
store. A site can add its own backend by declaring the same keys, since a backend is a pair of
commands.

### `entry` -> `str`
- Required: **No**
- Default: `""`

Entry name within the manager's store, relative to its root (e.g. `work/kimi`).

### `fetch` -> `str`
- Required: **No**
- Default: (supplied by the backend)

Command printing the key on standard output. Run at setup, and again by the shell wrapper at every
agent launch — so it must not block on a prompt.

### `store` -> `str`
- Required: **No**
- Default: (supplied by the backend)

Command reading the key from standard input. Run at setup only. A backend that can only be written
interactively leaves this unset.

### `probe` -> `str`
- Required: **No**
- Default: (supplied by the backend)

Executable that must be present; named in the error when it is missing.

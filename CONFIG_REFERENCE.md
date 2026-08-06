# Configuration Reference Manual

Persona-Grata general, persona, and harness settings structure and default values can be found in
this manual. This document uses the following definitions throughout:

`type NestedDict = dict[str, NestedDict | list[str] | str]`

If a value is required, it _must be supplied_ in any custom configuration. Otherwise, a default
fallback value will be substituted for any absent keys in the configuration.

Two conventions apply throughout:

- **`None` means "unset".** Any setting may be given the value `None` to clear it. For a persona
  or harness entry, this switches that entry off entirely (see `harnesses`, below).
- **Unset values are not emitted.** Keys whose value is empty or unset are omitted from rendered
  harness config files, so an unused `mind.model_2` does not become an env. variable set to `""`.

## Primary Elements

Agents are configured via the definitions of three (3) primary elements:

- **Mind**: The `endpoint` and `model` that provide the connitive function.
- **Persona**: Mind plus additional configuration variables / configuration (paths, context, etc.)
- **Harnesses**: Defines how to render the persona into the harness's native config (e.g., via
  templates and environment variables).

---

## Top-Level Configuration

### `persona_store` -> `str`
- Required: **No**
- Default: `"$XDG_CONFIG_HOME/personas"` (`~/.config/personas"` if `$XDG_CONFIG_HOME` unset/empty)

Persona config path (endpoint, harnesses, etc.)

### `personas` -> `NestedDict`
- Required: **Yes**

Mapping of persona name (key) to its settings (value): `{(<persona>: <settings>)*}`. The value may also be a list of the names of known personas (e.g., "kimi-k3,...").

---

## Persona Configuration

These settings go within a single entry for a custom persona configuration:

```yaml
personas:
  orion:
    <configuration>
  pax:
    <configuration>
```

### `persona_desc` -> `str`
- Required: **No**
- Default: `"{{__PARENT__.__KEY__}}"`

Short-form description of the persona; defaults to string representation of persona root key.

### `pid` -> `str`
- Required: **No**
- Default: (Set automatically to the persona's root key)

Persona identifier, supplied for use in templates (e.g. `"{{persona_store}}/{{pid}}"`).

### `path` -> `str`
- Required: **No**
- Default: `"{{persona_store}}/{{__PARENT__.__KEY__}}"`

Persona settings path.

#### `token` -> `str`
- Required: **No**
- Default: `"{{path}}/token"`

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

#### `mind.model_1` ... `mind.model_4` -> `str`
- Required: **No**
- Default: `""`

Alternate models, in descending order of capability (not sent if empty/unset). A harness that
offers model tiers maps them onto these; the Claude Code harness, for example, fills its
opus/sonnet/haiku slots from `model_1`, `model_2`, and `model_3`.

### `harnesses` -> `NestedDict`
- Required: **No**
- Default: (Filled in with known harnesses)

Map/Dict: `{(<harness>: <harness settings>)*}`.

Every known harness is configured in addition to those listed here. To switch one off for this
persona, set it to `None`:

```yaml
harnesses:
  codex: None
```

---

## Harness Configuration (Per Persona)

These settings go in a single entry for a custom harness configuration _for a particular persona_:

```yaml
personas:
  orion:
    ...
    harnesses:
      clod:
        <configuration>
      apex:
        <configuration>
```

### `harness_desc` -> `str`
- Required: **No**
- Default: `"{{__PARENT__.__KEY__}}"`

Short-form description of the harness; defaults to string representation of harness root key.

### `hid` -> `str`
- Required: **No**
- Default: (Set automatically to the harness's root key)

Harness identifier, supplied for use in templates (e.g. `model_provider: "{{hid}}"`).

### `path` -> `str`
- Required: **No**
- Default: `"{{path}}/{{__PARENT__.__KEY__}}"`

Path to custom harness settings files.

### `auth_var` -> `str`
- Required: **No**
- Default: `"API_KEY"`

Env. variable holding key/token.

### `path_var` -> `str`
- Required: **No**
- Default: `""`

Store variable for harness config path.

### `config_file` -> `str`
- Required: **No**
- Default: `""`

Main harness config file to write. Nothing is written if this or `content` is empty/unset.

### `config_store` -> `NestedDict`
- Required: **No**
- Default: `{}`

Store of config data, typically used to populate the `content` value. Nested maps are meaningful:
they become nested JSON objects, or TOML tables (`{a: {b: {...}}}` renders as `[a.b]`).

### `content` -> `str`
- Required: **No**
- Default: `""`

Content for main harness config (known harnesses have individual default values). Usually built
by serializing `config_store` with one of the terminal template calls below, which drop any
empty/unset entries as they render:

| Call | Renders as |
|------|-----------|
| `"{{config_store.__AS_JSON__()}}"` | Indented JSON |
| `"{{config_store.__AS_TOML__()}}"` | TOML, nesting maps into tables |

### `base_uri` -> `str`
- Required: **No**
- Default: `"{{mind.endpoint}}"`

Base URI for connections.

### `verify` -> `NestedDict`
- Required: **No**
- Default: N/A

Auth verification settings.

#### `verify.url` -> `str`
- Required: **No**
- Default: `"{{base_uri}}/v1/chat/completions"`

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
- Default: (A minimal `"ping"` completion request using `mind.model`)

Request body for the verification call. Supply this only if the endpoint rejects the standard
single-message probe.

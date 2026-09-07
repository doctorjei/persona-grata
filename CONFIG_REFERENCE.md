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

---

## Top-Level Configuration

### `persona_store` -> `str`
- Required: **No**
- Default: `"$XDG_CONFIG_HOME/personas"` (`~/.config/personas` if `$XDG_CONFIG_HOME` unset/empty)

Persona config path (endpoint, harnesses, etc.)

### `personas` -> `NestedDict`
- Required: **Yes**

Maps persona names (keys) to settings (values): `{(<persona>: <settings>)*}`. May be reference(s):

```yaml
personas: kimi              # one preset
personas: [kimi, minimax]   # several
```

Such personas, loaded as presents, resolve from templates (e.g., `{{personas.kimi.mind.model}}`).

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

### `token` -> `str`
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

#### `mind.model_{n}` -> `str`, for n in [1:4]
- Required: **No**
- Default: `""`

Additional model options for harness configuration; not sent if empty/unset. 

### `harnesses` -> `NestedDict`
- Required: **No**
- Default: (Filled in with known harnesses)

Map/Dict: `{(<harness>: <harness settings>)*}`.

Known harnesses are pre-configured. To disable for a persona, set it to `None`:

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
    # ...persona settings...
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

### `agent_desc` -> `str`
- Required: **No**
- Default: `"{{pid}}-{{hid}}"`

Display name for the *agent* (persona bound to a harness). Set per harness to label agents:

```yaml
personas:
  orion:
    harnesses:
      claude:
        agent_desc: "Orion (chat)"
```

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

Config data store used for `content` value. Nested maps become nested JSON, YAML, or TOML objects.

### `content` -> `str`
- Required: **No**
- Default: `""`

Content for harness config; often built from `config_store` via transformation calls:

| Call | Renders as |
|------|-----------|
| `"{{config_store.__AS_JSON__()}}"` | Indented JSON |
| `"{{config_store.__AS_TOML__()}}"` | TOML, nesting maps into tables |
| `"{{config_store.__AS_YAML__()}}"` | Block-style YAML, keys in declaration order |

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
- Default: `""` (minimal `"ping"` completion request using `mind.model`)

Body for verification call; only used if endpoint rejects standard single-message probe.

# Persona-Grata Configuration Manual

Persona-Grata general, persona, and harness settings structure and default values can be found in
this manual. This document uses the following definitions throughout:

`type NestedDict = dict[str, NestedDict | list[str] | str]`

If a value is required, it _must be supplied_ in any custom configuration. Otherwise, a default
fallback value will be substituted for any absent keys in the configuration.

## Top-Level Configuration

### persona_store -> `str`

- Required: **No**
- Default: `"$XDG_CONFIG_HOME/personas"` (`~/.config/personas"` if `$XDG_CONFIG_HOME` unset/empty)

Persona config path (endpoint, harnesses, etc.) personas: Map/Dict: 

### personas -> `NestedDict`

- Required: **Yes** (_Must be supplied_)

Mapping of persona name (key) to its settings (value): `{(<persona>: <settings>)*}`. The value may
also be a list of the names of known personas (e.g., "kimi-k3,...").

## Persona Configuration

These settings go within a single entry for a custom persona configuration:

```
personas:
  orion:
    <configuration>
  pax:
    <configuration>
```

### persona_desc -> `str`

- Required: **No**
- Default: `"{{__PARENT__.__KEY__}}"`

Short-form description of the persona; defaults to string representation of persona root key.


path: "{{persona_store}}/{{__PARENT__.__KEY__}}" Persona settings path
mind: Cognitive processing entity
mind.endpoint: <REQUIRED; NO DEFAULT> Connection endpoint (e.g., URL)
mind.model: "" Model to use (not sent if empty/unset)
mind.token_path: "{{path}}/token" Auth token/key path (not sent if empty/unset)

harnesses: Map/Dict: {(<harness>: <harness settings>)*} (By default, filled in with known harnesses)



## Harness Configuration (Per Persona)

These settings go in a single entry for a custom harness configuration _for a particular persona_:

```
personas:
  orion:
    ...
    harnesses:
      clod:
        <configuration>
      apex:
        <configuration>
  pax:
    ...
    harnesses:
      freecoder:
        <configuration>
```

### harness_desc -> `str`

- Required: **No**
- Default: `"{{__PARENT__.__KEY__}}"`

Short-form description of the harness; defaults to string representation of harness root key.



path: "{{path}}/{{__PARENT__.__KEY__}}" Path to custom harness settings files
config_file: "" Main harness config file (defaults to none)
auth_env: "API_KEY" Env. variable holding key/token
config_env: "" Store var. for harness config path (def: none)
template: "" Template for main harness config (known harnesses have individual default values)
base_uri: "{{mind.endpoint}}" # Base URI for connections
verify: Auth verification settings
verify.url: "{{base_uri}}/v1/chat/completions" URL for auth verification (default OpenAI std)
verify.key_header: "Authorization: Bearer" Auth header variant (default OpenAI std)
verify.headers: [] -->  List: [<header text>*]; may be omitted.

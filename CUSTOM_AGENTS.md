# Persona-Grata: Custom Agent Guide

> Persona-Grata allows the configuration of _minds_ (cognitive entity) from settings & _endpoints_
> (e.g., custom local / remote servers) with existing _harnesses_ (e.g., Claude Code, Codex CLI,
> Goose) to construct autonomous _agents_ (actors who can write code and/or perform other tasks).

Known endpoints and harnesses already include their defaults, so required user setup is minimized.
Custom configurations can be generated and supplied to end users for class, laboratory, and/or
workplace purposes.

## Agent Configuration

Agents are configured via a Yapyon (.yap) file with the following minimum elements:

```yapyon
personas:
  <persona>:
    mind:
      endpoint: <URI, Required>
```

For example, a typical agent configuration might look like this:

```yapyon
personas:
  orion:
    persona_desc: "Orion Toolkit"
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
```

This example includes harness URL and other customizations:

```yapyon
personas:
  pax:
    persona_desc: "Pax Toolkit"
    mind:
      endpoint: "https://api.moonbase.space"
      dialects:
        anthropic:
          api_uri: y"{endpoint}/anthropic_api"
          verify:
            check_uri: y"{api_uri}/verify_me"
            # Requires traditional Anthropic-style keys:
            key_header: "x-api-key:"
        responses:
          api_uri: y"{endpoint}/openai_api"
    harnesses:
      claude:
        harness_desc: "Sneaky Clod"
```

## Resolution of Names & Special Identifiers

To avoid repetitive entry of the same values multiple times, a value may be a yapyon **y-string**,
`y"…"`, whose `{…}` references resolve to existing, related entries. Identifiers within the
braces are resolved, where possible, to other values in the configuration tree on a nearest first,
upward-only fashion; a key can see its "siblings", "parents", and "uncles"/"aunts" (?!), but not
its "cousins". For example, a value in the harness may refer to `mind` key's child, `endpoint`,
as follows:

```yapyon
clod:
  wrapper_env:
    CLOD_HOST: y"{mind.endpoint}/misanthropic"          # Yields "https://api.clod.ai/misanthropic"
```

Special identifiers use dunders (double-underscores) and reference the tree around a value rather
than a key by name. `__PARENT__` is the mapping a value sits in, and `__KEY__`, which may only follow
`__PARENT__`, is that mapping's own key:

```yapyon
clod:
  harness_desc: y"{__PARENT__.__KEY__}'s fancy harness" # Yields "clod's fancy harness"
```

Each further `__PARENT__` climbs one level, so from inside `wrapper_env` the harness's key is two up:

```yapyon
clod:
  wrapper_env:
    CLOD_HOST: y"{mind.endpoint}/{__PARENT__.__PARENT__.__KEY__}"  # Yields "https://api.clod.ai/clod"
```

Environment variables are references too, written `{$NAME}`; a plain string is never expanded, so
a dollar sign needs no escaping. Write `{{` or `}}` for a literal brace inside a y-string:

```yapyon
orion:
  persona_desc: "Costs $5 per run"                       # Yields "Costs $5 per run"
  path: y"{$HOME}/agents/orion"                          # Yields "/home/you/agents/orion"
```

## Adding & Removing Harnesses

Every known harness the persona can talk to is configured in addition to those you list; one that
shares no dialect with it is skipped. To switch a harness off, set it to `None`:

```yapyon
personas:
  orion:
    mind:
      endpoint: "https://api.cybertron.space"
    harnesses:
      codex: None                                        # Claude Code & Goose still configured
```

A harness you name that isn't known is created from the harness defaults, so a custom harness needs
only what differs. There are two ways to configure one.

**By config file.** Put the harness's settings in `config_store` & render them into `content` with
a serializer — `__AS_JSON__()`, `__AS_TOML__()`, `__AS_YAML__()`, or `__AS_YAPYON__()`. Nested maps
become nested JSON objects, TOML tables, or YAML / Yapyon blocks, and empty/unset entries are
dropped, so a harness never sees a key the persona left empty. `__AS_YAPYON__()` is the one that
carries multimaps and bytes, and as of `yapyon 0.3.0` it prunes like the other three:

```yapyon
personas:
  orion:
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
    harnesses:
      apex:
        supported_dialects: ["chat"]                     # Wire protocols it speaks, best first
        path_var: "APEX_HOME"                            # Points the harness at its config dir
        config_file: y"{path}/config.toml"
        config_store:
          model: y"{mind.model}"
          providers:
            apex:
              base_url: y"{mind.dialects[protocol].api_uri}"
              env_key: y"{auth_var}"
        content: y"{config_store.__AS_TOML__()}"
```

**By environment.** Some harnesses cannot be pointed at a per-persona config directory. Use
`wrapper_env` instead; those variables are exported by the generated shell wrapper, and no config
file is written. This is how the bundled Goose harness works:

```yapyon
personas:
  orion:
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
    harnesses:
      apex:
        supported_dialects: ["chat"]
        auth_var: "APEX_API_KEY"
        wrapper_env:
          APEX_PROVIDER: "openai"
          APEX_MODEL: y"{mind.model}"
          APEX_HOST: y"{mind.dialects[protocol].api_uri}"
```

Settings that aren't part of the schema are reported as warnings and ignored, so a misspelled key
is visible rather than silent. Persona, harness, and dialect _names_ are yours to choose, but they
are 1–64 ASCII letters, digits, and `_`, and may not start with a digit (kanibako's rule, so every
name is also a valid shell function name); `default` is reserved.
`config_store` is free-form harness data that is passed through untouched.

## Resources

See the [Config Reference Manual](CONFIG_REFERENCE.md) for details about defaults and
setting non-default options.

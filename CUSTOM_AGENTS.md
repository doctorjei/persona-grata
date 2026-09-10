# Persona-Grata: Custom Agent Guide

> Persona-Grata allows the configuration of _minds_ (cognitive entity) from settings & _endpoints_
> (e.g., custom local / remote servers) with existing _harnesses_ (e.g., Claude Code, Codex CLI,
> Goose) to construct autonomous _agents_ (actors who can write code and/or perform other tasks).

Known endpoints and harnesses already include their defaults, so required user setup is minimized.
Custom configurations can be generated and supplied to end users for class, laboratory, and/or
workplace purposes.

## Agent Configuration

Agents are configured via a YAML file with the following minimum elements:

```
personas:
  <persona>:
    mind:
      endpoint: <URI, Required>
```

For example, a typical agent configuration might look like this:

```
personas:
  orion:
    persona_desc: "Orion Toolkit"
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
```

This example includes harness URL and other customizations:

```
personas:
  pax:
    persona_desc: "Pax Toolkit"
    mind:
      endpoint: "https://api.moonbase.space"
    harnesses:
      claude:
        harness_desc: "Sneaky Clod"
        base_uri: "{{mind.endpoint}}/anthropic_api"
        verify:
          check_uri: "{{base_uri}}/verify_me"
          # Requires traditional Anthropic-style keys:
          key_header: "x-api-key:"
      codex:
        base_uri: "{{mind.endpoint}}/openai_api"
```

## Resolution of Names & Special Identifiers

To avoid repetitive entry of the same values multiple times, the Persona-Grata parses a grammar
that resolves existing, related entries via braces-based syntax in values. Identifiers within the
braces are resolved, where possible, to other values in the configuration tree on a nearest first,
upward-only fashion; a key can see its "siblings", "parents", and "uncles"/"aunts" (?!), but not
its "cousins". For example, a value in the harness may refer to `mind` key's child, `endpoint`,
as follows:

```
clod:
  base_uri: "{{mind.endpoint}}/misanthropic"             # Yields "https://api.clod.ai/misanthropic"
```

Special identifiers use dunders (double-underscores) and can be used to indirectly reference keys
and metadata. A key's string representation can be accessed via the `__KEY__` identifier:

```
clod:
  base_uri: "{{mind.endpoint}}/{{clod.__KEY__}}"       # Yields "https://api.clod.ai/clod"
```

To access the direct parent of an element, use the `__PARENT__` identifier:
```
clod:
  harness_desc: "{{__PARENT__.__KEY__}}'s fancy harness" # Yields "clod's fancy harness"
```

Environment variables are substituted before the file is parsed, in a single pass. Write `$$` for
a literal dollar sign:

```
orion:
  persona_desc: "Costs $$5 per run"                      # Yields "Costs $5 per run"
```

## Adding & Removing Harnesses

Every known harness is configured for a persona in addition to those you list. To switch one off,
set it to `None`:

```
personas:
  orion:
    mind:
      endpoint: "https://api.cybertron.space"
    harnesses:
      codex: None                                        # Claude Code & Goose still configured
```

A harness you name that isn't known is created from the harness defaults, so a custom harness needs
only what differs. There are two ways to configure one.

**By config file.** Put the harness's settings in `config_store` and render them into `content`
with a serializer — `__AS_JSON__()`, `__AS_TOML__()`, or `__AS_YAML__()`. Nested maps become nested
JSON objects, TOML tables, or YAML blocks, and empty/unset entries are dropped:

```
personas:
  orion:
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
    harnesses:
      apex:
        path_var: "APEX_HOME"                            # Points the harness at its config dir
        config_file: "{{path}}/config.toml"
        config_store:
          model: "{{mind.model}}"
          providers:
            "{{hid}}":                                   # Templated key -> [providers.apex]
              base_url: "{{base_uri}}"
              env_key: "{{auth_var}}"
        content: "{{config_store.__AS_TOML__()}}"
```

**By environment.** Some harnesses cannot be pointed at a per-persona config directory. Use
`wrapper_env` instead; those variables are exported by the generated shell wrapper, and no config
file is written. This is how the bundled Goose harness works:

```
personas:
  orion:
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
    harnesses:
      apex:
        auth_var: "APEX_API_KEY"
        wrapper_env:
          APEX_PROVIDER: "openai"
          APEX_MODEL: "{{mind.model}}"
          APEX_HOST: "{{base_uri}}"
```

Settings that aren't part of the schema are reported as warnings and ignored, so a misspelled key
is visible rather than silent. Persona and harness _names_ are yours to choose and are never
checked, and `config_store` is free-form harness data that is passed through untouched.

## Resources

See the [Config Reference Manual](CONFIG_REFERENCE.md) for details about defaults and
setting non-default options.

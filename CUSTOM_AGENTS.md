# Persona-Grata: Custom Agent Guide

> Persona-Grata allows the configuration of _minds_ (congnitive entity) from settings & _endpoints_
> (e.g., custom local / remote servers) with existing _harnesses_ (e.g., Claude Code / Codex CLI)
> to construct autonomous _agents_ (actors who can write code and/or perform other tasks).

Known endpoints and harnesses already include their defaults, so required user setup is minimized.
Cutsom configurations can be generated and supplied to end users for class, laboratory, and/or
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
          url: "{{base_uri}}/verify_me"
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

To accesss the direct parent an element, use the `__PARENT__` identifier:
```
clod:
  harness_desc: "{{__PARENT__.__KEY__}}'s fancy harness" # Yields "clod's fancy harness"
```

## Resources

See CONFIG_MANUAL.md for details about defaults and setting non-default options.

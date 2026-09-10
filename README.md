# Persona-Grata

> **Persona-Grata** is a scaffolding utility that binds AI personas to harnesses to produce
> an embodied, functional agent. It helps users connect professional LLM orchestration harnesses
> (e.g., Claude Code, the Codex CLI, and Goose) with customized endpoints.

## Install

Install via pipx, uv, or your other favorite package management tool:

`pipx install persona-grata`

## Run

Run it directly:

`persona-grata <persona> [harness]*`

**Examples**

To install a persona for the official kimi servers and use it with Claude Code:

`persona-grata kimi claude`

If you have custom endpoints or other settings, create and run with an agent file:

`persona-grata custom_agents.yaml`

_Read more about configurations in the [Custom Agents Guide](CUSTOM_AGENTS.md)._

---

## Overview: Agents, Personas, & Harnesses

We combine a **persona** (representing the mind and experience) with a **harness** (the mechanism
used to impact the world) to yield an _agent_.

...but what does that actually _mean_?

An _agent_ is an entity that can act on the world around it - it has a model of its universe, an
inference-based (data-derived) decision-making process, a set of actions, and some knowledge of the
results of its actions (see also _The Craik Model of Intelligence_.) An agent requires not just
the ability to _think_, but also the ability to _act_.

A _persona_ is that thinking part of an agent - i.e., its 'mind' (endpoint, model, & parameters)
and 'experiences' (system prompt, context, etc.)

To interact with the world, we can use a _harness_ (sometimes called an _orchestrator_) to provide
toolsets and communication mechanisms; these include Claude Code, Codex CLI, Goose, & many others.

By combining a _persona_ with a _harness_, we get the fully embodied _agent_.

---

## Usage

The utility reads agent definitions from a provided YAML file (which describes how personas
connect to harnesses) and configures your local environment. The sample `agents-example.yaml` file
can serve as a starting point; the bundled harness profiles are 'ready' to go and do not require
modification.

### Known Endpoints & Harnesses

Persona-Grata comes with "out of the box" support for the following endpoints and harnesses;
for each endpoint, a default persona is derived:

_Endpoint Personas_: kimi, local_8000, local_8080, local_lemonade, local_llamacpp, local_lmstudio,
local_ollama, minimax, navigator

_Coding Harnesses_: claude, codex, goose

### Setup an Agent

To setup an agent from a persona and harness, run `persona-grata <persona> [harness]*`:

`persona-grata kimi`

_Sets up kimi for all known harnesses_

`persona-grata minimax claude codex`

_Sets up minimax for use with Claude Code and Codex CLI_

If you need a more customized solution, an agent definition file provides additional flexibility:

**agents.yaml**

```yaml
personas:
  orion:
    persona_desc: "Orion Toolkit"
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
```

Then, run:

`persona-grata agents.yaml orion claude`

_Setup the custom persona (Orion Toolkit) with Claude Code._

Or, to install all agents and harnesses, run without persona and/or harness names:

`persona-grata agents.yaml`

_Setup every persona in the file with all known harnesses._

`pg` is a shorter alias for the same command: `pg kimi claude`.

### Create a Configuration via CLI

For one-off variants or local model servers, persona configurations can be defined by arguments:

`pg --endpoint http://localhost:8675 --model llama3 --no-token ollama claude`

_Sets up a local Ollama model as the persona "ollama", for Claude Code._

The CLI can also be used to update an existing persona:

`pg --update --model kimi-k2-0905-preview kimi codex`

_Sets kimi up with Codex CLI, using a different model just this once._

By default, a definition is ephemeral, but the configuration can be exported if desired:

`pg --endpoint http://who.dr:99 --model llama3 --no-token --export "clauma.yaml" ollama claude`

_Exports the configuration to `clauma.yaml`._

Export, update, and new persona creation are mutually exclusive:

| Flag | New OK? | Existing OK? | Writes? | Reads New? | Reads Existing? |
|---|---|---|---|---|---|
|`--export`| Yes | Yes | No | Yes | Yes | 
|`--update`| No | Yes | Yes | Yes | Yes |
|`--create`| Yes | No | Yes | Yes | Yes |

The API key/token can be read, verified, then stored directly, replacing any existing key/token:

`pg --token ~/keys/moonshot.key kimi claude`

_Sets kimi up with Claude Code, taking the key from `~/keys/moonshot.key`._

The same settings can also be asked for rather than typed:

`pg -i`

_Asks for a name, description, endpoint, model, key, and harnesses, then offers to save the result._

Interactive mode supplies whatever flags did not, combining one of the actions above; e.g.,
`pg -i --update kimi` only asks for missing elements. It cannot be paired with `--remove`.

### Remove an Agent

Pass `--remove` (or `-r`) to remove the harness-persona wrapper & harness config directory:

`pg --remove kimi codex`

_Removes the Codex CLI agent for kimi, leaving Claude Code and the stored token alone._

`pg --remove kimi`

_Removes every kimi harness, then asks whether to delete the stored API token._

### Interaction

The script automatically adds a shell wrapper to your `.bashrc` or `.zshrc`. After running setup
and sourcing your config (`source ~/.bashrc`) or opening a new terminal, you can launch the agent:

```bash
<persona>-<harness> [arguments]
```

### Parameters Summary

| Parameter | Sets |
|------|------|
| `--endpoint URL` | Set `mind.endpoint` to `URL` |
| `--model NAME` | Set `mind.model` to `NAME` |
| `--desc TEXT` | Set `persona_desc` to `TEXT` |
| `--no-token` | `token` — no key prompt or verification*^ |
| `--create` | Stores a new persona; fails if name is taken |
| `--update` | Replace an existing persona, if it exists |
| `--export FILE` | Export a template for this configuration to file named `FILE` |
| `-i`, `--interactive` | Ask for whatever is not given above; offers to save the result |
| `--token FILE` | Read the API key from `FILE` rather than prompting for it^ |

_*If the `--no-token` flag is not included, connections to keyless-only servers will fail._

_^`--token` and `--no-token` are mutually exclusive._

## Agent Configuration & Templates

Definitions are managed in an agent YAML file. It describes one or more personas and/or harnesses.
Persona-Grata supports a dynamic variable resolution system via `{{variable}}` syntax.

- **Context Lookup**: Resolves variables defined in current context (e.g., `{{pid}}`, `{{path}}`).
- **Hierarchical References**: Absolute references to any value in `agents.yaml` via dot-notation
  (e.g., `{{personas.orion.mind.model}}`).
- **Reserved Identifiers**: `{{__PARENT__}}` resolves to the immediate parent node in the
  configuration tree and `{{__KEY__}}` to a node's own key, so `{{__PARENT__.__KEY__}}` in a
  persona definition yields that persona's ID.
- **Templated Keys**: dict *keys* may hold templates too, so a config section can be named from a
  value (e.g., `"{{hid}}":` under `model_providers` yields `[model_providers.codex]`).
- **Serializers**: a reference may end in `__AS_JSON__()`, `__AS_TOML__()`, or `__AS_YAML__()` to
  render a whole subtree as a harness config file (e.g., `content: "{{config_store.__AS_JSON__()}}"`).

Environment variables (`$FOO`, `${FOO}`) are also substituted, in a single pass, before the file
is parsed. Write `$$` for a literal dollar sign — `$$HOME` yields the text `$HOME`.

For more about templates & configuration, see the [Config Reference Manual](CONFIG_REFERENCE.md).

### Security

To prevent secret leakage:

- API tokens are stored in files with `0600` permissions.
- The utility handles tokens by path, avoiding printing secrets to logs or transcripts.
- A `.secret_path` file is created in the persona's home directory containing the path to the
  token for discovery by other tools.

---

## License & Contributing

Persona-Grata is released under the **GNU General Public License v3.0 or later**; see
[LICENSE.md](LICENSE.md).

Contributions generated in whole or in part with AI tools are welcome, subject to the
[project's AI policy](AIPOLICY.md).

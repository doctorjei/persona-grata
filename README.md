# Persona-Grata

> **Persona-Grata** is a scaffolding utility that binds AI personas to harnesses to produce a
> an embodied, functional agent. It helps users connect professional LLM orchestration harnesses
> (e.g., Claude Code and the Codex CLI) with customized endpoints.

## Install

Install via pipx, uv, or your other favorite package management tool:

`pipx install persona_grata`

## Run

Run it directly:

`persona-grata <persona> [harness]*`

**Examples**

To install a persona for the official kimi servers and use it with Claude Code:

`persona-grata kimi claude`

If you have a custom endpoints or other settings, create and run with an agent file:

`persona-grata cuastom_agents.yaml`

_Read more about configurations in the [Custom Agents Guide](CUSTOM_AGENTS.md)._

---

## Overview: Agents, Personas, & Harnesses

We combine a **persona** (representing the mind and experience) with a **harness** (the mechanism
used to impact the world) to yield an _agent_.

...but what does that actually _mean_?

An _agent_ is an entity that can act on the world around it - it has a model of its universe, an
inference-based (data-derived) decision-making process, set of actions, and some knowledge of the
results of its actions (see also _The Craik Model of Intelligence_.) An agent requires not just
the ability to _think_, but also the ability to _act_.

A _persona_ is that thinking part of an agent - i.e., it's 'mind' (endpoint, model, & parameters)
and 'experiences' (system prompt, context, etc.)

To interact with the world, we can use a _harness_ (sometimes called an _orchestrator_) to provide
toolsets and communication mechanisms; these include Claude Code, Codex CLI, Goose, & many others.

By combining a _persona_ with a _harness_, we get the fully embodied _agent_.

---

## Usage

The utility reads agent definitions from a provided YAML file (which describes how personas
connect to harnesses) and configures your local environment. The sample `agents.yaml.example` file
can serve as a starting point; the `claude` and `codex` profiles are 'ready' to go and do not
require modification.

### Known Endpoints & Harnesses

Persona-Grata comes with "out of the box" support for the following endpoints and harnesses;
for each endpoint, a default persona is derived:

_Endpoint Personas_: kimi, minimax

_Coding Harnesses_: claude, codex

### Setup an Agent

To setup an agent from a persona and harness, run `python persona-grata.py <persona> [harness]*`:

`python persona-grata.py kimi`

_Sets up kimi for all known harnesses_

`python persona-grata.py minimax claude codex`

_Sets up minimax for use with Claude Code and Codex CLI_

If you need a more customized solution, an agent definition file provides additional flexibility:

**agents.yaml**

```yaml
  orion:
    persona_desc: "Orion Toolkit"
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
```

Then, run:

`python persona-grata.py agents.yaml orion claude`

_Setup the custom persona (Orion Toolkit) with Claude Code._

Or, to install all agents and harnesses, run wtihout persona and/or harness names:

`python persona-grata.py agents.yaml`

_Setup the custom persona (Orion Toolkit) with all known harnesses._

### Interaction

The script automatically adds a shell wrapper to your `.bashrc` or `.zshrc`. After running setup
and sourcing your config (`source ~/.bashrc`) or opening a new terminal, you can launch the agent:

```bash
<persona>-<harness> [arguments]
```

## Agent Configuration & Templates

Definitions are managed in an agent YAML file. It describes one or more personas and/or harnesses.
Persona-Grata supports a dynamic variable resolution system via `{{variable}}` syntax.

- **Context Lookup**: Resolves variables defined in current context (e.g., `{{pid}}`, `{{home}}`).
- **Hierarchical References**: Absolute references to any value in `agents.yaml` via dot-notation
  (e.g., `{{personas.orion.model}}`).
- **Reserved Identifiers**: `{{__PARENT__}}` resolves to the key of the immediate parent node in
  the configuration tree (e.g., in a persona definition, it resolves to persona's ID).

For more about templates & configuration, see the [Config Reference Maunal](CONFIG_REFERENCE.md).

### Security

To prevent secret leakage:

- API tokens are stored in files with `0600` permissions.
- The utility handles tokens by path, avoiding printing secrets to logs or transcripts.
- A `.secret_path` file is created in the persona's home directory containing the path to the
  token for discovery by other tools.

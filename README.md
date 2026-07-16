# Persona-Grata
_Meeting of the Minds (and Bodies)_

**Persona-Grata** is a scaffolding utility that binds AI personas to agent
harnesses to produce a functional agent. It helps users connect professional
LLM harnesses (e.g., Claude Code and the Codex CLI) with customized endpoints.

## Quick-Start
1. Install pyyaml (e.g., `pipx install pyyaml`)
2. Copy `agents.yaml.example` to `agents.yaml` and change as follows:
  a. persona name (e.g., "orion" -> "my_provider")
  b. endpoint (URL)
  c. model name
  d. Optionally, `persona_desc` and/or `token_path` if desired
3. run `python persona-grata.py agents.yaml <provider> <harness>`
4. That's it! :)

## Overview: Agents, Personas, & Harnesses
We combine a **persona** (representing the mind and experience) with a **harness** (the
mechanism used to impact the world) to yield an _agent_.

...but what does that actually _mean_?

An _agent_ is an entity that can act on the world around it - it has a model
of its universe, an inference-based (data-derived) decision-making process,
set of actions, and some knowledge of the results of its actions (see also
_The Craik Model of Intelligence_.) An agent requires not just the ability
to _think_, but also the ability to _act_.

A _persona_ is that thinking part of an agent - i.e., it's 'mind' (endpoint,
model, & parameters) and 'experiences' (system prompt, context, etc.)

To interact with the world, we can use a _harness_ (sometimes called an
_orchestrator_) to provide toolsets and communication mechanisms; these
include Claude Code, Codex CLI, Goose, and many others.

By combining a _persona_ with a _harness_, we get the fully embodied _agent_.

## Installation
Ensure you have `PyYAML` installed with pipx, uv, or plain old pip:
```bash
pipx install pyyaml
```

## Usage
The utility reads agent definitions from a provided YAML file (which describes
how personas connect to harnesses) and configures your local environment. The
sample `agents.yaml.example` file can serve as a starting point; the `claude`
and `codex` profiles are 'ready' to go and do not require modification.

### Setup an Agent
To bind a persona to a specific harness and create an agent, run:
```bash
python persona-grata.py <agent-yaml> <persona> <harness>
```

**Example:**
```bash
python persona-grata.py agents.yaml orion claude
```

### Interaction
The script automatically adds a shell wrapper to your `.bashrc` or `.zshrc`.
After running the setup and sourcing your config (`source ~/.bashrc`), you
can launch the agent using the generated command:

```bash
<persona>-<harness> [arguments]
```

## Configuration
Definitions are managed in an agent description YAML file. It may contain
multiple personas and harnesses within it.

- **Mind**: Defines the `endpoint`, `model`, and the `token_path` where the API key is stored.
- **Harnesses**: Defines how to render the persona into the harness's native config (e.g., via templates and environment variables).

### Advanced Templating
The configuration system supports dynamic variable resolution using `{{variable}}` placeholders:

- **Context Lookup**: Resolves variables defined in the current context (e.g., `{{pid}}`, `{{home}}`).
- **Hierarchical References**: Absolute references to any value in `agents.yaml` using dot-notation (e.g., `{{personas.orion.model}}`).
- **Reserved Identifiers**: The `{{__PARENT__}}` variable resolves to the key of the immediate parent node in the configuration tree (e.g., in a persona definition, it resolves to the persona's ID).

## Security
To prevent secret leakage:
- API tokens are stored in files with `0600` permissions.
- The utility handles tokens by path, avoiding printing secrets to logs or transcripts.
- A `.secret_path` file is created in the persona's home directory containing the path to the token for discovery by other tools.

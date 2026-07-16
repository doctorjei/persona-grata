# persona-grata

**persona-grata** is a scaffolding utility that binds AI personas to agent harnesses to produce a functional agent. It facilitates the linking of a **persona** (representing the mind and experience) with a **harness** (the mechanism used to impact the world).

## Core Concepts

- **Persona**: A portable definition of a model, its endpoint, and its disposition (mind and experience).
- **Harness**: The execution environment or tool used to run the AI (e.g., `claude`, `codex`, `goose`).
- **Agent**: The resulting entity produced by binding a persona to a specific harness.

## Installation

Ensure you have `PyYAML` installed:
```bash
pip install pyyaml
```

## Usage

The utility reads agent definitions from `agents.yaml` (which describes how personas connect to harnesses) and configures your local environment.

### Setup an Agent
To bind a persona to a specific harness and create an agent, run:
```bash
python persona-grata.py <persona_name> <harness_name>
```

**Example:**
```bash
python persona-grata.py navigator claude
```

### Interaction
The script automatically adds a shell wrapper to your `.bashrc` or `.zshrc`. After running the setup and sourcing your config (`source ~/.bashrc`), you can launch the agent using the generated command:

```bash
navigator-claude [arguments]
```

## Configuration

Definitions are managed in `agents.yaml`.

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

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

`persona-grata custom_agents.yap`

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

The utility reads agent definitions from provided Yapyon (.yap) files (which describes how personas
connect to harnesses) and configures your local environment. The sample `agents-example.yap` file
can serve as a starting point; the bundled harness profiles are 'ready' to go and do not require
modification.

### Known Endpoints & Harnesses

Persona-Grata comes with "out of the box" support for the following endpoints and harnesses;
for each endpoint, a default persona is derived:

| Endpoint for Service  | Persona    |
|-----------------------|------------|
| api.moonshot.ai:443   | kimi       |
| localhost:13305       | lemonade   |
| localhost:9931        | llamacpp   |
| localhost:1234        | lmstudio   |
| localhost:8000        | local_8000 |
| localhost:8080        | local_8080 |
| localhost:11434       | ollama     |
| api.minimax.io:443    | minimax    |
| api.ai.it.ufl.edu:443 | navigator  |

| Application/Tool | Harness |
|------------------|---------|
| Claude Code      | claude  |
| OpenAI Codex CLI | codex   |
| Goose AI Agent   | goose   |

### Setup an Agent

To setup an agent from a persona and harness, run `persona-grata <persona> [harness]*`:

`persona-grata kimi`

_Sets up kimi for all known harnesses_

`persona-grata minimax claude codex`

_Sets up minimax for use with Claude Code and Codex CLI_

If you need a more customized solution, an agent definition file provides additional flexibility:

**agents.yap**

```yapyon
personas:
  orion:
    persona_desc: "Orion Toolkit"
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
```

Then, run:

`persona-grata agents.yap orion claude`

_Setup the custom persona (Orion Toolkit) with Claude Code._

Or, to install all agents and harnesses, run without persona and/or harness names:

`persona-grata agents.yap`

_Setup every persona in the file with all known harnesses._

`pg` is a shorter alias for the same command: `pg kimi claude`.

### Designations & Names

An agent's **designation** is `<persona>+<harness>`. It names one agent, so it settles the harness
on its own:

`pg kimi+claude`

_The same as `pg kimi claude`._

A **name** is yours to choose, and is how the agent is invoked. Pass `--name` to set one:

`pg --name scout local_ollama+codex`

_Sets up the agent and installs it as `scout` rather than `local_ollama_codex`._

A name replaces that command rather than adding a second one, so an agent always has exactly one
way to run it; naming it again renames it. Use the name wherever a designation goes — `pg -r scout`
removes it, and releases the name. An agent you never name keeps its designation as its name.

### Create a Configuration via CLI

For one-off variants or local model servers, persona configurations can be defined by arguments:

`pg --endpoint http://localhost:8675 --model llama3 --no-token ollama claude`

_Sets up a local Ollama model as the persona "ollama", for Claude Code._

The CLI can also be used to update an existing persona:

`pg --update --model kimi-k2-0905-preview kimi codex`

_Sets kimi up with Codex CLI, using a different model just this once._

By default, a definition is ephemeral, but the configuration can be exported if desired:

`pg --endpoint http://who.dr:99 --model llama3 --no-token --export "clauma.yap" ollama claude`

_Exports the configuration to clauma.yap._

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
_Asks for a name, description, endpoint, model, key, and harnesses, then offers to save the result._
Interactive mode supplies whatever flags did not, combining one of the actions above; e.g.,
`pg -i --update kimi` only asks for missing elements. It cannot be paired with `--remove`.

### Remove an Agent

Pass `--remove` (or `-r`) to remove the harness-persona wrapper & harness config directory:

`pg --remove kimi codex`

_Removes the Codex CLI agent for kimi, leaving Claude Code and the stored token alone._

`pg --remove kimi`

_Removes every kimi harness, then asks whether to delete the stored API token, if there is one._

### Interaction

The script automatically adds a shell wrapper to your `.bashrc`, `.zshrc`, or `.profile`, depending
on your shell. After running setup and sourcing your config (`source ~/.bashrc`) or opening a new
terminal, you can launch the agent:

```bash
<persona>_<harness> [arguments]
```

A `$SHELL` persona-grata doesn't recognise is reported, not assumed; the wrapper is written in
plain POSIX shell syntax, so any _POSIX_ shell will run it.

Names — of personas, harnesses, dialects, and agents — are 1–64 ASCII letters, digits, and `_`,
and do not start with a digit: the same rule kanibako uses, so every name is a valid shell
function name.

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
| `--name NAME` | Call this agent `NAME`, and install it as the command `NAME` |
| `--remove`, `-r` | Remove the named agent(s); offers to delete the token |

_*If the `--no-token` flag is not included, connections to keyless-only servers will fail._

_^`--token` and `--no-token` are mutually exclusive._

## Agent Configuration & Templates

Definitions are managed in an agents file written in [yapyon](https://pypi.org/project/yapyon/), a
YAML-like format. It describes one or more personas and/or harnesses. Unlike YAML, yapyon has no
unquoted strings — write `model: "kimi-k3"`, not `model: kimi-k3` — and keys are bare identifiers.

Values can be computed from other values with **y-strings**: `y"{<reference>}"` is replaced by the
value the reference names, and text around it is kept (`y"{path}/token"`).

- **Context Lookup**: A reference is looked up from where it is written outward, so a value nearby
  wins (e.g., `{api_uri}`, `{path}`).
- **Hierarchical References**: Absolute references to any value in the configuration via
  dot-notation (e.g., `{personas.orion.mind.model}`).
- **Reserved Identifiers**: `{__PARENT__}` resolves to the immediate parent node in the
  configuration tree and `{__KEY__}` to a node's own key, so `{__PARENT__.__KEY__}` in a
  persona definition yields that persona's ID.
- **Serializers**: a reference ending in `__AS_JSON__()`, `__AS_TOML__()`, `__AS_YAML__()`, or
  `__AS_YAPYON__()` renders a subtree as a harness file (`content: y"{config_store.__AS_JSON__()}"`).
  All four drop unset entries — `""`, `None`, empty maps — so the harness never sees a key the
  persona left empty. `__AS_YAPYON__` is the one that also carries multimaps and bytes.
- **Subscripts**: any segment may be indexed by another reference (e.g., `{providers[chosen].url}`
  is "the entry of `providers` named by `chosen`"). Lists index by position — `[1]`, `[-1]` included.
- **Functions**: `match_first(key_set, items)` yields the first member of `key_set` found among
  `items`' keys, reading the list as a preference order; it is how a harness and a persona settle on
  a dialect (`y"{match_first(supported_dialects, mind.dialects)}"`).

A y-string always produces text, so it can splice in a string, number, or `None`, but not a whole
list or map. A list may hold y-strings as its items (`supported_dialects: [y"{preferred}"]`).
Write `{{` or `}}` for a literal brace.

Environment variables are read with `{$NAME}` — `y"{$HOME}/agents"` — and an unset one is an error
rather than an empty string; `{__ENV__("NAME", "fallback")}` supplies a default instead. Only
variables the store allows can be read (by default the standard ones such as `HOME` and `PATH`;
see `env_allow` / `env_add` in the [Config Reference Manual](CONFIG_REFERENCE.md)). A plain string
is never expanded: `"$HOME/agents"` stays exactly that text.

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

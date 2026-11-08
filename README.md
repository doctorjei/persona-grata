# Persona-Grata

> **Persona-Grata** is a scaffolding utility that binds AI personas to harnesses to produce
> an embodied, functional agent. It helps users connect professional LLM orchestration harnesses
> with customized endpoints.

## Install

Install via pipx, uv, or your other favorite package management tool:

`pipx install persona-grata`

Python 3.11 or newer is required. Install the harness you intend to run separately.
For keyed agents, install and initialize a password manager. Bundled backends support `pass`,
`gopass`, and `prs`; custom backends can be defined in the agent store. For example, on Debian/Ubuntu:

```bash
sudo apt install pass gnupg
```

Keyless agents do not need a password manager.

## Run

`persona-grata` and `pg` are the same command. With no arguments, they list registered agents.
Creation is explicit and produces one agent, not every pairing of a persona and its harnesses:

```bash
pg --setup                   # Configure a manager before creating a keyed agent
pg --create kimi claude --name scout
pg --create ollama codex --name local_code --no-token
```

After setup, open a new shell or source your shell's startup file, then run the chosen name:

```bash
scout [arguments]
```

Use `pg --setup` to configure the store's default password manager. An agent document can select a
different backend. Credential verification is optional; for keyed agents, `--verify` requests a live
check that may incur provider charges. Setup does not prove that a server or harness accepts the
generated configuration.

## Overview: Agents, Personas, & Harnesses

We combine a **persona** (representing the mind and experience) with a **harness** (the mechanism
used to impact the world) to yield an _agent_.

An _agent_ requires not just the ability to _think_, but also the ability to _act_. A _persona_
is that thinking part — its endpoint, models, parameters, and context. A _harness_ supplies
its toolsets and communication mechanisms. A **dialect** describes the wire protocol the two
sides share.

## Usage

### Known Endpoints & Harnesses

Bundled presets supply configuration defaults, not a guarantee that every server implements every
dialect or that every pairing has been tested against a live harness.

| Endpoint for Service | Persona |
|----------------------|---------|
| api.moonshot.ai:443 | kimi |
| localhost:13305 | lemonade |
| localhost:9931 | llamacpp |
| localhost:1234 | lmstudio |
| localhost:8000 | local_8000 |
| localhost:8080 | local_8080 |
| localhost:11434 | ollama |
| api.minimax.io:443 | minimax |
| api.ai.it.ufl.edu:443 | navigator |

| Application/Tool | Harness |
|------------------|---------|
| Claude Code | claude |
| OpenAI Codex CLI | codex |

### Names & Paths

An agent's name is independent of its persona, harness, and directory. Several agents may use the
same persona and harness. Without `--name`, creation uses `<persona>_<harness>` as its name and
places it in `<agent-store>/store/<creation-name>/`. `--path` selects a different agent directory.

The registry maps each name to its absolute directory. Renaming an agent changes its name and
wrapper, not its directory; no symlinks connect names to paths. Names are 1–64 ASCII letters,
digits, and `_`, and cannot start with a digit. `default` and shell reserved words cannot be agent names.

### Lifecycle

```bash
pg                         # List registered agents
pg scout                   # Show its stored agent document (read-only)
pg --update scout          # Regenerate an existing agent's configuration
pg --update scout --name explorer
pg --export explorer.cfg explorer
pg --remove explorer
```

`--create`, `--update`, `--remove`, `--export FILE`, `--setup`, and `--get-secret NAME` are separate modes.
Creation accepts a persona and harness, or an agent document; existing-agent operations select one
registered name. Export writes a reusable, unfinished agent document, not a token or a resolved
harness settings file. `--get-secret NAME` prints only the fetched credential and a newline; failures,
including keyless agents, use stderr and a nonzero exit status. Its stdout is intentionally sensitive.

For custom endpoints or models, write an agent document rather than defining a variant with
CLI flags:

**orion.cfg**

```yapyon
agent<base.agent>:
  name: "orion"
  secret: None
  persona<base.persona>:
    name: "orion"
    desc: "Orion Toolkit"
    mind:
      endpoint: "https://api.cybertron.space"
      model_list: ["alpha-3-on"]
      model_active: "alpha-3-on"
  harness<claude.harness>: {}
```

```bash
pg --create orion.cfg
```

`secret: None` explicitly makes this example keyless. Remove that setting to inherit the store's
configured default backend, or select a backend and complete entry as described in the
[Custom Agent Guide](CUSTOM_AGENTS.md#credentials-updates--export).
The shipped [agents-example.yaml](agents-example.yaml) is also a single-agent **yapyon** document;
copy it to a `.cfg` or `.tmpl` file before using it with the CLI.

### Parameters Summary

| Parameter | Purpose |
|-----------|---------|
| `--create` | Create one agent from a persona/harness pair or agent document |
| `--update` | Update an existing registered agent |
| `--remove`, `-r` | Remove an agent's registration, wrapper, and generated files |
| `--export FILE` | Export an agent document without installing it |
| `--setup` | Configure the store's default secret backend |
| `--get-secret NAME` | Print NAME's credential to stdout (sensitive; read-only) |
| `--backend NAME` | Select `pass`, `gopass`, or `prs` during setup |
| `--secret-root ROOT` | Set the manager-entry prefix during setup |
| `--name NAME` | Choose a creation name or rename an existing agent |
| `--path DIR` | Select the agent directory |
| `--store DIR` | Select the agent-store root |
| `--no-token` | Make the agent explicitly keyless |
| `--token FILE` | Read a token from a supplied file and store it through the manager |
| `--verify` | Request live credential verification for keyed agents; off by default |
| `--yes` | Confirm removal without prompting |
| `--no-wrapper` | Skip shell-wrapper installation |
| `--force` | Allow export to replace an existing output file |

`--path` is creation-only. Credential-import, verification, and wrapper flags apply to creation/update;
`--token` and `--no-token` are mutually exclusive. Export selects an existing agent only, and cannot
overwrite its installed `<agent-directory>/agent.cfg`. See `pg --help` for mode-specific restrictions.
Removal deletes the registered agent directory, including files you added, and its wrapper. If the
directory is a symlink, only the link is removed, not its target. Shared components and password-manager
entries are retained.

## Agent Store & Configuration

The root is selected by `--store`, otherwise `AGENT_STORE_DIR`, then `$XDG_CONFIG_HOME/agents`,
then `~/.config/agents`. `<agent-store>/agent_store.cfg` contains explicit store-setting overrides
and the name-to-path registry; packaged defaults supply omitted settings. Default credential settings
are `defaults.secret.backend` and `defaults.secret.root`. Installed agents have
`<agent-directory>/agent.cfg` and `<agent-directory>/harness/`.
Composed components used by agents are stored in `<agent-store>/personas/`, `<agent-store>/harnesses/`,
`<agent-store>/dialects/`, and `<agent-store>/secrets/`; the entire bundled template library is not
copied into the store.

Documents use [yapyon](https://pypi.org/project/yapyon/) (version 0.6.5 or newer within the 0.6 series), not YAML:
quote strings, use `None` / `True` / `False`, and write references as `y"{persona.mind.endpoint}"`.
Named slots such as `persona<kimi.persona>` compose presets; `(persona.mind.model_list)` copies
a whole value without converting it to text. Runtime supplies the agent directory, effective store
defaults, and default secret backend; credential operations use each agent's selected backend.
Agent documents cannot set `agent.path`.

Saved documents preserve unfinished slots and expressions, but their comments and layout are
normalized. Safe merges are compacted; necessary slot/composition layers remain explicit. Source
that cannot be saved faithfully is refused rather than written with different semantics.
See the [Config Reference Manual](CONFIG_REFERENCE.md) for fields, store layout, and migration.

### Security

Tokens are held by each agent's selected password manager, not copied into plaintext token files or
`.secret_path` pointers. Generated wrappers fetch the key at launch; arrange for the manager to work
without an unattended passphrase prompt. `--token FILE` is an input source, not a file-token backend.
An explicit keyless agent neither fetches nor stores a key. If no secret setting or default backend is
configured at creation, keyless operation is saved and reported; configuring a default backend later
does not silently give that existing agent credentials. Named and inline backend selections remain explicit.

No live harness or paid-endpoint validation is implied by the bundled presets or offline tests.

## License & Contributing

Persona-Grata is released under the **GNU General Public License v3.0 or later**; see
[LICENSE.md](LICENSE.md).

Contributions generated in whole or in part with AI tools are welcome, subject to the
[project's AI policy](AIPOLICY.md).

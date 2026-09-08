# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.2] — Unreleased

### Added

- `pg` as a shorter alias for the `persona-grata` command.
- `--remove` / `-r` removes an agent: the shell wrapper and the harness's config directory. Once
  every harness for a persona is gone, offers to delete its stored API token. Refuses to delete a
  home or root directory even if a config points there.
- Goose harness preset. `GOOSE_PATH_ROOT` re-roots goose's whole directory tree, so each persona
  gets its own config, data, and state — the same isolation `CODEX_HOME` gives the Codex CLI. The
  config lands at `$GOOSE_PATH_ROOT/config/config.yaml`; the API key stays in the environment,
  which keeps it out of both that file and the shared system keyring.
- Define a persona from the command line, with no config file to write: `--endpoint`, `--model`,
  `--desc`, and `--no-token`. Chiefly for pointing an agent at a locally-run model —
  `pg --endpoint http://localhost:11434/v1 --model llama3 --no-token ollama claude`.
- `--create`, `--update`, and `--export FILE` are three mutually exclusive modes, each failing
  rather than guessing: `--create` writes a new persona and fails if the name is taken,
  `--update` changes an existing one and fails if it does not, and `--export` writes the
  configuration to a file and sets nothing up. `--create` is the default, and worth naming only
  to make a script's intent explicit.
- `--export FILE` loads the persona first, applies any replacements, and writes the result as an
  editable template — so `pg --export mine.yaml kimi` dumps kimi's own settings as a starting
  point. The file is edited as text, leaving comments and formatting intact, and a persona it
  already defines is left as written.
- `--interactive` / `-i` asks for a persona's settings instead of taking them as the flags above,
  then offers to save the result — so an agent can be set up with neither a config file nor a
  command to look up. Not a mode of its own: it supplies only what was not already given, and so
  combines with `--create`, `--update`, and `--export`. A name that is taken or unusable, an
  unknown harness, or an endpoint with no scheme is re-asked rather than fatal.
- `--remove <persona>` now works for a persona that exists in the store but in no config file,
  which is what a command-line definition produces. Everything removal needs derives from the
  persona id, so the flags no longer have to be repeated just to undo a setup. An unknown name
  with nothing in the store is still an error rather than a silent success.
- `agent_desc` harness setting: the display name for an agent, defaulting to
  `<persona>-<harness>` — the same string as the installed wrapper's command, so what setup
  prints is what you type to run it.
- `__AS_YAML__()` serializer, rendering a `config_store` subtree as block-style YAML with keys in
  declaration order.
- `wrapper_env` harness setting: extra environment variables exported by the shell wrapper, for
  harnesses configured by environment rather than by a config file.
- Unknown settings keys are reported as warnings instead of being silently ignored. Persona and
  harness *names* are unrestricted, and `config_store` remains free-form.
- `$$` escapes a literal `$`, so a value may contain text that would otherwise read as an
  environment macro.
- `verify.body` is declared in the harness schema.
- Continuous integration: tests on Python 3.9/3.11/3.13, plus a packaging job that verifies the
  preset library ships in the wheel and that both console scripts run.

### Changed

- The leading argument is treated as a config file only when named `*.yaml` / `*.yml`. Previously
  any existing file matched, so a stray file could turn `pg kimi` into a request to load a config
  named "kimi".

### Removed

- The `disposition` (system prompt) placeholder from the kimi preset; no harness rendered it.

### Fixed

- The harness example in `CONFIG_REFERENCE.md` used a bare `...` line, which is YAML's
  end-of-document marker, so the block could not be parsed as written.
- `CUSTOM_AGENTS.md` now covers adding and removing harnesses: the `None` opt-out, rendering a
  config file from `config_store` with a serializer, and configuring a harness by environment
  with `wrapper_env`.

## [0.0.1] — 2026-08-06

First release.

### Added

- Bind a persona (endpoint, model, and settings) to a harness to produce a runnable agent, with
  shipped presets for the kimi, minimax, and navigator endpoints and the Claude Code and Codex CLI
  harnesses.
- Layered configuration: global defaults, persona defaults and presets, harness defaults and
  presets, then user overrides — most specific wins.
- Template engine with hierarchical `{{...}}` resolution (scope-aware lookup, `__PARENT__` /
  `__KEY__` / `self`), single-pass `$FOO` environment macros, templated dict keys, and
  `__AS_JSON__()` / `__AS_TOML__()` serializers for rendering harness config files.
- API key verification against the endpoint before anything is written; tokens stored with `0600`
  permissions and referenced by path via a `.secret_path` file.
- Shell wrapper installation into `.bashrc` / `.zshrc`, launched as `<persona>-<harness>`.

[0.0.2]: https://github.com/doctorjei/persona-grata/commits/main/
[0.0.1]: https://pypi.org/project/persona-grata/0.0.1/

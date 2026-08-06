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
- Goose harness preset. Goose has no environment variable that relocates its config directory, so
  it is configured entirely through the wrapper's environment rather than a written config file.
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

[0.0.2]: https://github.com/doctorjei/persona-grata/compare/v0.0.1...HEAD
[0.0.1]: https://pypi.org/project/persona-grata/0.0.1/

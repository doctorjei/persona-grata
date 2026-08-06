"""Filesystem side effects of setup_harness: token, config file, shell wrapper."""

import json
import stat
import subprocess

import pytest

import persona_grata as pg


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway HOME + persona store, with the interactive bits stubbed out."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(pg, "_prompt_key", lambda desc: "sk-test-key")
    monkeypatch.setattr(pg, "_verify_key", lambda verify, model, key: None)
    monkeypatch.setattr(pg, "_confirm", lambda question, default=False: default)
    return tmp_path


def config(tmp_path, text):
    path = tmp_path / "agents.yaml"
    path.write_text(text)
    return pg.load_config(str(path))


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


BASIC = """
personas:
  orion:
    persona_desc: "Orion Toolkit"
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
"""

# Same persona, plus a harness the presets know nothing about: no config file,
# and (below) no variable names to export.
WITH_BARE_HARNESS = BASIC + """\
    harnesses:
      bare:
        auth_var: "BARE_KEY"
"""

NO_VARIABLES = BASIC + """\
    harnesses:
      bare:
        path_var: ""
        auth_var: ""
"""


def test_full_setup_writes_every_artifact(home, capsys):
    pg.setup_harness("orion", "claude", config(home, BASIC))

    store = home / ".config" / "personas" / "orion"
    token, secret = store / "token", store / ".secret_path"
    settings = store / "claude" / "settings.json"

    assert token.read_text() == "sk-test-key"
    assert mode(token) == 0o600
    assert secret.read_text() == f"{token}\n"
    assert mode(secret) == 0o600
    assert json.loads(settings.read_text())["model"] == "alpha-3-on"

    rc = (home / ".bashrc").read_text()
    assert "orion-claude() {" in rc
    assert f'CLAUDE_CONFIG_DIR="{store / "claude"}"' in rc
    assert f'ANTHROPIC_AUTH_TOKEN="$(cat {token})"' in rc


def test_wrapper_is_valid_shell(home, tmp_path):
    pg.setup_harness("orion", "claude", config(home, BASIC))
    subprocess.run(["bash", "-n", str(home / ".bashrc")], check=True)


def test_wrapper_omits_unset_variables(home):
    pg.setup_harness("orion", "bare", config(home, NO_VARIABLES))
    rc = (home / ".bashrc").read_text()
    # No name means no assignment -- a bare `="..."` word would break the function.
    assert '="' not in rc
    assert '  command bare "$@"' in rc
    subprocess.run(["bash", "-n", str(home / ".bashrc")], check=True)


def test_harness_without_config_file_is_skipped(home):
    cfg = config(home, WITH_BARE_HARNESS)
    pg.setup_harness("orion", "bare", cfg)          # must not raise IsADirectoryError
    assert (home / ".config" / "personas" / "orion" / "bare").is_dir()
    assert "orion-bare() {" in (home / ".bashrc").read_text()


def test_rerun_replaces_the_block_and_keeps_the_rest(home):
    (home / ".bashrc").write_text("export EXISTING=1\n")
    cfg = config(home, BASIC)
    pg.setup_harness("orion", "claude", cfg)
    pg.setup_harness("orion", "claude", cfg)

    rc = (home / ".bashrc").read_text()
    assert rc.count("orion-claude() {") == 1
    assert "export EXISTING=1" in rc


def test_legacy_description_keyed_block_is_replaced(home):
    (home / ".bashrc").write_text(
        "# >>> Orion Toolkit & Claude Code >>>\n"
        "orion-claude() { command claude \"$@\"; }\n"
        "# <<< Orion Toolkit & Claude Code <<<\n")
    pg.setup_harness("orion", "claude", config(home, BASIC))

    rc = (home / ".bashrc").read_text()
    assert rc.count("orion-claude() {") == 1
    assert "Orion Toolkit & Claude Code >>>" not in rc


def test_zsh_users_get_zshrc(home, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    pg.setup_harness("orion", "claude", config(home, BASIC))
    assert (home / ".zshrc").exists()
    assert not (home / ".bashrc").exists()


def test_existing_token_is_kept(home):
    cfg = config(home, BASIC)
    store = home / ".config" / "personas" / "orion"
    store.mkdir(parents=True)
    (store / "token").write_text("original-key")

    pg.setup_harness("orion", "claude", cfg)        # _confirm stub answers "no"
    assert (store / "token").read_text() == "original-key"


def test_tilde_in_paths_is_expanded(home):
    cfg = config(home, """
personas:
  orion:
    path: "~/agents/orion"
    mind:
      endpoint: "https://api.cybertron.space"
      model: "alpha-3-on"
    harnesses:
      claude:
        path: "~/agents/orion/claude"
""")
    pg.setup_harness("orion", "claude", cfg)

    assert (home / "agents" / "orion" / "token").exists()
    assert (home / "agents" / "orion" / "claude" / "settings.json").exists()
    assert not (home / "~").exists()                # not a literal directory
    assert "~" not in (home / ".bashrc").read_text()


def test_missing_endpoint_is_fatal(home):
    cfg = config(home, "personas:\n  bare_p:\n    mind:\n      model: 'm'\n")
    with pytest.raises(SystemExit):
        pg.setup_harness("bare_p", "claude", cfg)


def test_wrapper_env_is_exported(home):
    # Goose has no relocatable config dir, so it is configured entirely through
    # the wrapper's environment.
    pg.setup_harness("orion", "goose", config(home, BASIC))
    rc = (home / ".bashrc").read_text()
    assert 'GOOSE_PROVIDER="openai"' in rc
    assert 'GOOSE_MODEL="alpha-3-on"' in rc
    assert 'OPENAI_HOST="https://api.cybertron.space"' in rc
    assert 'OPENAI_API_KEY="$(cat ' in rc
    subprocess.run(["bash", "-n", str(home / ".bashrc")], check=True)
    # ...and nothing is written to disk for it.
    assert not list((home / ".config" / "personas" / "orion" / "goose").glob("*"))


def test_wrapper_env_skips_blank_values(home):
    cfg = config(home, """
personas:
  orion:
    mind:
      endpoint: "https://api.cybertron.space"
    harnesses:
      goose:
        wrapper_env:
          GOOSE_PROVIDER: "openai"
          GOOSE_MODEL: ""
""")
    pg.setup_harness("orion", "goose", cfg)
    rc = (home / ".bashrc").read_text()
    assert 'GOOSE_PROVIDER="openai"' in rc
    assert "GOOSE_MODEL" not in rc
    subprocess.run(["bash", "-n", str(home / ".bashrc")], check=True)


def test_config_argument_is_recognised_by_extension_only(home, monkeypatch):
    # A stray file named like a persona must not be read as a config file.
    monkeypatch.chdir(home)
    (home / "kimi").write_text("not a config")
    assert pg._looks_like_config("kimi") is False
    assert pg._looks_like_config("agents.yaml") is True
    assert pg._looks_like_config("a.yml") is True


# --------------------------------------------------------------------------- #
# Removal
# --------------------------------------------------------------------------- #
def test_remove_harness_takes_only_its_own(home):
    (home / ".bashrc").write_text("export KEEPME=1\n")
    cfg = config(home, BASIC)
    pg.setup_harness("orion", "claude", cfg)
    pg.setup_harness("orion", "codex", cfg)

    pg.remove_harness("orion", "codex", cfg)

    store = home / ".config" / "personas" / "orion"
    assert not (store / "codex").exists()
    assert (store / "claude" / "settings.json").exists()   # sibling untouched
    assert (store / "token").exists()                      # token is kept
    rc = (home / ".bashrc").read_text()
    assert "orion-codex() {" not in rc
    assert "orion-claude() {" in rc
    assert "export KEEPME=1" in rc


def test_remove_harness_is_idempotent(home):
    cfg = config(home, BASIC)
    pg.setup_harness("orion", "claude", cfg)
    pg.remove_harness("orion", "claude", cfg)
    pg.remove_harness("orion", "claude", cfg)          # must not raise
    assert not (home / ".config" / "personas" / "orion" / "claude").exists()


def test_remove_persona_store_deletes_the_token(home):
    cfg = config(home, BASIC)
    pg.setup_harness("orion", "claude", cfg)
    store = home / ".config" / "personas" / "orion"
    assert (store / "token").exists()

    pg.remove_persona_store("orion", cfg)
    assert not store.exists()


@pytest.mark.parametrize("target", ["home", "root"])
def test_removal_refuses_home_and_root(home, target, capsys):
    path = str(home) if target == "home" else "/"
    cfg = config(home, f"""
personas:
  bad:
    path: "{path}"
    mind:
      endpoint: "https://x.invalid"
    harnesses:
      claude:
        path: "{path}"
""")
    pg.remove_harness("bad", "claude", cfg)
    assert "refusing to remove" in capsys.readouterr().err
    assert home.is_dir()


def test_cli_remove_offers_the_token_only_when_all_harnesses_go(home, monkeypatch):
    prompts = []
    monkeypatch.setattr(pg, "_confirm", lambda q, default=False: prompts.append(q) or False)
    path = home / "agents.yaml"
    path.write_text(BASIC)

    def token_offers():
        return [q for q in prompts if "stored API token" in q]

    pg.main([str(path), "orion"])                       # install both
    pg.main(["--remove", str(path), "orion", "codex"])  # partial -> no offer
    assert token_offers() == []

    pg.main(["-r", str(path), "orion"])                 # the rest -> offer
    assert len(token_offers()) == 1


def test_unknown_option_is_rejected(home):
    with pytest.raises(SystemExit):
        pg.main(["--bogus", "kimi"])

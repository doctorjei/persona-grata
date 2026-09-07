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
    # A harness with no relocatable config dir is configured entirely through
    # the wrapper's environment.
    pg.setup_harness("orion", "bare", config(home, BASIC + """\
    harnesses:
      bare:
        auth_var: "BARE_KEY"
        wrapper_env:
          BARE_PROVIDER: "openai"
          BARE_MODEL: "{{mind.model}}"
          BARE_HOST: "{{base_uri}}"
"""))
    rc = (home / ".bashrc").read_text()
    assert 'BARE_PROVIDER="openai"' in rc
    assert 'BARE_MODEL="alpha-3-on"' in rc
    assert 'BARE_HOST="https://api.cybertron.space"' in rc
    assert 'BARE_KEY="$(cat ' in rc
    subprocess.run(["bash", "-n", str(home / ".bashrc")], check=True)
    # ...and nothing is written to disk for it.
    assert not list((home / ".config" / "personas" / "orion" / "bare").glob("*"))


def test_goose_isolates_its_whole_directory_tree(home):
    # Goose is relocated wholesale via GOOSE_PATH_ROOT, so it gets the same
    # treatment as codex/claude rather than being configured from the wrapper.
    import yaml
    pg.setup_harness("orion", "goose", config(home, BASIC))
    root = home / ".config" / "personas" / "orion" / "goose"

    rc = (home / ".bashrc").read_text()
    assert f'GOOSE_PATH_ROOT="{root}"' in rc
    assert 'OPENAI_API_KEY="$(cat ' in rc
    subprocess.run(["bash", "-n", str(home / ".bashrc")], check=True)

    # Goose appends its own "config" segment under the root it is given.
    written = yaml.safe_load((root / "config" / "config.yaml").read_text())
    assert written == {
        "GOOSE_PROVIDER": "openai",
        "GOOSE_MODEL": "alpha-3-on",
        "OPENAI_HOST": "https://api.cybertron.space",
    }


def test_goose_path_root_is_absolute(home):
    # Goose silently ignores a relative GOOSE_PATH_ROOT and falls back to the
    # shared ~/.config/goose, which would defeat the isolation entirely.
    cfg = config(home, BASIC)
    assert cfg["personas"]["orion"]["harnesses"]["goose"]["path"].startswith("/")


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


# --------------------------------------------------------------------------- #
# Defining a persona on the command line
# --------------------------------------------------------------------------- #
LOCAL_FLAGS = ["--endpoint", "http://localhost:11434/v1", "--model", "llama3", "--no-token"]


def test_flags_define_a_persona_with_no_config_file(home):
    pg.main(LOCAL_FLAGS + ["ollama", "claude"])
    settings = json.loads(
        (home / ".config/personas/ollama/claude/settings.json").read_text())
    assert settings["model"] == "llama3"
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://localhost:11434/v1"
    assert "ollama-claude" in (home / ".bashrc").read_text()


def test_no_token_skips_the_key_prompt_and_its_verification(home, monkeypatch):
    # A local endpoint usually has no key to paste and nothing to verify against.
    def fail(*a, **kw):
        raise AssertionError("should not prompt or verify")
    monkeypatch.setattr(pg, "_prompt_key", fail)
    monkeypatch.setattr(pg, "_verify_key", fail)

    pg.main(LOCAL_FLAGS + ["ollama", "claude"])
    assert not (home / ".config/personas/ollama/token").exists()


def test_agent_desc_defaults_to_the_wrapper_command_name(home):
    # The agent's display name and the command you type to run it should be the
    # same string; anything else means the setup notes tell you the wrong thing.
    cfg = config(home, BASIC)
    for hid in ("claude", "codex", "goose"):
        assert cfg["personas"]["orion"]["harnesses"][hid]["agent_desc"] == f"orion-{hid}"


def test_agent_desc_is_overridable_per_harness(home):
    cfg = config(home, BASIC + """\
    harnesses:
      claude:
        agent_desc: "Orion (chat)"
""")
    harnesses = cfg["personas"]["orion"]["harnesses"]
    assert harnesses["claude"]["agent_desc"] == "Orion (chat)"
    assert harnesses["codex"]["agent_desc"] == "orion-codex"      # others unaffected


def test_setup_announces_the_agent_by_its_command_name(home, capsys):
    pg.setup_harness("orion", "claude", config(home, BASIC))
    out = capsys.readouterr().out
    assert "orion-claude Setup Script" in out
    assert "To run orion-claude" in out


def test_a_store_only_persona_is_removable_by_name(home):
    # Defined by flags, so it exists in the store but in no config file. Removal
    # needs only paths, which derive from the id -- so it must not need the flags
    # repeated just to undo itself.
    pg.main(LOCAL_FLAGS + ["ollama", "claude"])
    assert (home / ".config/personas/ollama/claude").is_dir()

    pg.main(["--remove", "ollama", "claude"])
    assert not (home / ".config/personas/ollama/claude").exists()
    assert "ollama-claude" not in (home / ".bashrc").read_text()


def test_removing_a_persona_that_was_never_set_up_still_fails(home):
    # The store is what makes the rebuild legitimate; without it, a name that
    # resolves to nothing is a typo and should say so.
    with pytest.raises(SystemExit):
        pg.main(["--remove", "never-existed", "claude"])


def test_a_definition_is_ephemeral_unless_exported(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    pg.main([str(path)] + LOCAL_FLAGS + ["ollama", "claude"])

    assert "ollama" not in path.read_text()          # nothing written down
    with pytest.raises(SystemExit):                  # ...so it is gone next run
        pg.main([str(path), "ollama", "claude"])


def test_flags_override_an_existing_persona_without_clobbering_it(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    cfg = pg.load_config(str(path),
                         overrides={"personas": {"orion": {"mind": {"model": "swapped"}}}})
    mind = cfg["personas"]["orion"]["mind"]
    assert mind["model"] == "swapped"                # the flag won
    assert mind["endpoint"] == "https://api.cybertron.space"    # the rest survived


def test_inline_and_separated_flag_values_agree(home):
    inline, *_, one = pg._extract_options(["--model=m", "p"])
    spaced, *_, two = pg._extract_options(["--model", "m", "p"])
    assert inline == spaced == ["p"]
    assert one == two == {"mind": {"model": "m"}}


def test_export_path_is_taken_by_flag_not_position(home):
    positionals, *_, export_path, definition = pg._extract_options(
        ["--endpoint", "http://x", "--export", "out.yaml", "p", "claude"])
    assert positionals == ["p", "claude"]
    assert export_path == "out.yaml"
    assert definition == {"mind": {"endpoint": "http://x"}}


def test_a_definition_needs_a_persona_to_name(home):
    with pytest.raises(SystemExit):
        pg.main(["--model", "m"])


def test_export_needs_a_persona_to_export(home):
    with pytest.raises(SystemExit):
        pg.main(["--export", str(home / "out.yaml")])


def test_export_loads_an_existing_persona_then_applies_replacements(home):
    import yaml
    out = home / "out.yaml"
    pg.main(["--model", "swapped", "--no-token",
             "--export", str(out), "kimi", "codex"])
    exported = yaml.safe_load(out.read_text())["personas"]["kimi"]

    assert exported["mind"]["model"] == "swapped"                 # the flag applied
    assert exported["mind"]["endpoint"] == "https://api.moonshot.ai"   # preset kept
    assert exported["mind"]["model_1"] == "kimi-k2.7-code"             # and the rest
    assert exported["persona_desc"] == "Kimi"


def test_export_carries_the_presets_harness_settings(home):
    import yaml
    out = home / "out.yaml"
    pg.main(["--no-token", "--export", str(out), "kimi", "codex"])
    exported = yaml.safe_load(out.read_text())["personas"]["kimi"]
    # kimi's preset points Claude Code at a different base_uri; a template that
    # dropped it would not reproduce the agent it claims to describe.
    assert exported["harnesses"]["claude"]["base_uri"] == "{{mind.endpoint}}/anthropic"


def test_export_of_an_untouched_persona_needs_no_flags(home):
    import yaml
    path = home / "agents.yaml"
    path.write_text("personas:\n  lab:\n    token: None\n"
                    "    mind:\n      endpoint: 'http://localhost:9000/v1'\n"
                    "      model: 'mistral'\n")
    out = home / "out.yaml"
    pg.main([str(path), "--export", str(out), "lab", "claude"])
    exported = yaml.safe_load(out.read_text())["personas"]["lab"]
    assert exported["mind"] == {"endpoint": "http://localhost:9000/v1", "model": "mistral"}


def test_redefining_an_existing_persona_needs_update(home):
    # kimi is a shipped preset, so this name is already taken. Silently
    # replacing it would be an easy way to lose a working agent.
    with pytest.raises(SystemExit):
        pg.main(["--model", "swapped", "--no-token", "kimi", "codex"])


def test_update_allows_replacing_an_existing_persona(home):
    tomllib = pytest.importorskip("tomllib")
    pg.main(["--update", "--model", "swapped", "--no-token", "kimi", "codex"])
    written = tomllib.loads(
        (home / ".config/personas/kimi/codex/config.toml").read_text())
    assert written["model"] == "swapped"


def test_update_is_not_needed_for_a_new_name(home):
    pg.main(LOCAL_FLAGS + ["ollama", "claude"])       # no --update
    assert (home / ".config/personas/ollama/claude/settings.json").exists()


def test_update_requires_the_persona_to_exist(home):
    # --update only updates: naming something that isn't there is a mistake,
    # not an invitation to create it.
    with pytest.raises(SystemExit):
        pg.main(["--update", "--model", "m", "--no-token", "nosuch", "claude"])


def test_export_sets_nothing_up(home):
    # Export is the whole operation, not a step appended to a setup.
    pg.main(["--export", str(home / "out.yaml"), "kimi", "codex"])
    assert (home / "out.yaml").exists()
    assert not (home / ".config" / "personas").exists()
    assert not (home / ".bashrc").exists()


@pytest.mark.parametrize("flag", ["--update", "--remove", "--create"])
def test_export_is_exclusive_of_the_other_modes(home, flag):
    with pytest.raises(SystemExit):
        pg.main([flag, "--export", str(home / "out.yaml"), "kimi", "codex"])


@pytest.mark.parametrize("pair", [("--create", "--update"), ("--create", "--remove"),
                                  ("--update", "--remove")])
def test_modes_are_mutually_exclusive(home, pair):
    with pytest.raises(SystemExit):
        pg.main([*pair, "--no-token", "kimi", "codex"])


def test_create_is_the_default_spelled_out(home):
    # --create only makes the default explicit, so that a script meaning
    # "create" cannot silently update instead.
    pg.main(["--create"] + LOCAL_FLAGS + ["ollama", "claude"])
    assert (home / ".config/personas/ollama/claude/settings.json").exists()


def test_create_refuses_a_taken_name_even_with_no_replacements(home):
    with pytest.raises(SystemExit):
        pg.main(["--create", "kimi", "claude"])


COMMENTED = """\
# A hand-written config; the comments are the point.
personas:
  orion:                                   # already here
    mind:
      endpoint: "https://api.cybertron.space"

# trailing comment, after the personas block
"""


def test_export_records_the_persona_and_preserves_the_file(home):
    path = home / "agents.yaml"
    path.write_text(COMMENTED)
    pg.main(LOCAL_FLAGS + ["--export", str(path), "ollama", "claude"])
    text = path.read_text()

    # Comments survive: the file is edited as text, not re-serialized.
    assert "# A hand-written config; the comments are the point." in text
    assert "# already here" in text
    assert "# trailing comment, after the personas block" in text

    # The block lands *inside* personas, not appended past the end of the file.
    reloaded = pg.load_config(str(path))["personas"]
    assert reloaded["ollama"]["mind"]["model"] == "llama3"
    assert reloaded["orion"]["mind"]["endpoint"] == "https://api.cybertron.space"


def test_export_writes_the_schema_placeholder_for_unset(home):
    path = home / "agents.yaml"
    path.write_text(COMMENTED)
    pg.main(LOCAL_FLAGS + ["--export", str(path), "ollama", "claude"])

    assert "token: None" in path.read_text()         # not YAML's `null`
    assert pg.load_config(str(path))["personas"]["ollama"]["token"] is None


def test_export_leaves_an_already_defined_persona_alone(home, capsys):
    path = home / "agents.yaml"
    path.write_text(COMMENTED)
    pg.main([str(path), "--model", "nope", "--export", str(path), "orion", "claude"])

    assert "nope" not in path.read_text()
    assert "already defined" in capsys.readouterr().out

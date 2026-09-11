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
# and (below) no variable names to export. `supported_dialects` is required of
# any harness, hand-written ones included -- it is what gives it a URI to use
# and a way to check the key.
WITH_BARE_HARNESS = BASIC + """\
    harnesses:
      bare:
        supported_dialects: ["chat"]
        auth_var: "BARE_KEY"
"""

NO_VARIABLES = BASIC + """\
    harnesses:
      bare:
        supported_dialects: ["chat"]
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


def test_a_schemeless_endpoint_is_fatal(home):
    # Every harness issues HTTP against the endpoint once installed, so a bare
    # host is an unusable agent rather than merely an unverifiable one. It also
    # used to reach urllib as '127.0.0.1/v1/messages' and raise ValueError.
    cfg = config(home, "personas:\n  bare_p:\n    mind:\n      endpoint: '127.0.0.1'\n")
    with pytest.raises(SystemExit) as exit_info:
        pg.setup_harness("bare_p", "claude", cfg)
    assert "no scheme" in str(exit_info.value)
    assert not (home / ".bashrc").exists()


def test_verification_follows_the_negotiated_protocol(home, monkeypatch):
    # How to check a key is a property of the protocol, so the settings come
    # from the dialect the pairing negotiated -- not from the harness. This is
    # the bug it fixes: codex used to verify against /v1/chat/completions while
    # telling codex to speak Responses, so against a chat-only endpoint the key
    # check passed and the agent still failed.
    seen = {}
    monkeypatch.setattr(pg, "_verify_key",
                        lambda verify, model, key: seen.update({key: verify}))
    cfg = config(home, BASIC)

    pg.setup_harness("orion", "codex", cfg)
    assert seen["sk-test-key"]["check_uri"] == "https://api.cybertron.space/v1/responses"
    assert '"input": "ping"' in seen["sk-test-key"]["body"]   # Responses' shape, not messages'

    seen.clear()
    monkeypatch.setattr(pg, "_confirm", lambda question, default=False: True)
    pg.setup_harness("orion", "claude", cfg)
    verify = seen["sk-test-key"]
    assert verify["check_uri"] == "https://api.cybertron.space/v1/messages"
    assert verify["headers"] == ["anthropic-version: 2023-06-01"]


def test_an_unusable_verify_url_is_not_fatal(capsys):
    # A hand-written verify url bypasses the endpoint guard, so a malformed one
    # must degrade like any other unreachable host rather than traceback. No
    # `home` fixture here: this needs the real _verify_key, not its stub.
    pg._verify_key({"check_uri": "127.0.0.1/v1/messages"}, "m", "sk-test")
    assert "unreachable" in capsys.readouterr().err


def test_wrapper_env_is_exported(home):
    # A harness with no relocatable config dir is configured entirely through
    # the wrapper's environment.
    pg.setup_harness("orion", "bare", config(home, BASIC + """\
    harnesses:
      bare:
        supported_dialects: ["chat"]
        auth_var: "BARE_KEY"
        wrapper_env:
          BARE_PROVIDER: "openai"
          BARE_MODEL: "{{mind.model}}"
          BARE_HOST: "{{mind.dialects[protocol].api_uri}}"
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


def test_bare_invocation_asks_for_a_persona(home):
    # The shipped presets are a library to choose from: with no config file and
    # no name there is no intent to act on, and installing every one of them
    # would wire up endpoints the user has no account on and local servers they
    # are not running.
    with pytest.raises(SystemExit) as exit_info:
        pg.main([])
    assert "no persona named" in str(exit_info.value)
    assert "kimi" in str(exit_info.value)          # ...but say what is on offer
    assert not (home / ".bashrc").exists()
    assert not (home / ".config" / "personas").exists()


def test_a_config_file_still_means_all_of_its_personas(home):
    # A file *is* an expressed intent, so the "set up everything" path stays.
    path = home / "agents.yaml"
    path.write_text(BASIC + """\
  vector:
    mind:
      endpoint: "https://api.cybertron.space"
""")
    pg.main([str(path)])
    assert (home / ".config/personas/orion/claude").is_dir()
    assert (home / ".config/personas/vector/claude").is_dir()
    # ...and only those: the shipped presets are not dragged in with them.
    assert not (home / ".config/personas/kimi").exists()


def test_cli_sets_up_the_harnesses_that_work_and_names_the_ones_it_skips(home, capsys):
    # Naming no harness asks for whatever this persona has, so a pairing the
    # endpoint cannot serve is left out rather than failing the whole command.
    path = home / "agents.yaml"
    path.write_text("""
personas:
  legacy_box:
    token: None
    mind:
      endpoint: "https://api.cybertron.space"
      dialects:
        anthropic: None
        responses: None
""")
    pg.main([str(path), "legacy_box"])
    store = home / ".config" / "personas" / "legacy_box"
    assert (store / "codex").is_dir() and (store / "goose").is_dir()
    assert not (store / "claude").exists()
    assert "skipping legacy_box-claude" in capsys.readouterr().err

    rc = (home / ".bashrc").read_text()
    assert "legacy_box-codex() {" in rc and "legacy_box-claude() {" not in rc

    # Naming it explicitly is a different question, and gets a different answer.
    with pytest.raises(SystemExit) as exit_info:
        pg.main([str(path), "legacy_box", "claude"])
    assert "cannot be used with persona" in str(exit_info.value)


def test_a_designation_names_one_agent(home):
    # `persona+harness` is an agent's structural identifier, and also its default
    # name -- so it resolves without a registry, the two halves being right there.
    path = home / "agents.yaml"
    path.write_text(BASIC)
    pg.main([str(path), "orion+claude"])

    store = home / ".config" / "personas" / "orion"
    assert (store / "claude").is_dir()
    assert not (store / "codex").exists()        # only the one it named
    # The wrapper is still the shell-safe rendering: a function name cannot
    # contain '+'.
    assert "orion-claude() {" in (home / ".bashrc").read_text()


def test_a_designation_removes_the_agent_it_names(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    pg.main([str(path), "orion"])                # every harness
    pg.main(["-r", str(path), "orion+codex"])    # one of them

    assert not (home / ".config" / "personas" / "orion" / "codex").exists()
    rc = (home / ".bashrc").read_text()
    assert "orion-codex() {" not in rc and "orion-claude() {" in rc


def test_a_designation_already_names_its_harness(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    with pytest.raises(SystemExit) as exit_info:
        pg.main([str(path), "orion+claude", "codex"])
    assert "already names a harness" in str(exit_info.value)


@pytest.mark.parametrize("token", ["orion+", "+claude", "a+b+c", "kimi.k3+claude"],
                         ids=["no-harness", "no-persona", "two-separators", "dotted"])
def test_a_malformed_designation_says_which_half_is_wrong(home, token):
    with pytest.raises(SystemExit) as exit_info:
        pg.main([token])
    message = str(exit_info.value)
    assert f"designation '{token}'" in message
    assert "persona name" in message or "harness name" in message


# --------------------------------------------------------------------------- #
# Chosen agent names
# --------------------------------------------------------------------------- #
def names_file(home):
    return home / ".config" / "personas" / "agent_names.yaml"


def test_a_name_replaces_the_command_but_not_the_marker(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    pg.main(["--name", "scout", str(path), "orion+claude"])

    rc = (home / ".bashrc").read_text()
    # The function bears the NAME...
    assert "scout() {" in rc
    assert "orion-claude() {" not in rc
    # ...and the marker the DESIGNATION, which is what makes renaming safe.
    assert "# >>> persona-grata: orion-claude >>>" in rc
    assert "scout: orion+claude" in names_file(home).read_text()
    subprocess.run(["bash", "-n", str(home / ".bashrc")], check=True)


def test_renaming_leaves_exactly_one_wrapper(home):
    # The reason markers stay keyed on the designation: the old block has to be
    # found and replaced even though the command it installed has changed.
    path = home / "agents.yaml"
    path.write_text(BASIC)
    pg.main(["--name", "scout", str(path), "orion+claude"])
    pg.main(["--name", "pathfinder", str(path), "orion+claude"])

    rc = (home / ".bashrc").read_text()
    assert "pathfinder() {" in rc
    assert "scout() {" not in rc
    assert rc.count("# >>> persona-grata: orion-claude >>>") == 1
    # One name per agent, so the old entry is gone rather than accumulating.
    assert "scout" not in names_file(home).read_text()


def test_a_name_is_accepted_wherever_a_designation_is(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    pg.main(["--name", "scout", str(path), "orion+codex"])
    pg.main(["-r", str(path), "scout"])

    assert not (home / ".config" / "personas" / "orion" / "codex").exists()
    assert "scout" not in names_file(home).read_text()          # released
    assert "scout() {" not in (home / ".bashrc").read_text()


def test_setting_up_by_name_keeps_the_name(home):
    # Re-running setup is not a request to rename.
    path = home / "agents.yaml"
    path.write_text(BASIC)
    pg.main(["--name", "scout", str(path), "orion+claude"])
    pg.main([str(path), "scout"])

    assert "scout: orion+claude" in names_file(home).read_text()
    assert "scout() {" in (home / ".bashrc").read_text()


def test_a_name_and_a_persona_id_cannot_collide_either_way(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    pg.main(["--name", "scout", str(path), "orion+claude"])

    # A name that is already a persona, refused when it is chosen...
    with pytest.raises(SystemExit) as chosen:
        pg.main(["--name", "orion", str(path), "orion+codex"])
    assert "already a persona" in str(chosen.value)

    # ...and a persona defined under a name that is already taken.
    with pytest.raises(SystemExit) as defined:
        pg.main(["--endpoint", "http://x.test", "--no-token", str(path), "scout", "claude"])
    assert "is the name of the agent" in str(defined.value)


def test_a_name_belongs_to_one_agent(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    with pytest.raises(SystemExit) as exit_info:
        pg.main(["--name", "scout", str(path), "orion"])      # every harness
    assert "names one agent" in str(exit_info.value)

    with pytest.raises(SystemExit) as also:
        pg.main(["--name", "scout", str(path), "orion+claude", "codex"])
    assert "already names a harness" in str(also.value)


def test_a_name_is_held_to_the_shared_grammar(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    with pytest.raises(SystemExit) as exit_info:
        pg.main(["--name", "my scout", str(path), "orion+claude"])
    assert "agent name" in str(exit_info.value)


def test_name_contradicts_the_modes_that_set_nothing_up(home):
    path = home / "agents.yaml"
    path.write_text(BASIC)
    for argv in (["--name", "x", "--remove", str(path), "orion+claude"],
                 ["--name", "x", "--export", str(home / "o.yaml"), "orion"]):
        with pytest.raises(SystemExit) as exit_info:
            pg.main(argv)
        assert "--name names an agent being set up" in str(exit_info.value)


def test_an_unreadable_name_registry_is_not_fatal(home, capsys):
    # It is runtime state beside the persona dirs, so a user can corrupt it.
    # Losing a chosen name is survivable; refusing to run is not.
    path = home / "agents.yaml"
    path.write_text(BASIC)
    names = names_file(home)
    names.parent.mkdir(parents=True, exist_ok=True)
    names.write_text("this: [is not: valid yaml\n")

    pg.main([str(path), "orion+claude"])
    assert "orion-claude() {" in (home / ".bashrc").read_text()
    assert "cannot read" in capsys.readouterr().err


def test_a_persona_id_cannot_climb_out_of_the_store(home):
    # A persona id becomes a directory, so `..` used to land the harness config
    # in <persona_store>/../claude/ -- outside the store, where --remove would
    # then have aimed rmtree. kanibako dropped `.` from the same grammar on
    # 2026-08-04 for this reason; the charset is now shared.
    with pytest.raises(SystemExit) as exit_info:
        pg.main(["--endpoint", "http://127.0.0.1:1", "--model", "m", "--no-token",
                 "..", "claude"])
    assert "persona name" in str(exit_info.value)
    assert not (home / ".config" / "claude").exists()
    assert not (home / ".bashrc").exists()


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
    positionals, *_, export_path, agent_name, definition = pg._extract_options(
        ["--endpoint", "http://x", "--export", "out.yaml", "p", "claude"])
    assert agent_name is None
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


def test_export_carries_the_presets_placement_and_harness_settings(home):
    import yaml
    out = home / "out.yaml"
    pg.main(["--no-token", "--export", str(out), "kimi", "codex"])
    exported = yaml.safe_load(out.read_text())["personas"]["kimi"]
    # kimi mounts Anthropic somewhere other than its endpoint root; a template
    # that dropped that would not reproduce the agent it claims to describe.
    assert exported["mind"]["dialects"]["anthropic"]["api_uri"] == "{{endpoint}}/anthropic"

    # A preset's genuine per-harness settings still export from `harnesses`.
    other = home / "minimax.yaml"
    pg.main(["--no-token", "--export", str(other), "minimax"])
    minimax = yaml.safe_load(other.read_text())["personas"]["minimax"]
    env = minimax["harnesses"]["claude"]["config_store"]["env"]
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "1000000"


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


# --------------------------------------------------------------------------- #
# Supplying the key from a file
# --------------------------------------------------------------------------- #
def key_file(home, text="sk-from-a-file\n"):
    path = home / "moonshot.key"
    path.write_text(text)
    return str(path)


def test_token_file_is_used_instead_of_prompting(home, monkeypatch):
    def fail(*a, **kw):
        raise AssertionError("should not prompt for a key")
    monkeypatch.setattr(pg, "_prompt_key", fail)

    pg.main(["--token", key_file(home), "kimi", "claude"])
    stored = home / ".config/personas/kimi/token"
    # Stripped: a key file conventionally ends in a newline, and the wrapper
    # would otherwise store one inside the token.
    assert stored.read_text() == "sk-from-a-file"
    assert mode(stored) == 0o600


def test_token_file_is_verified_like_a_typed_one(home, monkeypatch):
    seen = []
    monkeypatch.setattr(pg, "_verify_key",
                        lambda verify, model, key: seen.append(key))
    pg.main(["--token", key_file(home), "kimi", "claude"])
    assert seen == ["sk-from-a-file"]


def test_token_file_replaces_an_existing_token_without_asking(home, monkeypatch):
    # Naming a file is already an explicit instruction to set the token, so the
    # "replace?" guard would only be an unanswerable prompt in a script.
    def fail(question, default=False):
        raise AssertionError(f"should not ask: {question}")
    store = home / ".config/personas/kimi"
    store.mkdir(parents=True)
    (store / "token").write_text("stale-key")
    monkeypatch.setattr(pg, "_confirm", fail)

    pg.main(["--token", key_file(home), "kimi", "claude"])
    assert (store / "token").read_text() == "sk-from-a-file"


def test_token_file_leaves_the_source_alone(home):
    pg.main(["--token", key_file(home), "kimi", "claude"])
    assert (home / "moonshot.key").read_text() == "sk-from-a-file\n"


def test_token_file_expands_a_tilde(home):
    (home / "keys").mkdir()
    (home / "keys" / "k").write_text("sk-tilde")
    pg.main(["--token", "~/keys/k", "kimi", "claude"])
    assert (home / ".config/personas/kimi/token").read_text() == "sk-tilde"


@pytest.mark.parametrize("text", ["", "   \n\n"])
def test_an_empty_key_file_is_rejected(home, text):
    with pytest.raises(SystemExit):
        pg.main(["--token", key_file(home, text), "kimi", "claude"])


def test_a_missing_key_file_fails_before_anything_is_written(home):
    with pytest.raises(SystemExit):
        pg.main(["--token", str(home / "nope.key"), "kimi", "claude"])
    assert not (home / ".config" / "personas").exists()
    assert not (home / ".bashrc").exists()


def test_token_file_and_no_token_contradict(home):
    with pytest.raises(SystemExit):
        pg.main(["--token", key_file(home), "--no-token", "--update", "kimi", "claude"])


@pytest.mark.parametrize("flag", ["--remove", "--export"])
def test_token_file_is_rejected_where_no_key_is_stored(home, flag):
    args = ["--token", key_file(home), flag]
    if flag == "--export":
        args.append(str(home / "out.yaml"))
    with pytest.raises(SystemExit):
        pg.main(args + ["kimi", "claude"])


def test_a_persona_with_no_token_path_rejects_a_key_file(home):
    # token: None and --token contradict each other just as --no-token does;
    # silently discarding the key would be the worst of the three outcomes.
    path = home / "agents.yaml"
    path.write_text("personas:\n  lab:\n    token: None\n"
                    "    mind:\n      endpoint: 'http://localhost:9000/v1'\n")
    with pytest.raises(SystemExit):
        pg.main(["--token", key_file(home), str(path), "lab", "claude"])


def test_token_file_defines_and_keys_a_persona_in_one_go(home, monkeypatch):
    monkeypatch.setattr(pg, "_verify_key", lambda verify, model, key: None)
    pg.main(["--endpoint", "https://api.cybertron.space", "--model", "alpha-3-on",
             "--token", key_file(home), "orion", "claude"])
    assert (home / ".config/personas/orion/token").read_text() == "sk-from-a-file"
    assert (home / ".config/personas/orion/claude/settings.json").exists()


def test_token_file_is_not_a_definition(home):
    # It supplies a key rather than changing a setting, so it must not drag an
    # existing persona into needing --update.
    positionals, *_, key, export_path, agent_name, definition = pg._extract_options(
        ["--token", "/k", "kimi", "claude"])
    assert positionals == ["kimi", "claude"]
    assert (key, export_path, agent_name, definition) == ("/k", None, None, {})


# --------------------------------------------------------------------------- #
# Defining a persona interactively
# --------------------------------------------------------------------------- #
class Interview:
    """Scripted answers for the interactive prompts.

    ``replies`` feed ``_ask`` in order, with a blank one taking the offered
    default just as a real Enter would; ``confirms`` maps a fragment of a yes/no
    question to its answer, so a test only says what it actually cares about.
    """

    def __init__(self, replies=(), confirms=None):
        self.replies = list(replies)
        self.confirms = confirms or {}
        self.asked, self.confirmed = [], []

    def install(self, monkeypatch):
        monkeypatch.setattr(pg, "_ask", self._ask)
        monkeypatch.setattr(pg, "_confirm", self._confirm)
        return self

    def _ask(self, question, default=None):
        self.asked.append(question)
        reply = self.replies.pop(0) if self.replies else ""
        return default if reply == "" and default is not None else reply

    def _confirm(self, question, default=False):
        self.confirmed.append(question)
        for fragment, answer in self.confirms.items():
            if fragment in question:
                return answer
        return default


HARNESS_QUESTION = "Harnesses (claude codex goose)"
LOCAL_ANSWERS = ["ollama", "Local Llama", "http://localhost:11434/v1", "llama3"]


def test_interactive_defines_a_persona_from_answers(home, monkeypatch):
    # The headline case: no config file, no flags, nothing but answers.
    Interview(LOCAL_ANSWERS + ["claude"],
              {"API key": False, "config file": False}).install(monkeypatch)
    pg.main(["-i"])

    settings = json.loads(
        (home / ".config/personas/ollama/claude/settings.json").read_text())
    assert settings["model"] == "llama3"
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://localhost:11434/v1"
    assert "ollama-claude" in (home / ".bashrc").read_text()
    assert not (home / ".config/personas/ollama/token").exists()   # no key wanted


def test_interactive_does_not_re_ask_what_the_flags_gave(home, monkeypatch):
    # The interview and the flags are two front ends to one definition, so a
    # value supplied either way is settled and must not be asked about again.
    interview = Interview(["ollama", "claude"], {"config file": False}).install(monkeypatch)
    pg.main(["-i", "--endpoint", "http://localhost:11434/v1", "--model", "llama3",
             "--desc", "Local Llama", "--no-token"])

    assert interview.asked == ["Persona name", HARNESS_QUESTION]
    assert not [q for q in interview.confirmed if "API key" in q]
    settings = json.loads(
        (home / ".config/personas/ollama/claude/settings.json").read_text())
    assert settings["model"] == "llama3"


def test_interactive_update_defaults_to_the_existing_settings(home, monkeypatch):
    tomllib = pytest.importorskip("tomllib")
    # Blank answers for the description and endpoint; only the model is changed.
    Interview(["", "", "swapped"], {"API key": True}).install(monkeypatch)
    pg.main(["-i", "--update", "kimi", "codex"])

    written = tomllib.loads(
        (home / ".config/personas/kimi/codex/config.toml").read_text())
    provider = written["model_providers"]["codex"]
    assert written["model"] == "swapped"                       # the answer applied
    assert provider["base_url"] == "https://api.moonshot.ai"   # preset kept
    assert provider["name"] == "Kimi"                          # ...and the rest


def test_interactive_can_switch_a_token_back_on(home, monkeypatch):
    path = home / "agents.yaml"
    path.write_text("personas:\n  lab:\n    token: None\n"
                    "    mind:\n      endpoint: 'http://localhost:9000/v1'\n")
    Interview(["", "", "mistral"], {"API key": True}).install(monkeypatch)
    pg.main(["-i", "--update", str(path), "lab", "claude"])

    # Answering "yes" to a persona that had none restores the schema's location
    # rather than leaving it switched off.
    assert (home / ".config/personas/lab/token").read_text() == "sk-test-key"


def test_declining_the_summary_sets_and_saves_nothing(home, monkeypatch):
    # The save is offered before the final confirmation, so declining has to
    # cancel that too -- a definition is only written down once it has been used.
    out = home / "mine.yaml"
    Interview(LOCAL_ANSWERS + ["claude", str(out)],
              {"API key": False, "config file": True, "Set up": False}).install(monkeypatch)

    assert pg.main(["-i"]) == 0
    assert not out.exists()
    assert not (home / ".config" / "personas").exists()
    assert not (home / ".bashrc").exists()


def test_interactive_offers_to_save_the_definition(home, monkeypatch):
    out = home / "mine.yaml"
    Interview(LOCAL_ANSWERS + ["claude", str(out)],
              {"API key": False, "config file": True}).install(monkeypatch)
    pg.main(["-i"])

    saved = pg.load_config(str(out))["personas"]["ollama"]
    assert saved["mind"]["endpoint"] == "http://localhost:11434/v1"
    assert saved["mind"]["model"] == "llama3"
    assert saved["persona_desc"] == "Local Llama"
    assert saved["token"] is None
    assert (home / ".config/personas/ollama/claude").is_dir()      # set up as well


def test_interactive_export_writes_the_config_and_sets_nothing_up(home, monkeypatch):
    out = home / "out.yaml"
    interview = Interview(LOCAL_ANSWERS, {"API key": False}).install(monkeypatch)
    pg.main(["-i", "--export", str(out)])

    assert pg.load_config(str(out))["personas"]["ollama"]["mind"]["model"] == "llama3"
    assert HARNESS_QUESTION not in interview.asked      # nothing is being set up
    assert not [q for q in interview.confirmed if "config file" in q]   # nor re-offered
    assert not (home / ".config" / "personas").exists()
    assert not (home / ".bashrc").exists()


def test_interactive_rejects_a_taken_name_before_asking_anything(home, monkeypatch):
    # The mode check comes first, so a doomed run does not conduct an interview
    # only to throw the answers away.
    interview = Interview().install(monkeypatch)
    with pytest.raises(SystemExit):
        pg.main(["-i", "kimi", "claude"])
    assert interview.asked == []


def test_interactive_re_asks_an_unusable_persona_name(home, monkeypatch):
    # A name it can still correct is re-asked, not fatal: "my agent" would not
    # survive as a directory or a shell function, and "kimi" is taken.
    interview = Interview(["my agent", "kimi"] + LOCAL_ANSWERS + ["claude"],
                          {"API key": False}).install(monkeypatch)
    pg.main(["-i"])

    assert interview.asked.count("Persona name") == 3
    assert (home / ".config/personas/ollama/claude").is_dir()


def test_interactive_re_asks_an_endpoint_with_no_scheme(home, monkeypatch):
    # Verification and every harness issue HTTP against this, so a bare host is
    # caught here rather than at first use.
    interview = Interview(["ollama", "Local Llama", "localhost:11434/v1",
                           "http://localhost:11434/v1", "llama3", "claude"],
                          {"API key": False}).install(monkeypatch)
    pg.main(["-i"])

    assert interview.asked.count("API endpoint") == 2
    settings = json.loads(
        (home / ".config/personas/ollama/claude/settings.json").read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://localhost:11434/v1"


def test_interactive_re_asks_an_unknown_harness(home, monkeypatch):
    interview = Interview(LOCAL_ANSWERS + ["gooose", "goose"],
                          {"API key": False}).install(monkeypatch)
    pg.main(["-i"])

    assert interview.asked.count(HARNESS_QUESTION) == 2
    assert (home / ".config/personas/ollama/goose").is_dir()
    assert not (home / ".config/personas/ollama/claude").exists()


def test_interactive_does_not_ask_about_a_key_it_was_handed(home, monkeypatch):
    # --token settles the question: a key was supplied, so one is plainly needed.
    interview = Interview(LOCAL_ANSWERS + ["claude"],
                          {"config file": False}).install(monkeypatch)
    pg.main(["-i", "--token", key_file(home)])

    assert not [q for q in interview.confirmed if "API key" in q]
    assert (home / ".config/personas/ollama/token").read_text() == "sk-from-a-file"


def test_interactive_is_not_a_removal_front_end(home):
    # --remove needs only a name; there is no definition to interview about.
    with pytest.raises(SystemExit):
        pg.main(["-i", "--remove", "kimi"])


def test_ask_takes_the_default_on_blank(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert pg._ask("Model", "llama3") == "llama3"
    assert pg._ask("Model", "") == ""            # a blank default is still an answer


def test_ask_re_asks_when_there_is_no_default(monkeypatch):
    replies = iter(["", "   ", "ollama"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    assert pg._ask("Persona name") == "ollama"


def test_ask_ends_the_interview_on_eof(monkeypatch):
    def eof(prompt):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(SystemExit):
        pg._ask("Persona name")

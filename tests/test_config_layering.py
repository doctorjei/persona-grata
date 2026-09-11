"""Layered-configuration assembly: defaults -> presets -> user overrides."""

import json

import pytest
import yaml

import persona_grata as pg


@pytest.fixture(autouse=True)
def fixed_store(monkeypatch):
    """Pin the store so resolved paths are predictable."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")


def write(tmp_path, text):
    path = tmp_path / "agents.yaml"
    path.write_text(text)
    return str(path)


# --------------------------------------------------------------------------- #
# Layering
# --------------------------------------------------------------------------- #
def test_minimal_user_config_gets_every_default(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          test_user:
            mind:
              endpoint: "https://api.test.com"
    """))
    persona = cfg["personas"]["test_user"]

    assert cfg["persona_store"] == "/xdg/personas"          # global default + env macro
    assert persona["persona_desc"] == "test_user"           # persona default template
    assert persona["path"] == "/xdg/personas/test_user"
    assert persona["token"] == "/xdg/personas/test_user/token"
    assert set(persona["harnesses"]) == set(pg.preset_names("harness"))  # automatic expansion
    assert persona["harnesses"]["claude"]["auth_var"] == "ANTHROPIC_AUTH_TOKEN"
    assert persona["harnesses"]["claude"]["path"] == "/xdg/personas/test_user/claude"


def test_pid_and_hid_are_injected(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          test_user:
            mind: {endpoint: "https://api.test.com"}
    """))
    persona = cfg["personas"]["test_user"]
    assert persona["pid"] == "test_user"
    assert persona["harnesses"]["codex"]["hid"] == "codex"


def test_persona_preset_is_unwrapped_and_applied(tmp_path):
    # persona.kimi.yaml wraps its body in `kimi:`; the body must land on the
    # persona itself, not as a nested `kimi` key.
    cfg = pg.load_config(write(tmp_path, "personas: [kimi]"))
    kimi = cfg["personas"]["kimi"]
    assert "kimi" not in kimi
    assert kimi["persona_desc"] == "Kimi"
    assert kimi["mind"]["model"].startswith("kimi-")


def test_persona_preset_dialect_override_beats_the_registry(tmp_path):
    # The kimi preset mounts anthropic on /anthropic; the registry default
    # ({{endpoint}}) must not clobber it, and the protocols kimi says nothing
    # about keep the root mount.
    cfg = pg.load_config(write(tmp_path, "personas: [kimi]"))
    dialects = cfg["personas"]["kimi"]["mind"]["dialects"]
    assert dialects["anthropic"]["api_uri"] == "https://api.moonshot.ai/anthropic"
    assert dialects["anthropic"]["msg_uri"] == "https://api.moonshot.ai/anthropic/v1/messages"
    assert dialects["chat"]["api_uri"] == "https://api.moonshot.ai"
    assert dialects["responses"]["msg_uri"] == "https://api.moonshot.ai/v1/responses"


def test_the_negotiated_mount_reaches_the_harness_config(tmp_path):
    # The whole point of placement: kimi states /anthropic once, and every
    # harness speaking that protocol picks it up without naming the harness.
    cfg = pg.load_config(write(tmp_path, "personas: [kimi]"))
    harnesses = cfg["personas"]["kimi"]["harnesses"]
    assert harnesses["claude"]["protocol"] == "anthropic"
    settings = json.loads(harnesses["claude"]["content"])
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "https://api.moonshot.ai/anthropic"
    # ...and a harness speaking something else gets that protocol's mount.
    assert harnesses["goose"]["protocol"] == "chat"
    assert yaml.safe_load(harnesses["goose"]["content"])["OPENAI_HOST"] == \
        "https://api.moonshot.ai"


def test_user_override_beats_everything(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          kimi:
            token: "/custom/token/path"
            mind:
              dialects:
                anthropic:
                  api_uri: "https://mine.example/anthropic"
    """))
    kimi = cfg["personas"]["kimi"]
    assert kimi["token"] == "/custom/token/path"
    assert kimi["mind"]["dialects"]["anthropic"]["api_uri"] == "https://mine.example/anthropic"
    assert json.loads(kimi["harnesses"]["claude"]["content"])["env"]["ANTHROPIC_BASE_URL"] == \
        "https://mine.example/anthropic"


def test_harness_opt_out_removes_it(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          test_user:
            mind: {endpoint: "https://api.test.com"}
            harnesses:
              claude: None
    """))
    harnesses = cfg["personas"]["test_user"]["harnesses"]
    assert "claude" not in harnesses
    assert "codex" in harnesses


def test_custom_persona_and_harness_get_defaults(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          custom_p:
            mind: {endpoint: "https://api.custom.com"}
            harnesses:
              custom_h:
                supported_dialects: ["chat"]
                auth_var: "CUSTOM_KEY"
    """))
    persona = cfg["personas"]["custom_p"]
    custom = persona["harnesses"]["custom_h"]
    assert persona["persona_desc"] == "custom_p"          # persona default
    assert custom["auth_var"] == "CUSTOM_KEY"             # user value kept
    assert custom["path_var"] == ""                       # harness default
    assert custom["path"] == "/xdg/personas/custom_p/custom_h"
    assert custom["protocol"] == "chat"                   # negotiated, not authored
    assert persona["mind"]["dialects"]["chat"]["api_uri"] == "https://api.custom.com"


def test_personas_shorthands(tmp_path):
    as_list = pg.load_config(write(tmp_path, "personas: [kimi, minimax]"))
    assert {"kimi", "minimax"} <= set(as_list["personas"])

    as_str = pg.load_config(write(tmp_path, "personas: kimi"))
    assert "kimi" in as_str["personas"]


def test_no_user_file_yields_the_shipped_presets():
    cfg = pg.load_config()
    assert set(cfg["personas"]) == set(pg.preset_names("persona"))


def test_every_persona_is_built_so_cross_references_resolve(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          borrower:
            mind:
              endpoint: "https://api.test.com"
              model: "{{personas.kimi.mind.model}}"
    """))
    assert cfg["personas"]["borrower"]["mind"]["model"] == cfg["personas"]["kimi"]["mind"]["model"]


def test_cross_references_still_resolve_when_targeting(tmp_path):
    # Personas are always built, whatever the targets -- otherwise an absolute
    # reference into one that is not a target would stop resolving.
    cfg = pg.load_config(write(tmp_path, """
        personas:
          borrower:
            mind:
              endpoint: "https://api.test.com"
              model: "{{personas.kimi.mind.model}}"
    """), targets={"borrower": ["claude"]})
    assert cfg["personas"]["borrower"]["mind"]["model"] == cfg["personas"]["kimi"]["mind"]["model"]
    assert list(cfg["personas"]["borrower"]["harnesses"]) == ["claude"]
    assert cfg["personas"]["kimi"]["harnesses"] == {}          # built, not expanded


def test_targets_build_only_what_was_asked_for(tmp_path):
    every = pg.load_config()
    one = pg.load_config(targets={"kimi": ["codex"]})
    nothing = pg.load_config(targets={})

    def crosses(cfg):
        return sum(len(p.get("harnesses") or {}) for p in cfg["personas"].values())

    assert crosses(every) == len(pg.preset_names("persona")) * len(pg.preset_names("harness"))
    assert crosses(one) == 1
    assert crosses(nothing) == 0
    # ...and the one that was asked for is fully built, not a stub.
    assert one["personas"]["kimi"]["harnesses"]["codex"]["content"]


def test_persona_values_resolve_with_no_harnesses(tmp_path):
    # The CLI reads names, descriptions and the store path off this layer, so
    # persona-level templates have to stand up without any harness beneath them.
    cfg = pg.load_config(targets={})
    kimi = cfg["personas"]["kimi"]
    assert kimi["persona_desc"] == "Kimi"
    assert kimi["path"].endswith("/personas/kimi")
    assert kimi["token"].endswith("/personas/kimi/token")
    assert cfg["persona_store"]


def test_an_unbuildable_harness_does_not_break_other_personas(tmp_path):
    # The reason targeting exists. One unresolvable reference aborts the whole
    # load, so before targets, a pairing that could never work took every other
    # command down with it.
    path = write(tmp_path, """
        personas:
          broken:
            mind:
              endpoint: "https://api.test.com"
            harnesses:
              claude:
                config_file: "{{mind.no_such_key}}"
          fine:
            mind:
              endpoint: "https://api.test.com"
    """)
    with pytest.raises(pg.te.TemplateError):               # still fatal if asked for
        pg.load_config(path, targets={"broken": ["claude"]})

    cfg = pg.load_config(path, targets={"fine": ["claude"]})    # ...but only then
    assert cfg["personas"]["fine"]["harnesses"]["claude"]["protocol"] == "anthropic"


# --------------------------------------------------------------------------- #
# Dialects: where a protocol is mounted, and which one a pairing speaks
# --------------------------------------------------------------------------- #
def test_every_persona_is_seeded_from_the_dialect_registry(tmp_path):
    # A persona that says nothing about protocols serves all of them at its
    # endpoint root -- which is what a local server, and most gateways, do.
    cfg = pg.load_config(write(tmp_path, """
        personas:
          plain:
            mind:
              endpoint: "https://api.test.com"
    """))
    dialects = cfg["personas"]["plain"]["mind"]["dialects"]
    assert set(dialects) == set(pg.preset_names("dialect"))
    assert all(d["api_uri"] == "https://api.test.com" for d in dialects.values())
    assert dialects["anthropic"]["msg_uri"] == "https://api.test.com/v1/messages"
    assert dialects["chat"]["msg_uri"] == "https://api.test.com/v1/chat/completions"


def test_a_dialect_switched_off_is_not_served(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          chat_only:
            mind:
              endpoint: "https://x.test"
              dialects:
                anthropic: None
                responses: None
    """), targets={})
    assert set(cfg["personas"]["chat_only"]["mind"]["dialects"]) == {"chat"}


def test_a_harness_falls_back_to_its_next_choice(tmp_path):
    # Codex prefers Responses and works over Chat, so the protocol it ends up
    # speaking depends on the persona -- and wire_api names whichever it got.
    tomllib = pytest.importorskip("tomllib")
    path = write(tmp_path, """
        personas:
          chat_only:
            mind:
              endpoint: "https://x.test"
              dialects:
                responses: None
    """)
    codex = pg.load_config(path, targets={"chat_only": ["codex"]})[
        "personas"]["chat_only"]["harnesses"]["codex"]
    assert codex["protocol"] == "chat"
    provider = tomllib.loads(codex["content"])["model_providers"]["codex"]
    assert provider["wire_api"] == "chat"
    assert provider["base_url"] == "https://x.test"


CHAT_ONLY = """
personas:
  chat_only:
    mind:
      endpoint: "https://x.test"
      dialects:
        anthropic: None
        responses: None
"""


def test_a_named_incompatible_pairing_is_refused_by_name(tmp_path):
    # Compatibility is settled while both sides are literal strings, so a
    # pairing that cannot work reads like "unknown harness" rather than
    # surfacing as a TemplateError about __MATCH_FIRST__.
    path = write(tmp_path, CHAT_ONLY)
    with pytest.raises(SystemExit) as exit_info:
        pg.load_config(path, targets={"chat_only": ["claude"]})
    message = str(exit_info.value)
    for expected in ("claude", "chat_only", "anthropic", "chat"):
        assert expected in message

    # ...and only for the pairing asked about. The persona still builds, and so
    # does every harness it can actually speak to.
    assert pg.load_config(path, targets={})["personas"]["chat_only"]["harnesses"] == {}
    goose = pg.load_config(path, targets={"chat_only": ["goose"]})
    assert goose["personas"]["chat_only"]["harnesses"]["goose"]["protocol"] == "chat"


def test_an_unnamed_incompatible_pairing_is_dropped_not_refused(tmp_path, capsys):
    # Asking for "every harness this persona has" is not asking for claude in
    # particular, so one impossible cross must not stop the possible ones.
    cfg = pg.load_config(write(tmp_path, CHAT_ONLY), targets={"chat_only": None})
    built = cfg["personas"]["chat_only"]["harnesses"]
    assert set(built) == {"codex", "goose"}
    assert built["codex"]["protocol"] == "chat"          # fell back from responses
    err = capsys.readouterr().err
    assert "skipping chat_only-claude" in err and "speaks anthropic" in err


def test_a_persona_no_harness_can_speak_to_is_fatal(tmp_path):
    # Dropping every harness leaves nothing to set up, so silence would be the
    # wrong answer even though no single cross was named.
    path = write(tmp_path, """
        personas:
          mystery_box:
            mind:
              endpoint: "https://x.test"
              dialects:
                anthropic: None
                chat: None
                responses: None
    """)
    with pytest.raises(SystemExit) as exit_info:
        pg.load_config(path, targets={"mystery_box": None})
    message = str(exit_info.value)
    assert "no harness can be used" in message and "mystery_box" in message
    assert "claude speaks anthropic" in message


@pytest.mark.parametrize("targets", [{"orion": ["homebrew"]}, {"orion": None}, None],
                         ids=["named", "implied", "everything"])
def test_a_harness_declaring_no_dialect_is_always_refused(tmp_path, targets):
    # Required of every harness, hand-written ones included: without it there is
    # no URI, nothing to verify against, and no protocol. That is a malformed
    # harness rather than an incompatible one, so unlike a failed negotiation it
    # is fatal however the harness was reached -- there is nothing to fall back
    # to and nothing the endpoint could have done differently.
    path = write(tmp_path, """
        personas:
          orion:
            mind: {endpoint: "https://x.test"}
            harnesses:
              homebrew:
                auth_var: "HOMEBREW_KEY"
    """)
    with pytest.raises(SystemExit) as exit_info:
        pg.load_config(path, targets=targets)
    message = str(exit_info.value)
    assert "supported_dialects" in message and "homebrew" in message


def test_a_site_can_add_a_dialect_of_its_own(tmp_path):
    # Dialects are ordinary presets, so a proxy or a variant does not need the
    # package patched -- declaring the mount is enough.
    cfg = pg.load_config(write(tmp_path, """
        personas:
          orion:
            mind:
              endpoint: "https://x.test"
              dialects:
                homegrown: {api_uri: "https://x.test/hg"}
            harnesses:
              custom:
                supported_dialects: ["homegrown"]
    """), targets={"orion": ["custom"]})
    persona = cfg["personas"]["orion"]
    assert persona["harnesses"]["custom"]["protocol"] == "homegrown"
    # The registry's defaults still apply beneath it.
    assert persona["mind"]["dialects"]["homegrown"]["msg_uri"] == \
        "https://x.test/hg/v1/chat/completions"


def test_harness_names_reports_without_building(tmp_path):
    assert pg.harness_names("kimi") == pg.preset_names("harness")
    # A persona's own harness is included, and one it switched off is not.
    path = write(tmp_path, """
        personas:
          custom:
            mind:
              endpoint: "https://api.test.com"
            harnesses:
              apex: {auth_var: "APEX_KEY"}
    """)
    assert "apex" in pg.harness_names("custom", path)


def test_missing_config_file_exits():
    with pytest.raises(SystemExit):
        pg.load_config("no-such-file.yaml")


def test_declared_personas(tmp_path):
    path = write(tmp_path, "personas:\n  a: {mind: {endpoint: 'x'}}\n  b: {mind: {endpoint: 'y'}}")
    assert pg.declared_personas(path) == ["a", "b"]
    assert pg.declared_personas(None) == []


# --------------------------------------------------------------------------- #
# The id grammar (shared with kanibako, which consumes this store)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("pid", ["..", "kimi.k3", "a+b", "my persona", "a/b", "default", ""],
                         ids=["dotdot", "dotted", "plus", "space", "slash", "reserved", "empty"])
def test_an_unusable_persona_id_is_refused(tmp_path, pid):
    path = write(tmp_path, "personas:\n  %r:\n    mind: {endpoint: 'https://x.test'}\n" % pid)
    with pytest.raises(SystemExit) as exit_info:
        pg.load_config(path, targets={})
    assert "persona name" in str(exit_info.value)


def test_a_dotted_id_says_why_dots_are_refused(tmp_path):
    # The mistake users actually make is `kimi.k3`, and "letters and digits" does
    # not explain why a dot is not one of them. kanibako carries the same hint.
    path = write(tmp_path, "personas:\n  kimi.k3:\n    mind: {endpoint: 'https://x.test'}\n")
    with pytest.raises(SystemExit) as exit_info:
        pg.load_config(path, targets={})
    assert "key-path separator" in str(exit_info.value)


def test_ids_may_be_any_language(tmp_path):
    # The rule is str.isalnum(), not ASCII -- harmonized with kanibako's.
    cfg = pg.load_config(write(tmp_path, """
        personas:
          läma-3_x:
            mind: {endpoint: "https://x.test"}
    """), targets={})
    assert cfg["personas"]["läma-3_x"]["path"].endswith("/personas/läma-3_x")


@pytest.mark.parametrize("block,word", [
    ("harnesses:\n              bad.name: {supported_dialects: ['chat']}", "harness"),
    ("mind:\n              endpoint: 'https://x.test'\n"
     "              dialects:\n                bad.name: {api_uri: 'https://x.test'}", "dialect"),
])
def test_harness_and_dialect_ids_share_the_rule(tmp_path, block, word):
    path = write(tmp_path, """
        personas:
          orion:
            mind: {endpoint: "https://x.test"}
            %s
    """ % block)
    with pytest.raises(SystemExit) as exit_info:
        pg.load_config(path, targets={})
    assert f"{word} name" in str(exit_info.value)


@pytest.mark.parametrize("kind", ["persona", "harness", "dialect"])
def test_every_shipped_preset_id_is_usable(kind):
    # We are the other author of these names; a preset that broke the rule would
    # be unreachable from the CLI it ships with.
    for name in pg.preset_names(kind):
        assert pg._id_error(kind, name) is None, name


# --------------------------------------------------------------------------- #
# Unknown-key warnings
# --------------------------------------------------------------------------- #
def test_misspelled_keys_are_reported_at_every_level(tmp_path, capsys):
    pg.load_config(write(tmp_path, """
        persona_stor: "/tmp/typo"
        personas:
          orion:
            persona_dsc: "typo"
            mind:
              endpoint: "https://x.test"
              modell: "typo"
              dialects:
                anthropic:
                  api_url: "typo"
                  verify:
                    urll: "typo"
            harnesses:
              claude:
                base_url: "typo"
    """))
    err = capsys.readouterr().err
    for expected in ("persona_stor",
                     "personas.orion.persona_dsc",
                     "personas.orion.mind.modell",
                     "personas.orion.mind.dialects.anthropic.api_url",
                     "personas.orion.mind.dialects.anthropic.verify.urll",
                     "personas.orion.harnesses.claude.base_url"):
        assert expected in err


def test_config_store_is_free_form_and_not_validated(tmp_path, capsys):
    pg.load_config(write(tmp_path, """
        personas:
          orion:
            mind: {endpoint: "https://x.test"}
            harnesses:
              claude:
                config_store:
                  whatever_the_harness_wants:
                    deeply: {nested: "value"}
    """))
    assert "unknown setting" not in capsys.readouterr().err


def test_valid_config_warns_about_nothing(tmp_path, capsys):
    pg.load_config(write(tmp_path, """
        persona_store: "/tmp/store"
        personas:
          orion:
            persona_desc: "Orion"
            token: "/tmp/store/orion/tok"
            mind:
              endpoint: "https://x.test"
              model: "m"
              model_1: "m1"
              dialects:
                anthropic:
                  api_uri: "https://x.test/anthropic"
                  verify: {check_uri: "https://x.test/v", key_header: "x-api-key:", body: "{}"}
            harnesses:
              claude:
                harness_desc: "CC"
                auth_var: "K"
    """))
    assert "unknown setting" not in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Rendered harness config files
# --------------------------------------------------------------------------- #
def test_claude_content_is_valid_json_with_unset_models_pruned(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          test_user:
            mind:
              endpoint: "https://api.test.com"
              model: "only-one"
    """))
    settings = json.loads(cfg["personas"]["test_user"]["harnesses"]["claude"]["content"])
    assert settings["model"] == "only-one"
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "https://api.test.com"
    # model_1..4 were never set, so their env vars must not appear at all.
    assert "ANTHROPIC_DEFAULT_OPUS_MODEL" not in settings["env"]
    assert "" not in settings["env"].values()


def test_every_shipped_harness_renders_a_parseable_config_file():
    tomllib = pytest.importorskip("tomllib")
    import yaml
    parsers = {".toml": tomllib.loads, ".yaml": yaml.safe_load, ".json": json.loads}
    cfg = pg.load_config()
    for pid, persona in cfg["personas"].items():
        for hid, harness in persona["harnesses"].items():
            content, config_file = harness["content"], harness["config_file"]
            if not (content and config_file):
                continue
            suffix = config_file[config_file.rfind("."):]
            parse = parsers.get(suffix, json.loads)
            parse(content)          # raises -> the preset ships a broken config


def test_codex_toml_provider_table_matches_model_provider():
    tomllib = pytest.importorskip("tomllib")
    cfg = pg.load_config()
    codex = tomllib.loads(cfg["personas"]["kimi"]["harnesses"]["codex"]["content"])
    assert codex["model_provider"] in codex["model_providers"]
    assert codex["model_providers"][codex["model_provider"]]["wire_api"] == "responses"


def test_codex_content_is_toml_naming_the_harness(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          test_user:
            persona_desc: "Test User"
            mind:
              endpoint: "https://api.test.com"
              model: "only-one"
    """))
    content = cfg["personas"]["test_user"]["harnesses"]["codex"]["content"]
    assert 'model = "only-one"' in content
    assert 'model_provider = "codex"' in content       # {{hid}}, not the persona id
    assert "[model_providers.codex]" in content
    assert 'name = "Test User"' in content
    assert 'env_key = "API_KEY"' in content


# --------------------------------------------------------------------------- #
# The shipped data/ library itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", sorted(pg.DATA_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_shipped_yaml_parses(path):
    assert yaml.safe_load(path.read_text()) is not None


@pytest.mark.parametrize("kind,name",
                         [("persona", n) for n in pg.preset_names("persona")] +
                         [("harness", n) for n in pg.preset_names("harness")] +
                         [("dialect", n) for n in pg.preset_names("dialect")])
def test_preset_wrapper_matches_its_filename(kind, name):
    data = pg.load_yaml(pg.DATA_DIR / f"{kind}.{name}.yaml", pg.ENV_DEFAULTS)
    if len(data) == 1:
        assert next(iter(data)) == name

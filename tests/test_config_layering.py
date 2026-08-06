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
    assert set(persona["harnesses"]) == {"claude", "codex"}  # automatic expansion
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


def test_persona_preset_harness_override_beats_harness_default(tmp_path):
    # The kimi preset sets claude's base_uri; the generic harness default
    # ({{mind.endpoint}}) must not clobber it.
    cfg = pg.load_config(write(tmp_path, "personas: [kimi]"))
    claude = cfg["personas"]["kimi"]["harnesses"]["claude"]
    assert claude["base_uri"] == "https://api.moonshot.ai/anthropic"
    assert claude["verify"]["url"] == "https://api.moonshot.ai/anthropic/v1/messages"


def test_user_override_beats_everything(tmp_path):
    cfg = pg.load_config(write(tmp_path, """
        personas:
          kimi:
            token: "/custom/token/path"
            harnesses:
              claude:
                base_uri: "https://mine.example/anthropic"
    """))
    kimi = cfg["personas"]["kimi"]
    assert kimi["token"] == "/custom/token/path"
    assert kimi["harnesses"]["claude"]["base_uri"] == "https://mine.example/anthropic"


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
                auth_var: "CUSTOM_KEY"
    """))
    persona = cfg["personas"]["custom_p"]
    custom = persona["harnesses"]["custom_h"]
    assert persona["persona_desc"] == "custom_p"          # persona default
    assert custom["auth_var"] == "CUSTOM_KEY"             # user value kept
    assert custom["path_var"] == ""                       # harness default
    assert custom["path"] == "/xdg/personas/custom_p/custom_h"
    assert custom["base_uri"] == "https://api.custom.com"


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


def test_missing_config_file_exits():
    with pytest.raises(SystemExit):
        pg.load_config("no-such-file.yaml")


def test_declared_personas(tmp_path):
    path = write(tmp_path, "personas:\n  a: {mind: {endpoint: 'x'}}\n  b: {mind: {endpoint: 'y'}}")
    assert pg.declared_personas(path) == ["a", "b"]
    assert pg.declared_personas(None) == []


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
                         [("harness", n) for n in pg.preset_names("harness")])
def test_preset_wrapper_matches_its_filename(kind, name):
    data = pg.load_yaml(pg.DATA_DIR / f"{kind}.{name}.yaml", pg.ENV_DEFAULTS)
    if len(data) == 1:
        assert next(iter(data)) == name

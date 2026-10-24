"""Model-table helpers and their use in the slot-based Codex template."""

import json
import pathlib
import tomllib

import pytest
import yapyon

from persona_grata.models import gen_codex_model_table


DATA = pathlib.Path(__file__).resolve().parents[1] / "src/persona_grata/data"


@pytest.mark.parametrize("models", [[], ["m1"], ["m-1", "m/2", 'm"3']])
def test_model_table(models):
    table = gen_codex_model_table("test-provider", models)
    assert list(table) == models
    assert table == {
        model: {"provider": "test-provider", "remote_model_id": model}
        for model in models
    }
    models.append("later")
    assert "later" not in table


@pytest.fixture
def contract():
    with (DATA / "functions.contract").open() as source:
        return yapyon.load_contract(source)


@pytest.mark.parametrize("models", [[], ["m1"], ["m-1", "m/2", 'm"3']])
def test_codex_model_table_renders_through_contract(contract, models):
    tree = yapyon.parse('''runtime:
  agent_path: "/tmp/example-agent"
agent:
  persona:
    name: "test-provider"
    desc: "Test Provider"
    mind:
      model_active: "m1"
      model_list: []
      dialects:
        chat:
          api_uri: "https://example.invalid"
  harness<codex.harness>: {}
''')
    tree["agent"]["persona"]["mind"]["model_list"].replace(
        yapyon.parse(json.dumps(models)))
    tree["agent"]["harness"].fill(
        yapyon.parse_file(DATA / "harnesses/codex.harness.tmpl"))
    tree["agent"]["harness"].fill(
        yapyon.parse_file(DATA / "harnesses/base.harness.tmpl"))
    assert not tree.slots()

    resolved = tree.resolve(contract=contract)
    harness = resolved["agent"]["harness"]
    native = tomllib.loads(harness["content"])
    expected = {
        model: {"provider": "test-provider", "remote_model_id": model}
        for model in models
    }
    assert harness["config_store"]["models"] == expected
    assert native.get("models", {}) == expected
    if not models:
        assert "models" not in native
    assert native["model"] == "m1"
    assert native["model_provider"] == "test-provider"
    assert native["model_providers"]["test-provider"]["base_url"] == "https://example.invalid"
    assert native["model_providers"]["test-provider"]["wire_api"] == "chat"
    assert harness["path"] == "/tmp/example-agent/harness"


def test_contract_rejects_non_string_model(contract):
    with pytest.raises(yapyon.AkanError):
        yapyon.loads('''models: GEN_CODEX_MODEL_TABLE("provider", [123])
''', contract=contract)

"""Codex model-table generation stays inside the document template."""

import json
import pathlib
import tomllib

import pytest
import yapyon


DATA = pathlib.Path(__file__).resolve().parents[1] / "src/persona_grata/data"


@pytest.mark.parametrize("models", [[], ["m1"], ["m-1", "m/2", 'm"3']])
def test_codex_model_table_renders_without_contract(models):
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

    restored = yapyon.parse(tree.to_source())
    resolved = restored.resolve()
    assert resolved == tree.resolve()
    harness = resolved["agent"]["harness"]
    native = tomllib.loads(harness["content"])
    expected = {
        model: {"provider": "test-provider", "remote_model_id": model}
        for model in models
    }
    assert harness["config_store"]["models"] == expected
    assert native["models"] == expected
    assert native["model"] == "m1"
    assert native["model_provider"] == "test-provider"
    assert native["model_providers"]["test-provider"]["base_url"] == "https://example.invalid"
    assert native["model_providers"]["test-provider"]["wire_api"] == "chat"
    assert harness["path"] == "/tmp/example-agent/harness"

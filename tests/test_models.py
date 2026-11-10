"""Codex model-table generation stays inside the document template."""

import json
import tomllib

import pytest
import yapyon

from persona_grata.composition import compose_preset


def _codex_tree(models, *, auth_var="API_KEY"):
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
  harness<harness.codex>: {}
''')
    tree["agent"]["persona"]["mind"]["model_list"].replace(
        yapyon.parse(json.dumps(models)))
    tree["agent"]["harness"].fill(compose_preset("harness", "codex")["harness"])
    tree["agent"]["harness"]["auth_var"].replace(yapyon.parse(json.dumps(auth_var)))
    assert not tree.slots()
    return tree


@pytest.mark.parametrize("models", [[], ["m1"], ["m-1", "m/2", 'm"3']])
def test_codex_model_table_renders_without_contract(models):
    tree = _codex_tree(models)
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
    assert harness["config_file"] == "config.toml"


@pytest.mark.parametrize("secret_source", ["secret: None", 'secret: {name: "fake-manager"}'])
@pytest.mark.parametrize("auth_var", ["", "API_KEY", "INHERITED_AUTH"])
def test_codex_native_env_key_depends_on_auth_var_not_secret(secret_source, auth_var):
    tree = _codex_tree(["m1"], auth_var=auth_var)
    tree["agent"].merge(yapyon.parse(secret_source))
    resolved = yapyon.parse(tree.to_source()).resolve()
    harness = resolved["agent"]["harness"]
    raw_provider = harness["config_store"]["model_providers"]["test-provider"]
    native_provider = tomllib.loads(harness["content"])["model_providers"]["test-provider"]
    assert harness["auth_var"] == auth_var
    if auth_var:
        assert raw_provider["env_key"] == auth_var
        assert native_provider["env_key"] == auth_var
    else:
        assert raw_provider["env_key"] is None
        assert "env_key" not in native_provider


def test_codex_output_copy_filters_only_none_without_changing_raw_native_data():
    tree = _codex_tree(["m1"], auth_var="")
    tree.merge(yapyon.parse('''agent:
  harness:
    config_store:
      optional: None
      false_value: False
      zero_value: 0
      empty_text: ""
      empty_list: []
      empty_mapping: {}
      nested:
        optional: None
        retained: ""
'''))
    harness = tree.resolve()["agent"]["harness"]
    raw = harness["config_store"]
    native = tomllib.loads(harness["content"])
    assert raw["optional"] is None
    assert raw["nested"] == {"optional": None, "retained": ""}
    assert raw["model_providers"]["test-provider"]["env_key"] is None
    assert "optional" not in native
    assert native["nested"] == {"retained": ""}
    for key, expected in {"false_value": False, "zero_value": 0, "empty_text": "",
                          "empty_list": [], "empty_mapping": {}}.items():
        assert raw[key] == expected
        assert native[key] == expected

"""Defaults use the same deferred merge semantics as ordinary yapyon source."""

import pytest
import yapyon

from persona_grata import AgentStore, make_agent
from persona_grata.composition import compose_preset
from persona_grata.schema import FORMAT


MIND = '''{
  endpoint: "http://127.0.0.1:8000",
  dialects: {chat: {name: "chat", api_uri: "http://127.0.0.1:8000"}}
}'''


def definition(*, mind=f"({MIND})", persona=None, harness=None, secret="None"):
    return yapyon.parse(f'''agent:
  format: {FORMAT!r}
  name: "authored"
  secret: {secret}
  persona: {persona or '{name: "local", supplied_mind: ' + MIND + ', mind: ' + mind + '}'}
  harness: {harness or '{name: "client", supported_dialects: ["chat"]}'}
''')


@pytest.mark.parametrize("mind", [
    "(supplied_mind)",
    "supplied_mind if True else missing_mind",
    '{y"endpoint": "http://127.0.0.1:8000", '
    'y"dialects": {chat: {name: "chat", api_uri: "http://127.0.0.1:8000"}}}',
])
def test_valid_deferred_mind_records_receive_ordinary_defaults(tmp_path, mind):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"persona": {"mind": {"context_size": 12345, "model_list": ["one"]}}})
    path = store.create("scout", definition(mind=mind))
    saved = (path / "agent.cfg").read_bytes()
    actual = store.load("scout")["agent"]["persona"]["mind"]
    assert actual["context_size"] == 12345
    assert actual["model_list"] == ["one"]
    assert actual["model_active"] == actual["model_subagent"] == "one"
    store.configure(defaults={"persona": {"mind": {"context_size": 23456, "model_list": ["two"]}}})
    updated = store.load("scout")["agent"]["persona"]["mind"]
    assert updated["context_size"] == 23456
    assert updated["model_active"] == "two"
    assert (path / "agent.cfg").read_bytes() == saved
    store.rename("scout", "renamed")
    assert store.load("renamed")["agent"]["name"] == "renamed"


@pytest.mark.parametrize("component", ["persona", "harness", "secret"])
def test_deferred_component_record_is_valid_after_resolution(tmp_path, component):
    store = AgentStore(tmp_path / "agents")
    if component == "persona":
        source = definition(persona='{name: "local", mind: ' + MIND + '} if True else missing_persona')
    elif component == "harness":
        source = definition(harness='{name: "client", supported_dialects: ["chat"]} if True else missing_harness')
    else:
        source = definition(secret='{name: "custom", entry: "shared/key", fetch: y"read {entry}"} if True else missing_secret')
    store.create("scout", source)
    actual = store.load("scout")["agent"][component]
    assert isinstance(actual, dict)
    if component == "secret":
        assert actual["entry"] == "shared/key"
        assert actual["fetch"] == "read shared/key"
    elif component == "harness":
        assert actual["protocol"] == "chat"
    else:
        assert actual["mind"]["endpoint"] == "http://127.0.0.1:8000"


@pytest.mark.parametrize("separator, expected_models, expected_native", [
    (":", ["default", "file"], {"lower": 1, "higher": 2}),
    (":=", ["file"], {"higher": 2}),
])
def test_defaults_and_files_follow_authored_collection_operators(
        tmp_path, separator, expected_models, expected_native):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={
        "persona": {"mind": {"model_list": ["default"]}},
        "harness": {"config_store": {"lower": 1}},
    })
    source = yapyon.merge(definition(), yapyon.parse(f'''agent:
  persona:
    mind:
      model_list{separator} ["file"]
  harness:
    config_store{separator} {{higher: 2}}
'''))
    store.create("scout", source)
    actual = store.load("scout")["agent"]
    assert actual["persona"]["mind"]["model_list"] == expected_models
    assert actual["harness"]["config_store"] == expected_native


@pytest.mark.parametrize("separator, expected", [(":", {"lower": 1}), (":=", {})])
def test_empty_native_mapping_combines_or_replaces_as_authored(tmp_path, separator, expected):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"harness": {"config_store": {"lower": 1}}})
    source = yapyon.merge(definition(), yapyon.parse(f"agent: {{harness: {{config_store{separator} {{}}}}}}"))
    store.create("scout", source)
    assert store.load("scout")["agent"]["harness"]["config_store"] == expected


@pytest.mark.parametrize("root, expected", [("team/keys", "team/keys"), (None, None)])
def test_guarded_secret_root_contributes_present_values(root, expected):
    secret = compose_preset("secret", "pass")
    tree = yapyon.merge(yapyon.parse(f'''runtime: {{defaults: {{secret: {{root: {root!r}}}}}}}
agent: {{name: "scout"}}
'''), secret)
    actual = tree.resolve()["secret"]
    assert actual["root"] == expected
    assert actual["entry"] == (f"{root}/scout" if root is not None else "scout")


def test_guarded_secret_root_leaves_builtin_when_reference_is_absent():
    secret = compose_preset("secret", "pass")
    actual = yapyon.merge(yapyon.parse('runtime: {defaults: {}}\nagent: {name: "scout"}'), secret).resolve()
    assert actual["secret"]["root"] == "agents"
    assert actual["secret"]["entry"] == "agents/scout"


def test_guarded_missing_file_value_preserves_store_default(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"harness": {"config_store": {"lower": 1}}})
    source = yapyon.merge(definition(), yapyon.parse("agent: {harness: {config_store:= (not_supplied!)}}"))
    store.create("scout", source)
    assert store.load("scout")["agent"]["harness"]["config_store"] == {"lower": 1}


def test_generic_dialect_defaults_are_not_broadcast_into_unrelated_inline_records(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"dialect": {"verify": {"body": "template-default"}}})
    store.create("scout", definition())
    assert "verify" not in store.load("scout")["agent"]["persona"]["mind"]["dialects"]["chat"]


def test_explicit_dialect_template_contribution_uses_generic_defaults(tmp_path):
    store = AgentStore(tmp_path / "agents")
    store.configure(defaults={"dialect": {"verify": {"body": "template-default"}}})
    store.create("scout", make_agent("ollama", "codex"))
    dialects = store.load("scout")["agent"]["persona"]["mind"]["dialects"]
    assert dialects["chat"]["verify"]["body"] == "template-default"

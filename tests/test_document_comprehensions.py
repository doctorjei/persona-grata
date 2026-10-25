"""Bundled expressions use core comprehensions without consumer callbacks or pruning."""

import json
import tomllib

import pytest
import yapyon

from persona_grata import AgentStore, make_agent
from persona_grata.composition import compose_preset, read_document
from persona_grata import yapyon_io as yio


def protocol_tree(preferences, available):
    tree = yio.value_tree({
        "supported_dialects": preferences,
        "persona": {"mind": {"dialects": available}},
        "dialect": "outer_binding",
    })
    harness = compose_preset("harness", "codex")
    tree.merge(harness["harness"]["protocol"], at=("protocol",))
    return tree


@pytest.mark.parametrize("preferences,available,expected", [
    (["responses", "chat"], {"chat": {}, "responses": {}}, "responses"),
    (["chat", "responses"], {"responses": {}, "chat": {}}, "chat"),
    (["responses", "chat"], {"responses": None, "chat": {}}, "chat"),
    (["missing", "chat"], {"chat": {}}, "chat"),
    (["responses", "chat"], {"responses": False, "chat": {}}, "responses"),
    (["responses", "chat"], {"responses": 0, "chat": {}}, "responses"),
    (["responses", "chat"], {"responses": "", "chat": {}}, "responses"),
])
def test_inline_protocol_filter_uses_explicit_non_none_test(preferences, available, expected):
    tree = protocol_tree(preferences, available)
    restored = yapyon.parse(tree.to_source())
    resolved = restored.resolve()
    assert resolved["protocol"] == expected
    assert resolved["dialect"] == "outer_binding"


@pytest.mark.parametrize("preferences,available", [
    ([], {"chat": {}}),
    (["responses", "chat"], {}),
    (["responses", "chat"], {"responses": None, "chat": None}),
])
def test_required_inline_protocol_has_no_silent_fallback(preferences, available):
    with pytest.raises(yapyon.AkanError):
        protocol_tree(preferences, available).resolve()


def test_inline_first_index_does_not_hide_later_errors():
    with pytest.raises(yapyon.AkanError):
        protocol_tree(["chat", 123], {"chat": {}}).resolve()


def test_inline_filter_survives_unfinished_source_roundtrip():
    tree = protocol_tree(["responses", "chat"], {"responses": None, "chat": {}})
    tree.merge(yapyon.parse("runtime<>: {}"))
    restored = yapyon.parse(tree.to_source())
    assert restored["runtime"].kind == "slot"
    restored["runtime"].fill(yapyon.parse("{}"))
    assert restored.resolve()["protocol"] == "chat"


@pytest.mark.parametrize("models", [
    [], ["m1"], ["M1", "m1", "m/2", 'm"3', "é", "é"],
])
def test_stored_model_comprehension_needs_no_contract(tmp_path, monkeypatch, models):
    monkeypatch.setattr(yio, "contract", lambda: None)
    definition = yapyon.merge(make_agent("minimax", "codex"), yio.value_tree({
        "agent": {"persona": {"mind": {"model_list": models, "model_active": "chosen"}}}}))
    store = AgentStore(tmp_path / "agents")
    store.create("models", definition)
    source = read_document(store.root / "harnesses/codex.harness.tmpl").to_source()
    assert "for model_id in persona.mind.model_list" in source
    assert "GEN_CODEX_MODEL_TABLE" not in source and "MATCH_FIRST" not in source
    result = store.load("models")
    native = tomllib.loads(result["agent"]["harness"]["content"])
    assert list(native["models"]) == models
    assert native["models"] == {
        model: {"provider": "minimax", "remote_model_id": model} for model in models}


def test_duplicate_model_keys_are_not_silently_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(yio, "contract", lambda: None)
    definition = yapyon.merge(make_agent("minimax", "codex"), yio.value_tree({
        "agent": {"persona": {"mind": {"model_list": ["same", "same"]}}}}))
    store = AgentStore(tmp_path / "agents")
    with pytest.raises(yapyon.AkanError):
        store.create("duplicates", definition)
    assert store.agents() == {}


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_native_serialization_keeps_declared_empty_false_and_zero_values(tmp_path, monkeypatch, harness):
    monkeypatch.setattr(yio, "contract", lambda: None)
    payload = {
        "empty_string": "", "empty_list": [], "empty_mapping": {}, "zero": 0, "flag": False,
        "values": ["", {}, [], 0, False],
    }
    definition = yapyon.merge(make_agent("minimax", harness), yio.value_tree({
        "agent": {"harness": {"config_store": {"payload": payload}}}}))
    store = AgentStore(tmp_path / "agents")
    store.create("preserved", definition)
    result = store.load("preserved")["agent"]["harness"]
    native = json.loads(result["content"]) if harness == "claude" else tomllib.loads(result["content"])
    assert native["payload"] == payload
    assert result["config_store"]["payload"] == payload


def test_json_native_serialization_preserves_null_and_list_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(yio, "contract", lambda: None)
    payload = {"explicit_null": None, "positions": [None, "", {}, [], 0, False]}
    definition = yapyon.merge(make_agent("minimax", "claude"), yio.value_tree({
        "agent": {"harness": {"config_store": {"payload": payload}}}}))
    store = AgentStore(tmp_path / "agents")
    store.create("nulls", definition)
    harness = store.load("nulls")["agent"]["harness"]
    assert json.loads(harness["content"])["payload"] == payload
    assert harness["config_store"]["payload"] == payload


def test_toml_null_behavior_is_upstreams_not_pg_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(yio, "contract", lambda: None)
    definition = yapyon.merge(make_agent("minimax", "codex"), yio.value_tree({
        "agent": {"harness": {"config_store": {"payload": {"explicit_null": None, "blank": ""}}}}}))
    store = AgentStore(tmp_path / "agents")
    store.create("toml_null", definition)
    harness = store.load("toml_null")["agent"]["harness"]
    payload = tomllib.loads(harness["content"])["payload"]
    assert harness["config_store"]["payload"]["explicit_null"] is None
    assert payload["blank"] == ""
    assert "explicit_null" not in payload
    assert payload["@_lexemes"]["explicit_null"] == "None"

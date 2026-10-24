"""Unfinished category documents survive save/reload without evaluating."""

from functools import partial
from pathlib import Path

import pytest
import yapyon

from persona_grata.composition import (
    DATA_DIR, KIND_DIRS, compose, compose_preset, load_preset, write_document,
)


def test_selected_category_keeps_unfilled_slots_and_expressions(tmp_path):
    original = load_preset(("minimax_claude", "agent"))
    before = original.to_source()
    tree = compose(original, kinds={"agent"})
    assert original.to_source() == before
    assert tree["agent"].kind == "mapping"
    assert tree["agent"]["desc"].lexeme == 'y"{name}"'
    assert {slot.label for slot in tree.slots()} == {
        ("minimax", "persona"), ("claude", "harness")}
    path = write_document(tree, tmp_path / "agent.cfg")
    restored = yapyon.parse_file(path)
    assert restored.to_source() == tree.to_source()
    assert restored["agent"]["harness"].overrides["config_store"]["env"][
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW"].value == "1000000"


def test_continue_composition_after_reload():
    initial = yapyon.parse('''runtime<>: {}
agent<minimax_claude.agent>: {}
''')
    first = compose(initial, kinds={"agent"})
    restored = yapyon.parse(first.to_source())
    continued = compose(restored)
    unsaved = compose(first)
    assert continued.to_source() == unsaved.to_source()
    assert [slot.label for slot in continued.slots()] == [()]
    assert continued["agent"]["persona"]["mind"]["model_list"][0].value


def test_no_environment_or_contract_evaluation(monkeypatch, tmp_path):
    monkeypatch.delenv("PG_COMPOSITION_PROBE", raising=False)
    tree = yapyon.parse('''runtime<>: {}
endpoint: y"{$PG_COMPOSITION_PROBE}"
value: NO_SUCH_FUNCTION(endpoint)
computed:
  y"{endpoint}": (value)
''')
    result = compose(tree)
    restored = yapyon.parse_file(write_document(result, tmp_path / "deferred.cfg"))
    assert restored["endpoint"].lexeme == 'y"{$PG_COMPOSITION_PROBE}"'
    assert [slot.label for slot in restored.slots()] == [()]


def test_inheritance_cycle_reports_source():
    definitions = {
        ("a", "persona"): yapyon.parse('persona<b.persona>: {}', source="a.tmpl"),
        ("b", "persona"): yapyon.parse('persona<a.persona>: {}', source="b.tmpl"),
    }
    with pytest.raises(ValueError, match="preset inheritance cycle") as error:
        compose(definitions[("a", "persona")], lookup=definitions.__getitem__)
    assert "a.persona" in str(error.value) and "b.persona" in str(error.value)
    assert ".tmpl:" in str(error.value)


def test_recursive_children_are_bounded():
    template = yapyon.parse('agent:\n  child<recursive.agent>: {}')
    with pytest.raises(ValueError, match="exceeds 5 fills"):
        compose(yapyon.parse('agent<recursive.agent>: {}'),
                lookup=lambda label: template, max_fills=5)


def test_abstract_and_unselected_slots_do_not_lookup():
    tree = yapyon.parse('runtime<>: {}\nagent<missing.agent>: {}')
    result = compose(tree, kinds=set(), lookup=lambda label: pytest.fail("looked up"))
    assert [slot.label for slot in result.slots()] == [(), ("missing", "agent")]


def test_missing_preset_reports_slot_location(tmp_path):
    tree = yapyon.parse('agent<absent.agent>: {}', source="custom.cfg")
    with pytest.raises(ValueError, match=r"custom.cfg:1:.*absent.agent.*not found"):
        compose(tree, lookup=partial(load_preset, data_dir=tmp_path))


@pytest.mark.parametrize("name", ["../x", "not-safe", "é", "9bad", "default", "x" * 65])
def test_invalid_preset_id_never_reaches_filesystem(name):
    with pytest.raises(ValueError):
        load_preset((name, "persona"))


@pytest.mark.parametrize("label", [("a", "missing"), ("a",), ("a", "persona", "extra")])
def test_invalid_labels(label):
    with pytest.raises(ValueError):
        load_preset(label)


def test_write_refuses_overwrite_and_cleans_temporary_file(tmp_path):
    path = tmp_path / "agent.cfg"
    path.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_document(yapyon.parse("agent<>: {}"), path)
    assert path.read_text() == "original"
    assert list(tmp_path.iterdir()) == [path]
    write_document(yapyon.parse("agent<>: {}"), path, overwrite=True)
    assert yapyon.parse_file(path)["agent"].kind == "slot"
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_serialization_does_not_touch_destination(tmp_path):
    class Refused:
        def to_source(self):
            raise ValueError("unrepresentable deferred state")

    target = tmp_path / "new" / "agent.cfg"
    with pytest.raises(ValueError, match="unrepresentable"):
        write_document(Refused(), target)
    assert not target.parent.exists()


def test_failed_atomic_replace_preserves_destination(monkeypatch, tmp_path):
    path = tmp_path / "agent.cfg"
    path.write_text("original")

    def fail(*args):
        raise OSError("write failed")

    monkeypatch.setattr("persona_grata.composition.os.replace", fail)
    with pytest.raises(OSError, match="write failed"):
        write_document(yapyon.parse("agent<>: {}"), path, overwrite=True)
    assert path.read_text() == "original"
    assert list(tmp_path.iterdir()) == [path]


PRESETS = [(kind, path.name.removesuffix(f".{kind}.tmpl"))
           for kind, directory in KIND_DIRS.items()
           for path in sorted((DATA_DIR / directory).glob(f"*.{kind}.tmpl"))]


@pytest.mark.parametrize("kind,name", PRESETS)
def test_every_bundled_category_composes_and_persists(kind, name):
    tree = compose_preset(kind, name)
    assert tree[kind].kind == "mapping"
    assert not any(slot.label and slot.label[1] == kind for slot in tree.slots())
    assert yapyon.parse(tree.to_source()).to_source() == tree.to_source()


def test_unrepresentable_computed_merge_is_not_silently_flattened(tmp_path):
    tree = yapyon.merge(
        yapyon.parse('key: "x"\nitems: {y"{key}": 1}'),
        yapyon.parse('items: {y"{key}": 2}'))
    path = tmp_path / "agent.cfg"
    path.write_text("keep")
    with pytest.raises(yapyon.AkanError):
        write_document(tree, path, overwrite=True)
    assert path.read_text() == "keep"

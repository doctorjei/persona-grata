"""Packaged sources use the same namespace paths as stored includes."""

import pytest
import yapyon

from persona_grata import make_agent
from persona_grata.composition import DATA_DIR, KIND_DIRS, compose, compose_preset, load_preset


KINDS = ("agent", "persona", "harness", "dialect", "secret")
LAYERS = ("_BUILTIN_", "_DEFAULT_")


def test_packaged_namespace_directories_match_include_categories():
    assert KIND_DIRS == {kind: kind for kind in KINDS}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("layer", LAYERS)
def test_packaged_default_layers_compose_without_resolving(kind, layer):
    if kind == "agent":
        tree = compose(yapyon.parse(f'''agent<agent.{layer}>:
  persona<>: {{}}
  harness<>: {{}}
  secret<>: {{}}
'''), kinds={kind})
    else:
        tree = compose_preset(kind, layer)
    assert tree[kind].kind == "mapping"
    assert not any(slot.label and slot.label[0] == kind for slot in tree.slots())
    assert yapyon.parse(tree.to_source()).to_source() == tree.to_source()


def test_packaged_lookup_uses_namespace_path_without_legacy_fallback(tmp_path):
    selected = tmp_path / "persona" / "local.tmpl"
    selected.parent.mkdir()
    selected.write_text('persona: {name: "selected"}')
    decoy = tmp_path / "personas" / "local.persona.tmpl"
    decoy.parent.mkdir()
    decoy.write_text('persona: {name: "legacy-decoy"}')
    assert load_preset(("persona", "local"), data_dir=tmp_path)["persona"]["name"].value == "selected"
    selected.unlink()
    with pytest.raises(FileNotFoundError):
        load_preset(("persona", "local"), data_dir=tmp_path)


def test_generated_agent_inherits_current_default_layer():
    tree = make_agent("minimax", "claude")
    assert tree["agent"].label == ("agent", "_DEFAULT_")
    assert tree["agent"].overrides["persona"].label == ("persona", "minimax")
    assert tree["agent"].overrides["harness"].label == ("harness", "claude")


def test_each_packaged_category_has_default_layers_and_uniform_filenames():
    for kind in KINDS:
        paths = sorted((DATA_DIR / kind).glob("*.tmpl"))
        assert paths, f"no templates discovered for {kind}"
        names = {path.stem for path in paths}
        assert set(LAYERS) <= names
        assert all("." not in name for name in names), (kind, names)

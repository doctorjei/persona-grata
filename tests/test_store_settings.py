"""Packaged defaults and sparse registry overrides remain separate layers."""

import pytest
import yapyon

from persona_grata import AgentStore
from persona_grata.store import DEFAULTS_PATH, FORMAT


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents")


def read_overrides(store):
    record = yapyon.loads_record(store.registry_path.read_text())
    if "format" in record:
        assert record.pop("format") == FORMAT
    return record


@pytest.mark.parametrize("source", [None, "", " \n\t\n", "# Defaults apply.\n  # Nothing overridden.\n", "{}\n"])
def test_missing_empty_or_comment_only_registry_uses_packaged_defaults(store, source):
    if source is not None:
        store.root.mkdir()
        store.registry_path.write_text(source)
    assert store.settings == yapyon.loads_record(DEFAULTS_PATH.read_text())
    if source is None:
        assert not store.root.exists()
    else:
        assert store.registry_path.read_text() == source


def test_nested_overrides_keep_other_default_fields(store):
    store.root.mkdir()
    store.registry_path.write_text('defaults: {secret: {root: "team/keys"}}\nenv_vars: {from_host: []}\n')
    settings = store.settings
    assert settings["defaults"]["secret"] == {"backend": None, "root": "team/keys"}
    assert settings["env_vars"] == {"from_host": [], "additions": []}


def test_configure_writes_only_explicit_nested_overrides(store):
    effective = store.configure(defaults={"secret": {"root": "team/keys"}})
    assert effective["defaults"]["secret"] == {"backend": None, "root": "team/keys"}
    assert read_overrides(store) == {"defaults": {"secret": {"root": "team/keys"}}}
    store.configure(env_vars={"additions": ["MY_ENDPOINT"]})
    assert read_overrides(store) == {
        "defaults": {"secret": {"root": "team/keys"}}, "env_vars": {"additions": ["MY_ENDPOINT"]}}
    assert list(read_overrides(store))[-1] == "env_vars"


def test_explicit_default_values_and_empty_lists_are_not_pruned(store):
    store.configure(defaults={"secret": {"backend": None, "root": "agents"}},
                    env_vars={"additions": [], "from_host": []})
    saved = read_overrides(store)
    assert saved == {"defaults": {"secret": {"backend": None, "root": "agents"}},
                     "env_vars": {"additions": [], "from_host": []}}
    store.configure(env_vars={"additions": ["MY_ENDPOINT"]})
    assert read_overrides(store)["defaults"] == saved["defaults"]
    assert read_overrides(store)["env_vars"]["from_host"] == []


def test_none_root_is_preserved_as_an_explicit_sparse_override(store):
    settings = store.configure(defaults={"secret": {"root": None}})
    assert settings["defaults"]["secret"] == {"backend": None, "root": None}
    assert read_overrides(store) == {"defaults": {"secret": {"root": None}}}
    store = AgentStore(store.root)
    assert store.settings["defaults"]["secret"]["root"] is None
    store.configure(env_vars={"from_host": []})
    assert read_overrides(store) == {"defaults": {"secret": {"root": None}}, "env_vars": {"from_host": []}}


def test_registry_updates_do_not_materialize_default_settings(store, tmp_path):
    store.root.mkdir()
    store.registry_path.write_text(yapyon.dumps({
        "agents": {"scout": {"path": str(tmp_path / "scout")}},
        "defaults": {"secret": {"backend": None}},
    }))
    store.unregister("scout")
    assert read_overrides(store) == {
        "agents": {}, "defaults": {"secret": {"backend": None}}}


def test_settings_use_defaults_document_not_a_python_copy(store, tmp_path, monkeypatch):
    defaults = yapyon.loads_record(DEFAULTS_PATH.read_text())
    path = tmp_path / "defaults.cfg"
    defaults["defaults"]["secret"] = {"backend": "custom", "root": "first"}
    path.write_text(yapyon.dumps(defaults))
    monkeypatch.setattr("persona_grata.store.DEFAULTS_PATH", path)
    store.root.mkdir()
    store.registry_path.write_text('defaults: {secret: {backend: None}}\n')
    assert store.settings["defaults"]["secret"] == {"backend": None, "root": "first"}
    store.configure(env_vars={"from_host": []})
    defaults["defaults"]["secret"]["root"] = "second"
    path.write_text(yapyon.dumps(defaults))
    assert store.settings["defaults"]["secret"] == {"backend": None, "root": "second"}
    assert read_overrides(store) == {"defaults": {"secret": {"backend": None}}, "env_vars": {"from_host": []}}


def test_custom_default_backend_is_not_limited_to_builtin_names(store):
    component = store.root / "shared/secret/custom.tmpl"
    component.parent.mkdir(parents=True)
    component.write_text('secret: {name: "custom", entry: "shared/key", fetch: "custom-read shared/key"}\n')
    before = component.read_bytes()
    store.configure(defaults={"secret": {"backend": "custom"}})
    assert store.settings["defaults"]["secret"]["backend"] == "custom"
    assert read_overrides(store) == {"defaults": {"secret": {"backend": "custom"}}}
    assert component.read_bytes() == before


def test_builtin_default_backend_materializes_only_its_source(store):
    store.configure(defaults={"secret": {"backend": "pass"}})
    assert (store.root / "shared/secret/pass.tmpl").is_file()
    assert not (store.root / "shared/secret/base.tmpl").exists()
    assert read_overrides(store) == {"defaults": {"secret": {"backend": "pass"}}}
    assert not (store.root / "entries").exists()


@pytest.mark.parametrize("source", [
    "None\n", "[]\n", '"not a registry"\n',
    "defaults: None\n", "defaults: {secret: None}\n",
    'defaults: {secret: {root: ""}}\n',
    'defaults: {secret: {root: "../bad"}}\n',
    "defaults: {secret: {backend: []}}\n", "defaults: {secret: {backend: True}}\n",
    'defaults: {secret: {backend: "../bad"}}\n',
    "defaults: {secret: {unexpected: 1}}\n", "defaults: {unexpected: 1}\n",
    'secret_backend: "pass"\n', 'secret_root: "agents"\n',
    "env_allow: None\n", 'env_add: ["9BAD"]\n', "unexpected: 1\n",
])
def test_invalid_registry_values_are_not_silently_treated_as_missing(store, source):
    store.root.mkdir()
    store.registry_path.write_text(source)
    with pytest.raises((ValueError, yapyon.AkanError)):
        _ = store.settings
    assert store.registry_path.read_text() == source


@pytest.mark.parametrize("root", ["", 0, False, [], {}])
def test_invalid_configure_does_not_replace_existing_overrides(store, root):
    store.configure(env_vars={"from_host": []})
    before = store.registry_path.read_bytes()
    with pytest.raises(ValueError):
        store.configure(defaults={"secret": {"root": root}})
    assert store.registry_path.read_bytes() == before


def test_sparse_registry_updates_follow_selected_symlink(store, tmp_path):
    store.root.mkdir()
    target = tmp_path / "registry.cfg"
    target.write_text('defaults: {secret: {root: "team"}}\n')
    store.registry_path.symlink_to(target)
    store.configure(env_vars={"from_host": []})
    assert store.registry_path.is_symlink()
    assert read_overrides(store) == {"defaults": {"secret": {"root": "team"}}, "env_vars": {"from_host": []}}


def test_settings_return_independent_native_values(store):
    settings = store.settings
    settings["defaults"]["secret"]["root"] = "changed"
    settings["env_vars"]["from_host"].clear()
    assert store.settings["defaults"]["secret"]["root"] == "agents"
    assert store.settings["env_vars"]["from_host"]

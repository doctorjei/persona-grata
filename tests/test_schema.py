"""The profile's typed schema is independent of store IO and source composition."""

import copy

import pytest

from persona_grata.schema import (
    DEFAULT_FIELDS, DIALECT_FIELDS, FORMAT, validate_defaults, validate_definition,
    validate_entry, validate_env_name,
)


AGENT_PATH = "/tmp/selected-agent"


def definition():
    return {
        "format": FORMAT,
        "name": "scout",
        "persona": {
            "name": "A descriptive provider / not an include ID",
            "mind": {
                "endpoint": "https://example.invalid/custom/path?version=1",
                "dialects": {
                    "vendor/chat.v2": {"name": "Chat dialect", "api_uri": "/v2/chat"},
                    "disabled": None,
                },
            },
        },
        "harness": {
            "name": "A descriptive client",
            "path": AGENT_PATH + "/harness",
            "supported_dialects": ["disabled", "vendor/chat.v2"],
            "protocol": "vendor/chat.v2",
        },
        "secret": None,
    }


def validate(agent):
    return validate_definition(agent, name="scout", path=AGENT_PATH)


def put(record, path, value):
    for key in path[:-1]:
        record = record[key]
    record[path[-1]] = value


def keyed_definition():
    agent = definition()
    agent["secret"] = {
        "name": "A descriptive credential manager",
        "root": None,
        "entry": "team's keys/$(not-a-shell); credential",
        "fetch": "manager read team's keys/$(not-a-shell); credential",
    }
    return agent


def test_defaults_schema_exposes_record_and_atomic_boundaries():
    assert set(DEFAULT_FIELDS) == {"persona", "harness", "dialect", "secret"}
    assert DEFAULT_FIELDS["dialect"] == DIALECT_FIELDS
    assert DEFAULT_FIELDS["persona"]["mind"]["dialects"] == "dialects"
    assert DEFAULT_FIELDS["harness"]["wrapper_env"] == "data"
    assert DEFAULT_FIELDS["harness"]["config_store"] == "data"
    assert DEFAULT_FIELDS["secret"]["backend"] is None
    assert "backend" not in DIALECT_FIELDS
    assert "name" not in DIALECT_FIELDS
    assert "desc" not in DIALECT_FIELDS
    assert DIALECT_FIELDS["verify"] == {
        "check_uri": None, "key_header": None, "headers": None, "body": None,
    }


def test_partial_and_complete_defaults_are_nonmutating():
    defaults = {
        "persona": {"mind": {
            "endpoint": "http://[::1]:1234/api",
            "context_size": None,
            "model_list": ["provider/model:version", "", "x y"],
            "model_active": "not in the list",
            "model_subagent": None,
            "dialects": {
                "vendor/chat.v2": {"api_uri": "/api", "verify": None},
                "disabled": None,
                "no-fields-yet": {},
            },
        }},
        "harness": {
            "path": "bound-path-is-checked-later", "auth_var": "", "path_var": "_PATH",
            "wrapper_env": {"EMPTY": "", "VALUE": "1"},
            "config_file": "native/config.toml", "config_store": {
                "name": None, "desc": False, "empty": {}, "list": [], "zero": 0,
            },
            "content": "", "supported_dialects": ["vendor/chat.v2"],
            "protocol": "vendor/chat.v2",
        },
        "dialect": {
            "api_uri": "/api", "msg_uri": None, "model_uri": "/models", "emb_uri": None,
            "verify": {"check_uri": "", "key_header": "", "headers": [], "body": ""},
        },
        "secret": {
            "backend": "alternate_secret", "root": None, "entry": "shared/team",
            "fetch": "manager read shared/team", "store": "", "check": None,
            "probe": "/tmp/credential tools/manager",
        },
    }
    before = copy.deepcopy(defaults)
    assert validate_defaults(defaults) is defaults
    assert defaults == before
    assert validate_defaults({}) == {}
    for group in DEFAULT_FIELDS:
        assert validate_defaults({group: {}}) == {group: {}}
    assert validate_defaults({"persona": {"mind": {"dialects": {}}}})
    assert validate_defaults({"dialect": {"verify": None}})
    assert validate_defaults({"secret": {"backend": None, "root": None}})


@pytest.mark.parametrize("defaults", [
    None, [], "", {"agent": {}}, {"runtime": {}}, {"unknown": {}},
    {"persona": None}, {"harness": None}, {"dialect": None}, {"secret": None},
    {"persona": {"name": "provider"}}, {"harness": {"desc": "description"}},
    {"dialect": {"name": "chat"}}, {"secret": {"name": "manager"}},
    {"persona": {"mind": None}}, {"persona": {"mind": {"dialects": None}}},
    {"persona": {"mind": {"unknown": 1}}},
    {"persona": {"mind": {"dialects": {"p": {"name": "chat"}}}}},
    {"persona": {"mind": {"dialects": {"p": {"desc": "description"}}}}},
    {"persona": {"mind": {"dialects": {"p": {"verify": {"unknown": "value"}}}}}},
    {"dialect": {"verify": {"name": "no"}}},
    {"harness": {"unknown": {"name": "not native data"}}},
    {"secret": {"backend": "default"}}, {"secret": {"backend": "bad-backend"}},
    {"secret": {"backend": False}},
    {"persona": {"mind": {"dialects": {"": None}}}},
    {"persona": {"mind": {"dialects": {1: None}}}},
    {"persona": {"mind": {"dialects": {"p": []}}}},
    {1: {}},
])
def test_defaults_reject_ineligible_or_nonrecord_values(defaults):
    with pytest.raises(ValueError):
        validate_defaults(defaults)


@pytest.mark.parametrize("group,fields", [
    ("persona", {"mind": {"endpoint": "https://"}}),
    ("persona", {"mind": {"context_size": True}}),
    ("persona", {"mind": {"model_list": [1]}}),
    ("persona", {"mind": {"model_active": False}}),
    ("persona", {"mind": {"dialects": {"unused": {"api_uri": ""}}}}),
    ("persona", {"mind": {"dialects": {"unused": {"verify": {"headers": [1]}}}}}),
    ("harness", {"path": ""}), ("harness", {"path": "a\x00b"}),
    ("harness", {"auth_var": None}), ("harness", {"path_var": "1BAD"}),
    ("harness", {"wrapper_env": {"GOOD": None}}),
    ("harness", {"wrapper_env": {"GOOD": False}}),
    ("harness", {"wrapper_env": {"bad-name": ""}}),
    ("harness", {"wrapper_env": None}),
    ("harness", {"config_store": None}), ("harness", {"config_store": []}),
    ("harness", {"config_store": {1: "value"}}),
    ("harness", {"config_file": "../config"}), ("harness", {"config_file": "/config"}),
    ("harness", {"config_file": False}), ("harness", {"content": None}),
    ("harness", {"supported_dialects": []}), ("harness", {"protocol": ""}),
    ("dialect", {"api_uri": None}), ("dialect", {"msg_uri": ""}),
    ("dialect", {"verify": []}), ("dialect", {"verify": {"body": None}}),
    ("secret", {"root": ""}), ("secret", {"entry": None}),
    ("secret", {"fetch": ""}), ("secret", {"fetch": None}),
    ("secret", {"store": 1}), ("secret", {"check": " \t"}),
    ("secret", {"fetch": "manager\x00"}), ("secret", {"probe": False}),
    ("secret", {"probe": "\x00"}),
])
def test_defaults_validate_all_supplied_types_even_when_unselected(group, fields):
    with pytest.raises(ValueError):
        validate_defaults({group: fields})


def test_defaults_defer_command_splitting_until_entry_is_bound():
    # The final entry may legally contain an unmatched quote, as inert argv data.
    assert validate_defaults({"secret": {"fetch": "manager read team's key"}})
    agent = keyed_definition()
    validate(agent)
    agent["secret"]["entry"] = "different-entry"
    with pytest.raises(ValueError, match="quoting"):
        validate(agent)


def test_complete_definition_preserves_all_auth_and_raw_data():
    agent = definition()
    agent["desc"] = ""
    agent["persona"]["extension"] = {"flag": False, "data": None}
    mind = agent["persona"]["mind"]
    mind.update({
        "context_size": None, "model_list": [], "model_active": None,
        "model_subagent": "outside/list:version", "extension": [],
    })
    dialect = mind["dialects"]["vendor/chat.v2"]
    dialect.update({
        "msg_uri": None, "model_uri": "/models", "emb_uri": None,
        "verify": {"headers": [], "body": "", "key_header": "", "check_uri": ""},
        "extension": {"name": None},
    })
    agent["harness"].update({
        "auth_var": "TOKEN", "path_var": "", "wrapper_env": {"TOKEN": "", "OTHER": "1"},
        "config_file": "", "config_store": {
            "env": {"TOKEN": None}, "false": False, "zero": 0, "empty": {}, "list": [],
        }, "content": "", "extension": None,
    })
    before = copy.deepcopy(agent)
    assert validate(agent) is agent
    assert agent == before
    assert agent["persona"]["mind"]["dialects"]["disabled"] is None
    assert agent["harness"]["auth_var"] == "TOKEN"
    agent["secret"] = keyed_definition()["secret"]
    agent["secret"]["extension"] = {"name": False}
    before = copy.deepcopy(agent)
    assert validate(agent) is agent
    assert agent == before
    assert "TOKEN" in agent["harness"]["config_store"]["env"]


@pytest.mark.parametrize("path", [
    ("format",), ("name",), ("persona",), ("harness",), ("secret",),
    ("persona", "name"), ("persona", "mind"), ("persona", "mind", "endpoint"),
    ("persona", "mind", "dialects"),
    ("persona", "mind", "dialects", "vendor/chat.v2", "name"),
    ("persona", "mind", "dialects", "vendor/chat.v2", "api_uri"),
    ("harness", "name"), ("harness", "path"),
    ("harness", "supported_dialects"), ("harness", "protocol"),
])
def test_definition_requires_complete_typed_records(path):
    agent = definition()
    record = agent
    for key in path[:-1]:
        record = record[key]
    del record[path[-1]]
    with pytest.raises(ValueError):
        validate(agent)


@pytest.mark.parametrize("path,value", [
    (("format",), "agents-yapyon-v0.1"), (("format",), None),
    (("name",), "another_name"), (("name",), None), (("desc",), None),
    (("persona",), []), (("persona", "name"), ""), (("persona", "desc"), False),
    (("persona", "mind"), None),
    (("persona", "mind", "endpoint"), "https://"),
    (("persona", "mind", "endpoint"), "http:///missing-host"),
    (("persona", "mind", "endpoint"), "relative/path"),
    (("persona", "mind", "endpoint"), "ftp://example.invalid"),
    (("persona", "mind", "endpoint"), "http://has space.invalid"),
    (("persona", "mind", "endpoint"), " https://example.invalid"),
    (("persona", "mind", "endpoint"), "https://example.invalid\n"),
    (("persona", "mind", "context_size"), True),
    (("persona", "mind", "context_size"), 0),
    (("persona", "mind", "context_size"), -1),
    (("persona", "mind", "context_size"), 1.0),
    (("persona", "mind", "model_list"), "model"),
    (("persona", "mind", "model_list"), [None]),
    (("persona", "mind", "model_active"), []),
    (("persona", "mind", "model_subagent"), False),
    (("persona", "mind", "dialects"), None),
    (("persona", "mind", "dialects"), {"": None}),
    (("persona", "mind", "dialects"), {1: None}),
    (("persona", "mind", "dialects", "vendor/chat.v2", "name"), ""),
    (("persona", "mind", "dialects", "vendor/chat.v2", "api_uri"), None),
    (("persona", "mind", "dialects", "vendor/chat.v2", "msg_uri"), ""),
    (("persona", "mind", "dialects", "vendor/chat.v2", "verify"), []),
    (("persona", "mind", "dialects", "vendor/chat.v2", "verify"), {"check_uri": None}),
    (("persona", "mind", "dialects", "vendor/chat.v2", "verify"), {"key_header": False}),
    (("persona", "mind", "dialects", "vendor/chat.v2", "verify"), {"headers": "header"}),
    (("persona", "mind", "dialects", "vendor/chat.v2", "verify"), {"headers": [None]}),
    (("harness", "name"), ""), (("harness", "desc"), None),
    (("harness", "path"), "/tmp/another/harness"),
    (("harness", "path"), AGENT_PATH + "/harness/"),
    (("harness", "path"), "harness"), (("harness", "path"), "a\x00b"),
    (("harness", "auth_var"), "BAD-NAME"), (("harness", "auth_var"), None),
    (("harness", "path_var"), "1BAD"),
    (("harness", "wrapper_env"), {"OK": None}),
    (("harness", "wrapper_env"), {"": ""}),
    (("harness", "config_file"), "../outside"),
    (("harness", "config_file"), "a/../outside"),
    (("harness", "config_file"), "/absolute"),
    (("harness", "config_file"), "a\x00b"),
    (("harness", "config_store"), None), (("harness", "content"), False),
    (("harness", "supported_dialects"), []),
    (("harness", "supported_dialects"), [""]),
    (("harness", "supported_dialects"), [1]),
    (("harness", "protocol"), "disabled"),
    (("harness", "protocol"), "unlisted"),
    (("secret",), False),
])
def test_definition_rejects_invalid_fields(path, value):
    agent = definition()
    put(agent, path, value)
    with pytest.raises(ValueError):
        validate(agent)


@pytest.mark.parametrize("field", ["path", "runtime", "unexpected"])
def test_direct_agent_boundary_is_closed(field):
    agent = definition()
    agent[field] = {}
    with pytest.raises(ValueError, match="unknown agent fields"):
        validate(agent)


@pytest.mark.parametrize("endpoint", [
    "http://localhost:8000/", "https://[::1]/a//b?c=d", "HTTP://example.invalid/a",
])
def test_valid_endpoint_spelling_is_not_rewritten(endpoint):
    agent = definition()
    agent["persona"]["mind"]["endpoint"] = endpoint
    validate(agent)
    assert agent["persona"]["mind"]["endpoint"] == endpoint


@pytest.mark.parametrize("filename", [None, "", "config.toml", "native/config.json", "./config"])
def test_optional_native_filename(filename):
    agent = definition()
    agent["harness"]["config_file"] = filename
    validate(agent)


def test_opaque_models_exact_protocols_and_none_verification_are_valid():
    agent = definition()
    mind = agent["persona"]["mind"]
    mind.update({"context_size": 1, "model_list": ["", "a/b:c"],
                 "model_active": "not/listed", "model_subagent": None})
    mind["dialects"]["vendor/chat.v2"]["verify"] = None
    validate(agent)
    agent["harness"]["protocol"] = "VENDOR/CHAT.V2"
    with pytest.raises(ValueError):
        validate(agent)


@pytest.mark.parametrize("missing", ["name", "entry", "fetch"])
def test_secret_requires_complete_mapping(missing):
    agent = keyed_definition()
    del agent["secret"][missing]
    with pytest.raises(ValueError):
        validate(agent)


@pytest.mark.parametrize("field,value", [
    ("name", ""), ("root", ""), ("root", "a/../b"), ("entry", "/absolute"),
    ("entry", ""), ("fetch", None), ("fetch", ""), ("fetch", " \t"),
    ("fetch", "'' argument"), ("fetch", 'manager "unclosed'),
    ("fetch", "manager\x00 read"), ("store", []), ("store", 'manager "unclosed'),
    ("check", False), ("check", " \t"), ("probe", 1), ("probe", "x\x00y"),
])
def test_secret_field_and_command_validation(field, value):
    agent = keyed_definition()
    agent["secret"][field] = value
    with pytest.raises(ValueError):
        validate(agent)


@pytest.mark.parametrize("optional", [None, ""])
def test_optional_secret_operations_can_be_disabled(optional):
    agent = keyed_definition()
    for key in ("store", "check", "probe"):
        agent["secret"][key] = optional
    validate(agent)


@pytest.mark.parametrize("separator", [" ", "\t"])
def test_final_entry_is_exact_inert_argv_data(separator):
    agent = keyed_definition()
    entry = agent["secret"]["entry"]
    for key in ("fetch", "store", "check"):
        agent["secret"][key] = "manager action" + separator + entry
    validate(agent)
    # Its unmatched quote is not repaired in an embedded or nonfinal argument.
    agent["secret"]["fetch"] = "manager action " + entry + " --extra"
    with pytest.raises(ValueError, match="quoting"):
        validate(agent)


def test_final_entry_rule_does_not_hide_invalid_prefix_quoting():
    agent = keyed_definition()
    agent["secret"]["fetch"] = 'manager "broken ' + agent["secret"]["entry"]
    with pytest.raises(ValueError, match="quoting"):
        validate(agent)


def test_final_entry_rule_requires_space_or_tab_boundary():
    agent = keyed_definition()
    agent["secret"]["fetch"] = "manager --entry=" + agent["secret"]["entry"]
    with pytest.raises(ValueError, match="quoting"):
        validate(agent)


@pytest.mark.parametrize("entry", [
    "agents/scout", "team's keys/$(literal); token", "nested/-segment", "contains\\backslash",
])
def test_entry_preserves_safe_opaque_punctuation(entry):
    assert validate_entry(entry) is entry


@pytest.mark.parametrize("entry", [
    None, "", False, "/absolute", "trailing/", "a//b", ".", "..", "a/./b",
    "a/../b", "-option", "a\x00b", "a\rb", "a\nb",
])
def test_entry_rejects_invalid_manager_locators(entry):
    with pytest.raises(ValueError):
        validate_entry(entry)


@pytest.mark.parametrize("name", ["_", "HOME", "lower_case2", "A" * 100])
def test_environment_name(name):
    assert validate_env_name(name) is name


@pytest.mark.parametrize("name", [None, False, "", "1BAD", "BAD-NAME", "A=B", "A\n", "é"])
def test_invalid_environment_name(name):
    with pytest.raises(ValueError):
        validate_env_name(name)

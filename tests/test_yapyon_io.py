"""Plain configuration values retain literal keys and the existing type boundary."""

import pytest
import yapyon

from persona_grata import yapyon_io as yio


def assert_literals(actual, expected):
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert list(actual) == list(expected)
        for key, value in expected.items():
            assert_literals(actual[key], value)
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for item, value in zip(actual, expected):
            assert_literals(item, value)
    else:
        assert type(actual) is type(expected)
        assert actual == expected


def assert_value_tree_roundtrip(value):
    reference = yapyon.parse(yapyon.dumps(value)).resolve()
    assert_literals(reference, value)
    tree = yio.value_tree(value)
    assert_literals(tree.resolve(), reference)
    assert_literals(yapyon.parse(tree.to_source()).resolve(), reference)
    return tree


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
@pytest.mark.parametrize("location", ["root", "nested", "list"])
def test_value_tree_keeps_canonically_equivalent_literal_keys(location, reverse):
    pairs = [(chr(0x00E9), "composed"), ("e" + chr(0x0301), "decomposed")]
    value = dict(reversed(pairs) if reverse else pairs)
    if location == "nested":
        value = {"outer": {"inner": value}}
    elif location == "list":
        value = [None, {"inner": value}, False]
    assert_value_tree_roundtrip(value)


@pytest.mark.parametrize("value", [
    None, False, True, 0, 1, -7, 2**100, "", "plain", "é é",
    'y"{not_a_reference}"', "quotes: '\"\\\n\t", [], {},
    [None, False, 0, "", [], {}],
], ids=[
    "none", "false", "true", "zero", "positive", "negative", "large-int",
    "empty-string", "string", "unicode", "source-looking-string", "escapes",
    "empty-list", "empty-dict", "false-like-list",
])
def test_value_tree_preserves_supported_root_values(value):
    assert_value_tree_roundtrip(value)


def test_value_tree_preserves_nested_false_like_values_and_literal_keys():
    value = {
        "é": {"none": None, "false": False, "zero": 0},
        "é": {"empty": "", "list": [], "dict": {}},
        "punctuation": [{"a.b": "not a path", "a:b": "not syntax"}],
        'quoted"key': {"back\\slash": 'y"{missing}"'},
        "nested": [{"é": [None, False, 0, "", [], {}], "é": True}],
    }
    assert_value_tree_roundtrip(value)


@pytest.mark.parametrize("value", [1.5, b"literal", (1,), {1}, object()],
                         ids=["float", "bytes", "tuple", "set", "object"])
@pytest.mark.parametrize("location", ["root", "dict", "list"])
def test_value_tree_rejects_unsupported_types(value, location):
    if location == "dict":
        value = {"child": value}
    elif location == "list":
        value = [value]
    with pytest.raises(TypeError, match="cannot store .* as configuration"):
        yio.value_tree(value)


@pytest.mark.parametrize("key", [1, None, True, ("key",)],
                         ids=["int", "none", "bool", "tuple"])
@pytest.mark.parametrize("location", ["root", "nested"])
def test_value_tree_rejects_non_string_keys(key, location):
    value = {key: "value"}
    if location == "nested":
        value = {"outer": [value]}
    with pytest.raises(TypeError, match="configuration keys must be strings"):
        yio.value_tree(value)

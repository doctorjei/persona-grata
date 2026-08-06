"""Checks for template_engine against spec/TEMPLATE_RESOLUTION.md."""

import textwrap

import pytest

from persona_grata import template_engine as te


def _resolve(yaml_text, env=None):
    return te.render(textwrap.dedent(yaml_text), env or {})


# --------------------------------------------------------------------------- #
# Stage 1 -- env macros
# --------------------------------------------------------------------------- #
def test_env_real_env_wins(monkeypatch):
    monkeypatch.setenv("FOO", "real")
    assert te.substitute_env("$FOO/${FOO}", {"FOO": "dflt"}) == "real/real"


def test_env_default_mapping_used_when_unset(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    assert te.substitute_env("$NOPE/x", {"NOPE": "def"}) == "def/x"


def test_env_unmatched_is_empty(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    assert te.substitute_env("a${MISSING}b") == "ab"


def test_env_single_pass_no_rescan(monkeypatch):
    # A value that itself looks like a macro must NOT be re-expanded (rule 1d).
    monkeypatch.setenv("A", "$B")
    monkeypatch.setenv("B", "boom")
    assert te.substitute_env("$A") == "$B"


# --------------------------------------------------------------------------- #
# Stage 3 -- reserved identifiers + tree scoping
# --------------------------------------------------------------------------- #
def test_self_and_parent_key_chain():
    cfg = _resolve("""
        foo:
          bar: "{{__PARENT__.__KEY__}}"
          baz: "{{self.__PARENT__.bar}}/z"
    """)
    assert cfg["foo"]["bar"] == "foo"
    assert cfg["foo"]["baz"] == "foo/z"


def test_root_beats_sibling():
    cfg = _resolve("""
        name: "root"
        outer:
          name: "sib"
          v: "{{name}}"
    """)
    assert cfg["outer"]["v"] == "root"      # absolute/root wins (rule 3b)


def test_nearest_uncle_beats_far_ancestor():
    # 'v' references {{a}}. A near uncle (mid.a) must win over the far ancestor
    # key 'a' (outer.a) -- level-based nearest-first, not category-based.
    cfg = _resolve("""
        outer:
          a:
            mid:
              a: "NEAR_UNCLE"
              kid:
                v: "{{a}}"
    """)
    assert cfg["outer"]["a"]["mid"]["kid"]["v"] == "NEAR_UNCLE"


def test_unresolvable_raises():
    with pytest.raises(te.TemplateError):
        _resolve('a: "{{nope}}"')


def test_traverse_into_scalar_raises():
    with pytest.raises(te.TemplateError):
        _resolve("""
            a: "hi"
            b: "{{a.x}}"
        """)


# --------------------------------------------------------------------------- #
# Regressions for the two MAJOR crash bugs (list leaf / stray brace)
# --------------------------------------------------------------------------- #
def test_template_inside_list_resolves():
    cfg = _resolve("""
        x: "hello"
        items:
          - "{{x}}"
          - "plain"
    """)
    assert cfg["items"] == ["hello", "plain"]


def test_stray_open_brace_left_untouched():
    cfg = _resolve('a: "plain {{ text"')
    assert cfg["a"] == "plain {{ text"


def test_value_with_literal_braces_is_injectable():
    # A resolved value that merely contains a stray "{{" must not defer forever.
    cfg = _resolve("""
        src: "a{{b"
        dst: "{{src}}/x"
    """)
    assert cfg["dst"] == "a{{b/x"


# --------------------------------------------------------------------------- #
# Canonical worked example from TEMPLATE_RESOLUTION.md (lines 35-48)
# --------------------------------------------------------------------------- #
def test_spec_worked_example(monkeypatch):
    monkeypatch.setenv("FOO", "$BAR")           # single pass: stays literal "$BAR"
    monkeypatch.delenv("BIZZLE", raising=False)
    c = _resolve("""
        foo:
          tek: "$FOO"
          sof: "$BIZZLE"
          bar: "{{__PARENT__.__KEY__}}"
          baz:
            tek: "{{__PARENT__.__PARENT__.bar}}/daf"
            zod: "{{baz.tek}}/zod"
            viq: "{{tek}}/viq"
            wel: "{{bar}}/wel"
          yot: "{{baz.tek}}/yot"
          buz: "{{self.__PARENT__.yot}}/buz"
    """)["foo"]
    assert c["tek"] == "$BAR"
    assert c["sof"] == ""
    assert c["bar"] == "foo"
    assert c["baz"] == {"tek": "foo/daf", "zod": "foo/daf/zod",
                        "viq": "foo/daf/viq", "wel": "foo/wel"}
    assert c["yot"] == "foo/daf/yot"
    assert c["buz"] == "foo/daf/yot/buz"


# --------------------------------------------------------------------------- #
# Scope precedence + cousins
# --------------------------------------------------------------------------- #
def test_root_beats_near_uncle():
    cfg = _resolve("""
        name: "ROOT"
        outer:
          name: "UNCLE"
          mid:
            leaf: "{{name}}"
    """)
    assert cfg["outer"]["mid"]["leaf"] == "ROOT"     # absolute/root wins (rule 3b)


def test_bare_cousin_is_unresolvable():
    with pytest.raises(te.TemplateError):
        _resolve("""
            root:
              uncle:
                cousin: "C"
              me:
                leaf: "{{cousin}}"
        """)


def test_cousin_reachable_by_qualification():
    cfg = _resolve("""
        root:
          uncle:
            cousin: "C"
          me:
            leaf: "{{uncle.cousin}}"
    """)
    assert cfg["root"]["me"]["leaf"] == "C"


# --------------------------------------------------------------------------- #
# Reserved-identifier edge cases
# --------------------------------------------------------------------------- #
def test_key_must_terminate_chain():
    with pytest.raises(te.TemplateError):
        _resolve('foo:\n  a: "{{__KEY__.x}}"\n')


def test_qualified_parent_key():
    cfg = _resolve("""
        grp:
          sib: "s"
          leaf: "{{sib.__PARENT__.__KEY__}}"
    """)
    assert cfg["grp"]["leaf"] == "grp"


# --------------------------------------------------------------------------- #
# Fixpoint: deferral, cycles, containers, ordering
# --------------------------------------------------------------------------- #
def test_deep_deferral_chain():
    cfg = _resolve("""
        a: "{{b}}/a"
        b: "{{c}}/b"
        c: "root"
    """)
    assert cfg["a"] == "root/b/a"


def test_cycle_raises():
    with pytest.raises(te.TemplateError):
        _resolve('a: "{{b}}"\nb: "{{a}}"\n')


def test_container_reference_raises():
    with pytest.raises(te.TemplateError):
        _resolve("""
            grp:
              x: "1"
            ref: "{{grp}}"
        """)


def test_ordering_independence():
    a = _resolve('a: "{{b}}/a"\nb: "z"\n')["a"]
    b = _resolve('b: "z"\na: "{{b}}/a"\n')["a"]
    assert a == b == "z/a"


# --------------------------------------------------------------------------- #
# Templated dict keys
# --------------------------------------------------------------------------- #
def test_templated_key_is_renamed():
    cfg = _resolve("""
        hid: "codex"
        providers:
          "{{hid}}":
            name: "n"
    """)
    assert list(cfg["providers"]) == ["codex"]


def test_templated_key_visible_to_values():
    # The rename lands before the value pass, so a value may reference it.
    cfg = _resolve("""
        hid: "codex"
        providers:
          "{{hid}}":
            name: "x"
        ref: "{{providers.codex.name}}"
    """)
    assert cfg["ref"] == "x"


def test_templated_key_resolves_at_its_own_position():
    cfg = _resolve("""
        outer:
          label: "L"
          "{{label}}-suffix": "v"
    """)
    assert list(cfg["outer"])[1] == "L-suffix"


def test_templated_key_preserves_order():
    cfg = _resolve("""
        k: "mid"
        m:
          first: 1
          "{{k}}": 2
          last: 3
    """)
    assert list(cfg["m"]) == ["first", "mid", "last"]


def test_templated_key_collision_raises():
    with pytest.raises(te.TemplateError):
        _resolve("""
            k: "dup"
            m:
              dup: 1
              "{{k}}": 2
        """)


def test_unresolvable_key_raises():
    with pytest.raises(te.TemplateError):
        _resolve('m:\n  "{{nope}}": 1\n')


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
def test_prune_drops_unset_but_keeps_falsey_scalars():
    assert te.prune({"a": "", "b": None, "c": 0, "d": False, "e": {}, "f": []}) == \
        {"c": 0, "d": False}


def test_prune_is_recursive():
    assert te.prune({"env": {"A": "1", "B": ""}, "empty": {"X": None}}) == {"env": {"A": "1"}}


def test_as_json_renders_and_prunes():
    cfg = _resolve("""
        model: "m1"
        store:
          model: "{{model}}"
          blank: ""
        content: "{{store.__AS_JSON__()}}"
    """)
    assert cfg["content"] == '{\n  "model": "m1"\n}'


def test_as_toml_nests_tables():
    cfg = _resolve("""
        hid: "codex"
        store:
          model: "m1"
          providers:
            "{{hid}}":
              wire_api: "responses"
        content: "{{store.__AS_TOML__()}}"
    """)
    assert cfg["content"] == (
        'model = "m1"\n'
        "\n"
        "[providers.codex]\n"
        'wire_api = "responses"\n'
    )


def test_toml_value_types_and_quoting():
    out = te.to_toml({"s": 'a"b\\c', "i": 3, "f": 1.5, "yes": True, "no": False,
                      "list": ["x", "y"], "odd key": "v"})
    assert 's = "a\\"b\\\\c"' in out
    assert "i = 3" in out and "f = 1.5" in out
    assert "yes = true" in out and "no = false" in out
    assert 'list = ["x", "y"]' in out
    assert '"odd key" = "v"' in out


def test_serializer_must_terminate_chain():
    with pytest.raises(te.TemplateError):
        _resolve('a:\n  b: "x"\nc: "{{a.__AS_JSON__().d}}"\n')


def test_unknown_serializer_raises():
    with pytest.raises(te.TemplateError):
        _resolve('a:\n  b: "x"\nc: "{{a.__AS_YAML__()}}"\n')


def test_serializer_defers_until_subtree_is_resolved():
    # 'content' is serialized only after every {{}} inside the subtree is gone,
    # regardless of declaration order.
    cfg = _resolve("""
        content: "{{store.__AS_JSON__()}}"
        store:
          a: "{{late}}"
        late: "value"
    """)
    assert cfg["content"] == '{\n  "a": "value"\n}'

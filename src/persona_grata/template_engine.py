"""Hierarchical template-resolution engine (conforms to TEMPLATE_RESOLUTION.md).

Standalone: no persona/harness knowledge. Three stages, in spec order:

  1. substitute_env(raw_text, defaults) -- $FOO / ${FOO} macros on RAW TEXT,
     single pass, before parsing (rule 1).
  2. yaml.safe_load                     -- parse (caller's concern, or use render()).
  3. resolve_tree(data)                 -- {{ident}} resolution over the parsed
     tree: reserved identifiers + tree-scoped lookup, fixpoint until stable (rules 0/2/3).

Both dict *values* and dict *keys* may hold templates; a templated key is resolved
as if it were a node at its own position, then renamed in place.

A reference may end in a serializer call -- ``{{some.subtree.__AS_JSON__()}}`` --
which renders that subtree as text instead of resolving to a scalar.
"""

import os
import re
import copy
import json

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_TMPL_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_CALL_RE = re.compile(r"^(__[A-Za-z0-9_]+__)\(\)$")

_RESERVED = ("self", "__PARENT__", "__KEY__")


class TemplateError(Exception):
    """A reference is malformed or cannot be resolved in any way (rule 0c)."""


class _Deferred(Exception):
    """Reference is not resolvable *this pass* (target missing/still pending)."""


# --------------------------------------------------------------------------- #
# Stage 1 -- environment macros (rule 1)
# --------------------------------------------------------------------------- #
def substitute_env(text, defaults=None):
    """Substitute ``$FOO`` / ``${FOO}`` on raw text in a single pass.

    Value precedence: real environment -> ``defaults`` mapping -> empty string
    (rule 1e, generalized so callers can supply defaults for chosen vars).
    Substituted content is not re-scanned (rule 1d).
    """
    if not isinstance(text, str):
        return text
    defaults = defaults or {}

    def repl(m):
        name = m.group(1) or m.group(2)
        if name in os.environ:
            return os.environ[name]
        if name in defaults:
            return defaults[name]
        return ""

    return _ENV_RE.sub(repl, text)


# --------------------------------------------------------------------------- #
# Serializers -- terminal `__AS_XXX__()` calls that render a subtree as text.
# --------------------------------------------------------------------------- #
def prune(value):
    """Drop unset entries: ``None``, ``""``, and containers left empty by pruning.

    This implements the schema's "not sent if empty/unset" convention, so a
    persona that leaves (say) ``mind.model_2`` blank does not emit a key set to
    the empty string into the harness's config file.
    """
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            pruned = prune(val)
            if pruned is None or pruned == "" or (isinstance(pruned, (dict, list)) and not pruned):
                continue
            out[key] = pruned
        return out
    if isinstance(value, list):
        return [prune(v) for v in value if v is not None and v != ""]
    return value


def to_json(value):
    """Render a subtree as indented JSON (unset entries pruned)."""
    return json.dumps(prune(value), indent=2)


def _toml_key(key):
    key = str(key)
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else _toml_str(key)


def _toml_str(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_value(value):
    if isinstance(value, bool):                    # before int -- bool subclasses int
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return _toml_str(value)


def _toml_table(data, prefix, out):
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}

    # A super-table holding nothing but sub-tables needs no header of its own.
    if prefix and (scalars or not tables):
        out.append("[" + ".".join(_toml_key(k) for k in prefix) + "]")
    for key, val in scalars.items():
        out.append(f"{_toml_key(key)} = {_toml_value(val)}")
    for key, val in tables.items():
        if out and out[-1] != "":
            out.append("")
        _toml_table(val, prefix + [key], out)


def to_toml(value):
    """Render a subtree as TOML (unset entries pruned).

    Nested dicts become tables: ``{a: {b: {x: 1}}}`` -> ``[a.b]`` / ``x = 1``.
    """
    value = prune(value)
    if not isinstance(value, dict):
        raise TemplateError("__AS_TOML__() needs a mapping, not " + type(value).__name__)
    out = []
    _toml_table(value, [], out)
    return "\n".join(out).strip() + "\n"


_SERIALIZERS = {
    "__AS_JSON__": to_json,
    "__AS_TOML__": to_toml,
}


# --------------------------------------------------------------------------- #
# Tree navigation helpers -- a "node" is addressed by a path (list of keys).
# --------------------------------------------------------------------------- #
def _get(root, path):
    """Return the value at ``path`` (dict keys and/or list indices) or raise KeyError."""
    cur = root
    for key in path:
        if isinstance(cur, list) and isinstance(key, int) and -len(cur) <= key < len(cur):
            cur = cur[key]
        elif isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            raise KeyError(key)
    return cur


def _scope_lookup(name, path, root):
    """Resolve a first regular identifier to a node path by scope (rule 3 b-e).

    Precedence: absolute/root -> sibling -> ancestral -> ancestral-sibling.
    ``path`` is the address of the node whose value holds the template.
    Returns the resolved node path, or raises _Deferred if nothing matches.
    """
    # (b) absolute / root identifier -- beats sibling (rule 3b).
    if isinstance(root, dict) and name in root:
        return [name]

    # (c) sibling -- same parent dict, excluding the current node's own key.
    if path:
        parent = _get(root, path[:-1])
        if isinstance(parent, dict) and name in parent and name != path[-1]:
            return path[:-1] + [name]

    # Climb level by level, nearest first. At each ancestor level check the
    # ancestor itself (d, rule 3d) then its siblings -- uncle/aunt (e, rule 3e)
    # -- before climbing further. Because it is level-based, a *near* uncle
    # beats a *far* ancestor; cousins (an uncle's children) are never searched.
    for depth in range(len(path) - 1, 0, -1):
        if path[depth - 1] == name:                      # (d) this ancestor's key
            return path[:depth]
        gp = _get(root, path[:depth - 1])                # (e) a sibling of this ancestor
        anc_key = path[depth - 1]
        if isinstance(gp, dict) and name in gp and name != anc_key:
            return path[:depth - 1] + [name]

    raise _Deferred(name)


def _resolve_ref(ref, path, root):
    """Resolve one ``{{...}}`` reference to a Python value.

    ``ref`` is the trimmed inner text (a dotted chain). ``path`` is the address
    of the node whose value holds the template. Reserved identifiers apply to
    the running cursor, or to the current node when unqualified (rule 2b). A
    trailing ``__AS_XXX__()`` segment serializes the subtree the chain reached.
    """
    segments = ref.split(".")
    cursor = None            # None => relative to the current node
    key_result = None        # set once __KEY__ terminates the chain

    for i, seg in enumerate(segments):
        if key_result is not None:
            raise TemplateError(f"'__KEY__' must end a reference: {ref!r}")

        call = _CALL_RE.match(seg)
        if call:
            name = call.group(1)
            if name not in _SERIALIZERS:
                raise TemplateError(f"unknown serializer {name}() in {ref!r}")
            if i != len(segments) - 1:
                raise TemplateError(f"{name}() must end a reference: {ref!r}")
            if cursor is None:
                raise TemplateError(f"{name}() needs a target: {ref!r}")
            return _SERIALIZERS[name](_get(root, cursor))

        if seg == "self":
            if i != 0:
                raise TemplateError(f"'self' is only valid as the first segment: {ref!r}")
            cursor = list(path)

        elif seg == "__PARENT__":
            base = cursor if cursor is not None else path
            if not base:
                raise TemplateError(f"'__PARENT__' has no parent at root: {ref!r}")
            cursor = base[:-1]

        elif seg == "__KEY__":
            base = cursor if cursor is not None else path
            if not base:
                raise TemplateError(f"'__KEY__' has no key at root: {ref!r}")
            key_result = base[-1]

        elif cursor is None:
            # First regular segment -> scope lookup (may defer).
            cursor = _scope_lookup(seg, path, root)

        else:
            # Later regular segment -> plain child traversal.
            node = _get(root, cursor)
            if not isinstance(node, dict) or seg not in node:
                raise TemplateError(f"cannot traverse into {'.'.join(cursor)!r} for {ref!r}")
            cursor = cursor + [seg]

    if key_result is not None:
        return key_result

    value = _get(root, cursor)
    if isinstance(value, (dict, list)):
        raise TemplateError(f"reference {ref!r} resolves to a container, not a scalar")
    return value


def _resolve_leaf(s, path, root):
    """One resolution pass over a single string leaf.

    Returns (new_string, progressed). A reference resolves only if its target is
    fully resolved (no residual ``{{}}``); otherwise it is left in place for a
    later pass (rule 0b). Malformed references raise immediately (rule 0c).
    """
    progressed = False

    def repl(m):
        nonlocal progressed
        ref = m.group(1).strip()
        try:
            value = _resolve_ref(ref, path, root)
        except _Deferred:
            return m.group(0)
        sval = str(value)
        if _TMPL_RE.search(sval):     # target still holds a real {{...}} -> defer
            return m.group(0)
        progressed = True
        return sval

    return _TMPL_RE.sub(repl, s), progressed


# --------------------------------------------------------------------------- #
# Stage 3 -- tree resolution (rules 0/2/3)
# --------------------------------------------------------------------------- #
def _value_leaves(node, path):
    """Yield the path of every string leaf that still holds a ``{{...}}``."""
    if isinstance(node, dict):
        for key, val in node.items():
            yield from _value_leaves(val, path + [key])
    elif isinstance(node, list):
        for idx, val in enumerate(node):
            yield from _value_leaves(val, path + [idx])
    elif isinstance(node, str) and _TMPL_RE.search(node):
        yield path


def _key_leaves(node, path):
    """Yield ``(parent_path, key)`` for every dict key that still holds a ``{{...}}``."""
    if isinstance(node, dict):
        for key, val in node.items():
            if isinstance(key, str) and _TMPL_RE.search(key):
                yield path, key
            yield from _key_leaves(val, path + [key])
    elif isinstance(node, list):
        for idx, val in enumerate(node):
            yield from _key_leaves(val, path + [idx])


def _rename_key(root, parent_path, old, new):
    """Rename a key in place, preserving insertion order."""
    parent = _get(root, parent_path)
    if new in parent:
        where = ".".join(map(str, parent_path)) or "<root>"
        raise TemplateError(f"resolved key {new!r} collides with an existing key at {where}")
    renamed = [(new if k == old else k, v) for k, v in parent.items()]
    parent.clear()
    parent.update(renamed)


def _resolve_keys_pass(root):
    """Resolve at most one templated key, then report back.

    A rename invalidates every path below it, so the caller restarts the sweep
    rather than continuing with stale addresses. Returns ``(renamed, pending)``.
    """
    pending = list(_key_leaves(root, []))
    for parent_path, key in pending:
        new_key, _ = _resolve_leaf(key, parent_path + [key], root)
        if new_key != key:
            _rename_key(root, parent_path, key, new_key)
            return True, pending
    return False, pending


def resolve_tree(data):
    """Resolve every ``{{ident}}`` in a parsed tree; return a resolved deep copy.

    Iterates to a fixpoint: each pass resolves what it can; a pass that makes no
    progress while ``{{}}`` remain means the remaining references are unresolvable
    (missing target or cycle) -> TemplateError (rule 0c). Keys get first crack at
    each pass so that values referring to them see their final names.
    """
    root = copy.deepcopy(data)

    while True:
        renamed, pending_keys = _resolve_keys_pass(root)
        if renamed:
            continue                     # paths shifted -- re-sweep from the top
        pending_vals = list(_value_leaves(root, []))
        if not pending_keys and not pending_vals:
            break

        progressed = False
        for path in pending_vals:
            new_val, moved = _resolve_leaf(_get(root, path), path, root)
            if new_val != _get(root, path):
                _set(root, path, new_val)
            progressed = progressed or moved

        if not progressed:
            stuck = [f"{_TMPL_RE.search(k).group(0)} (key at {'.'.join(map(str, p)) or '<root>'})"
                     for p, k in pending_keys]
            stuck += [f"{_TMPL_RE.search(_get(root, p)).group(0)} (at {'.'.join(map(str, p))})"
                      for p in pending_vals]
            raise TemplateError("unresolvable references: " + "; ".join(stuck))

    return root


def _set(root, path, value):
    cur = root
    for key in path[:-1]:
        cur = cur[key]
    cur[path[-1]] = value


# --------------------------------------------------------------------------- #
# Convenience: raw text -> resolved tree
# --------------------------------------------------------------------------- #
def render(raw_text, env_defaults=None):
    """substitute_env -> yaml.safe_load -> resolve_tree."""
    import yaml
    substituted = substitute_env(raw_text, env_defaults)
    return resolve_tree(yaml.safe_load(substituted))

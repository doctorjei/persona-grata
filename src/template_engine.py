"""Hierarchical template-resolution engine (conforms to TEMPLATE_RESOLUTION.md).

Standalone: no persona/harness knowledge. Three stages, in spec order:

  1. substitute_env(raw_text, defaults) -- $FOO / ${FOO} macros on RAW TEXT,
     single pass, before parsing (rule 1).
  2. yaml.safe_load                     -- parse (caller's concern, or use render()).
  3. resolve_tree(data)                 -- {{ident}} resolution over the parsed
     tree: reserved identifiers + tree-scoped lookup, fixpoint until stable (rules 0/2/3).
"""

import os
import re
import copy

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_TMPL_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}")

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
    the running cursor, or to the current node when unqualified (rule 2b).
    """
    segments = ref.split(".")
    cursor = None            # None => relative to the current node
    key_result = None        # set once __KEY__ terminates the chain

    for i, seg in enumerate(segments):
        if key_result is not None:
            raise TemplateError(f"'__KEY__' must end a reference: {ref!r}")

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
def resolve_tree(data):
    """Resolve every ``{{ident}}`` in a parsed tree; return a resolved deep copy.

    Iterates to a fixpoint: each pass resolves what it can; a pass that makes no
    progress while ``{{}}`` remain means the remaining references are unresolvable
    (missing target or cycle) -> TemplateError (rule 0c).
    """
    root = copy.deepcopy(data)

    def leaves(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from leaves(v, path + [k])
        elif isinstance(node, list):
            for idx, v in enumerate(node):
                yield from leaves(v, path + [idx])
        elif isinstance(node, str) and _TMPL_RE.search(node):
            yield path

    while True:
        pending = list(leaves(root, []))
        if not pending:
            break
        progressed = False
        for path in pending:
            new_val, moved = _resolve_leaf(_get(root, path), path, root)
            if new_val != _get(root, path):
                _set(root, path, new_val)
            progressed = progressed or moved
        if not progressed:
            remaining = ["{{" + _TMPL_RE.search(_get(root, p)).group(1).strip() + "}}"
                         + f" (at {'.'.join(map(str, p))})" for p in pending]
            raise TemplateError("unresolvable references: " + "; ".join(remaining))

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

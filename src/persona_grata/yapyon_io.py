"""Reading and writing yapyon documents for persona-grata.

The data files are **yapyon**, not YAML: a preset in ``data/`` is loaded with
``yapyon.parse()``, which resolves nothing, and the pieces are merged by
persona-grata's own layering.

**One document is resolved, not a graft.**  A node tree assembled from several
``parse()`` calls resolves *silently wrong* -- ``{mind.model}`` splices as
``''`` and ``{endpoint}`` as ``None`` -- while the same tree re-emitted as one
document and handed back to yapyon resolves correctly.  So the pipeline merges
plain Python data here, and :func:`resolve_document` calls yapyon once, on the
whole thing.

**yapyon does all of the resolving now** (0.4.0, 2026-10-21).  Dialect
negotiation is a *declared function*, ``match_first``, handed to the loader in
``data/functions.contract`` (SPEC §13); nested references are ordinary holes;
environment variables are ``{$NAME}`` (strict -- unset is akan) and
``{__ENV__(name, fallback)}`` (SPEC §5.1.3).  Our former second pass
(``resolver.py``) and env layer (``_substitute`` / ``_env``) are gone.

**What a document may read from the environment is decided per file.**  yapyon
exposes only what ``env_allow`` names.  Our base file (``agents.tmpl``) reads
:data:`RESERVED_ENV` and is resolved on its own, early, because assembly needs
``persona_store`` before anything else.  Everything the *user* wrote resolves
in the final pass under ``RESERVED_ENV | env_allow | env_add``, the last two
from ``persona_store.cfg`` -- see ``__init__.load_config``.
"""

import contextlib
import functools
import os
from pathlib import Path

import yapyon

__all__ = ["YStr", "RESERVED_ENV", "load", "load_resolved", "settle", "emit",
           "resolve_document", "ast_prune"]


class YStr:
    """A y-string held as its source spelling, unresolved.

    A leaf, deliberately: it must not be a ``dict`` or ``list`` or
    :func:`persona_grata.deep_merge` would merge it as a container instead of
    replacing it.  ``str`` is deliberately *not* its base, so nothing mistakes
    a deferred reference for a settled value.
    """

    __slots__ = ("source",)

    def __init__(self, source):
        self.source = source

    def __repr__(self):
        return f"YStr({self.source!r})"

    def __eq__(self, other):
        return isinstance(other, YStr) and other.source == self.source

    def __hash__(self):
        return hash(("YStr", self.source))


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #

def _plain(node):
    """A parsed node tree as plain Python, with unresolved y-strings as YStr."""
    if isinstance(node, yapyon.Mapping):
        return {p.key: _plain(p.value) for p in node.pairs}
    if isinstance(node, yapyon.Sequence):
        return [_plain(i) for i in node.items]
    if isinstance(node, yapyon.MultiMap):
        # A multimap is keyed and ordered with repeatable keys; persona-grata has
        # no use for one today, so an entry is materialised under its key and a
        # repeat is an authoring error rather than a silent last-one-wins.
        out = {}
        for entry in node.entries:
            if entry.key in out:
                raise ValueError(
                    f"multimap key {entry.key!r} repeats; persona-grata has no "
                    f"multimap form, so a repeat cannot be represented")
            out[entry.key] = _plain(entry.value)
        return out
    if isinstance(node, yapyon.YString):
        # A y-string with **no holes** is self-contained -- its text is its value.
        # Anything with a hole waits for the assembled document as a YStr.
        if all(kind == "text" for kind, _ in node.parts):
            return "".join(v for k, v in node.parts if k == "text")
        return YStr(_ystring_source(node))
    if isinstance(node, yapyon.Scalar):
        return node.value
    if isinstance(node, yapyon.ResolvedTemplate):
        raise ValueError("a yt template reached the loader; it needs a consumer")
    raise ValueError(f"unknown node type {type(node).__name__}")


def _ystring_source(node):
    """The y-string as it was authored: text braces escaped, holes as written."""
    body = []
    for kind, value in node.parts:
        if kind == "text":
            body.append(value.replace("{", "{{").replace("}", "}}"))
        else:
            body.append("{" + value + "}")
    body = "".join(body)
    quote = "'" if '"' in body else '"'
    return f"{node.prefix}{quote}{body}{quote}"


def load(path):
    """Parse one document. Resolves nothing -- that happens once, on the whole.

    Returns plain Python with :class:`YStr` leaves, or ``None`` if the file is
    absent, so callers can keep their existing ``or {}`` handling.
    """
    path = Path(path)
    if not path.exists():
        return None
    return _plain(yapyon.parse(path.read_text(), source=str(path)))


# --------------------------------------------------------------------------- #
# environment and the contract
# --------------------------------------------------------------------------- #

#: The environment persona-grata itself needs, and always gets: the persona-store
#: chain ``$PERSONA_STORE_DIR -> $XDG_CONFIG_HOME/personas -> ~/.config/personas``.
#: Ruled 2026-10-21: these three are reserved -- a user's ``env_allow`` cannot
#: remove them.  Only ``PERSONA_STORE_DIR`` is outside ``yapyon.STANDARD_ENV``.
RESERVED_ENV = frozenset({"HOME", "PERSONA_STORE_DIR", "XDG_CONFIG_HOME"})


def environment(env_defaults=None):
    """The mapping yapyon reads ``{$NAME}`` from: the real environment, over defaults.

    Same precedence the old ``$VAR`` layer had -- a set variable always wins.
    ``env_allow`` still decides what a document may *see* of this; a default
    for a name nobody allowed is never visible.
    """
    return {**(env_defaults or {}), **os.environ}


@functools.lru_cache(maxsize=None)
def contract():
    """The functions our templates may call (``data/functions.contract``)."""
    path = Path(__file__).parent / "data" / "functions.contract"
    with path.open() as fp:
        return yapyon.load_contract(fp, source=str(path))


def load_resolved(path, env_allow, env_defaults=None):
    """Load *and resolve* one document on its own, as plain Python.

    For our base file only: it must be settled before assembly (``persona_store``
    locates the store whose settings are layered in next), and it reads a
    different slice of the environment than anything the user wrote.
    """
    path = Path(path)
    return yapyon.loads(path.read_text(), source=str(path),
                        env_allow=env_allow, env=environment(env_defaults))


def settle(value, env_allow, env_defaults=None):
    """Resolve a lone value early, if it is still a :class:`YStr`.

    A user's ``persona_store: y"{$HOME}/personas"`` is a hole, so it would
    otherwise wait for the final pass -- but assembly needs it now.  It is
    resolved as a one-key document, so a reference to anything *else* in the
    tree is akan, loudly, rather than guessed at.
    """
    if not isinstance(value, YStr):
        return value
    return yapyon.loads(f"value: {value.source}\n", env_allow=env_allow,
                        env=environment(env_defaults))["value"]


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #

def _quote(text):
    if '"' in text and "'" not in text:
        return f"'{text}'"
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _scalar(value):
    if value is None:
        return "None"
    if value is True:
        return "True"
    if value is False:
        return "False"
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError(f"cannot write {type(value).__name__} as yapyon")


def emit(data, indent=0):
    """Plain Python back to yapyon source, so yapyon can resolve it once.

    Deliberately the only place we re-serialise: a hand-rolled emitter is
    cheaper here than a resolver that tolerates a grafted tree, which it does
    not.
    """
    pad = "  " * indent
    if isinstance(data, dict):
        if not data:
            return pad + "{}\n"
        out = []
        for key, value in data.items():
            out.append(f"{pad}{key}:\n" + emit(value, indent + 1))
        return "".join(out)
    if isinstance(data, list):
        if not data:
            return pad + "[]\n"
        return "".join(f"{pad}-\n" + emit(v, indent + 1) for v in data)
    if isinstance(data, YStr):
        return pad + data.source + "\n"
    return pad + _scalar(data) + "\n"


def holds_ystring(value):
    """True if *value* still holds a deferred y-string anywhere -- not yet resolved."""
    if isinstance(value, YStr):
        return True
    if isinstance(value, dict):
        return any(holds_ystring(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(holds_ystring(v) for v in value)
    return False


def _inline(value):
    """A value small enough to sit beside its key, or ``None`` if it is not."""
    if isinstance(value, YStr):
        return value.source
    if isinstance(value, (dict, list)):
        if not value:
            return "{}" if isinstance(value, dict) else "[]"
        if isinstance(value, list) and all(_inline(v) is not None
                                           and not isinstance(v, (dict, list)) for v in value):
            return "[" + ", ".join(_inline(v) for v in value) + "]"
        return None
    return _scalar(value)


def emit_config(data, indent=0):
    """A mapping as yapyon someone would write by hand: scalars beside their keys.

    For files a person reads and edits (``--export``), where :func:`emit`'s one
    value per line is correct but unrecognisable next to the shipped presets.
    """
    pad = "  " * indent
    out = []
    for key, value in data.items():
        inline = _inline(value)
        if inline is not None:
            out.append(f"{pad}{key}: {inline}\n")
        elif isinstance(value, dict):
            out.append(f"{pad}{key}:\n" + emit_config(value, indent + 1))
        else:
            out.append(f"{pad}{key}:\n" + emit(value, indent + 1))
    return "".join(out)


#: yapyon deliberately does not prune.  Its serializers' own docstring is
#: explicit: dropping keys the document wrote is a silent reinterpretation (law 7),
#: so "a consumer wanting that behaviour prunes its own tree first."  That is the
#: 2026-10-05 ruling in their words -- *prune, then let yapyon serialize*.
#:
#: The pruning cannot happen before the resolve.  Measured: pruning the merged tree
#: first makes every reference to an unset key an error -- ``y"{mind.model_1}"``
#: becomes "there is no key 'model_1' here" -- because the templates deliberately
#: point at keys the schema leaves empty (``harness/claude.harness.tmpl`` reads
#: ``mind.model_1..4``) and rely on them resolving to ``""`` and *then* vanishing.
#: So the prune has to land at the one moment both orders hold: after the target is
#: settled, before the bytes are written.  That is inside the serializer.
#:
#: ⚠️ There is still no registration hook for this -- ``SERIALIZERS`` is a
#: module-level dict yapyon's resolver looks up by name.  So the policy is installed
#: around our own resolve and put back after, rather than left patched in: nothing
#: here should silently change what yapyon means for anyone else in the process.
def _ast_empty(node):
    """Is this resolved node nothing at all -- ``None``, ``""``, or an emptied container?

    Keeps the notion of "unset" the retired template engine's ``prune`` had, but over
    yapyon's AST rather than over plain ``dict``/``list``.  ⚠️ ``0`` and ``False`` are NOT
    empty; only ``None`` and the empty string are, same as the dict version.
    """
    if isinstance(node, yapyon.Scalar):
        return node.value is None or node.value == ""
    if isinstance(node, yapyon.Mapping):
        return all(_ast_empty(pair.value) for pair in node.pairs)
    if isinstance(node, yapyon.Sequence):
        return all(_ast_empty(item) for item in node.items)
    return False


def ast_prune(node):
    """Return ``node`` with every unset entry dropped, walking yapyon's AST.

    ⚠️ **Why this exists at all, and why it is not the old dict-based ``prune``.**
    Through yapyon 0.2.0 the plain-value writers were handed resolved *values*,
    so a ``dict``-keyed prune acted on them.  0.3.0 removed that split -- every
    serializer is now handed the resolved **AST** (``Mapping`` / ``Sequence`` /
    ``Scalar``, not ``dict`` / ``list``), so ``prune``'s
    ``isinstance(value, dict)`` test never fires and the wrap silently became a
    no-op: ``__AS_JSON__`` started emitting ``"b": ""`` and ``"c": null`` into
    rendered harness configs.  Measured on 0.3.0, all four writers.

    Rebuilding the nodes rather than mutating them keeps the emitter's line/col
    intact, so a refusal inside a writer still points at the right place.
    """
    if isinstance(node, yapyon.Mapping):
        return yapyon.Mapping(
            node.line, node.col,
            [yapyon.Pair(pair.line, pair.col, pair.key, ast_prune(pair.value),
                         getattr(pair, "lexeme", ""))
             for pair in node.pairs if not _ast_empty(pair.value)])
    if isinstance(node, yapyon.Sequence):
        return yapyon.Sequence(
            node.line, node.col,
            [ast_prune(item) for item in node.items if not _ast_empty(item)])
    return node


@contextlib.contextmanager
def _pruned_serializers():
    """Put :func:`ast_prune` in front of every yapyon writer, for this block only.

    ⚠️ **0.3.0 changed the shape of a serializer and broke the old wrap.**  A
    serializer is no longer a bare callable -- it is a
    :class:`yapyon.serializers.Builtin`` carrying ``write``, ``takes``,
    ``keywords`` and ``note``, and the resolver type-checks the argument against
    ``takes`` before calling.  Wrapping one in a plain lambda therefore raises
    ``AttributeError: 'function' object has no attribute 'takes'`` -- which is
    what 138 test failures came from on the first run against 0.3.0.  The wrap
    rebuilds a ``Builtin`` and copies those fields across.

    ⚠️ **``READS_AST`` is gone, and with it the skip.**  Through 0.2.0 only
    ``__AS_YAPYON__`` read the AST, so the wrap skipped it and the other three
    pruned -- the asymmetry ruled on 2026-10-12 as "accept and document".  0.3.0
    hands the AST to all four, so there is nothing to skip and, with
    :func:`ast_prune`, no reason to.  **All four now prune alike, which closes
    that asymmetry** -- see devnotes, the 0.3.0 integration entry.
    """
    from yapyon import serializers as _ys

    def wrap(builtin):
        def write(node, **kwargs):
            return builtin.write(ast_prune(node), **kwargs)

        return type(builtin)(write, takes=builtin.takes,
                            keywords=builtin.keywords, note=builtin.note)

    saved = dict(_ys.SERIALIZERS)
    _ys.SERIALIZERS.update({name: wrap(b) for name, b in saved.items()})
    try:
        yield
    finally:
        _ys.SERIALIZERS.clear()
        _ys.SERIALIZERS.update(saved)


def resolve_document(data, env_defaults=None, env_allow=None):
    """The whole pipeline's last step: one yapyon resolve of the assembled tree.

    ``env_allow`` is what the *user's* documents may read -- ``RESERVED_ENV`` plus
    ``persona_store.cfg``'s ``env_allow`` / ``env_add``, worked out by the caller.
    Left ``None`` it is ``RESERVED_ENV | STANDARD_ENV``, the default a store with
    no settings gets.
    """
    if env_allow is None:
        env_allow = RESERVED_ENV | yapyon.STANDARD_ENV
    # The document resolved here is one we generated, so its line numbers are not
    # the user's -- naming it says so, instead of sending someone to look for line
    # 751 of a three-line file.
    with _pruned_serializers():
        return yapyon.loads(emit(data), contract=contract(), env_allow=env_allow,
                            env=environment(env_defaults),
                            source="<assembled configuration>")

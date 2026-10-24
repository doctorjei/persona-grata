"""Compose in-use definitions without evaluating their remaining slots or expressions."""

import os
from pathlib import Path
import stat
import tempfile

import yapyon

from .identifiers import validate_id


DATA_DIR = Path(__file__).parent / "data"
KIND_DIRS = {
    "agent": "agents",
    "persona": "personas",
    "harness": "harnesses",
    "dialect": "dialects",
    "secret": "secret",
}


def _check_label(label):
    if len(label) != 2 or label[1] not in KIND_DIRS:
        raise ValueError("a preset label must be <id>.<kind>: " + ".".join(label))
    name, kind = label
    validate_id(name, kind)


def load_preset(label, *, data_dir=DATA_DIR):
    """Read an explicit ``(id, kind)`` from one catalog; never evaluate it."""
    _check_label(label)
    name, kind = label
    path = Path(data_dir) / KIND_DIRS[kind] / f"{name}.{kind}.tmpl"
    return read_document(path)


def read_text(path):
    """Read at most 8 MiB of regular UTF-8 source without following a final symlink."""
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"configuration must be a regular file: {path}")
        if os.fstat(stream.fileno()).st_size > 8 * 1024 * 1024:
            raise ValueError(f"configuration exceeds 8 MiB: {path}")
        text = stream.read(8 * 1024 * 1024 + 1)
        if len(text) > 8 * 1024 * 1024:
            raise ValueError(f"configuration exceeds 8 MiB: {path}")
        return text


def read_document(path):
    """Read unfinished source without following a final symlink or blocking on a FIFO."""
    return yapyon.parse(read_text(path), source=str(path))


def read_record(path):
    """Read literal registry data with the same filesystem guards as documents."""
    return yapyon.loads_record(read_text(path), source=str(path))


def _slot_error(slot, message):
    source, line, column = slot.where
    location = f"{source or '<document>'}:{line}:{column}"
    return ValueError(f"{location}: {message}")


def compose(tree, *, kinds=None, lookup=load_preset, max_fills=1000):
    """Fill selected named slots in a copy of ``tree``, without resolving it.

    ``kinds=None`` fills every supported named slot. Abstract slots, slots in
    their overrides, and unselected kinds stay deferred. ``lookup(label)``
    supplies a Tree or Handle; callers choose a catalog explicitly.
    """
    if kinds is not None:
        kinds = frozenset(kinds)
        unknown = kinds - KIND_DIRS.keys()
        if unknown:
            raise ValueError("unknown preset kinds: " + ", ".join(sorted(unknown)))
    if max_fills < 1:
        raise ValueError("max_fills must be positive")

    result = yapyon.merge(tree)
    result.source = tree.source
    result.warnings = list(tree.warnings)
    history = {}
    fills = 0
    while True:
        for slot in result.slots():
            label = slot.label
            if not label:
                continue
            if kinds is not None and len(label) >= 2 and label[1] not in kinds:
                continue
            try:
                _check_label(label)
            except ValueError as exc:
                raise _slot_error(slot, str(exc)) from exc

            previous = history.setdefault(slot.path, [])
            if label in previous:
                chain = " -> ".join(".".join(item) for item in [*previous, label])
                raise _slot_error(slot, f"preset inheritance cycle: {chain}")
            if fills >= max_fills:
                raise _slot_error(
                    slot, f"preset expansion exceeds {max_fills} fills; "
                    "check for recursive presets")
            try:
                filler = lookup(label)
            except FileNotFoundError as exc:
                raise _slot_error(slot, f"preset {'.'.join(label)!r} not found: {exc}") from exc
            slot.fill(filler)
            if isinstance(filler, yapyon.Tree):
                result.warnings.extend(filler.warnings)
            previous.append(label)
            fills += 1
            break
        else:
            return result


def compose_preset(kind, name, *, lookup=load_preset, max_fills=1000):
    """Collapse one category's inheritance, leaving other categories as slots."""
    label = (name, kind)
    _check_label(label)
    tree = lookup(label)
    if not isinstance(tree, yapyon.Tree):
        raise TypeError("a category document must be a yapyon Tree")
    if kind not in tree:
        raise ValueError(f"preset {name}.{kind!s} has no {kind!r} branch")
    return compose(tree, kinds={kind}, lookup=lookup, max_fills=max_fills)


def write_document(tree, path, *, overwrite=False):
    """Atomically save unfinished yapyon; replacing a file requires opt-in.

    Yapyon may refuse non-representable deferred merge state. Serialize before
    touching the filesystem so such a refusal cannot damage an existing file.
    Comments and original layout are not preserved by ``to_source()``.
    """
    text = tree.to_source()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path

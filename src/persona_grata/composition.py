"""Compose in-use definitions without evaluating their remaining slots or expressions."""

import os
from pathlib import Path
import stat
import tempfile

import yapyon

from .identifiers import validate_id


DATA_DIR = Path(__file__).parent / "data"
KIND_DIRS = {
    "agent": "agent",
    "persona": "persona",
    "harness": "harness",
    "dialect": "dialect",
    "secret": "secret",
}
MAX_SOURCE_BYTES = 8 * 1024 * 1024


def _check_label(label):
    if not label:
        raise ValueError("an include label must have at least one name segment")
    for segment in label:
        validate_id(segment, "include segment")


def load_preset(label, *, data_dir=DATA_DIR):
    """Read an explicitly selected packaged ``kind.name``; never evaluate it."""
    _check_label(label)
    if len(label) != 2 or label[0] not in KIND_DIRS:
        raise FileNotFoundError("no packaged preset for " + ".".join(label))
    kind, name = label
    path = Path(data_dir) / KIND_DIRS[kind] / f"{name}.tmpl"
    return read_document(path)


def read_text(path):
    """Read at most 8 MiB of regular UTF-8 source, following user-selected links."""
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"configuration must be a regular file: {path}")
        if os.fstat(stream.fileno()).st_size > MAX_SOURCE_BYTES:
            raise ValueError(f"configuration exceeds 8 MiB: {path}")
        text = stream.read(MAX_SOURCE_BYTES + 1)
        if len(text) > MAX_SOURCE_BYTES:
            raise ValueError(f"configuration exceeds 8 MiB: {path}")
        return text


def read_document(path):
    """Read unfinished source while refusing non-regular files such as FIFOs."""
    return yapyon.parse(read_text(path), source=str(path))


def read_record(path):
    """Read literal registry data with the same regular-file guard as documents."""
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
    ancestry = {}
    fills = 0
    while True:
        for slot in result.slots():
            label = slot.label
            if not label or (kinds is not None and label[0] not in kinds):
                continue
            try:
                _check_label(label)
            except ValueError as exc:
                raise _slot_error(slot, str(exc)) from exc
            ancestors = [path for path in ancestry if slot.path[:len(path)] == path]
            history = ancestry[max(ancestors, key=len)] if ancestors else ()
            state = (label, slot.overrides.to_source())
            if state in history:
                chain = " -> ".join(".".join(item[0]) for item in (*history, state))
                raise _slot_error(slot, f"preset inheritance cycle: {chain}")
            if fills >= max_fills or len(history) >= 64:
                raise _slot_error(slot, f"preset expansion exceeds {max_fills} fills or depth 64")
            try:
                loaded = lookup(label)
            except FileNotFoundError as exc:
                raise _slot_error(slot, f"preset {'.'.join(label)!r} not found: {exc}") from exc
            if isinstance(loaded, yapyon.Tree):
                if (loaded.root.kind != "mapping" or len(loaded.keys()) != 1
                        or "runtime" in loaded
                        or not isinstance(next(iter(loaded.keys())), str)):
                    raise _slot_error(slot, "include must contain one literal non-runtime branch")
                filler = loaded[next(iter(loaded.keys()))]
                result.warnings.extend(loaded.warnings)
            else:
                filler = loaded
            if (slot.path == ("agent",) and filler.kind == "mapping"
                    and "secret" in filler and filler["secret"].kind == "slot"
                    and not filler["secret"].label and "secret" not in slot.overrides):
                slot.merge(yapyon.parse("secret<>: {}"))
            ancestry = {path: chain for path, chain in ancestry.items()
                        if path[:len(slot.path)] != slot.path}
            ancestry[slot.path] = (*history, state)
            slot.fill(filler)
            fills += 1
            break
        else:
            return result


def compose_preset(kind, name, *, lookup=load_preset, max_fills=1000):
    """Collapse one category's inheritance, leaving other categories as slots."""
    label = (kind, name)
    _check_label(label)
    tree = lookup(label)
    if not isinstance(tree, yapyon.Tree):
        raise TypeError("a category document must be a yapyon Tree")
    if kind not in tree:
        raise ValueError(f"preset {kind}.{name!s} has no {kind!r} branch")
    return compose(tree, kinds={kind}, lookup=lookup, max_fills=max_fills)


def check_write_path(path):
    """Check an atomic-write target without creating files or directories."""
    path = Path(path).resolve()
    if path.exists() and not path.is_file():
        raise ValueError(f"configuration target must be a regular file: {path}")
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError(f"configuration parent must be a directory: {parent}")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError(f"configuration directory is not writable: {parent}")
    return path


def _write_text(text, path, *, overwrite=False):
    """Atomically write text through user-selected links; replacement needs opt-in."""
    path = Path(path).resolve()
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


def write_document(tree, path, *, overwrite=False):
    """Atomically save unfinished yapyon; replacing a file requires opt-in.

    Yapyon may refuse non-representable deferred merge state. Serialize before
    touching the filesystem so such a refusal cannot damage an existing file.
    Comments and original layout are not preserved by ``to_source()``.
    """
    return _write_text(tree.to_source(), path, overwrite=overwrite)


def write_record(value, path, *, overwrite=False):
    """Atomically save native record data through yapyon's public serializer."""
    return _write_text(yapyon.dumps(value, max_size=MAX_SOURCE_BYTES), path, overwrite=overwrite)

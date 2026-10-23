"""Functions a template may call, declared to yapyon in ``data/functions.contract``.

yapyon 0.3.0 (SPEC §13) lets a document call a function the *loader* declares in
a contract, so dialect negotiation runs inside the one ``yapyon.loads`` rather
than in a second pass of ours.  ⚑ The name cannot be ``__MATCH_FIRST__`` any
more: dunder names are the format's alone, and a contract that declares one is
refused when it is loaded.

A declared function receives its container arguments as read-only *borrows*,
valid only for the duration of the call (§13.1) -- so nothing here keeps a
reference to an argument after returning.
"""

from __future__ import annotations

__all__ = ["match_first"]


def match_first(keys, items):
    """The first member of ``keys`` that is also a key of ``items``.

    Order comes from ``keys``, not from ``items``: the first argument says what
    is acceptable, best first (a harness's ``supported_dialects``), and the
    second says what is available (a persona's ``mind.dialects``).

    No overlap raises, and yapyon reports it as akan at the call site, naming
    this function.  ``__init__._check_dialects`` normally refuses an
    incompatible pairing by name before resolution ever gets here, so this is a
    backstop -- but it must never quietly return something.
    """
    for key in keys:
        if key in items:
            return key
    raise ValueError(f"no dialect in common: wanted one of {list(keys)}, "
                     f"have {sorted(items)}")

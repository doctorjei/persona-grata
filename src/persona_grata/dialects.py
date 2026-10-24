"""Dialect negotiation declared in ``data/functions.contract``."""


def match_first(keys, items):
    """Choose the first supported dialect that the persona has not disabled."""
    for key in keys:
        if key in items and items[key] is not None:
            return key
    available = sorted(key for key in items if items[key] is not None)
    raise ValueError(f"no dialect in common: wanted one of {list(keys)}, have {available}")

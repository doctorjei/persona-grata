"""pg's unset-value policy, applied before native configuration serialization."""

from collections.abc import Mapping, Sequence


def prune_config(config):
    """Copy a resolved configuration without unset values; keep zero and false."""
    def prune(value):
        if isinstance(value, Mapping):
            value = {key: prune(item) for key, item in value.items()}
            return {key: item for key, item in value.items() if not empty(item)}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [item for item in map(prune, value) if not empty(item)]
        return value

    def empty(value):
        return value is None or value == "" or value == {} or value == []

    return prune(config)

"""Public yapyon APIs used by pg's composition and final evaluation stages."""

import os

import yapyon

from .composition import read_document


RESERVED_ENV = frozenset({"HOME", "XDG_CONFIG_HOME", "AGENT_STORE_DIR"})


def environment(env_defaults=None):
    return {**(env_defaults or {}), **os.environ}


def value_tree(value):
    """Represent plain configuration literals, never deferred source fragments."""
    if isinstance(value, dict):
        tree = yapyon.parse("{}")
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("configuration keys must be strings")
            tree.merge(value_tree(item), at=(key,))
        return tree
    if isinstance(value, list):
        tree = yapyon.parse(repr([None] * len(value)))
        for index, item in enumerate(value):
            tree[index].replace(value_tree(item))
        return tree
    if value is not None and not isinstance(value, (str, bool, int)):
        raise TypeError(f"cannot store {type(value).__name__} as configuration")
    return yapyon.parse(repr(value))


def load(path):
    return read_document(path)


def emit_config(value):
    """Emit plain native data as a strict yapyon record."""
    return yapyon.dumps(value)


def resolve_document(tree, env_defaults=None, env_allow=None):
    if env_allow is None:
        env_allow = RESERVED_ENV | yapyon.STANDARD_ENV
    return tree.resolve(env_allow=env_allow, env=environment(env_defaults))

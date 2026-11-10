"""Public yapyon APIs used by pg's composition and final evaluation stages."""

import os

import yapyon

from .composition import read_document


RESERVED_ENV = frozenset({"HOME", "XDG_CONFIG_HOME", "AGENT_STORE_DIR"})


def environment(env_defaults=None):
    return {**(env_defaults or {}), **os.environ}


def value_tree(value):
    """Represent plain configuration literals, never deferred source fragments."""
    def validate(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError("configuration keys must be strings")
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and not isinstance(item, (str, bool, int)):
            raise TypeError(f"cannot store {type(item).__name__} as configuration")

    validate(value)
    # Record serialization preserves literal keys rather than folding path names.
    return yapyon.parse(yapyon.dumps(value))


def load(path):
    return read_document(path)


def emit_config(value):
    """Emit plain native data as a strict yapyon record."""
    return yapyon.dumps(value)


def resolve_document(tree, env_defaults=None, env_allow=None):
    if env_allow is None:
        env_allow = RESERVED_ENV | yapyon.STANDARD_ENV
    return tree.resolve(env_allow=env_allow, env=environment(env_defaults))

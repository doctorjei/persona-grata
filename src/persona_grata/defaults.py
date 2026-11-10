"""Compose schema defaults below deferred source using ordinary yapyon merges."""

from copy import deepcopy

import yapyon

from .schema import DEFAULT_FIELDS, DIALECT_FIELDS


ATOMIC_FIELDS = frozenset({"config_store", "wrapper_env"})


def overlay(base, higher, *, _path=()):
    result = deepcopy(base)
    for key, value in higher.items():
        atomic = _path == ("defaults", "harness") and key in ATOMIC_FIELDS
        if not atomic and isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = overlay(result[key], value, _path=(*_path, key))
        else:
            result[key] = deepcopy(value)
    return result


def _mapping(entries, *, quoted=False):
    entries = list(entries)
    spelling = lambda key: repr(key) if quoted else key
    tree = yapyon.parse("{" + ", ".join(f"{spelling(key)}: None" for key, _ in entries) + "}")
    for key, value in entries:
        tree[key].replace(value)
    return tree


def _literal(value):
    return yapyon.parse(yapyon.dumps(value))


def _record(values, schema):
    entries = []
    for key, value in values.items():
        shape = schema[key]
        if isinstance(shape, dict) and isinstance(value, dict):
            child = _record(value, shape)
        elif shape == "dialects" and isinstance(value, dict):
            child = _mapping(((protocol, _record(fields, DIALECT_FIELDS)
                               if isinstance(fields, dict) else _literal(fields))
                              for protocol, fields in value.items()), quoted=True)
        else:
            child = _literal(value)
        entries.append((key, child))
    return _mapping(entries)


def defaults_source(values):
    """Represent schema names and exact native/protocol keys without evaluating data."""
    return _record(values, DEFAULT_FIELDS)


def bind_defaults(source, settings):
    """Keep defaults below the complete authored source program, without resolving it."""
    intrinsic = yapyon.parse('''persona:
  mind:
    model_list: []
    model_active: __PARENT__["model_list"].__get__(0)
    model_subagent: (__PARENT__["model_active"])
harness:
  path: y"{runtime.agent_path}/harness"
  protocol: [dialect for dialect in __PARENT__["supported_dialects"] if agent["persona"]["mind"]["dialects"].__get__(dialect) is not None][0]
secret:
  root: (runtime.defaults.secret.root)
  entry: y"{__PARENT__['root']}/{agent.name}" if __PARENT__["root"] is not None else agent.name
''')
    configured = defaults_source({
        kind: {key: value for key, value in settings[kind].items() if key != "backend"}
        for kind in ("persona", "harness", "secret")
    })
    lower = _mapping((("agent", yapyon.merge(intrinsic, configured)),))
    return yapyon.merge(lower, source)

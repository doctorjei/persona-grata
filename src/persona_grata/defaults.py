"""Bind schema defaults to deferred source without changing authored file composition."""

from copy import deepcopy

import yapyon

from .schema import DEFAULT_FIELDS, DIALECT_FIELDS


ATOMIC_FIELDS = frozenset({"config_store", "wrapper_env"})


def overlay(base, higher):
    result = deepcopy(base)
    for key, value in higher.items():
        if (key not in ATOMIC_FIELDS and isinstance(value, dict)
                and isinstance(result.get(key), dict)):
            result[key] = overlay(result[key], value)
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


def _overlay_record(base, higher, schema):
    for key, child in higher.items():
        shape = schema[key]
        if (key in base and isinstance(shape, dict) and child.kind == "mapping"
                and base[key].kind == "mapping"):
            _overlay_record(base[key], child, shape)
        elif key in base:
            base[key].replace(child)
        else:
            base.merge(_mapping(((key, child),)))


def _missing(defaults, source, schema):
    if source.kind != "mapping":
        raise ValueError("unsupported deferred schema-record defaults binding")
    if defaults.keys() and any(not isinstance(key, str) for key in source.keys()):
        raise ValueError("unsupported defaults binding to computed schema-record keys")
    entries = []
    for key, default in defaults.items():
        if key not in source:
            entries.append((key, default))
            continue
        shape = schema.get(key)
        actual = source[key]
        if (isinstance(shape, dict) and default.kind == "mapping"
                and not (actual.kind == "scalar" and actual.value is None)):
            child = _missing(default, actual, shape)
            if child.keys():
                entries.append((key, child))
        elif shape == "dialects" and default.kind == "mapping":
            if actual.kind != "mapping":
                raise ValueError("unsupported deferred dialect-map defaults binding")
            child = _missing_dialects(default, actual)
            if child.keys():
                entries.append((key, child))
    return _mapping(entries)


def _missing_dialects(defaults, source):
    entries = []
    for protocol, default in defaults.items():
        if protocol not in source:
            entries.append((protocol, default))
        elif default.kind == "mapping":
            actual = source[protocol]
            if actual.kind == "scalar" and actual.value is None:
                continue
            child = _missing(default, actual, DIALECT_FIELDS)
            if child.keys():
                entries.append((protocol, child))
    return _mapping(entries, quoted=True)


def bind_defaults(source, settings):
    """Return a copy with missing-field fallbacks below all explicit file history."""
    agent = source["agent"]
    intrinsic = yapyon.parse('''persona:
  mind:
    model_list: []
    model_active: model_list.__get__(0)
    model_subagent: (model_active)
harness:
  path: y"{runtime.agent_path}/harness"
  protocol: [dialect for dialect in supported_dialects if persona.mind.dialects.__get__(dialect) is not None][0]
secret:
  root: (runtime.defaults.secret.root)
  entry: y"{root}/{agent.name}" if root is not None else agent.name
''')
    fallbacks = []
    for kind in ("persona", "harness", "secret"):
        if kind not in agent:
            continue
        actual = agent[kind]
        if actual.kind == "scalar" and actual.value is None:
            continue
        values = {key: value for key, value in settings[kind].items() if key != "backend"}
        base = yapyon.merge(intrinsic[kind])
        _overlay_record(base.root, _record(values, DEFAULT_FIELDS[kind]), DEFAULT_FIELDS[kind])
        if kind == "persona":
            generic = settings["dialect"]
            specific = values.get("mind", {}).get("dialects", {})
            mind = actual["mind"] if actual.kind == "mapping" and "mind" in actual else None
            protocols = set(specific)
            if mind is not None and mind.kind == "mapping" and "dialects" in mind:
                dialects = mind["dialects"]
                if dialects.kind == "mapping":
                    protocols.update(dialects.keys())
                elif generic:
                    raise ValueError("unsupported deferred dialect-map defaults binding")
            defaults = {}
            for protocol in protocols:
                if not isinstance(protocol, str):
                    if generic:
                        raise ValueError("unsupported defaults binding to computed protocol keys")
                    continue
                per_protocol = specific.get(protocol, {})
                if per_protocol is None:
                    defaults[protocol] = None
                elif generic or per_protocol:
                    defaults[protocol] = overlay(generic, per_protocol)
            if defaults:
                if "mind" not in base:
                    base.merge(yapyon.parse("mind: {}"))
                dialect_defaults = _record({"dialects": defaults}, DEFAULT_FIELDS["persona"]["mind"])
                if "dialects" in base["mind"]:
                    base["mind"]["dialects"].replace(dialect_defaults["dialects"])
                else:
                    base["mind"].merge(dialect_defaults)
        missing = _missing(base.root, actual, DEFAULT_FIELDS[kind])
        if missing.keys():
            fallbacks.append((kind, missing))
    lower = _mapping((("agent", _mapping(fallbacks)),))
    return yapyon.merge(lower, source)

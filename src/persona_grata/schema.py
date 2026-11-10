"""Typed, non-mutating validation for the agents-yapyon-v0.6.5 profile.

Validation never resolves source, contacts an endpoint or executes manager commands.
The defaults schema also describes record versus whole-data binding boundaries.
"""

from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit

from .identifiers import validate_id


FORMAT = "agents-yapyon-v0.6.5"

DIALECT_FIELDS = {
    "api_uri": None,
    "msg_uri": None,
    "model_uri": None,
    "emb_uri": None,
    "verify": {"check_uri": None, "key_header": None, "headers": None, "body": None},
}
DEFAULT_FIELDS = {
    "persona": {"mind": {
        "endpoint": None, "context_size": None, "model_list": None,
        "model_active": None, "model_subagent": None, "dialects": "dialects",
    }},
    "harness": {
        "path": None, "auth_var": None, "path_var": None, "wrapper_env": "data",
        "config_file": None, "config_store": "data", "content": None,
        "supported_dialects": None, "protocol": None,
    },
    "dialect": DIALECT_FIELDS,
    "secret": {
        "backend": None, "root": None, "entry": None, "fetch": None,
        "store": None, "probe": None, "check": None,
    },
}

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_AGENT_FIELDS = {"format", "name", "desc", "persona", "harness", "secret"}


def validate_entry(value):
    """Return a complete manager-relative root/entry, without interpreting it."""
    if (not isinstance(value, str) or not value or value.startswith(("/", "-"))
            or any(char in value for char in "\x00\r\n")
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("secret entry/root must be a nonempty manager-relative string "
                         "without slash endpoints, dot segments, leading '-' or NUL/CR/LF")
    return value


def validate_env_name(value):
    """Return an environment identifier (not an assignment or an empty value)."""
    if not isinstance(value, str) or not _ENV_NAME.fullmatch(value):
        raise ValueError("environment variable name must match [A-Za-z_][A-Za-z0-9_]*")
    return value


def _mapping(value, field):
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a string-keyed mapping")
    return value


def _string(value, field, *, nonempty=False, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or (nonempty and not value):
        qualifier = "nonempty " if nonempty else ""
        suffix = " or None" if nullable else ""
        raise ValueError(f"{field} must be a {qualifier}string{suffix}")


def _strings(value, field, *, nonempty=False, nonempty_items=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{field} must be a {'nonempty ' if nonempty else ''}list of strings")
    for item in value:
        _string(item, field + " item", nonempty=nonempty_items)


def _required(record, fields, field):
    missing = set(fields) - record.keys()
    if missing:
        raise ValueError(f"{field} is missing required fields: " + ", ".join(sorted(missing)))


def _description(record, field):
    if "desc" in record:
        _string(record["desc"], field + ".desc")


def _endpoint(value, field):
    _string(value, field, nonempty=True)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
    except ValueError:
        raise ValueError(f"{field} must be an absolute HTTP(S) URL with a host") from None
    if (not value.lower().startswith(("http://", "https://"))
            or parsed.scheme not in {"http", "https"} or not host
            or any(char.isspace() for char in host)
            or any(char in value for char in "\x00\r\n\t")):
        raise ValueError(f"{field} must be an absolute HTTP(S) URL with a host")


def _path(value, field):
    _string(value, field, nonempty=True)
    if "\x00" in value:
        raise ValueError(f"{field} must not contain NUL")


def _verify(value, field):
    if value is None:
        return
    record = _mapping(value, field)
    for key in ("check_uri", "key_header", "body"):
        if key in record:
            _string(record[key], field + "." + key)
    if "headers" in record:
        _strings(record["headers"], field + ".headers")


def _dialect(value, field, *, partial):
    record = _mapping(value, field)
    if not partial:
        _required(record, {"name", "api_uri"}, field)
    if "name" in record:
        _string(record["name"], field + ".name", nonempty=True)
    _description(record, field)
    if "api_uri" in record:
        _string(record["api_uri"], field + ".api_uri", nonempty=True)
    for key in ("msg_uri", "model_uri", "emb_uri"):
        if key in record:
            _string(record[key], field + "." + key, nonempty=True, nullable=True)
    if "verify" in record:
        _verify(record["verify"], field + ".verify")


def _dialects(value, field, *, partial):
    record = _mapping(value, field)
    for protocol, dialect in record.items():
        _string(protocol, field + " protocol", nonempty=True)
        if dialect is not None:
            _dialect(dialect, f"{field}[{protocol!r}]", partial=partial)


def _mind(value, field, *, partial):
    record = _mapping(value, field)
    if not partial:
        _required(record, {"endpoint", "dialects"}, field)
    if "endpoint" in record:
        _endpoint(record["endpoint"], field + ".endpoint")
    if "context_size" in record:
        size = record["context_size"]
        if size is not None and (type(size) is not int or size <= 0):
            raise ValueError(f"{field}.context_size must be a positive integer or None")
    if "model_list" in record:
        _strings(record["model_list"], field + ".model_list")
    for key in ("model_active", "model_subagent"):
        if key in record:
            _string(record[key], field + "." + key, nullable=True)
    if "dialects" in record:
        _dialects(record["dialects"], field + ".dialects", partial=partial)


def _persona(value, field, *, partial):
    record = _mapping(value, field)
    if not partial:
        _required(record, {"name", "mind"}, field)
    if "name" in record:
        _string(record["name"], field + ".name", nonempty=True)
    _description(record, field)
    if "mind" in record:
        _mind(record["mind"], field + ".mind", partial=partial)


def _harness(value, field, *, partial, path=None):
    record = _mapping(value, field)
    if not partial:
        _required(record, {"name", "path", "supported_dialects", "protocol"}, field)
    if "name" in record:
        _string(record["name"], field + ".name", nonempty=True)
    _description(record, field)
    if "path" in record:
        _path(record["path"], field + ".path")
        if not partial and record["path"] != str(Path(path) / "harness"):
            raise ValueError(f"{field}.path must equal the registered agent directory / harness")
    for key in ("auth_var", "path_var"):
        if key in record:
            value = record[key]
            if value != "":
                validate_env_name(value)
    if "wrapper_env" in record:
        for key, value in _mapping(record["wrapper_env"], field + ".wrapper_env").items():
            validate_env_name(key)
            _string(value, field + ".wrapper_env value")
    if "config_file" in record and record["config_file"] not in (None, ""):
        filename = record["config_file"]
        _path(filename, field + ".config_file")
        if Path(filename).is_absolute() or ".." in Path(filename).parts or Path(filename) == Path("."):
            raise ValueError(f"{field}.config_file must be relative without '..' segments")
    if "config_store" in record:
        _mapping(record["config_store"], field + ".config_store")
    if "content" in record:
        _string(record["content"], field + ".content")
    if "supported_dialects" in record:
        _strings(record["supported_dialects"], field + ".supported_dialects",
                 nonempty=True, nonempty_items=True)
    if "protocol" in record:
        _string(record["protocol"], field + ".protocol", nonempty=True)


def _command(value, field, *, required=False, entry=None):
    if not required and value in (None, ""):
        return
    _string(value, field, nonempty=True)
    if not value.strip() or "\x00" in value:
        raise ValueError(f"{field} must contain a nonempty executable and no NUL")
    # Splitting depends on the final bound entry. Defaults have no such context:
    # an unmatched quote might legitimately belong to a later final entry argument.
    if entry is None:
        return
    try:
        prefix = value[:-len(entry)] if value.endswith(entry) else ""
        if prefix.endswith((" ", "\t")):
            argv = shlex.split(prefix, posix=True) + [entry]
        else:
            argv = shlex.split(value, posix=True)
    except ValueError:
        raise ValueError(f"{field} has invalid POSIX argument quoting") from None
    if not argv or not argv[0] or any("\x00" in arg for arg in argv):
        raise ValueError(f"{field} must contain a nonempty executable and no NUL")


def _secret(value, field, *, partial):
    record = _mapping(value, field)
    if not partial:
        _required(record, {"name", "entry", "fetch"}, field)
    if "name" in record:
        _string(record["name"], field + ".name", nonempty=True)
    _description(record, field)
    if "root" in record and record["root"] is not None:
        validate_entry(record["root"])
    if "entry" in record:
        validate_entry(record["entry"])
    for key in ("fetch", "store", "check"):
        if key in record:
            _command(record[key], field + "." + key, required=key == "fetch",
                     entry=None if partial else record["entry"])
    if "probe" in record and record["probe"] not in (None, ""):
        probe = record["probe"]
        _string(probe, field + ".probe")
        if not probe.strip() or "\x00" in probe:
            raise ValueError(f"{field}.probe must be a nonempty executable name/path without NUL")


def _eligible(value, schema, field):
    record = _mapping(value, field)
    for key, value in record.items():
        if key not in schema:
            raise ValueError(f"{field}.{key} is not an eligible defaults field")
        subschema = schema[key]
        child = field + "." + key
        if isinstance(subschema, dict):
            if key == "verify" and value is None:
                continue
            _eligible(value, subschema, child)
        elif subschema == "dialects":
            for protocol, dialect in _mapping(value, child).items():
                _string(protocol, child + " protocol", nonempty=True)
                if dialect is not None:
                    _eligible(dialect, DIALECT_FIELDS, f"{child}[{protocol!r}]")


def validate_defaults(defaults):
    """Validate partial/full reusable defaults, including currently unused values.

    Cross-field/path/compatibility checks belong to the bound definition. Return
    the supplied mapping unchanged; this function does not overlay or prune data.
    """
    _eligible(defaults, DEFAULT_FIELDS, "defaults")
    validators = {"persona": _persona, "harness": _harness,
                  "dialect": _dialect, "secret": _secret}
    for group, value in defaults.items():
        validators[group](value, "defaults." + group, partial=True)
    backend = defaults.get("secret", {}).get("backend")
    if backend is not None:
        validate_id(backend, "secret backend")
    return defaults


def validate_definition(agent, *, name, path):
    """Validate a complete resolved agent against its selected registry identity."""
    record = _mapping(agent, "agent")
    unknown = record.keys() - _AGENT_FIELDS
    if unknown:
        raise ValueError("unknown agent fields: " + ", ".join(sorted(unknown)))
    _required(record, {"format", "name", "persona", "harness", "secret"}, "agent")
    if record["format"] != FORMAT:
        raise ValueError("unsupported agent format; expected " + FORMAT)
    validate_id(name)
    if record["name"] != name:
        raise ValueError("agent.name must match the selected registered name")
    _description(record, "agent")
    _persona(record["persona"], "agent.persona", partial=False)
    _harness(record["harness"], "agent.harness", partial=False, path=path)
    dialects = record["persona"]["mind"]["dialects"]
    harness = record["harness"]
    protocol = harness["protocol"]
    if protocol not in harness["supported_dialects"] or dialects.get(protocol) is None:
        raise ValueError("agent.harness.protocol must select a supported, enabled persona dialect")
    if record["secret"] is not None:
        _secret(record["secret"], "agent.secret", partial=False)
    return agent

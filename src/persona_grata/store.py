"""Agent registry, composed in-use definitions, and invocation-owned runtime values."""

import contextlib
import copy
from dataclasses import dataclass, field
import fcntl
import os
from pathlib import Path

import yapyon

from .composition import (
    DATA_DIR, KIND_DIRS, MAX_SOURCE_BYTES, _write_text, check_write_path, compose, compose_preset,
    load_preset, read_document, read_record, read_text, write_document, write_record,
)
from .identifiers import validate_id
from . import yapyon_io as yio


FORMAT = "agents-yapyon-v0.1"
COMPONENT_DIRS = KIND_DIRS
DEFAULTS_PATH = DATA_DIR / "agent_store-defaults.cfg"


def _overlay(base, overrides):
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _overlay(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def default_store():
    configured = os.environ.get("AGENT_STORE_DIR")
    if configured:
        return Path(configured).expanduser().absolute()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    return (Path(config_home).expanduser() if config_home else Path.home() / ".config") / "agents"


def make_agent(persona, harness, *, name=None):
    validate_id(persona, "persona")
    validate_id(harness, "harness")
    name = validate_id(name if name is not None else f"{persona}_{harness}")
    return yapyon.parse(
        f"agent<base.agent>:\n  name: {name!r}\n"
        f"  persona<{persona}.persona>: {{}}\n"
        f"  harness<{harness}.harness>: {{}}\n")


def _entry(value, *, allow_empty=False):
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError("a secret entry must be a nonempty string")
    if value and (value.startswith(("/", "-")) or
                  any(part in ("", ".", "..") for part in value.split("/")) or
                  any(ch in value for ch in "\x00\r\n")):
        raise ValueError("a secret entry must be store-relative without dot segments or control characters")
    return value


@dataclass(frozen=True)
class _PreparedAgent:
    root: Path
    name: str
    path: Path
    definition: object = field(repr=False)
    resolved: dict = field(repr=False)
    components: dict = field(repr=False)
    registry: dict = field(repr=False)
    previous_name: str | None = None
    previous_source: str | None = field(default=None, repr=False)


class AgentStore:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else default_store()).expanduser().resolve()
        self.registry_path = self.root / "agent_store.cfg"

    @staticmethod
    def _defaults():
        return read_record(DEFAULTS_PATH)

    def _read_overrides(self):
        if not self.registry_path.exists():
            return {}
        source = read_text(self.registry_path)
        if all(not line.strip() or line.lstrip().startswith("#") for line in source.splitlines()):
            return {}
        data = yapyon.loads_record(source, source=str(self.registry_path))
        if not isinstance(data, dict):
            raise ValueError("agent_store.cfg must be a mapping")
        return data

    def _read(self):
        return self._validate_settings(_overlay(self._defaults(), self._read_overrides()))

    def _validate_settings(self, data):
        unknown = data.keys() - self._defaults().keys()
        if unknown:
            raise ValueError("unknown store settings: " + ", ".join(sorted(unknown)))
        if data["format"] != FORMAT:
            raise ValueError(f"unsupported agent store format: {data['format']!r}")
        defaults = data["defaults"]
        if not isinstance(defaults, dict) or set(defaults) != {"secret"}:
            raise ValueError("defaults must contain only secret settings")
        secret = defaults["secret"]
        if not isinstance(secret, dict) or set(secret) != {"backend", "root"}:
            raise ValueError("defaults.secret must contain backend and root")
        backend = secret["backend"]
        if backend is not None:
            validate_id(backend, "secret backend")
        _entry(secret["root"])
        for key in ("env_allow", "env_add"):
            if not isinstance(data[key], list) or not all(
                    isinstance(item, str) and item.isascii() and item.replace("_", "a").isalnum()
                    and not item[0].isdigit() for item in data[key]):
                raise ValueError(f"{key} must be a list of environment variable names")
        if not isinstance(data["agents"], dict):
            raise ValueError("agents must map names to path records")
        paths = []
        for name, record in data["agents"].items():
            validate_id(name)
            if not isinstance(record, dict) or set(record) != {"path"}:
                raise ValueError(f"registry entry {name!r} must contain only path")
            value = record["path"]
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError(f"registry path for {name!r} must be absolute")
            path = Path(value)
            if ".." in path.parts:
                raise ValueError(f"registry path for {name!r} must not contain '..'")
            self._check_location(path)
            for previous in paths:
                if path == previous or path in previous.parents or previous in path.parents:
                    raise ValueError("registered agent directories cannot overlap")
            paths.append(path)
        return data

    def _write(self, data):
        ordered = {key: value for key, value in data.items() if key not in {"env_add", "env_allow"}}
        ordered.update((key, data[key]) for key in ("env_add", "env_allow") if key in data)
        write_record(ordered, self.registry_path, overwrite=True)

    def _write_agents(self, data):
        overrides = self._read_overrides()
        overrides["agents"] = data["agents"]
        self._write(overrides)

    @contextlib.contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.root / ".agent_store.lock",
                             os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @property
    def settings(self):
        return copy.deepcopy(self._read())

    def configure(self, **changes):
        if changes.keys() - {"defaults", "env_allow", "env_add"}:
            raise ValueError("only default and environment settings may be configured")
        with self._locked():
            overrides = _overlay(self._read_overrides(), changes)
            proposed = self._validate_settings(_overlay(self._defaults(), overrides))
            components = {}
            backend = proposed["defaults"]["secret"]["backend"]
            if backend is not None:
                self._materialize((backend, "secret"), components=components)
            written = self._save_components(components)
            try:
                self._write(overrides)
            except BaseException:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
            return copy.deepcopy(proposed)

    def agents(self):
        return {name: Path(record["path"]) for name, record in self._read()["agents"].items()}

    def _check_location(self, path):
        components = {self.root / part for part in COMPONENT_DIRS.values()}
        protected = {self.root, self.root / "store", *components,
                     Path.home().absolute(), Path(path.anchor)}
        if (path in protected or path in self.root.parents or
                any(directory in path.parents for directory in components)):
            raise ValueError(f"unsafe agent directory: {path}")

    def locate(self, name):
        validate_id(name)
        try:
            path = self.agents()[name]
        except KeyError:
            raise ValueError(f"unknown agent {name!r}") from None
        self._check_location(path)
        return path

    def component_path(self, label):
        name, kind = label
        validate_id(name, kind)
        if kind not in COMPONENT_DIRS or kind == "agent":
            raise ValueError(f"not a stored component kind: {kind!r}")
        return self.root / COMPONENT_DIRS[kind] / f"{name}.{kind}.tmpl"

    def _source_lookup(self, label):
        if label[1] != "agent":
            path = self.component_path(label)
            if path.exists():
                return read_document(path)
        return load_preset(label)

    def _stored_lookup(self, label):
        path = self.component_path(label)
        return read_document(path)

    def _materialize(self, label, pending=(), *, components):
        if label in pending:
            raise ValueError("recursive component dependency: " + " -> ".join(
                ".".join(item) for item in (*pending, label)))
        if label in components:
            return components[label]
        path = self.component_path(label)
        exists = path.exists()
        tree = read_document(path) if exists else compose_preset(
            label[1], label[0], lookup=self._source_lookup)
        if not exists:
            tree.to_source()
        for slot in tree.slots():
            if slot.label:
                self._materialize(slot.label, (*pending, label), components=components)
        if not exists:
            components[label] = tree
        return tree

    def _save_components(self, components):
        written = []
        try:
            for label, tree in components.items():
                written.append(write_document(tree, self.component_path(label)))
        except BaseException:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return written

    @staticmethod
    def _validate_agent(agent, *, filled=False):
        if agent.kind != "mapping":
            raise ValueError("agent must be a mapping after composing its inheritance")
        if "path" in agent:
            raise ValueError("agent.path is forbidden; the registry determines the agent directory")
        unknown = set(agent.keys()) - {"format", "name", "desc", "persona", "harness", "secret"}
        if unknown:
            raise ValueError("unknown agent fields: " + ", ".join(map(str, unknown)))
        for kind in ("persona", "harness"):
            if kind not in agent:
                raise ValueError(f"agent requires a {kind} definition")
            if filled and agent[kind].kind != "mapping":
                raise ValueError(f"agent {kind} has a required slot or is not a filled mapping")
        if "format" in agent:
            marker = agent["format"]
            if marker.kind != "scalar" or marker.value != FORMAT:
                raise ValueError(f"unsupported agent format; expected {FORMAT}")
        else:
            agent.merge(yio.value_tree({"format": FORMAT}))

    def _prepare(self, definition, name, data, *, components):
        if definition.root.kind != "mapping" or set(definition.keys()) != {"agent"}:
            raise ValueError("an agent document must contain only agent; runtime is invocation-owned")
        tree = compose(definition, kinds={"agent"}, lookup=self._source_lookup)
        agent = tree["agent"]
        self._validate_agent(agent)
        agent.merge(yio.value_tree({"name": name}))
        backend = data["defaults"]["secret"]["backend"]
        if "secret" not in agent:
            agent.merge(yapyon.parse("secret<>: {}"))
        secret = agent["secret"]
        if secret.kind == "slot" and not secret.label and backend is None:
            if secret.overrides.keys():
                raise ValueError("agent needs a secret backend; select one or explicitly set secret: None")
            secret.replace(yio.value_tree(None))
        tree.to_source()
        for slot in tree.slots():
            if slot.label:
                self._materialize(slot.label, components=components)
        if data["defaults"]["secret"]["backend"]:
            self._materialize((data["defaults"]["secret"]["backend"], "secret"), components=components)
        return tree

    def _assemble(self, definition, name, path, data, *, components=None):
        components = components or {}

        def lookup(label):
            return components[label] if label in components else self._stored_lookup(label)

        backend = data["defaults"]["secret"]["backend"]
        manager = (compose(lookup((backend, "secret")), lookup=lookup)["secret"]
                   if backend is not None else yapyon.parse("{}").root)
        assembled = compose(definition, lookup=lookup)
        agent = assembled["agent"]
        self._validate_agent(agent, filled=True)
        agent.merge(yio.value_tree({"name": name}))
        if "secret" not in agent:
            agent.merge(yapyon.parse("secret<>: {}"))
        secret = agent["secret"]
        if secret.kind == "slot" and not secret.label:
            if backend is None and secret.overrides.keys():
                raise ValueError("agent needs a secret backend; select one or explicitly set secret: None")
            secret.fill(manager if backend is not None else yio.value_tree(None))
        keyless = secret is None or (secret.kind == "scalar" and secret.value is None)
        if not keyless and secret.kind != "mapping":
            raise ValueError("agent.secret must be a backend mapping or None")
        if keyless:
            agent.merge(yio.value_tree({"secret": None}))
            if "harness" in agent and agent["harness"].kind == "mapping":
                agent["harness"].merge(yio.value_tree({"auth_var": ""}))
        if not keyless:
            harness = agent["harness"]
            if "config_store" in harness and "env" in harness["config_store"]:
                env = harness["config_store"]["env"]
                auth = harness["auth_var"]
                if auth.kind == "scalar" and auth.value in env:
                    env[auth.value].remove()
        tree = read_document(DATA_DIR / "global.tmpl")
        tree["runtime"].fill(yapyon.parse("{}"))
        tree["runtime"]["agent_path"].replace(yio.value_tree(str(path)))
        tree["runtime"]["defaults"].fill(yio.value_tree(data["defaults"]))
        tree["runtime"]["secret_backend"].fill(manager)
        tree["agent"].fill(agent)
        remaining = tree.slots()
        if remaining:
            slot = remaining[0]
            raise ValueError(f"{slot.where}: required slot remains unfilled at {slot.path}")
        result = yio.resolve_document(
            tree, env_allow=yio.RESERVED_ENV | set(data["env_allow"]) | set(data["env_add"]))
        resolved_agent = result["agent"]
        if "path" in resolved_agent:
            raise ValueError("agent.path is forbidden, including computed keys")
        if resolved_agent.get("format") != FORMAT:
            raise ValueError(f"unsupported agent format; expected {FORMAT}")
        persona = resolved_agent.get("persona")
        harness = resolved_agent.get("harness")
        if not isinstance(persona, dict) or not isinstance(harness, dict):
            raise ValueError("agent requires persona and harness mappings")
        mind = persona.get("mind")
        endpoint = mind.get("endpoint") if isinstance(mind, dict) else None
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            raise ValueError(f"agent {name!r} requires an http:// or https:// persona endpoint")
        supported = harness.get("supported_dialects")
        if not isinstance(supported, list) or not supported or not all(
                isinstance(dialect, str) and dialect for dialect in supported):
            raise ValueError("harness.supported_dialects must be a nonempty list of dialect names")
        dialects = mind.get("dialects")
        protocol = harness.get("protocol")
        if (not isinstance(dialects, dict) or not isinstance(protocol, str) or
                protocol not in supported or not isinstance(dialects.get(protocol), dict)):
            raise ValueError("harness.protocol must select a shared, enabled dialect")
        if not isinstance(harness.get("name"), str) or not harness["name"]:
            raise ValueError("agent harness requires a name")
        if harness.get("path") != str(path / "harness"):
            raise ValueError("harness.path must be the agent directory's harness subdirectory")
        secret = resolved_agent["secret"]
        if secret is not None:
            if not isinstance(secret, dict):
                raise ValueError("agent.secret must be a backend mapping or None")
            _entry(secret.get("entry"))
            if "root" in secret:
                _entry(secret["root"])
            for key in ("name", "fetch"):
                if not isinstance(secret.get(key), str) or not secret[key]:
                    raise ValueError(f"agent.secret.{key} must be a nonempty string")
            for key in ("store", "probe", "check"):
                if key in secret and secret[key] is not None and not isinstance(secret[key], str):
                    raise ValueError(f"agent.secret.{key} must be a string or None")
        return result

    def _check_new_agent(self, name, destination, data):
        if ".." in destination.parts:
            raise ValueError("agent directory must not contain '..'")
        self._check_location(destination)
        if name in data["agents"]:
            raise FileExistsError(f"agent name {name!r} is already occupied")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"agent directory already exists: {destination}")
        for record in data["agents"].values():
            previous = Path(record["path"])
            if destination in previous.parents or previous in destination.parents:
                raise ValueError("registered agent directories cannot overlap")

    def _candidate(self, name, definition, path, data, *, previous_name=None, previous_source=None):
        components = {}
        tree = self._prepare(definition, name, data, components=components)
        resolved = self._assemble(tree, name, path, data, components=components)
        proposed = copy.deepcopy(data)
        if previous_name is not None:
            del proposed["agents"][previous_name]
        proposed["agents"][name] = {"path": str(path)}
        yapyon.dumps(proposed, max_size=MAX_SOURCE_BYTES)
        targets = [path / "agent.cfg", *(self.component_path(label) for label in components)]
        if previous_name != name:
            targets.append(self.registry_path)
        for target in targets:
            check_write_path(target)
        return _PreparedAgent(self.root, name, path, tree, resolved, components, data,
                              previous_name, previous_source)

    def prepare_create(self, name, definition, *, path=None):
        """Resolve a prospective agent without creating its store or files."""
        validate_id(name)
        destination = Path(path).expanduser().absolute() if path is not None else self.root / "store" / name
        data = self._read()
        self._check_new_agent(name, destination, data)
        return self._candidate(name, definition, destination, data)

    def prepare_update(self, name, definition, *, new_name=None):
        """Resolve a replacement and optional rename without changing saved state."""
        validate_id(name)
        selected = validate_id(new_name) if new_name is not None else name
        data = self._read()
        if name not in data["agents"]:
            raise ValueError(f"unknown agent {name!r}")
        if selected != name and selected in data["agents"]:
            raise ValueError(f"agent name {selected!r} is already occupied")
        path = Path(data["agents"][name]["path"])
        previous = read_text(path / "agent.cfg")
        return self._candidate(selected, definition, path, data,
                               previous_name=name, previous_source=previous)

    def commit(self, prepared):
        """Persist a prepared source definition, refusing stale preflight state."""
        if not isinstance(prepared, _PreparedAgent) or prepared.root != self.root:
            raise ValueError("prepared agent belongs to a different store")
        path = prepared.path
        with self._locked():
            data = self._read()
            if data != prepared.registry:
                raise ValueError("agent store changed during preflight; retry the operation")
            updating = prepared.previous_name is not None
            if updating:
                if read_text(path / "agent.cfg") != prepared.previous_source:
                    raise ValueError("agent source changed during preflight; retry the operation")
            else:
                self._check_new_agent(prepared.name, path, data)
            resolved = self._assemble(prepared.definition, prepared.name, path, data,
                                      components=prepared.components)
            if resolved != prepared.resolved:
                raise ValueError("agent configuration changed during preflight; retry the operation")
            if not updating:
                path.mkdir(parents=True)
            written = []
            saved = False
            try:
                written = self._save_components(prepared.components)
                write_document(prepared.definition, path / "agent.cfg", overwrite=updating)
                saved = True
                if prepared.previous_name != prepared.name:
                    if updating:
                        del data["agents"][prepared.previous_name]
                    data["agents"][prepared.name] = {"path": str(path)}
                    self._write_agents(data)
            except BaseException:
                if saved:
                    if updating:
                        _write_text(prepared.previous_source, path / "agent.cfg", overwrite=True)
                    else:
                        (path / "agent.cfg").unlink(missing_ok=True)
                for component in written:
                    component.unlink(missing_ok=True)
                if not updating:
                    try:
                        path.rmdir()
                    except OSError:
                        pass
                raise
        return path

    def create(self, name, definition, *, path=None):
        return self.commit(self.prepare_create(name, definition, path=path))

    def update(self, name, definition):
        return self.commit(self.prepare_update(name, definition))

    def rename(self, old, new):
        validate_id(new)
        with self._locked():
            data = self._read()
            path = self.locate(old)
            if new in data["agents"]:
                raise FileExistsError(f"agent name {new!r} is already occupied")
            previous = read_document(path / "agent.cfg")
            tree = yapyon.merge(previous)
            if tree.root.kind != "mapping" or set(tree.keys()) != {"agent"}:
                raise ValueError("installed agent.cfg must contain only agent")
            self._validate_agent(tree["agent"])
            tree["agent"].merge(yio.value_tree({"name": new}))
            record = data["agents"].pop(old)
            data["agents"][new] = record
            write_document(tree, path / "agent.cfg", overwrite=True)
            try:
                self._write_agents(data)
            except BaseException:
                write_document(previous, path / "agent.cfg", overwrite=True)
                raise
            return path

    def unregister(self, name):
        with self._locked():
            data = self._read()
            if name not in data["agents"]:
                raise ValueError(f"unknown agent {name!r}")
            del data["agents"][name]
            self._write_agents(data)

    def load(self, name):
        path = self.locate(name)
        config = path / "agent.cfg"
        definition = read_document(config)
        if definition.root.kind != "mapping" or set(definition.keys()) != {"agent"}:
            raise ValueError("installed agent.cfg must contain only agent")
        return self._assemble(definition, name, path, self._read())

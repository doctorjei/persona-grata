"""Agent registry, composed in-use definitions, and invocation-owned runtime values."""

import contextlib
import copy
import fcntl
import json
import os
from pathlib import Path

import yapyon

from .composition import (
    DATA_DIR, KIND_DIRS, compose, compose_preset, load_preset, read_document, read_record, write_document,
)
from .identifiers import validate_id
from . import yapyon_io as yio


FORMAT = "agents-yapyon-v0.1"
COMPONENT_DIRS = {**KIND_DIRS, "secret": "secrets"}
BACKENDS = frozenset({"pass", "gopass", "prs"})


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


class AgentStore:
    def __init__(self, root=None):
        self.root = Path(root if root is not None else default_store()).expanduser().resolve()
        self.registry_path = self.root / "agent_store.cfg"

    @staticmethod
    def _defaults():
        return {"format": FORMAT, "secret_backend": None, "secret_root": "",
                "env_allow": sorted(yapyon.STANDARD_ENV), "env_add": [], "agents": {}}

    def _read(self):
        self._check_root()
        if self.registry_path.is_symlink():
            raise ValueError("agent_store.cfg must not be a symlink")
        if not self.registry_path.exists():
            return self._defaults()
        self._check_root()
        data = read_record(self.registry_path)
        if not isinstance(data, dict):
            raise ValueError("agent_store.cfg must be a mapping")
        unknown = data.keys() - self._defaults().keys()
        if unknown:
            raise ValueError("unknown store settings: " + ", ".join(sorted(unknown)))
        data = {**self._defaults(), **data}
        if data["format"] != FORMAT:
            raise ValueError(f"unsupported agent store format: {data['format']!r}")
        if data["secret_backend"] is not None and (
                not isinstance(data["secret_backend"], str) or data["secret_backend"] not in BACKENDS):
            raise ValueError("secret_backend must be pass, gopass, prs, or None")
        _entry(data["secret_root"], allow_empty=True)
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
            self._check_location(path)
            for previous in paths:
                if path == previous or path in previous.parents or previous in path.parents:
                    raise ValueError("registered agent directories cannot overlap")
            paths.append(path)
        return data

    def _write(self, data):
        write_document(yio.value_tree(data), self.registry_path, overwrite=True)

    def _check_root(self):
        if self.root.resolve() != self.root:
            raise ValueError("store directory or an ancestor must not become a symlink")

    @contextlib.contextmanager
    def _locked(self):
        self._check_root()
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.root / ".agent_store.lock",
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
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
        allowed = {"secret_backend", "secret_root", "env_allow", "env_add"}
        if changes.keys() - allowed:
            raise ValueError("only secret and environment settings may be configured")
        with self._locked():
            data = self._read()
            proposed = {**data, **changes}
            if proposed["secret_backend"] is not None and (
                    not isinstance(proposed["secret_backend"], str) or proposed["secret_backend"] not in BACKENDS):
                raise ValueError("secret_backend must be pass, gopass, prs, or None")
            _entry(proposed["secret_root"], allow_empty=True)
            for key in ("env_allow", "env_add"):
                value = proposed[key]
                if not isinstance(value, list) or not all(
                        isinstance(item, str) and item.isascii() and item.replace("_", "a").isalnum()
                        and not item[0].isdigit() for item in value):
                    raise ValueError(f"{key} must be a list of environment variable names")
            components = {}
            if proposed["secret_backend"]:
                self._materialize((proposed["secret_backend"], "secret"), components=components)
            written = self._save_components(components)
            try:
                self._write(proposed)
            except BaseException:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
            return copy.deepcopy(proposed)

    def agents(self):
        return {name: Path(record["path"]) for name, record in self._read()["agents"].items()}

    def _check_location(self, path):
        if path.resolve() != path:
            raise ValueError(f"agent directory or an ancestor must not be a symlink: {path}")
        components = {self.root / part for part in COMPONENT_DIRS.values()}
        protected = {self.root, self.root / "store", *components,
                     Path.home().resolve(), Path(path.anchor)}
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
        if path.is_symlink():
            raise ValueError(f"agent directory must not be a symlink: {path}")
        return path

    def component_path(self, label):
        name, kind = label
        validate_id(name, kind)
        if kind not in COMPONENT_DIRS or kind == "agent":
            raise ValueError(f"not a stored component kind: {kind!r}")
        path = self.root / COMPONENT_DIRS[kind] / f"{name}.{kind}.tmpl"
        if path.parent.resolve() != path.parent or path.is_symlink():
            raise ValueError(f"stored component must not use symlinks: {path}")
        return path

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
                raise ValueError(f"agent {kind} must be a filled mapping")
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
        if "secret" not in agent:
            agent.merge(yio.value_tree({"secret": {} if data["secret_backend"] else None}))
        tree.to_source()
        for slot in tree.slots():
            if slot.label:
                self._materialize(slot.label, components=components)
        if data["secret_backend"]:
            self._materialize((data["secret_backend"], "secret"), components=components)
        return tree

    def _assemble(self, definition, name, path, data, *, components=None):
        components = components or {}

        def lookup(label):
            return components[label] if label in components else self._stored_lookup(label)

        tree = read_document(DATA_DIR / "global.tmpl")
        tree["agent"].fill(definition["agent"])
        tree = compose(tree, lookup=lookup)
        agent = tree["agent"]
        self._validate_agent(agent, filled=True)
        agent.merge(yio.value_tree({"name": name}))
        secret = agent["secret"] if "secret" in agent else None
        keyless = secret is None or (secret.kind == "scalar" and secret.value is None)
        backend = data["secret_backend"]
        if not keyless and not backend:
            raise ValueError("agent needs a secret backend; run --setup or explicitly set secret: None")
        manager = yapyon.parse("{}")
        if not keyless:
            if secret.kind != "mapping":
                raise ValueError("agent.secret must be None or a mapping with an optional entry")
            if set(secret.keys()) - {"entry"}:
                raise ValueError("agent.secret supports only entry; backend belongs to the store")
            if "entry" not in secret:
                secret.merge(yio.value_tree({"entry": path.name}))
            manager = yapyon.merge(lookup((backend, "secret"))["secret"])
            prefix = data["secret_root"].rstrip("/")
            prefix = prefix + "/" if prefix else ""
            spelling = json.dumps(prefix.replace("{", "{{").replace("}", "}}") + "{agent.secret.entry}")
            manager["entry"].replace(yapyon.parse("y" + spelling))
        else:
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
        tree["runtime"].fill(yapyon.parse("{}"))
        tree["runtime"]["agent_path"].fill(yio.value_tree(str(path)))
        tree["runtime"]["secret_backend"].fill(manager)
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
        if not isinstance(harness.get("name"), str) or not harness["name"]:
            raise ValueError("agent harness requires a name")
        if harness.get("path") != str(path / "harness"):
            raise ValueError("harness.path must be the agent directory's harness subdirectory")
        if result["runtime"]["secret_backend"]:
            _entry(result["agent"]["secret"]["entry"])
            _entry(result["runtime"]["secret_backend"]["entry"])
        return result

    def create(self, name, definition, *, path=None):
        validate_id(name)
        destination = Path(path).expanduser().absolute() if path is not None else self.root / "store" / name
        if destination.is_symlink():
            raise ValueError("agent directory must not be a symlink")
        destination = destination.resolve()
        self._check_location(destination)
        with self._locked():
            data = self._read()
            if name in data["agents"]:
                raise FileExistsError(f"agent name {name!r} is already occupied")
            if destination.exists():
                raise FileExistsError(f"agent directory already exists: {destination}")
            for record in data["agents"].values():
                previous = Path(record["path"])
                if destination in previous.parents or previous in destination.parents:
                    raise ValueError("registered agent directories cannot overlap")
            components = {}
            tree = self._prepare(definition, name, data, components=components)
            self._assemble(tree, name, destination, data, components=components)
            destination.mkdir(parents=True)
            written = []
            created_config = False
            try:
                written = self._save_components(components)
                write_document(tree, destination / "agent.cfg")
                created_config = True
                data["agents"][name] = {"path": str(destination)}
                self._write(data)
            except BaseException:
                if created_config:
                    (destination / "agent.cfg").unlink(missing_ok=True)
                for component in written:
                    component.unlink(missing_ok=True)
                try:
                    destination.rmdir()
                except OSError:
                    pass
                raise
        return destination

    def update(self, name, definition):
        with self._locked():
            path = self.locate(name)
            data = self._read()
            if (path / "agent.cfg").is_symlink():
                raise ValueError("agent.cfg must not be a symlink")
            components = {}
            tree = self._prepare(definition, name, data, components=components)
            self._assemble(tree, name, path, data, components=components)
            written = self._save_components(components)
            try:
                write_document(tree, path / "agent.cfg", overwrite=True)
            except BaseException:
                for component in written:
                    component.unlink(missing_ok=True)
                raise
            return path

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
                self._write(data)
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
            self._write(data)

    def load(self, name):
        path = self.locate(name)
        config = path / "agent.cfg"
        if config.is_symlink():
            raise ValueError("agent.cfg must not be a symlink")
        definition = read_document(config)
        if definition.root.kind != "mapping" or set(definition.keys()) != {"agent"}:
            raise ValueError("installed agent.cfg must contain only agent")
        return self._assemble(definition, name, path, self._read())

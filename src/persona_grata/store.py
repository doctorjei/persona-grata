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
from .defaults import bind_defaults, defaults_source, overlay as _overlay
from .schema import FORMAT, validate_defaults, validate_definition, validate_env_name
from . import yapyon_io as yio



DEFAULTS_PATH = DATA_DIR / "agent_store-defaults.cfg"


def default_store():
    configured = os.environ.get("AGENT_STORE_DIR")
    if configured:
        return Path(configured).expanduser().absolute()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    return ((Path(config_home).expanduser() if config_home else Path.home() / ".config") / "agents").absolute()


def make_agent(persona, harness, *, name=None):
    validate_id(persona, "persona")
    validate_id(harness, "harness")
    name = validate_id(name if name is not None else f"{persona}_{harness}")
    return yapyon.parse(
        f"agent<agent._DEFAULT_>:\n  name: {name!r}\n"
        f"  persona<persona.{persona}>: {{}}\n"
        f"  harness<harness.{harness}>: {{}}\n")


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
    inputs: dict = field(default_factory=dict, repr=False)


class AgentStore:
    def __init__(self, root=None):
        if root is not None and not os.fspath(root):
            raise ValueError("an explicit store root must not be empty")
        self.root = Path(root if root is not None else default_store()).expanduser().resolve()
        self.registry_path = self.root / "agent_store.cfg"

    @staticmethod
    def _defaults():
        return read_record(DEFAULTS_PATH)

    def _read_overrides(self):
        if not self.registry_path.exists() and not self.registry_path.is_symlink():
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
        validate_defaults(data["defaults"])
        paths_config = data["paths"]
        if not isinstance(paths_config, dict) or set(paths_config) != {"shared", "entries"}:
            raise ValueError("paths must contain only shared and entries")
        for value in paths_config.values():
            if not isinstance(value, str) or not value or "\x00" in value:
                raise ValueError("paths settings must be nonempty path strings without NUL")
        environment = data["env_vars"]
        if not isinstance(environment, dict) or set(environment) != {"from_host", "additions"}:
            raise ValueError("env_vars must contain only from_host and additions")
        for key, values in environment.items():
            if not isinstance(values, list):
                raise ValueError(f"env_vars.{key} must be a list of environment variable names")
            for value in values:
                validate_env_name(value)
        if not isinstance(data["agents"], dict):
            raise ValueError("agents must map names to path records")
        paths = []
        for name, record in data["agents"].items():
            validate_id(name)
            if not isinstance(record, dict) or set(record) != {"path"}:
                raise ValueError(f"registry entry {name!r} must contain only path")
            value = record["path"]
            if not isinstance(value, str) or not value or "\x00" in value or not Path(value).is_absolute():
                raise ValueError(f"registry path for {name!r} must be absolute")
            path = Path(value)
            if ".." in path.parts:
                raise ValueError(f"registry path for {name!r} must not contain '..'")
            self._check_location(path, data)
            for previous in paths:
                if path == previous or path in previous.parents or previous in path.parents:
                    raise ValueError("registered agent directories cannot overlap")
            paths.append(path)
        return data

    def _write(self, data):
        ordered = {key: value for key, value in data.items() if key != "env_vars"}
        if data and not self._read_overrides():
            ordered = {"format": FORMAT, **ordered}
        if "env_vars" in data:
            ordered["env_vars"] = data["env_vars"]
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
        if changes.keys() - {"defaults", "paths", "env_vars"}:
            raise ValueError("only default, path and environment settings may be configured")
        with self._locked():
            overrides = _overlay(self._read_overrides(), changes)
            proposed = self._validate_settings(_overlay(self._defaults(), overrides))
            components = {}
            backend = proposed["defaults"]["secret"]["backend"]
            if backend is not None:
                provision = lambda label: self._materialize(label, data=proposed, components=components)
                compose(yapyon.parse(f"secret<secret.{backend}>: {{}}"), lookup=provision)
            written = self._save_components(components, data=proposed)
            try:
                self._write(overrides)
            except BaseException:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
            return copy.deepcopy(proposed)

    def agents(self):
        return {name: Path(record["path"]) for name, record in self._read()["agents"].items()}

    def _configured_path(self, key, data):
        selected = Path(data["paths"][key])
        return Path(os.path.abspath(selected if selected.is_absolute() else self.root / selected))

    def _check_location(self, path, data=None):
        data = self._read() if data is None else data
        shared = self._configured_path("shared", data)
        entries = self._configured_path("entries", data)
        protected = {self.root, shared, entries, Path.home().absolute(), Path(path.anchor)}
        if (path in protected or path in self.root.parents
                or shared in path.parents or path in shared.parents):
            raise ValueError(f"unsafe agent directory: {path}")

    def locate(self, name):
        validate_id(name)
        try:
            path = self.agents()[name]
        except KeyError:
            raise ValueError(f"unknown agent {name!r}") from None
        self._check_location(path)
        return path

    def component_path(self, label, *, settings=None):
        from .composition import _check_label

        _check_label(label)
        data = self._read() if settings is None else settings
        return self._configured_path("shared", data).joinpath(*label).with_suffix(".tmpl")

    def _stored_lookup(self, label, *, data=None):
        return read_document(self.component_path(label, settings=data))

    @staticmethod
    def _remember_input(inputs, path, text):
        if inputs is not None:
            if path in inputs and inputs[path] != text:
                raise ValueError("agent source changed during preparation; retry the operation")
            inputs[path] = text

    def _source_lookup(self, label, data, *, inputs=None):
        selected = self.component_path(label, settings=data)
        text = self._signature(selected)
        self._remember_input(inputs, selected, text)
        if text is not None:
            return yapyon.parse(text, source=str(selected))
        return load_preset(label)

    def _materialize(self, label, *, data, components, inputs=None):
        if label in components:
            return components[label]
        path = self.component_path(label, settings=data)
        text = self._signature(path)
        self._remember_input(inputs, path, text)
        if text is not None:
            return yapyon.parse(text, source=str(path))
        if len(label) != 2 or label[0] not in KIND_DIRS:
            raise FileNotFoundError(f"include {'.'.join(label)!r} is not available for provisioning")
        tree = compose_preset(label[0], label[1],
                              lookup=lambda item: self._source_lookup(item, data, inputs=inputs))
        tree.to_source()
        components[label] = tree
        return tree

    def _save_components(self, components, *, data):
        written = []
        try:
            for label, tree in components.items():
                written.append(write_document(tree, self.component_path(label, settings=data)))
        except BaseException:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return written

    @staticmethod
    def _validate_agent(agent):
        if agent.kind != "mapping":
            raise ValueError("agent must be a mapping after composing its inheritance")
        if "path" in agent:
            raise ValueError("agent.path is forbidden; the registry determines the agent directory")
        keys = agent.keys()
        literal_keys = {key for key in keys if isinstance(key, str)}
        unknown = literal_keys - {"format", "name", "desc", "persona", "harness", "secret"}
        if unknown:
            raise ValueError("unknown agent fields: " + ", ".join(sorted(unknown)))
        if len(literal_keys) == len(keys):
            for kind in ("persona", "harness"):
                if kind not in agent:
                    raise ValueError(f"agent requires a {kind} definition")
        if "format" in agent:
            marker = agent["format"]
            if marker.kind != "scalar" or marker.value != FORMAT:
                raise ValueError(f"unsupported agent format; expected {FORMAT}")

    def _prepare(self, definition, name, data, *, components, inputs=None):
        if definition.root.kind != "mapping" or set(definition.keys()) != {"agent"}:
            raise ValueError("an agent document must contain only agent; runtime is invocation-owned")
        tree = compose(definition, kinds={"agent"},
                       lookup=lambda label: self._source_lookup(label, data, inputs=inputs))
        provision = lambda label: self._materialize(label, data=data, components=components, inputs=inputs)
        filled = compose(tree, lookup=provision)
        agent = filled["agent"]
        self._validate_agent(agent)
        tree["agent"].merge(yio.value_tree({"name": name}))
        backend = data["defaults"]["secret"]["backend"]
        if backend is not None:
            compose(yapyon.parse(f"secret<secret.{backend}>: {{}}"), lookup=provision)
        secret = agent["secret"] if "secret" in agent else None
        default_slot = secret is None or (secret.kind == "slot" and not secret.label)
        if default_slot and backend is None:
            if secret is not None:
                if secret.overrides.keys():
                    raise ValueError("agent needs a secret backend; select one or explicitly set secret: None")
                tree["agent"]["secret"].replace(yio.value_tree(None))
            else:
                tree = yapyon.merge(yapyon.parse("agent: {secret: None}"), tree)
        tree.to_source()
        return tree

    @staticmethod
    def _signature(path):
        return read_text(path) if path.exists() or path.is_symlink() else None

    def _assemble(self, definition, name, path, data, *, components=None, inputs=None):
        components = components or {}
        snapshots = {}
        environment = dict(os.environ)

        def lookup(label):
            if label in components:
                return components[label]
            selected = self.component_path(label, settings=data)
            text = read_text(selected)
            if selected in snapshots and snapshots[selected] != text:
                raise ValueError("agent source changed during resolution; retry the operation")
            snapshots[selected] = text
            self._remember_input(inputs, selected, text)
            return yapyon.parse(text, source=str(selected))

        backend = data["defaults"]["secret"]["backend"]
        manager = (compose(yapyon.parse(f"secret<secret.{backend}>: {{}}"), lookup=lookup)["secret"]
                   if backend is not None else yapyon.parse("{}").root)
        if backend is not None:
            default_source = yapyon.parse("agent: {secret<>: {}}")
            default_source["agent"]["secret"].fill(manager)
            manager = bind_defaults(default_source, data["defaults"])["agent"]["secret"]
        assembled = yapyon.merge(
            yapyon.parse(f"agent: {{format: {FORMAT!r}, secret<>: {{}}}}"),
            compose(definition, lookup=lookup))
        agent = assembled["agent"]
        self._validate_agent(agent)
        if "name" in agent:
            agent["name"].replace(yio.value_tree(name))
        else:
            agent.merge(yio.value_tree({"name": name}))
        secret = agent["secret"]
        if secret.kind == "slot" and not secret.label:
            if backend is None and secret.overrides.keys():
                raise ValueError("agent needs a secret backend; select one or explicitly set secret: None")
            secret.fill(manager if backend is not None else yio.value_tree(None))
        assembled = bind_defaults(assembled, data["defaults"])
        tree = yio.value_tree({"runtime": {
            "agent_path": str(path), "defaults": {}, "secret_backend": {},
        }})
        tree["runtime"]["defaults"].replace(defaults_source(data["defaults"]))
        tree["runtime"]["secret_backend"].replace(manager)
        tree = yapyon.merge(tree, assembled)
        remaining = tree.slots()
        if remaining:
            slot = remaining[0]
            raise ValueError(f"{slot.where}: required slot remains unfilled at {slot.path}")
        policy = data["env_vars"]
        result = tree.resolve(env=environment, env_allow=(
            yio.RESERVED_ENV | set(policy["from_host"]) | set(policy["additions"])))
        validate_definition(result["agent"], name=name, path=path)
        for selected, text in snapshots.items():
            if self._signature(selected) != text:
                raise ValueError("agent source changed during resolution; retry the operation")
        if inputs is not None:
            inputs.update(snapshots)
        return result

    def _check_new_agent(self, name, destination, data):
        if ".." in destination.parts:
            raise ValueError("agent directory must not contain '..'")
        self._check_location(destination, data)
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
        inputs = {self.registry_path: self._signature(self.registry_path),
                  DEFAULTS_PATH: self._signature(DEFAULTS_PATH)}
        tree = self._prepare(definition, name, data, components=components, inputs=inputs)
        resolved = self._assemble(tree, name, path, data, components=components, inputs=inputs)
        proposed = copy.deepcopy(data)
        if previous_name is not None:
            del proposed["agents"][previous_name]
        proposed["agents"][name] = {"path": str(path)}
        yapyon.dumps(proposed, max_size=MAX_SOURCE_BYTES)
        targets = [path / "agent.cfg", *(self.component_path(label, settings=data) for label in components)]
        if previous_name != name:
            targets.append(self.registry_path)
        for target in targets:
            check_write_path(target)
        return _PreparedAgent(self.root, name, path, tree, resolved, components, data,
                              previous_name, previous_source, inputs)

    def prepare_create(self, name, definition, *, path=None):
        """Resolve a prospective agent without creating its store or files."""
        validate_id(name)
        data = self._read()
        destination = (Path(path).expanduser().absolute() if path is not None
                       else self._configured_path("entries", data) / name)
        self._check_new_agent(name, destination, data)
        return self._candidate(name, definition, destination, data)

    def prepare_update(self, name, definition=None, *, new_name=None):
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
        if definition is None:
            definition = yapyon.parse(previous, source=str(path / "agent.cfg"))
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
            for selected, signature in prepared.inputs.items():
                if self._signature(selected) != signature:
                    raise ValueError("agent source or settings changed during preflight; retry the operation")
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
                written = self._save_components(prepared.components, data=data)
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
        return self.commit(self.prepare_update(old, new_name=new))

    def unregister(self, name):
        with self._locked():
            data = self._read()
            if name not in data["agents"]:
                raise ValueError(f"unknown agent {name!r}")
            del data["agents"][name]
            self._write_agents(data)

    def load(self, name):
        validate_id(name)
        registry_signature = self._signature(self.registry_path)
        data = self._read()
        if name not in data["agents"]:
            raise ValueError(f"unknown agent {name!r}")
        path = Path(data["agents"][name]["path"])
        config = path / "agent.cfg"
        text = read_text(config)
        definition = yapyon.parse(text, source=str(config))
        if definition.root.kind != "mapping" or set(definition.keys()) != {"agent"}:
            raise ValueError("installed agent.cfg must contain only agent")
        result = self._assemble(definition, name, path, data)
        if self._signature(config) != text or self._signature(self.registry_path) != registry_signature:
            raise ValueError("agent source or settings changed during resolution; retry the operation")
        return result

"""Check published docs against current agent documents, presets, and CLI help."""

import ast
import json
import re
import shlex
import tomllib
from pathlib import Path

import pytest
import yapyon

import persona_grata as pg
from persona_grata.cli import _parser, _validate_options
from persona_grata.composition import KIND_DIRS, compose
from persona_grata.store import AgentStore
from persona_grata.schema import DEFAULT_FIELDS


ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "CUSTOM_AGENTS.md", "CONFIG_REFERENCE.md", "CHANGELOG.md", "AIPOLICY.md")
CONFIG_DOCS = DOCS[:3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "pyproject.toml").exists(), reason="docs are not installed with the package")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("AGENT_STORE_DIR", str(tmp_path / "agents"))


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def fenced_blocks(text, tags=("", "yaml", "yapyon")):
    """Scan complete fences, never pairing an ignored opener with another closer."""
    blocks = []
    tag = None
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if tag is None:
            if stripped.startswith("```"):
                tag = stripped[3:].strip().lower()
                lines = []
        elif stripped.startswith("```"):
            if tag in tags:
                blocks.append("".join(lines))
            tag = None
        else:
            lines.append(line)
    return blocks


def doc_blocks():
    return [(name, index, block) for name in CONFIG_DOCS
            for index, block in enumerate(fenced_blocks(read(name)))]


BLOCKS = doc_blocks()
BLOCK_IDS = [f"{name}-{index}" for name, index, _ in BLOCKS]
_PLAIN_REFERENCE = re.compile(r'(?<![A-Za-z_])"[^"\n]*\{\{?\$?[A-Za-z_][^"\n]*\}[^"\n]*"')


def test_each_configuration_guide_contains_a_complete_agent_example():
    for name in CONFIG_DOCS:
        assert any("agent" in yapyon.parse(block) for block in fenced_blocks(read(name))), name


def test_schema_gate_rejects_retired_fields():
    for retired in ('  path: "/tmp/agent"\n', '  persona<persona._DEFAULT_>:\n    mind:\n      model: "retired"\n'):
        tree = yapyon.parse('agent<agent._DEFAULT_>:\n' + retired)
        with pytest.raises(AssertionError, match="unknown field"):
            _assert_known_fields(tree, "broken example")


def test_schema_gate_checks_agent_secret_fields_against_the_secret_component():
    valid = yapyon.parse('agent<agent._DEFAULT_>: {secret<>: {root: "team", entry: "shared/key"}}')
    _assert_known_fields(valid, "secret overrides")
    invalid = yapyon.parse('agent<agent._DEFAULT_>: {secret<>: {token_file: "retired"}}')
    with pytest.raises(AssertionError, match=r"unknown field secret.token_file"):
        _assert_known_fields(invalid, "unknown secret field")


def test_fence_scanner_skips_other_languages_without_consuming_prose():
    source = "```bash\npg --help\n```\nprose:\n```yapyon\nname: \"scout\"\n```\n"
    assert fenced_blocks(source) == ['name: "scout"\n']


def relative_links():
    for name in DOCS:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", read(name)):
            if not target.startswith(("http://", "https://", "#", "mailto:")):
                yield name, target


@pytest.mark.parametrize("name,target", sorted(set(relative_links())))
def test_relative_links_resolve(name, target):
    assert (ROOT / target.split("#")[0]).exists(), f"{name} links to missing {target}"


def _mapping(handle):
    return handle.overrides.root if handle.kind == "slot" else handle


def _field_names(handle, prefix):
    """Schema fields, excluding free-form config and per-dialect slot names."""
    mapping = _mapping(handle)
    fields = set()
    for key in mapping.keys():
        path = f"{prefix}.{key}"
        fields.add(path)
        if key in {"mind", "verify"} and mapping[key].kind == "mapping":
            fields.update(_field_names(mapping[key], path))
    return fields


def schema_fields():
    fields = {f"agent.{key}" for key in ("format", "name", "desc", "persona", "harness", "secret")}

    def walk(schema, prefix):
        for key, shape in schema.items():
            if prefix == "secret" and key == "backend":
                continue
            field = f"{prefix}.{key}"
            fields.add(field)
            if isinstance(shape, dict):
                walk(shape, field)

    for kind, schema in DEFAULT_FIELDS.items():
        fields.update({f"{kind}.name", f"{kind}.desc"})
        walk(schema, kind)
    return fields


def documented_fields():
    return set(re.findall(r"^#{3,4} `([A-Za-z0-9_.]+)`", read("CONFIG_REFERENCE.md"), re.M))


def test_reference_fields_match_complete_standard_schema():
    actual = schema_fields()
    documented = documented_fields()
    assert actual == documented, (
        f"undocumented: {sorted(actual - documented)}; obsolete: {sorted(documented - actual)}")


def test_documented_model_fallbacks_match_runtime(tmp_path):
    text = read("CONFIG_REFERENCE.md")
    active = text.split("#### `persona.mind.model_active`", 1)[1].split("####", 1)[0]
    subagent = text.split("#### `persona.mind.model_subagent`", 1)[1].split("####", 1)[0]
    assert "first" in active and "model_list" in active
    assert "model_active" in subagent
    store = AgentStore(tmp_path / "fallbacks")
    store.create("fallbacks", pg.make_agent("navigator", "codex"))
    mind = store.load("fallbacks")["agent"]["persona"]["mind"]
    assert mind["model_active"] == mind["model_list"][0]
    assert mind["model_subagent"] == mind["model_active"]


def test_reference_heading_depth_keeps_nested_fields_distinct():
    for level, name in re.findall(r"^(#{3,4}) `([A-Za-z0-9_.]+)`", read("CONFIG_REFERENCE.md"), re.M):
        expected = 4 if name.count(".") > 1 else 3
        assert len(level) == expected, f"{name} must be an H{expected} field"


def _assert_known_fields(tree, description):
    """Parsing alone must not certify an ignored, retired setting."""
    allowed = schema_fields()

    def visit(handle, kind, prefix):
        mapping = _mapping(handle)
        if mapping.kind != "mapping":
            return
        for key in mapping.keys():
            path = f"{prefix}.{key}"
            assert path in allowed, f"{description} sets unknown field {path}"
            value = mapping[key]
            if kind == "agent" and key in {"persona", "harness", "secret"}:
                visit(value, key, key)
            elif key in {"mind", "verify", "secret"}:
                visit(value, kind, path)
            elif kind == "persona" and key == "dialects":
                dialects = _mapping(value)
                if dialects.kind == "mapping":
                    for dialect in dialects.keys():
                        visit(dialects[dialect], "dialect", "dialect")

    visit(tree["agent"], "agent", "agent")


def _assert_rendered(result):
    agent = result["agent"]
    assert agent["persona"]["mind"]["endpoint"]
    harness = agent["harness"]
    assert isinstance(agent["persona"]["mind"]["dialects"].get(harness["protocol"]), dict)
    assert harness["protocol"] in harness["supported_dialects"]
    for value in harness.get("wrapper_env", {}).values():
        assert not re.search(r"\{(?:persona|runtime|mind)[.}]|\{\{", str(value))
    content = harness.get("content")
    filename = harness.get("config_file")
    if content and filename:
        suffix = Path(filename).suffix
        if suffix == ".json":
            assert isinstance(json.loads(content), dict)
        elif suffix == ".toml":
            assert isinstance(tomllib.loads(content), dict)


def _load_agent_example(tree, description, tmp_path):
    _assert_known_fields(tree, description)
    store = AgentStore(tmp_path / "example-store")
    # Resolution does not execute the manager. This also exercises keyed examples.
    store.configure(defaults={"secret": {"backend": "pass", "root": "docs"}})
    store.create("doc_example", tree)
    result = store.load("doc_example")
    _assert_rendered(result)
    saved = yapyon.parse_file(store.locate("doc_example") / "agent.cfg")
    assert "runtime" not in saved
    assert "path" not in _mapping(saved["agent"])
    return result


@pytest.mark.parametrize("name,index,block", BLOCKS, ids=BLOCK_IDS)
def test_documented_config_composes_without_resolution(name, index, block):
    tree = compose(yapyon.parse(block, source=f"{name} block {index}"))
    assert not any(slot.label for slot in tree.slots())


@pytest.mark.parametrize("name,index,block", BLOCKS, ids=BLOCK_IDS)
def test_documented_config_parses_and_agent_examples_resolve(name, index, block, tmp_path):
    assert not _PLAIN_REFERENCE.findall(block), f"{name} block {index} has a literal reference"
    tree = yapyon.parse(block)
    if "agent" in tree:
        result = _load_agent_example(tree, f"{name} block {index}", tmp_path)
        # Every endpoint reference must resolve through its declared dialect mount.
        for dialect in result["agent"]["persona"]["mind"]["dialects"].values():
            if dialect is None:
                continue
            assert isinstance(dialect["api_uri"], str)
            assert not re.search(r"\{(?:endpoint|api_uri)\}", dialect["api_uri"])
    elif "format" in tree:
        # The reference manual's registry example goes through the actual registry reader.
        store = AgentStore(tmp_path / "registry-example")
        store.root.mkdir()
        store.registry_path.write_text(block, encoding="utf-8")
        assert store.settings["format"] == tree.resolve()["format"]


@pytest.mark.parametrize("fragment,backend,entry,fetch", [
    ('entry: "shared/team-key"', "pass", "shared/team-key", "pass show shared/team-key"),
    ('secret<secret.gopass>', "gopass", "personal/keys/doc_example",
     "gopass show -o personal/keys/doc_example"),
    ('root: None', "pass", "doc_example", "pass show doc_example"),
])
def test_reference_secret_examples_resolve_selected_backend_and_complete_entry(
        fragment, backend, entry, fetch, tmp_path):
    matches = [block for name, _, block in BLOCKS
               if name == "CONFIG_REFERENCE.md" and fragment in block]
    assert len(matches) == 1
    result = _load_agent_example(yapyon.parse(matches[0]), fragment, tmp_path)
    secret = result["agent"]["secret"]
    assert secret["name"] == backend
    assert secret["entry"] == entry
    assert secret["fetch"] == fetch
    assert result["runtime"]["secret_backend"]["name"] == "pass"


def test_shipped_example_is_a_complete_agent_document(tmp_path):
    tree = yapyon.parse_file(ROOT / "agents-example.yaml")
    assert tree.keys() == ["agent"]
    result = _load_agent_example(tree, "agents-example.yaml", tmp_path)
    mind = result["agent"]["persona"]["mind"]
    assert mind["dialects"]["anthropic"]["api_uri"] == mind["endpoint"] + "/anthropic_api"
    assert mind["dialects"].get("responses") is None
    assert result["agent"]["secret"] is None


def table_column(document, header):
    lines = read(document).splitlines()
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header not in cells:
            continue
        column = cells.index(header)
        values = set()
        for row in lines[index + 1:]:
            if not row.strip().startswith("|"):
                break
            if set(row.replace("|", "").strip()) <= {"-", ":", " "}:
                continue
            values.add(row.strip().strip("|").split("|")[column].strip())
        return values
    return set()


@pytest.mark.parametrize("header,kind", [("Persona", "persona"), ("Harness", "harness")])
def test_readme_lists_current_shipped_presets(header, kind):
    directory = ROOT / "src" / "persona_grata" / "data" / KIND_DIRS[kind]
    shipped = {path.stem for path in directory.glob("*.tmpl")
               if path.stem not in {"_BUILTIN_", "_DEFAULT_"}}
    assert shipped, f"no current {kind} presets found"
    documented = table_column("README.md", header)
    assert documented == shipped, (
        f"missing: {sorted(shipped - documented)}; obsolete: {sorted(documented - shipped)}")


def test_store_table_matches_current_registry_settings(tmp_path):
    documented = table_column("CONFIG_REFERENCE.md", "Setting")
    assert {name.strip("`") for name in documented} == set(AgentStore(tmp_path / "registry").settings)


def test_readme_parameter_table_matches_cli_help():
    parser = _parser()
    help_text = parser.format_help()
    options = set(re.findall(r"--[a-z][a-z-]*", help_text)) - {"--help", "--version"}
    documented = set().union(*(set(re.findall(r"--[a-z][a-z-]*", cell))
                               for cell in table_column("README.md", "Parameter")))
    assert documented == options, (
        f"undocumented: {sorted(options - documented)}; obsolete: {sorted(documented - options)}")


def cli_examples():
    for name in CONFIG_DOCS:
        for block in fenced_blocks(read(name), tags=("bash", "sh", "shell")):
            for line in block.splitlines():
                args = shlex.split(line, comments=True)
                if args and args[0] in {"pg", "persona-grata"}:
                    yield name, args[1:]


@pytest.mark.parametrize("name,args", list(cli_examples()))
def test_documented_cli_examples_have_current_valid_mode_options(name, args):
    parser = _parser()
    try:
        _validate_options(parser, parser.parse_args(args))
    except SystemExit as error:
        pytest.fail(f"{name}: {' '.join(args)} rejected by CLI parser: {error}")


def test_version_agrees_with_project_and_changelog():
    metadata = tomllib.loads(read("pyproject.toml"))["project"]
    version = metadata["version"]
    assert version == pg.__version__
    prerelease = re.search(r"(?:\.dev\d*|(?:a|b|rc)\d*)$", version)
    section = "Unreleased" if prerelease else version
    assert re.search(rf"^## \[{re.escape(section)}\]", read("CHANGELOG.md"), re.M)


def test_documented_yapyon_floor_matches_declared_dependency():
    metadata = tomllib.loads(read("pyproject.toml"))["project"]
    requirement = next(item for item in metadata["dependencies"] if item.startswith("yapyon"))
    floor = re.search(r">=([0-9.]+)", requirement)
    assert floor, "yapyon dependency must declare a minimum version"
    for name in ("README.md", "CONFIG_REFERENCE.md"):
        assert f"{floor.group(1)} or newer" in read(name), f"{name} has a stale yapyon floor"


def source_files():
    return sorted(path.relative_to(ROOT).as_posix()
                  for directory in (ROOT / "src", ROOT / "tests") for path in directory.rglob("*.py"))


@pytest.mark.parametrize("relative", source_files())
def test_sources_parse_under_declared_python_floor(relative):
    requirement = tomllib.loads(read("pyproject.toml"))["project"]["requires-python"]
    floor = re.search(r">=(\d+)\.(\d+)", requirement)
    assert floor, "requires-python must declare its minimum version"
    target = tuple(map(int, floor.groups()))
    try:
        ast.parse(read(relative), feature_version=target)
    except SyntaxError as error:
        pytest.fail(f"{relative} is not valid Python {target[0]}.{target[1]}: {error}")

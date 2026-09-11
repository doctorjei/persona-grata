"""Documentation consistency: what the docs claim, checked against the code.

These verify the docs against the data files and the CLI rather than reading
them for plausibility -- a link that resolves, a YAML example that still loads,
a schema key that is actually documented. They have caught an unparseable
example, a persona list that went stale, and a `verify.url` that had been dead
for a day in a shipped example config.

Everything here reads the *source checkout* (the docs are not installed), so the
whole module skips when there is no pyproject.toml beside it.
"""

import ast
import contextlib
import io
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

import persona_grata as pg

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "CUSTOM_AGENTS.md", "CONFIG_REFERENCE.md", "CHANGELOG.md", "AIPOLICY.md"]

pytestmark = pytest.mark.skipif(not (ROOT / "pyproject.toml").exists(),
                                reason="docs are not part of an installed package")


@pytest.fixture(autouse=True)
def fixed_store(monkeypatch):
    """Pin the store, so a doc example resolves the same way everywhere."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")


def read(name):
    return (ROOT / name).read_text()


def fenced_blocks(text):
    """Every fenced block that is YAML (untagged or ```yaml), in order."""
    return re.findall(r"```(?:yaml)?\n(.*?)```", text, re.S)


def load_quietly(path):
    """``load_config`` with stderr captured; returns the warnings it printed.

    Warnings are the point rather than noise: an unknown key is *ignored*, so a
    stale setting in a doc example loads perfectly well and does nothing.
    """
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        pg.load_config(str(path))
    return stderr.getvalue()


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #
def _relative_links():
    for md in sorted(ROOT.glob("*.md")):
        for _text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", md.read_text()):
            if not target.startswith(("http://", "https://", "#", "mailto:")):
                yield md.name, target


@pytest.mark.parametrize("md,target", sorted(set(_relative_links())),
                         ids=lambda v: v.replace("/", "-"))
def test_relative_links_resolve(md, target):
    assert (ROOT / target.split("#")[0]).exists(), f"{md} links to a missing {target}"


# --------------------------------------------------------------------------- #
# YAML examples
# --------------------------------------------------------------------------- #
def _doc_blocks():
    for name in DOCS:
        for i, block in enumerate(fenced_blocks(read(name))):
            if "<" in block:                    # a placeholder skeleton, not real YAML
                continue
            yield name, i, block


@pytest.mark.parametrize("doc,index,block", list(_doc_blocks()),
                         ids=lambda v: str(v) if not isinstance(v, str) or "\n" not in v else "")
def test_documented_yaml_parses_and_loads(doc, index, block, tmp_path):
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError as error:
        pytest.fail(f"{doc} block {index} is not parseable: "
                    f"{str(error).splitlines()[0]}")
    if not (isinstance(parsed, dict) and "personas" in parsed):
        return                                  # a fragment, not a whole config
    path = tmp_path / "a.yaml"
    path.write_text(block)
    try:
        warnings = load_quietly(path)
    except SystemExit as error:
        # load_config reports a *user* error by exiting rather than raising, and
        # SystemExit is a BaseException -- so it needs catching on its own.
        pytest.fail(f"{doc} block {index} is rejected: {error}")
    # Held to the same bar as the shipped example configs, and for the same
    # reason: a setting that no longer exists still *loads*, because unknown
    # keys merge in and are ignored. Parsing is not the interesting failure.
    unknown = [line for line in warnings.splitlines() if "unknown setting" in line]
    assert not unknown, (f"{doc} block {index} sets keys that no longer exist: "
                         + "; ".join(unknown))


def _example_configs():
    return sorted(p.name for p in ROOT.glob("*example*.yaml"))


@pytest.mark.parametrize("name", _example_configs())
def test_shipped_example_configs_have_no_stale_settings(name):
    # These ship, and nothing else reads them: agents-example.yaml kept a
    # `verify.url` for a day after the key was renamed, because an unknown key
    # merges in, warns, and is ignored. So "it loads" is not the bar.
    try:
        warnings = load_quietly(ROOT / name)
    except SystemExit as error:
        pytest.fail(f"{name} is rejected: {error}")
    unknown = [line for line in warnings.splitlines() if "unknown setting" in line]
    assert not unknown, f"{name} sets keys that no longer exist: " + "; ".join(unknown)


# --------------------------------------------------------------------------- #
# CONFIG_REFERENCE against the schema
# --------------------------------------------------------------------------- #
#: Keys that are real but have no line in a schema file. `mind.dialects` is
#: seeded from the dialect registry; pid/hid are injected by the loader.
_UNDECLARED_KEYS = {"pid", "hid", "mind.dialects"}

#: Type names that appear in headings without being settings.
_NOT_KEYS = {"str", "NestedDict"}


def schema_keys():
    """Every key the `.default.yaml` files define -- those files *are* the schema."""
    keys = set()
    for name in ("agents.default.yaml", "persona.default.yaml",
                 "harness.default.yaml", "dialect.default.yaml"):
        data = yaml.safe_load((pg.DATA_DIR / name).read_text()) or {}
        for key, value in data.items():
            keys.add(key)
            if isinstance(value, dict) and key in ("mind", "verify"):
                keys |= {f"{key}.{child}" for child in value}
    return keys | _UNDECLARED_KEYS


def documented_keys():
    keys = set()
    for line in read("CONFIG_REFERENCE.md").splitlines():
        if not re.match(r"^#{3,} `", line):
            continue
        keys |= set(re.findall(r"`([A-Za-z0-9_.]+)`", line))
        # One heading may stand for a numbered family, written as
        # `mind.model_{n}` -> ..., for n in [1:4]. Expand it rather than
        # hardcoding which members are covered.
        family = re.search(r"`([A-Za-z0-9_.]+)\{n\}`", line)
        span = re.search(r"for n in \[(\d+):(\d+)\]", line)
        if family and span:
            keys |= {f"{family.group(1)}{n}"
                     for n in range(int(span.group(1)), int(span.group(2)) + 1)}
    return keys


def test_every_schema_key_is_documented():
    missing = schema_keys() - documented_keys()
    assert not missing, f"CONFIG_REFERENCE.md documents no {sorted(missing)}"


def test_no_documented_key_has_left_the_schema():
    extra = documented_keys() - schema_keys() - _NOT_KEYS
    assert not extra, f"CONFIG_REFERENCE.md still documents {sorted(extra)}"


def _config_reference_headings():
    for line in read("CONFIG_REFERENCE.md").splitlines():
        match = re.match(r"^(#{3,})\s+`([A-Za-z0-9_.]+)`", line)
        if match:
            yield len(match.group(1)), match.group(2)


@pytest.mark.parametrize("level,key", sorted(set(_config_reference_headings())),
                         ids=lambda v: str(v))
def test_heading_depth_mirrors_schema_nesting(level, key):
    # A dotted key is a child and gets H4; everything else is top level and gets
    # H3. Getting this wrong reads as though `token` were nested under `path`.
    want = 4 if "." in key else 3
    assert level == want, f"`{key}` is H{level}, expected H{want}"


# --------------------------------------------------------------------------- #
# README's preset lists
# --------------------------------------------------------------------------- #
def untracked_presets():
    """Preset ids present in data/ but not committed -- someone's WIP.

    Those are discovered as shipped presets and would fail the list check while
    they are being written, so they are excused. Empty in CI, and empty without
    a git checkout.
    """
    try:
        out = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {Path(p).stem.split(".", 1)[-1] for p in out.split() if "/data/" in p}


@pytest.mark.parametrize("label,kind", [("Endpoint Personas", "persona"),
                                        ("Coding Harnesses", "harness")])
def test_readme_lists_every_shipped_preset(label, kind):
    # Read to the blank line rather than the newline: the list wraps once it
    # outgrows the README's width, and a one-line regex silently sees half of it.
    match = re.search(rf"_{label}_: (.+?)\n\s*\n", read("README.md"), re.S)
    assert match, f"README.md has no '_{label}_:' list"
    documented = sorted(name.strip() for name in match.group(1).split(","))
    excused = untracked_presets()
    shipped = sorted(name for name in pg.preset_names(kind) if name not in excused)
    assert documented == shipped


# --------------------------------------------------------------------------- #
# Rendered output the config-file check cannot see
# --------------------------------------------------------------------------- #
def test_every_wrapper_env_fully_resolves():
    # A harness configured entirely by environment writes no config file, so the
    # "renders a parseable config" check skips it and an unresolved template
    # would reach the user's shell verbatim.
    with contextlib.redirect_stderr(io.StringIO()):
        config = pg.load_config()
    unresolved = {
        f"{pid}/{hid}": [key for key, value in (harness.get("wrapper_env") or {}).items()
                         if isinstance(value, str) and "{{" in value]
        for pid, persona in config["personas"].items()
        for hid, harness in persona["harnesses"].items()
    }
    assert not {k: v for k, v in unresolved.items() if v}


# --------------------------------------------------------------------------- #
# Version agreement, and the declared Python floor
# --------------------------------------------------------------------------- #
def test_version_agrees_across_the_project():
    declared = re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M).group(1)
    assert declared == pg.__version__, "pyproject.toml and __version__ disagree"
    assert f"[{declared}]" in read("CHANGELOG.md"), f"CHANGELOG.md has no {declared} section"


def _source_files():
    return sorted(p.relative_to(ROOT).as_posix()
                  for p in list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")))


@pytest.mark.parametrize("relative", _source_files())
def test_sources_parse_under_the_declared_python_floor(relative):
    # requires-python is a promise; syntax newer than the floor breaks it for
    # exactly the users who read it before installing.
    floor = re.search(r'requires-python = ">=(\d+)\.(\d+)"', read("pyproject.toml"))
    target = (int(floor.group(1)), int(floor.group(2)))
    try:
        ast.parse((ROOT / relative).read_text(), feature_version=target)
    except SyntaxError as error:
        pytest.fail(f"{relative} is not valid Python {target[0]}.{target[1]}: {error}")

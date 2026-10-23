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
import yapyon

import persona_grata as pg
from persona_grata import yapyon_io

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "CUSTOM_AGENTS.md", "CONFIG_REFERENCE.md", "CHANGELOG.md", "AIPOLICY.md"]

pytestmark = pytest.mark.skipif(not (ROOT / "pyproject.toml").exists(),
                                reason="docs are not part of an installed package")


@pytest.fixture(autouse=True)
def fixed_store(monkeypatch):
    """Pin the store, so a doc example resolves the same way everywhere."""
    monkeypatch.delenv("PERSONA_STORE_DIR", raising=False)   # outranks XDG
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")


def read(name):
    return (ROOT / name).read_text()


def fenced_blocks(text, tags=("", "yaml", "yapyon")):
    """Every fenced block whose tag is a config dialect, in order.

    ⚠️ Written as a scanner rather than one regex, because a regex cannot tell a
    fence *opener* from a fence *closer*. The old
    ``re.findall(r"```(?:yaml)?\\n(.*?)```", ...)`` matched an opener only for a
    bare or ```yaml fence, so a block tagged ```yapyon left its **closing** fence
    to be paired with the next block's opener -- which handed the **prose
    between two examples** to the parser. That prose is full of lines ending
    ``as follows:``, and a trailing colon is exactly what makes a YAML scanner
    say "mapping values are not allowed here". Nine params were failing on
    nothing but that, none of them a config.

    A block tagged with anything else (```bash, ```text) is now skipped whole,
    which is what was always meant.
    """
    blocks = []
    open_tag = None
    buf = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if open_tag is None:
            if stripped.startswith("```"):
                open_tag = stripped[3:].strip().lower()
                buf = []
        elif stripped.startswith("```"):
            if open_tag in tags:
                blocks.append("".join(buf))
            open_tag = None
        else:
            buf.append(line)
    return blocks


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


#: A plain (unprefixed) string holding what looks like a reference: `"{x}"`,
#: `"{{x}}"`, `"${{x}}"`, `"{$X}"`. yapyon reads it as literal text, so an example
#: spelled this way parses, loads, and silently does nothing -- which is how the
#: retired `${{...}}` examples survived the gate above for weeks.
_PLAIN_REFERENCE = re.compile(r'(?<![A-Za-z])"[^"\n]*\{\{?\$?[A-Za-z_][^"\n]*\}[^"\n]*"')


@pytest.mark.parametrize("doc,index,block", list(_doc_blocks()),
                         ids=lambda v: str(v) if not isinstance(v, str) or "\n" not in v else "")
def test_no_documented_reference_is_a_plain_string(doc, index, block):
    stray = _PLAIN_REFERENCE.findall(block)
    assert not stray, f"{doc} block {index}: {stray} is literal text -- write it as a y-string"


@pytest.mark.parametrize("doc,index,block", list(_doc_blocks()),
                         ids=lambda v: str(v) if not isinstance(v, str) or "\n" not in v else "")
def test_documented_yaml_parses_and_loads(doc, index, block, tmp_path):
    # Parsed with yapyon, because that is what reads a user's file now. The gate
    # has to be stricter than the docs it checks: while this said `yaml.safe_load`
    # the prose moved to yapyon ahead of the thing validating it, and the gate was
    # certifying examples in a grammar nobody uses.
    try:
        parsed = yapyon.parse(block)
    except yapyon.AkanError as error:
        pytest.fail(f"{doc} block {index} is not parseable: "
                    f"{str(error).splitlines()[0]}")
    plain = yapyon_io._plain(parsed)
    if not (isinstance(plain, dict) and "personas" in plain):
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
    return sorted(p.name for p in list(ROOT.glob("*example*.yaml"))
                 + list(ROOT.glob("*example*.yap")))


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
    """Every key the schema-default templates define -- those files *are* the schema.

    Read through the loader rather than `yaml.safe_load`, so this sees the
    documents as the code does. Only the key names matter, and both readers
    agree on them today, but a schema that stops being valid yapyon should fail
    the docs gate too rather than quietly yielding a short key set.

    Paths come from the loader's own `default_path()` rather than a literal list
    of filenames: the hand-written ones here outlived two layout changes and then
    silently yielded a short key set when the defaults moved into data/<kind>/.
    """
    keys = set()
    paths = [pg.DATA_DIR / "agents.tmpl"] + [pg.default_path(kind) for kind in
                                            ("persona", "harness", "dialect", "secret")]
    for path in paths:
        data = pg.load_template(path) or {}
        for key, value in data.items():
            keys.add(key)
            if isinstance(value, dict) and key in ("mind", "verify"):
                keys |= {f"{key}.{child}" for child in value}
    # The store-only settings live in persona_store.cfg, not in any shipped schema.
    return keys | _UNDECLARED_KEYS | set(pg._ENV_SETTINGS)


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
    # Id is the part BEFORE the first dot, matching preset_names(). The old
    # layout spelled presets `persona.kimi`, so [-1] was right there; on
    # `kimi.persona.tmpl` it would yield the kind instead of the id.
    return {Path(p).stem.split(".", 1)[0] for p in out.split() if "/data/" in p}


def readme_column(header):
    """The values in the README table column named `header`.

    The README now lists shipped presets as markdown tables rather than the
    `_Label_: a, b, c` lines this gate used to parse. The gate's job is
    unchanged -- every shipped preset is named somewhere the reader can find --
    so it reads the column instead of the line.
    """
    lines = read("README.md").splitlines()
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header not in cells:
            continue
        col = cells.index(header)
        values = set()
        for row in lines[i + 1:]:
            row = row.strip()
            if not row.startswith("|"):
                break                                   # end of the table
            if set(row.replace("|", "")) <= {"-", ":", " "}:
                continue                                # the |----| separator
            values.add(row.strip("|").split("|")[col].strip())
        return values
    return set()


@pytest.mark.parametrize("header,kind", [("Persona", "persona"), ("Harness", "harness")])
def test_readme_lists_every_shipped_preset(header, kind):
    documented = readme_column(header)
    assert documented, f"README.md has no table with a '{header}' column"
    excused = untracked_presets()
    shipped = {name for name in pg.preset_names(kind) if name not in excused}
    # Set difference in the message, not just `a != b`: the useful half of a
    # mismatch is which names are missing and which are stale.
    assert documented == shipped, (
        f"README '{header}' column disagrees with the shipped presets -- "
        f"missing from README: {sorted(shipped - documented) or 'none'}, "
        f"not shipped: {sorted(documented - shipped) or 'none'}")


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
#: A PEP 440 pre-release or development suffix, which is what makes a version
#: a snapshot of unreleased work rather than a release.
_PRERELEASE = re.compile(r"(\.dev\d*|(?:a|b|rc)\d*)$")


def test_version_agrees_across_the_project():
    declared = re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M).group(1)
    assert declared == pg.__version__, "pyproject.toml and __version__ disagree"
    # Compare against the state the version declares, not the string itself: a
    # pre-release describes work that has not been released, so its section is
    # [Unreleased]. Demanding a [0.0.4.dev0] heading would put a version nobody
    # can install into the changelog, and this check would then fail for every
    # tree between releases -- which is the shape we keep the .dev bump for.
    expected = "Unreleased" if _PRERELEASE.search(declared) else declared
    # Match the heading, not the string: every section also has a link
    # definition at the foot of the file, so a bare `[0.0.3]` search passes on
    # a changelog that links the version and documents nothing.
    heading = re.compile(rf"^## \[{re.escape(expected)}\]", re.M)
    assert heading.search(read("CHANGELOG.md")), f"CHANGELOG.md has no {expected} section"


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

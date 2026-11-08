"""Canonical registry defaults ship independently of the user's sample workspace."""

from fnmatch import fnmatch
from importlib.resources import files
from pathlib import Path
import re
import shlex
import tomllib

import pytest
import yapyon


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_RESOURCE = "data/agent_store-defaults.cfg"


def test_packaged_defaults_are_the_current_literal_schema():
    resource = files("persona_grata").joinpath(DEFAULTS_RESOURCE)
    data = yapyon.loads_record(resource.read_text(encoding="utf-8"))
    expected_env = """COLUMNS DATEMSK HOME LANG LANGUAGE LC_ALL LC_COLLATE LC_CTYPE
        LC_MESSAGES LC_MONETARY LC_NUMERIC LC_TIME LINES LOGNAME MSGVERB NLSPATH
        PATH PWD SHELL TERM TEXTDOMAIN TEXTDOMAINDIR TMPDIR TZ XDG_CACHE_HOME
        XDG_CONFIG_DIRS XDG_CONFIG_HOME XDG_DATA_DIRS XDG_DATA_HOME XDG_RUNTIME_DIR
        XDG_STATE_HOME""".split()
    assert data == {
        "format": "agents-yapyon-v0.1",
        "agents": {},
        "defaults": {"secret": {"backend": None, "root": "agents"}},
        "env_add": [],
        "env_allow": expected_env,
    }


def test_wheel_package_data_includes_canonical_defaults():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["persona_grata"]
    assert any(fnmatch(DEFAULTS_RESOURCE, pattern) for pattern in patterns)


def test_sdist_manifest_includes_canonical_defaults():
    rules = [line.split() for line in (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()]
    assert any(
        len(rule) >= 3
        and rule[:2] == ["recursive-include", "src/persona_grata/data"]
        and any(fnmatch("agent_store-defaults.cfg", pattern) for pattern in rule[2:])
        for rule in rules
    )


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="workflow checks require a repository checkout")
@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_workflow_wheel_gate_references_existing_runtime_assets(workflow):
    source = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    gate = re.search(r"for f in (.*?);\s*do", source, re.S)
    assert gate, f"{workflow} must check the wheel's runtime assets"
    assets = shlex.split(gate.group(1).replace("\\\n", " "))
    assert {"secrets/pass.secret.tmpl", "agent_store-defaults.cfg"} <= set(assets)
    for asset in assets:
        assert (ROOT / "src" / "persona_grata" / "data" / asset).is_file(), asset

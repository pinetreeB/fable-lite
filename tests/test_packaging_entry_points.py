from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read_pyproject() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _scripts_section() -> dict[str, str]:
    text = _read_pyproject()
    match = re.search(r"^\[project\.scripts\][ \t]*\r?\n(?P<body>.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        return {}
    entries: dict[str, str] = {}
    for line in match.group("body").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        name, _, target = line.partition("=")
        entries[name.strip()] = target.strip().strip('"')
    return entries


# PKG-01: smtw / fable-lite console_scripts entry points -------------------


def test_project_scripts_declares_smtw_and_fable_lite_aliases() -> None:
    scripts = _scripts_section()
    assert scripts.get("smtw") == "fable_lite.cli:main"
    assert scripts.get("fable-lite") == "fable_lite.cli:main"


@pytest.mark.parametrize("script_name", ["smtw", "fable-lite"])
def test_declared_entry_point_target_actually_imports_and_is_callable(script_name: str) -> None:
    # "script 진입점 import 테스트": the string declared in [project.scripts] must
    # resolve to a real, importable, callable target -- not just be present as text.
    scripts = _scripts_section()
    target = scripts.get(script_name)
    assert target is not None, f"{script_name} is not declared in [project.scripts]"
    module_name, _, attr_name = target.partition(":")
    module = importlib.import_module(module_name)
    entry = getattr(module, attr_name)
    assert callable(entry)


# PKG-01: `version` subcommand ----------------------------------------------


def test_cli_has_a_version_subcommand_matching_pyproject_version() -> None:
    from fable_lite.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["version"])
    assert hasattr(args, "func")


def test_version_subcommand_prints_the_pyproject_version() -> None:
    match = re.search(r'^version\s*=\s*"(?P<version>[^"]+)"', _read_pyproject(), re.MULTILINE)
    assert match is not None
    expected = match.group("version")

    result = subprocess.run(
        [sys.executable, "-m", "fable_lite", "version"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert expected in result.stdout

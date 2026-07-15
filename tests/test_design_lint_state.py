from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import TypeAlias, cast

import pytest

from core.ledger import load_ledger, record_event


ROOT = Path(__file__).resolve().parents[1]
JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _git(project: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr


def _init_repo(project: Path) -> None:
    _git(project, "init")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")


def _write(project: Path, relative: str, text: str) -> None:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _run_design(project: Path) -> tuple[subprocess.CompletedProcess[str], JsonObject]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    process = subprocess.run(
        [sys.executable, "-m", "fable_lite", "check", "--root", str(project), "--design"],
        cwd=ROOT,
        env={
            **os.environ,
            "FABLE_LITE_DESIGN_GATE": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": python_path,
        },
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    decoded = cast(object, json.loads(process.stdout))
    assert isinstance(decoded, dict)
    return process, cast(JsonObject, decoded)


def _violations(payload: JsonObject) -> list[JsonObject]:
    raw = payload["violations"]
    assert isinstance(raw, list)
    assert all(isinstance(item, dict) for item in raw)
    return cast(list[JsonObject], raw)


def test_design_cli_persists_result_for_stop_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an enabled UI turn has changed a file with a raw color.
    _init_repo(tmp_path)
    monkeypatch.setenv("FABLE_LITE_DESIGN_GATE", "1")
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "src/App.tsx UI 화면을 수정해줘",
        }
    )
    _write(tmp_path, "src/App.tsx", 'export const ink = "#123456";\n')
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "src/App.tsx",
            "kind": "code",
        }
    )

    # When: the one-shot CLI executes the design lint.
    process, payload = _run_design(tmp_path)
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: the exact result and fresh epoch are available to Stop without a second lint run.
    assert process.returncode == 1
    assert payload["passed"] is False
    assert ledger["design_check_passed"] is False
    assert ledger["design_check_seq"] > ledger["design_last_change_seq"]
    assert ledger["design_violations"] == payload["violations"]


def test_design_lint_ignores_preexisting_dirty_violation_in_touched_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a raw-color violation was already dirty before the enabled UI turn began.
    _init_repo(tmp_path)
    _write(tmp_path, "src/App.css", ".base { color: var(--ink); }\n")
    _git(tmp_path, "add", "src/App.css")
    _git(tmp_path, "commit", "-m", "ui base")
    _write(
        tmp_path,
        "src/App.css",
        ".base { color: var(--ink); }\n.legacy { color: #123456; }\n",
    )
    monkeypatch.setenv("FABLE_LITE_DESIGN_GATE", "1")
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "src/App.css UI 화면을 수정해줘",
        }
    )

    # When: the turn appends only a tokenized rule and records that UI mutation.
    _write(
        tmp_path,
        "src/App.css",
        ".base { color: var(--ink); }\n.legacy { color: #123456; }\n.new { color: var(--brand); }\n",
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "src/App.css",
            "kind": "code",
        }
    )
    process, payload = _run_design(tmp_path)

    # Then: only turn-local lines are linted, so pre-turn dirty debt does not block.
    assert process.returncode == 0
    assert payload["passed"] is True
    assert _violations(payload) == []


def test_design_lint_blocks_a_preexisting_dirty_violation_moved_during_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a dirty raw-color line exists before the enabled UI turn.
    _init_repo(tmp_path)
    _write(
        tmp_path,
        "src/App.css",
        ".legacy { color: #123456; }\n.clean { color: var(--ink); }\n",
    )
    monkeypatch.setenv("FABLE_LITE_DESIGN_GATE", "1")
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "src/App.css UI 화면을 수정해줘",
        }
    )

    # When: the turn moves that violation below the clean line.
    _write(
        tmp_path,
        "src/App.css",
        ".clean { color: var(--ink); }\n.legacy { color: #123456; }\n",
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "src/App.css",
            "kind": "code",
        }
    )
    process, payload = _run_design(tmp_path)

    # Then: moving legacy debt counts as a current-turn change and cannot launder it.
    assert process.returncode == 1
    assert [(item["line"], item["rule_id"]) for item in _violations(payload)] == [
        (2, "design/raw-color")
    ]


def test_design_lint_keeps_same_line_legacy_debt_exempt_during_clean_reorder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: legacy raw-color debt is dirty at line two before the UI turn.
    _init_repo(tmp_path)
    _write(
        tmp_path,
        "src/App.css",
        ".clean-a { color: var(--a); }\n.legacy { color: #123456; }\n.clean-b { color: var(--b); }\n",
    )
    monkeypatch.setenv("FABLE_LITE_DESIGN_GATE", "1")
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "src/App.css UI 화면을 수정해줘",
        }
    )

    # When: only the clean surrounding lines trade places.
    _write(
        tmp_path,
        "src/App.css",
        ".clean-b { color: var(--b); }\n.legacy { color: #123456; }\n.clean-a { color: var(--a); }\n",
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "src/App.css",
            "kind": "code",
        }
    )
    process, payload = _run_design(tmp_path)

    # Then: unchanged same-line legacy debt remains outside current-turn scope.
    assert process.returncode == 0
    assert payload["passed"] is True
    assert _violations(payload) == []

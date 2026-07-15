from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from core.classify import classify_prompt
from core.design_gate_state import design_lint_scope, record_design_result
from core.design_lint import DesignLintResult
from core.ledger import load_ledger, record_event, save_ledger
from core.ledger_v1 import default_ledger
from core.verify_state import evaluate_stop


def _record(root: Path, payload: dict[str, object]) -> None:
    _ = record_event({"project_root": str(root), **payload})


def _start_ui_turn(root: Path) -> None:
    _record(
        root,
        {
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "src/App.tsx UI 화면을 수정해줘",
        },
    )
    _record(root, {"event": "change", "path": "src/App.tsx", "kind": "code"})


def _verify(root: Path) -> None:
    _record(
        root,
        {
            "event": "verification",
            "command": "python -m pytest tests/test_ui.py -q",
            "success": True,
            "evidence": "1 passed",
        },
    )


def _run_design(root: Path, agent: str = "") -> tuple[int, dict[str, object]]:
    command = [sys.executable, "-m", "fable_lite", "check", "--root", str(root), "--design"]
    if agent:
        command.extend(("--agent", agent))
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode, json.loads(completed.stdout)


def test_design_gate_runs_after_ordinary_stop_counter_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an enabled UI turn lacks both ordinary and design verification.
    monkeypatch.setenv("FABLE_LITE_DESIGN_GATE", "1")
    _start_ui_turn(tmp_path)

    # When: Stop is attempted until both independent counters are exhausted.
    decisions = [evaluate_stop({"project_root": str(tmp_path)}) for _ in range(5)]
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: ordinary fail-open cannot skip the two design blocks.
    assert [item["decision"] for item in decisions] == [
        "block",
        "block",
        "block",
        "block",
        "allow",
    ]
    assert ledger["stop_blocks"] == 2
    assert ledger["design_blocks"] == 2


def test_design_result_store_rejects_a_change_after_lint_scope_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: lint captured a passing scope before another UI mutation occurred.
    monkeypatch.setenv("FABLE_LITE_DESIGN_GATE", "1")
    _start_ui_turn(tmp_path)
    scope = design_lint_scope(tmp_path, "")
    _record(root=tmp_path, payload={"event": "change", "path": "src/Panel.tsx", "kind": "code"})
    _verify(tmp_path)

    # When: the stale result attempts to store against its captured change epoch.
    stored = record_design_result(
        tmp_path,
        "",
        DesignLintResult((), ("src/App.tsx",)),
        expected_change_seq=scope.change_seq,
        turn_key=scope.turn_key,
    )
    decision = evaluate_stop({"project_root": str(tmp_path)})

    # Then: the stale pass is rejected and Stop still requires a fresh design check.
    assert stored is False
    assert decision["decision"] == "block"
    assert decision["reason_code"] == "stop_design_lint_missing"


@pytest.mark.parametrize(
    "prompt",
    ["UI/UX 개선해줘", "Improve UI.", "UI를 개선해줘", "UX를 개선해줘"],
)
def test_ui_keyword_boundaries_survive_slashes_and_punctuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    # Given: the gate is enabled and the UI keyword touches punctuation.
    monkeypatch.setenv("FABLE_LITE_DESIGN_GATE", "1")

    # When: the existing classifier handles the prompt.
    result = classify_prompt({"prompt": prompt, "project_root": str(tmp_path)})

    # Then: punctuation cannot downgrade an explicit UI task to GENERAL.
    assert result["domain"] == "UI"
    assert result["design_required"] is True


def test_enabled_turn_cannot_disable_its_own_design_stop_gate(tmp_path: Path) -> None:
    # Given: project config enabled a UI turn with a fresh passing design check.
    config = tmp_path / "design" / "gate.config"
    config.parent.mkdir(parents=True)
    config.write_text('{"enabled": true}\n', encoding="utf-8", newline="\n")
    _start_ui_turn(tmp_path)
    _verify(tmp_path)
    code, payload = _run_design(tmp_path)
    assert code == 0 and payload["passed"] is True

    # When: the same turn records a policy change to disabled before Stop.
    config.write_text('{"enabled": false}\n', encoding="utf-8", newline="\n")
    _record(tmp_path, {"event": "change", "path": "design/gate.config", "kind": "code"})
    _verify(tmp_path)
    decision = evaluate_stop({"project_root": str(tmp_path)})

    # Then: prompt-time opt-in remains authoritative and the old PASS is stale.
    assert decision["decision"] == "block"
    assert decision["reason_code"] == "stop_design_lint_missing"


def test_allowlist_change_invalidates_previously_passing_design_result(tmp_path: Path) -> None:
    # Given: a temporary allowlist made the current raw color pass design lint.
    config = tmp_path / "design" / "gate.config"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "enabled": True,
                "allowlist": [
                    {
                        "path": "src/App.tsx",
                        "rule_id": "design/raw-color",
                        "reason": "temporary migration",
                        "expires": "2099-12-31",
                    }
                ],
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    _start_ui_turn(tmp_path)
    target = tmp_path / "src" / "App.tsx"
    target.parent.mkdir(parents=True)
    target.write_text('export const ink = "#123456";\n', encoding="utf-8", newline="\n")
    _record(tmp_path, {"event": "change", "path": "src/App.tsx", "kind": "code"})
    _verify(tmp_path)
    code, payload = _run_design(tmp_path)
    assert code == 0 and payload["passed"] is True

    # When: the turn removes that allowlist and records the policy change.
    config.write_text('{"enabled": true}\n', encoding="utf-8", newline="\n")
    _record(tmp_path, {"event": "change", "path": "design/gate.config", "kind": "code"})
    _verify(tmp_path)
    decision = evaluate_stop({"project_root": str(tmp_path)})

    # Then: the old PASS is stale even though the UI file itself did not change again.
    assert decision["decision"] == "block"
    assert decision["reason_code"] == "stop_design_lint_missing"


def test_design_cli_records_each_same_agent_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: one agent owns two active UI sessions in the same project.
    monkeypatch.setenv("FABLE_LITE_DESIGN_GATE", "1")
    for session, path in (("one", "src/App.tsx"), ("two", "src/Panel.tsx")):
        common = {"agent": "codex", "host": "codex_cli", "session_id": session}
        _ = record_event(
            {
                "project_root": str(tmp_path),
                **common,
                "event": "prompt",
                "task_mode": "normal",
                "prompt": f"{path} UI 화면을 수정해줘",
            }
        )
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('export const ink = "var(--ink)";\n', encoding="utf-8", newline="\n")
        _ = record_event(
            {
                "project_root": str(tmp_path),
                **common,
                "event": "change",
                "path": path,
                "kind": "code",
            }
        )

    # When: the agent-scoped manual design check runs once.
    code, payload = _run_design(tmp_path, "codex")
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: both exact session turns receive their own fresh result.
    turns = ledger["active_turns"]
    assert code == 0 and payload["passed"] is True
    assert isinstance(turns, dict)
    assert all(
        isinstance(turn, dict)
        and isinstance(turn.get("design_check_seq"), int)
        and isinstance(turn.get("design_last_change_seq"), int)
        and turn["design_check_seq"] > turn["design_last_change_seq"]
        for turn in turns.values()
    )


def test_design_cli_reuses_result_with_v1_ledger_projection(tmp_path: Path) -> None:
    # Given: a compatible v1 ledger already represents a touched design-required turn.
    target = tmp_path / "src" / "App.tsx"
    target.parent.mkdir(parents=True)
    target.write_text('export const ink = "var(--ink)";\n', encoding="utf-8", newline="\n")
    ledger = default_ledger()
    ledger.update(
        {
            "event_seq": 2,
            "changed_files_seen": ["src/App.tsx"],
            "design_required": True,
            "design_touched": True,
            "design_blocks": 0,
            "design_last_change_seq": 2,
            "design_check_passed": False,
            "design_check_seq": 0,
            "design_violations": [],
            "design_baseline_revision": "HEAD",
            "design_dirty_baseline": {},
        }
    )
    assert save_ledger({"project_root": str(tmp_path)}, ledger) is True

    # When: manual design check runs against the v1 project state.
    code, payload = _run_design(tmp_path)
    reloaded = load_ledger({"project_root": str(tmp_path)})

    # Then: the projection itself stores a fresh reusable PASS.
    assert code == 0 and payload["passed"] is True
    assert reloaded["design_check_passed"] is True
    assert reloaded["design_check_seq"] > reloaded["design_last_change_seq"]

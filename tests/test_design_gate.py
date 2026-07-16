from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.classify import classify_prompt
from core.ledger import load_ledger, record_event
from core.verify_state import evaluate_stop, evaluate_without_io


DESIGN_GATE_ENV = "FABLE_LITE_DESIGN_GATE"


def _write_gate_config(root: Path, payload: dict[str, bool]) -> None:
    config = root / "design" / "gate.config"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(payload), encoding="utf-8", newline="\n")


def _classify_ui(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.chdir(root)
    return classify_prompt({"prompt": "src/App.tsx UI 화면을 수정해줘"})


def test_design_gate_is_off_without_explicit_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: neither the environment nor the project config opts in.
    monkeypatch.delenv(DESIGN_GATE_ENV, raising=False)

    # When: an otherwise UI-shaped prompt is classified.
    result = _classify_ui(tmp_path, monkeypatch)

    # Then: the domain is observable, but the design gate remains inactive.
    assert result["domain"] == "UI"
    assert result["design_required"] is False
    assert "design-review" not in result["packs"]


def test_design_gate_env_opt_in_enables_ui_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the global opt-in is enabled and no project config exists.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")

    # When: a UI prompt is classified.
    result = _classify_ui(tmp_path, monkeypatch)

    # Then: the existing classifier activates the design domain and pack.
    assert result["domain"] == "UI"
    assert result["design_required"] is True
    assert "design-review" in result["packs"]


def test_project_config_true_overrides_disabled_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the environment is off but the project explicitly opts in.
    monkeypatch.setenv(DESIGN_GATE_ENV, "0")
    _write_gate_config(tmp_path, {"enabled": True})

    # When: a UI prompt is classified.
    result = _classify_ui(tmp_path, monkeypatch)

    # Then: the project setting wins.
    assert result["design_required"] is True
    assert "design-review" in result["packs"]


def test_project_config_false_overrides_enabled_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the environment is on but the project explicitly opts out.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")
    _write_gate_config(tmp_path, {"enabled": False})

    # When: a UI prompt is classified.
    result = _classify_ui(tmp_path, monkeypatch)

    # Then: the project setting wins and injects nothing.
    assert result["design_required"] is False
    assert "design-review" not in result["packs"]


def test_config_without_enabled_field_does_not_activate_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a project config exists without the required explicit field.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")
    _write_gate_config(tmp_path, {})

    # When: a UI prompt is classified.
    result = _classify_ui(tmp_path, monkeypatch)

    # Then: config presence alone is not activation and overrides the env.
    assert result["design_required"] is False
    assert "design-review" not in result["packs"]


def test_enabled_gate_does_not_require_design_for_non_ui_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the gate is enabled for a non-UI coding request.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")
    monkeypatch.chdir(tmp_path)

    # When: the existing classifier handles the request.
    result = classify_prompt({"prompt": "core/ledger.py 로직을 수정해줘"})

    # Then: it reuses classification without widening design scope.
    assert result["domain"] != "UI"
    assert result["design_required"] is False
    assert "design-review" not in result["packs"]


def _record_ui_prompt(root: Path) -> None:
    _ = record_event(
        {
            "project_root": str(root),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "src/App.tsx UI 화면을 수정해줘",
        }
    )


def _record_ui_change(root: Path, path: str = "src/App.tsx") -> None:
    _ = record_event(
        {
            "project_root": str(root),
            "event": "change",
            "path": path,
            "kind": "code",
        }
    )


def _record_general_verification(root: Path) -> None:
    _ = record_event(
        {
            "project_root": str(root),
            "event": "verification",
            "command": "python -m pytest tests/test_ui.py -q",
            "success": True,
            "evidence": "1 passed",
        }
    )


def _record_design_check(root: Path, *, passed: bool) -> None:
    violations: list[dict[str, object]] = []
    if not passed:
        violations.append(
            {
                "file": "src/App.tsx",
                "line": 1,
                "rule_id": "design/raw-color",
                "message": "raw color",
            }
        )
    _ = record_event(
        {
            "project_root": str(root),
            "event": "design_check",
            "passed": passed,
            "violations": violations,
        }
    )


def test_enabled_ui_turn_records_required_and_touched_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a project opts in to a UI task.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")
    _record_ui_prompt(tmp_path)

    # When: a shared change event records a UI file mutation.
    _record_ui_change(tmp_path)
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: the common turn projection carries design state and its independent counter.
    assert ledger["design_required"] is True
    assert ledger["design_touched"] is True
    assert isinstance(ledger["design_last_change_seq"], int)
    assert ledger["design_last_change_seq"] > 0
    assert ledger["design_blocks"] == 0


def test_enabled_ui_turn_ignores_non_ui_file_for_design_touch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: design is required for the current prompt.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")
    _record_ui_prompt(tmp_path)

    # When: the turn changes only a backend Python file.
    _record_ui_change(tmp_path, "core/ledger.py")
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: design remains required but untouched, so Stop has no design reason to block.
    assert ledger["design_required"] is True
    assert ledger["design_touched"] is False


def test_fresh_design_result_is_invalidated_by_later_ui_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a UI change has a passing design check and ordinary verification.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")
    _record_ui_prompt(tmp_path)
    _record_ui_change(tmp_path)
    _record_design_check(tmp_path, passed=True)
    _record_general_verification(tmp_path)
    before = load_ledger({"project_root": str(tmp_path)})
    assert evaluate_without_io(before, {"project_root": str(tmp_path)})["decision"] == "allow"

    # When: the same turn records a later UI mutation and re-verifies only ordinary behavior.
    _record_ui_change(tmp_path, "src/Panel.tsx")
    _record_general_verification(tmp_path)
    decision = evaluate_stop({"project_root": str(tmp_path)})

    # Then: the stale design result is not reused.
    assert decision["decision"] == "block"
    assert decision["reason_code"] == "stop_design_lint_missing"


def test_design_stop_blocks_twice_then_fail_opens_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: ordinary verification is fresh, but design lint has not passed.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")
    _record_ui_prompt(tmp_path)
    _record_ui_change(tmp_path)
    _record_general_verification(tmp_path)

    # When: Stop evaluates the unchanged design failure three times.
    decisions = [
        evaluate_stop({"project_root": str(tmp_path)}),
        evaluate_stop({"project_root": str(tmp_path)}),
        evaluate_stop({"project_root": str(tmp_path)}),
    ]
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: only the design counter reaches its two-block cap.
    assert [decision["decision"] for decision in decisions] == ["block", "block", "allow"]
    assert ledger["design_blocks"] == 2
    assert ledger["stop_blocks"] == 0


def test_fresh_passing_design_result_allows_design_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: both the design result and ordinary verification follow the UI mutation.
    monkeypatch.setenv(DESIGN_GATE_ENV, "1")
    _record_ui_prompt(tmp_path)
    _record_ui_change(tmp_path)
    _record_design_check(tmp_path, passed=True)
    _record_general_verification(tmp_path)

    # When: Stop evaluates the turn.
    decision = evaluate_stop({"project_root": str(tmp_path)})
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: design consumes no block and the existing Stop gate allows.
    assert decision["decision"] == "allow"
    assert ledger["design_blocks"] == 0
    assert ledger["stop_blocks"] == 0


def test_toggle_off_preserves_ledger_shape_and_existing_stop_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a UI-shaped turn runs without either opt-in mechanism.
    monkeypatch.delenv(DESIGN_GATE_ENV, raising=False)
    _record_ui_prompt(tmp_path)
    _record_ui_change(tmp_path)
    _record_general_verification(tmp_path)

    # When: the existing Stop gate evaluates the verified turn.
    decision = evaluate_stop({"project_root": str(tmp_path)})
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: no design state is introduced and the prior decision remains unchanged.
    assert decision == {"decision": "allow", "message": "[smtw] Stop gate allow."}
    assert all(not key.startswith("design_") for key in ledger)

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from core import contract
from core.ledger import JsonObject, load_ledger, record_event, save_ledger
from core.ledger_storage import ledger_path
from core.scorecard_store import cached_session_summary
from core.verify_state import evaluate_stop


def _identity(root: Path, suffix: str = "review") -> JsonObject:
    return {
        "project_root": str(root),
        "host": "codex_cli",
        "session_id": f"{suffix}-session",
        "agent": "codex",
        "turn_id": f"{suffix}-turn",
    }


def _seed_stop(root: Path, suffix: str = "review") -> JsonObject:
    payload = _identity(root, suffix)
    _ = record_event(
        payload
        | {
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "review regression",
        }
    )
    _ = record_event(
        payload | {"event": "change", "path": "app.py", "kind": "code"}
    )
    return payload


def test_invalid_derived_cache_preserves_authoritative_stop_decision(
    tmp_path: Path,
) -> None:
    control = _seed_stop(tmp_path / "control")
    fault = _seed_stop(tmp_path / "fault")
    expected = evaluate_stop(control)
    destination = ledger_path(str(tmp_path / "fault"))
    raw = json.loads(destination.read_text(encoding="utf-8"))
    raw["scorecard_cache"] = []
    raw["scorecard_journal_offset"] = "invalid"
    raw["scorecard_evicted_keys"] = "invalid"
    destination.write_text(json.dumps(raw), encoding="utf-8")

    sanitized = load_ledger(fault)
    assert "scorecard_cache" not in sanitized
    assert "scorecard_journal_offset" not in sanitized
    assert "scorecard_evicted_keys" not in sanitized
    destination.write_text(json.dumps(raw), encoding="utf-8")
    assert evaluate_stop(fault) == expected


def test_scorecard_recovery_lock_failure_preserves_low_risk_allow(
    tmp_path: Path,
) -> None:
    payload = _identity(tmp_path) | {
        "tool_name": "Edit",
        "file_paths": ["app.py"],
    }
    expected = contract.evaluate_pretool_contract(payload)

    with patch.object(
        contract,
        "recover_checkpoint_gates",
        side_effect=PermissionError("scorecard lock denied"),
    ):
        assert contract.evaluate_pretool_contract(payload) == expected


def test_r1_scorecard_lock_timeout_preserves_original_block(tmp_path: Path) -> None:
    payload = _identity(tmp_path) | {
        "tool_name": "Edit",
        "file_paths": ["migrations/001_init.sql"],
        "prompt": "DB migration change",
    }
    expected = contract.evaluate_r1_contract(payload)

    @contextmanager
    def timeout_transaction(_root: str) -> Iterator[None]:
        raise TimeoutError("scorecard lock timeout")
        yield

    with patch.object(contract, "ledger_transaction", timeout_transaction):
        assert contract.evaluate_pretool_contract(payload) == expected


def test_cache_key_identity_mismatch_is_discarded(tmp_path: Path) -> None:
    payload = _seed_stop(tmp_path)
    assert evaluate_stop(payload)["decision"] == "block"
    destination = ledger_path(str(tmp_path))
    raw = json.loads(destination.read_text(encoding="utf-8"))
    key = "codex_cli:review-session:codex"
    raw["scorecard_cache"][key]["session_id"] = "different-session"
    destination.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_ledger(payload)
    assert "scorecard_cache" not in loaded
    assert cached_session_summary(loaded, payload) is None


def test_lost_cache_commit_invalidates_next_transition_summary(
    tmp_path: Path,
) -> None:
    payload = _seed_stop(tmp_path)
    assert evaluate_stop(payload)["decision"] == "block"
    with patch("core.verify_state.save_ledger", return_value=None):
        assert evaluate_stop(payload)["decision"] == "block"
    assert evaluate_stop(payload)["decision"] == "allow"

    assert cached_session_summary(load_ledger(payload), payload) is None


def test_lost_cache_commit_omits_scorecard_on_next_routine_allow(
    tmp_path: Path,
) -> None:
    payload = _seed_stop(tmp_path)
    assert evaluate_stop(payload)["decision"] == "block"
    with patch("core.verify_state.save_ledger", return_value=False):
        assert evaluate_stop(payload)["decision"] == "block"
    quick = payload | {"turn_id": "quick-allow-turn"}
    _ = record_event(
        quick | {"event": "prompt", "task_mode": "quick", "prompt": "status"}
    )

    decision = evaluate_stop(quick)

    assert decision["decision"] == "allow"
    assert "이번 세션" not in str(decision.get("message", ""))


def test_deleted_cache_for_observed_turn_does_not_claim_partial_truth(
    tmp_path: Path,
) -> None:
    payload = _seed_stop(tmp_path)
    assert evaluate_stop(payload)["decision"] == "block"
    ledger = load_ledger(payload)
    _ = ledger.pop("scorecard_cache", None)
    save_ledger(payload, ledger)

    assert evaluate_stop(payload)["decision"] == "block"
    assert cached_session_summary(load_ledger(payload), payload) is None

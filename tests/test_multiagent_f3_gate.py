from __future__ import annotations

from pathlib import Path

from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import JsonObject, JsonValue, record_event
from core.verify_state import evaluate_stop


def _identity(root: Path, agent: str) -> JsonObject:
    return {
        "project_root": str(root),
        "host": "host",
        "session_id": f"session-{agent}",
        "agent": agent,
        "turn_id": f"turn-{agent}",
        "attribution": "exact",
    }


def test_r2_uses_only_open_leased_peer_invocation_candidates(tmp_path: Path) -> None:
    # Given: beta has an open candidate that blocks alpha before path attribution exists.
    beta = _identity(tmp_path, "beta")
    _ = record_event(
        beta
        | {
            "event": "prompt",
            "prompt": "beta work",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        }
    )
    _ = record_event(
        beta
        | {
            "event": "invocation",
            "invocation_id": "beta-edit",
            "candidate_paths": ["peer.py"],
        }
    )
    alpha: dict[str, JsonValue] = _identity(tmp_path, "alpha") | {
        "tool_name": "Bash",
        "command": "rm peer.py",
    }
    def health(ledger: JsonObject) -> JsonObject:
        return {"degraded": False, "capacity_exceeded": False}

    def lookup(ledger: JsonObject, path: str) -> JsonObject | None:
        return None

    # When: R2 evaluates before and after beta's next event closes the invocation.
    opened = evaluate_r2_destructive_gate(
        alpha,
        lookup_path_attribution=lookup,
        attribution_health=health,
    )
    _ = record_event(
        beta
        | {
            "event": "observation",
            "invocation_id": "beta-edit",
            "provenance_incomplete": False,
            "provenance_status": "complete",
            "provenance_status_reason": "",
        }
    )
    closed = evaluate_r2_destructive_gate(
        alpha,
        lookup_path_attribution=lookup,
        attribution_health=health,
    )

    # Then: the pre-attribution block ends with the actual window instead of persisting forever.
    assert opened["decision"] == "block"
    assert closed["decision"] == "allow"


def test_mutation_capable_missing_baseline_keeps_two_block_cap(tmp_path: Path) -> None:
    # Given: alpha has an explicit degraded turn and later attempts a local mutation.
    alpha = _identity(tmp_path, "alpha")
    _ = record_event(
        alpha
        | {
            "event": "prompt",
            "prompt": "alpha work",
            "baseline_snapshot_id": "snapshot:unavailable",
            "current_snapshot_id": "snapshot:unavailable",
            "provenance_incomplete": True,
            "provenance_status": "incomplete",
            "provenance_status_reason": "observation_error",
        }
    )
    _ = record_event(
        alpha
        | {
            "event": "invocation",
            "invocation_id": "alpha-edit",
            "candidate_paths": ["app.py"],
            "provenance_mutation_capable": True,
        }
    )

    # When: the mutation-capable degraded turn reaches Stop three times.
    decisions = [evaluate_stop(alpha)["decision"] for _ in range(3)]

    # Then: the existing conservative two-block cap remains unchanged.
    assert decisions == ["block", "block", "allow"]

from __future__ import annotations

from pathlib import Path

from core.ledger import JsonObject, JsonValue, capture_verification_covers, record_event
from core.ledger_v2 import apply_v2_event, default_v2_ledger
from core.verification_covers import active_turn, pending_revisions
from core.verify_state import evaluate_without_io, has_successful_verification


def _identity(root: Path, agent: str, event: str) -> dict[str, JsonValue]:
    return {
        "project_root": str(root),
        "event": event,
        "host": "host",
        "session_id": f"session-{agent}",
        "agent": agent,
        "turn_id": f"turn-{agent}",
        "attribution": "exact",
    }


def _prompt(root: Path, agent: str) -> JsonObject:
    return record_event(
        _identity(root, agent, "prompt")
        | {
            "prompt": f"{agent} work",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        }
    )


def _change(
    root: Path,
    agent: str,
    *,
    event_id: str,
    generation: int,
    before: str,
    after: str,
    owner: str | None,
    path: str = "app.py",
) -> JsonObject:
    return record_event(
        _identity(root, agent, "change")
        | {
            "event_id": event_id,
            "manifest_generation": generation,
            "commit_state": "committed",
            "owner": owner,
            "attribution_status": "exclusive",
            "observed_by": [agent],
            "invocation_id": f"inv-{event_id}",
            "current_snapshot_id": f"snapshot:{event_id}",
            "paths": [
                {
                    "change_id": f"change:{event_id}",
                    "path": path,
                    "kind": "code",
                    "before": before,
                    "after": after,
                    "requires_verification": True,
                }
            ],
        }
    )


def _verify(root: Path, agent: str) -> JsonObject:
    payload = _identity(root, agent, "verification")
    covers = capture_verification_covers(payload)
    return record_event(
        payload
        | {
            "invocation_id": f"verify-{agent}",
            "command": "python -m pytest tests/test_multiagent_f2.py -q",
            "success": True,
            "evidence": "passed",
            "covers": covers,
        }
    )


def test_late_peer_revision_does_not_invalidate_covered_self_revision(tmp_path: Path) -> None:
    # Given: alpha has a covered self revision and its Stop decision is already allow.
    _ = _prompt(tmp_path, "alpha")
    _ = _change(
        tmp_path,
        "alpha",
        event_id="alpha-self",
        generation=1,
        before="digest:base",
        after="digest:alpha",
        owner="alpha",
    )
    verified = _verify(tmp_path, "alpha")
    alpha_payload = _identity(tmp_path, "alpha", "stop")
    assert evaluate_without_io(verified, alpha_payload)["decision"] == "allow"

    # When: beta commits a newer self change and alpha later observes that peer digest.
    _ = _prompt(tmp_path, "beta")
    _ = _change(
        tmp_path,
        "beta",
        event_id="beta-self",
        generation=2,
        before="digest:alpha",
        after="digest:beta",
        owner="beta",
        path="peer.py",
    )
    ledger = _change(
        tmp_path,
        "alpha",
        event_id="alpha-observes-beta",
        generation=2,
        before="digest:alpha",
        after="digest:beta",
        owner=None,
        path="peer.py",
    )

    # Then: the peer revision is filtered at pending/covers comparison without re-verifying alpha.
    turn = active_turn(ledger, alpha_payload)
    assert turn is not None
    revisions = turn["path_revisions"]
    assert isinstance(revisions, dict)
    revision = revisions["peer.py"]
    assert isinstance(revision, dict)
    assert revision["attribution"] == "peer"
    pending = pending_revisions(turn)
    assert [item["path"] for item in pending] == ["app.py"]
    assert has_successful_verification(ledger, alpha_payload) is True
    assert evaluate_without_io(ledger, alpha_payload)["decision"] == "allow"


def test_ledger_only_peer_attribution_does_not_exempt_revision(tmp_path: Path) -> None:
    # Given: an in-memory ledger contains peer attribution but no peer agent JSONL evidence.
    ledger = default_v2_ledger()
    events = (
        _identity(tmp_path, "alpha", "prompt")
        | {
            "seq": 1,
            "prompt": "alpha",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        },
        _identity(tmp_path, "beta", "prompt")
        | {
            "seq": 2,
            "prompt": "beta",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        },
        _identity(tmp_path, "beta", "change")
        | {
            "seq": 3,
            "event_id": "beta-only-ledger",
            "manifest_generation": 1,
            "commit_state": "committed",
            "owner": "beta",
            "attribution_status": "exclusive",
            "invocation_id": "inv-beta",
            "paths": [
                {
                    "change_id": "change:beta",
                    "path": "app.py",
                    "kind": "code",
                    "before": "digest:base",
                    "after": "digest:beta",
                    "requires_verification": True,
                }
            ],
        },
        _identity(tmp_path, "alpha", "change")
        | {
            "seq": 4,
            "event_id": "alpha-observation",
            "manifest_generation": 1,
            "commit_state": "committed",
            "owner": None,
            "attribution_status": "exclusive",
            "invocation_id": "inv-alpha",
            "paths": [
                {
                    "change_id": "change:observed",
                    "path": "app.py",
                    "kind": "code",
                    "before": "digest:base",
                    "after": "digest:beta",
                    "requires_verification": True,
                }
            ],
        },
    )

    # When: the events are reduced without append-only peer audit evidence.
    for event in events:
        _ = apply_v2_event(ledger, event)

    # Then: the apparent peer revision degrades to external and remains verification-required.
    turn = active_turn(ledger, _identity(tmp_path, "alpha", "stop"))
    assert turn is not None
    revisions = pending_revisions(turn)
    assert len(revisions) == 1
    assert revisions[0]["attribution"] == "external"


def test_peer_filter_does_not_satisfy_remote_mutation_epoch(tmp_path: Path) -> None:
    # Given: alpha has no pending local revisions but does have an uncovered remote epoch.
    _ = _prompt(tmp_path, "alpha")
    ledger = record_event(
        _identity(tmp_path, "alpha", "invocation")
        | {
            "invocation_id": "remote-deploy",
            "candidate_paths": [],
            "provenance_remote_mutation": True,
            "remote_target_ids": ["ssh://host"],
        }
    )

    # When: verification state is evaluated with the peer filter active.
    payload = _identity(tmp_path, "alpha", "stop")

    # Then: remote epochs remain an independent verification requirement.
    assert has_successful_verification(ledger, payload) is False
    assert evaluate_without_io(ledger, payload)["decision"] == "block"

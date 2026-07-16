from __future__ import annotations

from pathlib import Path

from core.ledger import JsonObject, JsonValue, record_event
from core.verification_covers import active_turn, pending_revisions


def _identity(
    root: Path,
    agent: str,
    session_id: str,
    event: str,
) -> dict[str, JsonValue]:
    return {
        "project_root": str(root),
        "event": event,
        "host": "host",
        "session_id": session_id,
        "agent": agent,
        "turn_id": f"turn-{session_id}",
        "attribution": "exact",
    }


def _prompt(root: Path, agent: str, session_id: str) -> JsonObject:
    return record_event(
        _identity(root, agent, session_id, "prompt")
        | {
            "prompt": f"{session_id} work",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        }
    )


def _change(
    root: Path,
    agent: str,
    session_id: str,
    *,
    event_id: str,
    generation: int,
    before: str,
    after: str,
    owner: str | None,
) -> JsonObject:
    return record_event(
        _identity(root, agent, session_id, "change")
        | {
            "event_id": event_id,
            "manifest_generation": generation,
            "commit_state": "committed",
            "owner": owner,
            "attribution_status": "exclusive",
            "invocation_id": f"inv-{event_id}",
            "paths": [
                {
                    "change_id": f"change:{event_id}",
                    "path": "app.py",
                    "kind": "code",
                    "before": before,
                    "after": after,
                    "requires_verification": True,
                }
            ],
        }
    )


def test_traversal_agent_key_cannot_gain_peer_exemption(tmp_path: Path) -> None:
    # Given: a traversal-shaped peer identity has a matching sanitized audit log.
    malicious_agent = "../../../../tmp/hacker"
    _ = _prompt(tmp_path, malicious_agent, "malicious")
    _ = _change(
        tmp_path,
        malicious_agent,
        "malicious",
        event_id="malicious-owner",
        generation=1,
        before="digest:base",
        after="digest:malicious",
        owner=malicious_agent,
    )
    _ = _prompt(tmp_path, "observer", "observer")

    # When: the observer sees the malicious owner's digest.
    ledger = _change(
        tmp_path,
        "observer",
        "observer",
        event_id="observer-sees-malicious",
        generation=1,
        before="digest:base",
        after="digest:malicious",
        owner=None,
    )

    # Then: unsafe identity normalization cannot satisfy dual evidence.
    turn = active_turn(
        ledger,
        _identity(tmp_path, "observer", "observer", "stop"),
    )
    assert turn is not None
    revisions = pending_revisions(turn)
    assert len(revisions) == 1
    assert revisions[0]["attribution"] == "external"


def test_earlier_owner_keeps_its_manifest_generation_for_peer_exemption(
    tmp_path: Path,
) -> None:
    # Given: a later owner advances the entry generation after an earlier committed owner.
    _ = _prompt(tmp_path, "first", "first")
    _ = _change(
        tmp_path,
        "first",
        "first",
        event_id="first-owner",
        generation=10,
        before="digest:base",
        after="digest:first",
        owner="first",
    )
    _ = _prompt(tmp_path, "second", "second")
    _ = _change(
        tmp_path,
        "second",
        "second",
        event_id="second-owner",
        generation=20,
        before="digest:first",
        after="digest:second",
        owner="second",
    )
    _ = _prompt(tmp_path, "observer", "observer")

    # When: the observer later sees the earlier owner's exact digest.
    ledger = _change(
        tmp_path,
        "observer",
        "observer",
        event_id="observer-sees-first",
        generation=20,
        before="digest:second",
        after="digest:first",
        owner=None,
    )

    # Then: the earlier owner's own generation validates its committed audit evidence.
    turn = active_turn(
        ledger,
        _identity(tmp_path, "observer", "observer", "stop"),
    )
    assert turn is not None
    revisions = turn.get("path_revisions")
    assert isinstance(revisions, dict)
    revision = revisions.get("app.py")
    assert isinstance(revision, dict)
    assert revision["attribution"] == "peer"
    assert pending_revisions(turn) == []

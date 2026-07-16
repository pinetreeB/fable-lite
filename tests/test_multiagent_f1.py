from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
from pathlib import Path

import pytest

from core.ledger import load_ledger, save_ledger
from core.ledger_schema import JsonObject, LedgerSchemaError, validate_v2_ledger
from core.ledger_v2 import (
    apply_v2_event,
    attribution_health,
    default_v2_ledger,
    lookup_path_attribution,
)


@dataclass(frozen=True, slots=True)
class ChangeSpec:
    agent: str
    seq: int
    generation: int
    path: str
    digest: str
    attribution_status: str = "exclusive"


@dataclass(frozen=True, slots=True)
class VerificationSpec:
    agent: str
    seq: int
    path: str
    digest: str
    through_seq: int


def _change(spec: ChangeSpec, commit_state: str = "committed") -> JsonObject:
    return {
        "schema_version": 2,
        "event": "change",
        "event_id": f"change-{spec.agent}-{spec.seq}",
        "seq": spec.seq,
        "manifest_generation": spec.generation,
        "commit_state": commit_state,
        "host": "codex_cli",
        "session_id": f"session-{spec.agent}",
        "turn_id": f"turn-{spec.agent}",
        "agent": spec.agent,
        "source": "edit",
        "owner": spec.agent,
        "attribution_status": spec.attribution_status,
        "observed_by": [spec.agent],
        "confidence": 1.0,
        "source_confidence": 1.0,
        "invocation_id": f"invocation-{spec.agent}-{spec.seq}",
        "observed_at": "post_tool",
        "snapshot_before": "blake2b-256:before",
        "snapshot_after": spec.digest,
        "current_snapshot_id": spec.digest,
        "paths": [
            {
                "change_id": f"change-id-{spec.agent}-{spec.seq}",
                "path": spec.path,
                "op": "modify",
                "kind": "code",
                "before": "blake2b-256:before",
                "after": spec.digest,
                "requires_verification": True,
            }
        ],
    }


def _verification(spec: VerificationSpec) -> JsonObject:
    return {
        "schema_version": 2,
        "event": "verification",
        "event_id": f"verification-{spec.agent}-{spec.seq}",
        "seq": spec.seq,
        "host": "codex_cli",
        "session_id": f"session-{spec.agent}",
        "turn_id": f"turn-{spec.agent}",
        "agent": spec.agent,
        "invocation_id": f"verification-invocation-{spec.agent}-{spec.seq}",
        "command": "python -m pytest tests/test_multiagent_f1.py -q",
        "success": True,
        "evidence": "1 passed",
        "covers": {
            "through_seq": spec.through_seq,
            "snapshot_id": spec.digest,
            "change_ids": [f"change-id-{spec.agent}-1"],
            "change_event_ids": [f"change-{spec.agent}-1"],
            "path_revisions": [
                {
                    "change_id": f"change-id-{spec.agent}-1",
                    "path": spec.path,
                    "after": spec.digest,
                    "change_event_id": f"change-{spec.agent}-1",
                }
            ],
        },
    }


def _owner(agent: str, revision_seq: int, digest: str) -> JsonObject:
    return {
        "agent_key": f"codex_cli:session-{agent}:{agent}",
        "turn_id": f"turn-{agent}",
        "revision_seq": revision_seq,
        "after_digest": digest,
        "invocation_id": f"invocation-{agent}-{revision_seq}",
        "settled": False,
    }


def _entry(agent: str, revision_seq: int, digest: str) -> JsonObject:
    return {
        "generation": 1,
        "status": "exclusive",
        "owners": [_owner(agent, revision_seq, digest)],
    }


def _owners(entry: JsonObject) -> list[JsonObject]:
    owners = entry["owners"]
    assert isinstance(owners, list)
    assert all(isinstance(owner, dict) for owner in owners)
    return [owner for owner in owners if isinstance(owner, dict)]


def test_lookup_contract_ignores_uncommitted_and_stale_changes() -> None:
    # Given: the frozen query interface and an empty v2 ledger.
    signature = inspect.signature(lookup_path_attribution)
    ledger = default_v2_ledger()
    path = "src/app.py"

    # When: an uncommitted revision, a newer commit, and then a stale commit arrive.
    _ = apply_v2_event(ledger, _change(ChangeSpec("alpha", 1, 1, path, "digest:pending"), "uncommitted"))
    pending = lookup_path_attribution(ledger, path)
    _ = apply_v2_event(ledger, _change(ChangeSpec("alpha", 2, 2, path, "digest:new")))
    _ = apply_v2_event(ledger, _change(ChangeSpec("alpha", 3, 1, path, "digest:stale")))
    current = lookup_path_attribution(ledger, path)

    # Then: the signature is unchanged and only the authoritative commit is indexed.
    assert tuple(signature.parameters) == ("ledger", "canonical_path")
    assert signature.parameters["ledger"].annotation == "dict[str, JsonValue]"
    assert signature.return_annotation == "dict[str, JsonValue] | None"
    assert pending is None
    assert current == {
        "generation": 2,
        "status": "exclusive",
        "owners": [_owner("alpha", 2, "digest:new")],
    }


def test_change_owner_must_match_event_identity() -> None:
    # Given: a committed event whose claimed owner differs from its event identity.
    ledger = default_v2_ledger()
    event = _change(ChangeSpec("observer", 1, 1, "src/peer.py", "digest:peer"))
    event["owner"] = "peer"

    # When: the event reaches the attribution reducer.
    _ = apply_v2_event(ledger, event)

    # Then: the observer cannot acquire ownership for the mismatched claim.
    assert lookup_path_attribution(ledger, "src/peer.py") is None


def test_owner_contention_settles_by_verification() -> None:
    # Given: two agents have the same current digest and therefore unresolved ownership.
    ledger = default_v2_ledger()
    path = "src/shared.py"
    _ = apply_v2_event(ledger, _change(ChangeSpec("alpha", 1, 1, path, "digest:shared")))
    _ = apply_v2_event(ledger, _change(ChangeSpec("beta", 2, 2, path, "digest:shared")))
    contended = lookup_path_attribution(ledger, path)
    assert contended is not None
    assert contended["status"] == "contended"
    assert [owner["settled"] for owner in _owners(contended)] == [False, False]

    # When: alpha verifies its covered revision after the change sequence.
    _ = apply_v2_event(ledger, _verification(VerificationSpec("alpha", 3, path, "digest:shared", 2)))
    verified = lookup_path_attribution(ledger, path)

    # Then: alpha is settled and beta remains the only live owner.
    assert verified is not None
    assert verified["status"] == "exclusive"
    assert {owner["agent_key"]: owner["settled"] for owner in _owners(verified)} == {
        "codex_cli:session-alpha:alpha": True,
        "codex_cli:session-beta:beta": False,
    }


def test_digest_replacement_settles_prior_owners() -> None:
    # Given: two unresolved owners point at the same current digest.
    ledger = default_v2_ledger()
    path = "src/shared.py"
    _ = apply_v2_event(ledger, _change(ChangeSpec("alpha", 1, 1, path, "digest:shared")))
    _ = apply_v2_event(ledger, _change(ChangeSpec("beta", 2, 2, path, "digest:shared")))

    # When: gamma records a newer revision with the observed replacement digest.
    _ = apply_v2_event(ledger, _change(ChangeSpec("gamma", 3, 3, path, "digest:replacement")))
    replaced = lookup_path_attribution(ledger, path)

    # Then: both prior digests are settled and gamma is the only live owner.
    assert replaced is not None
    assert replaced["status"] == "exclusive"
    assert {owner["agent_key"]: owner["settled"] for owner in _owners(replaced)} == {
        "codex_cli:session-alpha:alpha": True,
        "codex_cli:session-beta:beta": True,
        "codex_cli:session-gamma:gamma": False,
    }


def test_ninth_unsettled_owner_marks_path_contended_without_eviction() -> None:
    # Given: one path receives eight unresolved owner revisions.
    ledger = default_v2_ledger()
    path = "src/hot.py"
    for index in range(8):
        agent = f"agent-{index}"
        _ = apply_v2_event(
            ledger,
            _change(ChangeSpec(agent, index + 1, index + 1, path, "digest:shared")),
        )

    # When: a ninth unresolved owner touches the same revision.
    _ = apply_v2_event(ledger, _change(ChangeSpec("agent-8", 9, 9, path, "digest:shared")))
    entry = lookup_path_attribution(ledger, path)

    # Then: no owner is evicted and overflow is explicit and conservative.
    assert entry is not None
    assert len(_owners(entry)) == 8
    assert entry["status"] == "contended"
    assert entry["overflow"] is True
    assert all(owner["agent_key"] != "codex_cli:session-agent-8:agent-8" for owner in _owners(entry))


def test_attribution_capacity_exceeded_preserves_all_existing_live_paths() -> None:
    # Given: the live attribution index is exactly at its 10,000-path cap.
    ledger = default_v2_ledger()
    ledger["path_attribution"] = {
        f"src/existing-{index}.py": _entry(f"agent-{index}", index + 1, f"digest:{index}")
        for index in range(10_000)
    }

    # When: a new live path would exceed the cap.
    _ = apply_v2_event(
        ledger,
        _change(ChangeSpec("overflow", 10_001, 2, "src/overflow.py", "digest:overflow")),
    )
    health = attribution_health(ledger)
    index = ledger["path_attribution"]

    # Then: no LRU eviction occurs and the conservative capacity flag is durable state.
    assert isinstance(index, dict)
    assert len(index) == 10_000
    assert "src/existing-0.py" in index
    assert "src/overflow.py" not in index
    assert health == {"degraded": False, "capacity_exceeded": True}


def test_settled_path_ages_before_capacity_rejection() -> None:
    # Given: the 10,000-entry index has one settled-only path and 9,999 live paths.
    ledger = default_v2_ledger()
    index = {
        f"src/existing-{item}.py": _entry(f"agent-{item}", item + 1, f"digest:{item}")
        for item in range(10_000)
    }
    settled = index["src/existing-0.py"]["owners"]
    assert isinstance(settled, list)
    assert isinstance(settled[0], dict)
    settled[0]["settled"] = True
    ledger["path_attribution"] = index

    # When: one new live path enters at the memory boundary.
    _ = apply_v2_event(
        ledger,
        _change(ChangeSpec("new-owner", 10_001, 2, "src/new.py", "digest:new")),
    )

    # Then: only the settled path ages out and the conservative capacity flag stays clear.
    assert len(index) == 10_000
    assert "src/existing-0.py" not in index
    assert "src/new.py" in index
    assert attribution_health(ledger)["capacity_exceeded"] is False


def test_path_attribution_schema_rejects_malformed_owner_and_overflow() -> None:
    # Given: one valid attribution entry and two malformed variants.
    valid = default_v2_ledger()
    valid["path_attribution"] = {"src/app.py": _entry("alpha", 1, "digest:one")}
    missing_owner_field = json.loads(json.dumps(valid))
    invalid_overflow = json.loads(json.dumps(valid))
    del missing_owner_field["path_attribution"]["src/app.py"]["owners"][0]["invocation_id"]
    invalid_overflow["path_attribution"]["src/app.py"]["overflow"] = True

    # When/Then: the valid shape passes and malformed ownership is rejected at the boundary.
    assert validate_v2_ledger(valid) is valid
    with pytest.raises(LedgerSchemaError, match="invocation_id"):
        _ = validate_v2_ledger(missing_owner_field)
    with pytest.raises(LedgerSchemaError, match="status"):
        _ = validate_v2_ledger(invalid_overflow)


def test_corrupt_backup_keeps_attribution_degraded_across_valid_reloads(tmp_path: Path) -> None:
    # Given: loading a corrupt ledger creates the durable backup marker.
    state = tmp_path / ".fable-lite"
    state.mkdir()
    ledger_path = state / "ledger.json"
    ledger_path.write_text("{broken", encoding="utf-8")
    first = load_ledger({"project_root": str(tmp_path)})
    backups = list(state.glob("*.corrupt-*.bak"))

    # When: a valid v2 ledger replaces the corrupt source and is loaded repeatedly.
    assert save_ledger({"project_root": str(tmp_path)}, default_v2_ledger()) is True
    second = load_ledger({"project_root": str(tmp_path)})
    third = load_ledger({"project_root": str(tmp_path)})

    # Then: the unresolved on-disk marker keeps every health read degraded.
    assert len(backups) == 1
    assert attribution_health(first)["degraded"] is True
    assert attribution_health(second)["degraded"] is True
    assert attribution_health(third) == {"degraded": True, "capacity_exceeded": False}

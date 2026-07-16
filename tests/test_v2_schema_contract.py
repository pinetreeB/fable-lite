from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable
from unittest.mock import patch
from typing import TypeAlias

import core.ledger as ledger_module
from core.ledger import (
    JsonObject,
    JsonValue,
    load_ledger,
    save_ledger,
)
from core.ledger_event_schema import (
    deserialize_v2_event,
    serialize_v2_event,
    validate_v2_event,
)
from core.ledger_schema import (
    LedgerSchemaError,
    deserialize_v2_ledger,
    serialize_v2_ledger,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "v2-provenance"


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _json_fixture(name: str) -> JsonObject:
    value: JsonValue = json.loads(_fixture(name))
    assert isinstance(value, dict)
    return value


def _copy_json(value: JsonObject) -> JsonObject:
    copied: JsonValue = json.loads(json.dumps(value, ensure_ascii=False))
    assert isinstance(copied, dict)
    return copied


def _paths(event: JsonObject) -> list[JsonObject]:
    value = event["paths"]
    assert isinstance(value, list)
    assert all(isinstance(path, dict) for path in value)
    return [path for path in value if isinstance(path, dict)]


def _covers(event: JsonObject) -> JsonObject:
    value = event["covers"]
    assert isinstance(value, dict)
    return value


def _revisions(covers: JsonObject) -> list[JsonObject]:
    value = covers["path_revisions"]
    assert isinstance(value, list)
    assert all(isinstance(revision, dict) for revision in value)
    return [revision for revision in value if isinstance(revision, dict)]


Mutation: TypeAlias = Callable[[JsonObject], None]


def _set_high_confidence(event: JsonObject) -> None:
    event["confidence"] = 1.01


def _set_negative_confidence(event: JsonObject) -> None:
    event["confidence"] = -0.01


def _drop_manifest_generation(event: JsonObject) -> None:
    _ = event.pop("manifest_generation")


def _set_boolean_manifest_generation(event: JsonObject) -> None:
    event["manifest_generation"] = True


def _drop_commit_state(event: JsonObject) -> None:
    _ = event.pop("commit_state")


def _set_invalid_commit_state(event: JsonObject) -> None:
    event["commit_state"] = "pending"


def _drop_path_before(event: JsonObject) -> None:
    _ = _paths(event)[0].pop("before")


def _drop_path_verification_requirement(event: JsonObject) -> None:
    _ = _paths(event)[0].pop("requires_verification")


def _set_covers_revisions_to_object(event: JsonObject) -> None:
    _covers(event)["path_revisions"] = {}


def _set_unknown_revision_change_id(event: JsonObject) -> None:
    _revisions(_covers(event))[0]["change_id"] = "blake2b-256:unknown"


def test_v2_golden_json_round_trips_change_verification_and_ledger() -> None:
    # Given: rev2 change, verification, and ledger fixtures.
    change = _json_fixture("change-event.json")
    verification = _json_fixture("verification-event.json")
    ledger_text = _fixture("ledger.json")

    # When: each fixture is parsed through the W1 schema boundary and serialized.
    restored_change = deserialize_v2_event(_fixture("change-event.json"))
    restored_verification = deserialize_v2_event(_fixture("verification-event.json"))
    restored_ledger = deserialize_v2_ledger(ledger_text)
    serialized_change = serialize_v2_event(restored_change)
    serialized_verification = serialize_v2_event(restored_verification)
    serialized_ledger = serialize_v2_ledger(restored_ledger)

    # Then: all rev2 fields and the v1 top-level projection survive unchanged.
    assert json.loads(serialized_change) == change
    assert json.loads(serialized_verification) == verification
    assert json.loads(serialized_ledger) == json.loads(ledger_text)


def test_v2_schema_rejects_malformed_confidence_paths_and_covers() -> None:
    # Given: malformed v2 events at the trust boundary.
    cases: list[tuple[str, Mutation, str]] = [
        ("change-event.json", _set_high_confidence, "confidence"),
        ("change-event.json", _set_negative_confidence, "confidence"),
        ("change-event.json", _drop_manifest_generation, "manifest_generation"),
        ("change-event.json", _set_boolean_manifest_generation, "manifest_generation"),
        ("change-event.json", _drop_commit_state, "commit_state"),
        ("change-event.json", _set_invalid_commit_state, "commit_state"),
        ("change-event.json", _drop_path_before, "paths[0].before"),
        (
            "change-event.json",
            _drop_path_verification_requirement,
            "paths[0].requires_verification",
        ),
        ("verification-event.json", _set_covers_revisions_to_object, "covers.path_revisions"),
        ("verification-event.json", _set_unknown_revision_change_id, "covers.path_revisions[0].change_id"),
    ]

    # When/Then: every malformed shape is rejected with the failing field named.
    for name, mutate, field in cases:
        event = _copy_json(_json_fixture(name))
        mutate(event)
        try:
            validate_v2_event(event)
        except LedgerSchemaError as exc:
            assert field in str(exc)
        else:
            raise AssertionError(f"expected {field} to be rejected")


def test_save_ledger_rejects_invalid_v2_shape_before_writing(tmp_path: Path) -> None:
    # Given: a malformed v2 ledger that has no complete active_turn contract.
    malformed = _copy_json(_json_fixture("ledger.json"))
    _ = malformed.pop("active_turns")

    # When/Then: the persistence boundary rejects it before creating a ledger file.
    try:
        save_ledger({"project_root": str(tmp_path)}, malformed)
    except LedgerSchemaError as exc:
        assert "ledger.active_turns" in str(exc)
    else:
        raise AssertionError("expected malformed v2 ledger to be rejected")
    assert not (tmp_path / ".fable-lite" / "ledger.json").exists()


def test_load_ledger_reads_v1_fixture_without_writing_or_migrating(tmp_path: Path) -> None:
    # Given: an untouched v1 fixture in an isolated project state directory.
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    destination = state_dir / "ledger.json"
    original = _fixture("v1-ledger.json")
    destination.write_text(original, encoding="utf-8")

    # When: W1 only recognizes the legacy schema.
    loaded = load_ledger({"project_root": str(tmp_path)})

    # Then: its bytes and v1 semantics are unchanged; no migration artifact exists.
    assert destination.read_text(encoding="utf-8") == original
    assert loaded["changed_files_seen"] == ["legacy.py"]
    assert loaded["verification_results"] == _json_fixture("v1-ledger.json")["verification_results"]
    assert not (state_dir / "ledger.v1.json.bak").exists()


def test_save_ledger_keeps_existing_destination_when_atomic_replace_fails(tmp_path: Path) -> None:
    # Given: a pre-existing valid destination and a replacement write that fails at os.replace.
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    destination = state_dir / "ledger.json"
    original = '{"preserved": true}\n'
    destination.write_text(original, encoding="utf-8")
    observed: list[Path] = []

    def fail_replace(source: str, target: Path) -> None:
        observed.append(Path(source))
        assert target == destination
        raise OSError("replace failed")

    # When: the temp-file replacement is fault-injected.
    with patch.object(ledger_module.os, "replace", side_effect=fail_replace):
        save_ledger({"project_root": str(tmp_path)}, {"task_mode": "deep"})

    # Then: direct partial JSON never reaches the destination and the temp is cleaned up.
    assert destination.read_text(encoding="utf-8") == original
    assert len(observed) == 1
    assert observed[0].parent == state_dir
    assert not list(state_dir.glob("ledger-*.tmp"))


def test_save_ledger_retries_one_transient_atomic_replace_failure(tmp_path: Path) -> None:
    # Given: Windows denies the first atomic replacement but accepts the next attempt.
    destination = tmp_path / ".fable-lite" / "ledger.json"
    real_replace = ledger_module.os.replace
    attempts = 0

    def replace_after_transient_denial(source: str, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("transient replace denial")
        real_replace(source, target)

    # When: the normal persistence boundary writes a valid ledger.
    with patch.object(ledger_module.os, "replace", side_effect=replace_after_transient_denial):
        save_ledger({"project_root": str(tmp_path)}, {"task_mode": "deep"})

    # Then: one bounded retry commits the destination without leaving temp files.
    assert attempts == 2
    assert json.loads(destination.read_text(encoding="utf-8"))["task_mode"] == "deep"
    assert not list(destination.parent.glob("ledger-*.tmp"))


def test_corrupt_ledger_backup_keeps_existing_bak_and_creates_unique_suffix(tmp_path: Path) -> None:
    # Given: an old backup and a new unreadable ledger in the same isolated state directory.
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    previous_backup = state_dir / "ledger.json.bak"
    previous_backup.write_text("older backup", encoding="utf-8")
    (state_dir / "ledger.json").write_text("{broken", encoding="utf-8")

    # When: a normal ledger event needs to recover from the corruption.
    ledger_module.record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "recover",
        }
    )

    # Then: the existing backup remains intact and the corrupt bytes use a unique suffix name.
    backups = list(state_dir.glob("ledger.json.corrupt-*.bak"))
    assert previous_backup.read_text(encoding="utf-8") == "older backup"
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken"

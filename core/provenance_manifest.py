from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .agent_log import append_agent_event, ledger_transaction
from .ledger import JsonObject, JsonValue, load_ledger, save_ledger
from .ledger_storage import ledger_path
from .ledger_v1 import apply_v1_event, sequence_value
from .ledger_v2 import apply_v2_event, default_v2_ledger
from .provenance_store import (
    SnapshotStoreError,
    load_workspace_current,
    save_turn_baseline,
    save_turn_baseline_from_current,
    save_workspace_current,
    workspace_current_path,
)
from .provenance_types import Snapshot


UNAVAILABLE_SNAPSHOT: Final = "snapshot:unavailable"
JOURNAL_LIMIT: Final = 256


@dataclass(frozen=True, slots=True)
class ManifestView:
    generation: int
    snapshot: Snapshot | None


@dataclass(frozen=True, slots=True)
class ManifestCommit:
    generation: int
    snapshot: Snapshot
    events: tuple[JsonObject, ...]


class ManifestStoreError(RuntimeError):
    pass


def load_manifest_view(root: Path) -> ManifestView:
    resolved = root.resolve()
    with ledger_transaction(str(resolved)):
        try:
            ledger, payload = _load_locked(resolved)
            current = load_workspace_current(resolved)
            ledger = _recover_locked(resolved, ledger, payload, current)
            return ManifestView(_generation(ledger), current)
        except SnapshotStoreError as exc:
            raise ManifestStoreError(str(exc)) from exc


def commit_manifest(
    root: Path,
    expected_generation: int,
    expected_snapshot_id: str,
    snapshot: Snapshot,
    event_templates: tuple[JsonObject, ...],
    baseline: tuple[str, str] | None = None,
) -> ManifestCommit | None:
    resolved = root.resolve()
    with ledger_transaction(str(resolved)):
        try:
            return _commit_locked(
                resolved,
                expected_generation,
                expected_snapshot_id,
                snapshot,
                event_templates,
                baseline,
            )
        except SnapshotStoreError as exc:
            raise ManifestStoreError(str(exc)) from exc


def save_manifest_baseline(
    root: Path,
    expected_generation: int,
    snapshot: Snapshot,
    agent: str,
    turn_id: str,
) -> None:
    resolved = root.resolve()
    with ledger_transaction(str(resolved)):
        try:
            ledger, payload = _load_locked(resolved)
            current = load_workspace_current(resolved)
            ledger = _recover_locked(resolved, ledger, payload, current)
            if (
                _generation(ledger) == expected_generation
                and _snapshot_id(current) == snapshot.snapshot_id
            ):
                save_turn_baseline_from_current(
                    resolved,
                    agent,
                    turn_id,
                    snapshot,
                )
            else:
                save_turn_baseline(resolved, agent, turn_id, snapshot)
        except SnapshotStoreError as exc:
            raise ManifestStoreError(str(exc)) from exc


def _commit_locked(
    root: Path,
    expected_generation: int,
    expected_snapshot_id: str,
    snapshot: Snapshot,
    event_templates: tuple[JsonObject, ...],
    baseline: tuple[str, str] | None,
) -> ManifestCommit | None:
    ledger, payload = _load_locked(root)
    current = load_workspace_current(root)
    ledger = _recover_locked(root, ledger, payload, current)
    if (
        _generation(ledger) != expected_generation
        or _snapshot_id(current) != expected_snapshot_id
    ):
        return None
    if current == snapshot:
        if baseline is not None:
            save_turn_baseline_from_current(root, *baseline, snapshot)
        return ManifestCommit(expected_generation, snapshot, ())
    target_generation = expected_generation + 1
    events = _prepare_events(ledger, event_templates, target_generation)
    pending: JsonObject = {
        "base_generation": expected_generation,
        "target_generation": target_generation,
        "snapshot_before": expected_snapshot_id,
        "snapshot_after": snapshot.snapshot_id,
        "events": list(events),
    }
    if baseline is not None:
        pending["baseline_agent"] = baseline[0]
        pending["baseline_turn_id"] = baseline[1]
    ledger["manifest_pending"] = pending
    _append_journal(ledger, events)
    _advance_audit_sequence(ledger, events)
    _save_or_raise(root, payload, ledger)
    for event in events:
        append_agent_event(str(root), _event_agent(event), event)
    save_workspace_current(root, snapshot)
    if baseline is not None:
        save_turn_baseline_from_current(root, *baseline, snapshot)
    committed = _finalize_locked(ledger, pending)
    _save_or_raise(root, payload, ledger)
    for event in committed:
        append_agent_event(str(root), _event_agent(event), event)
    return ManifestCommit(target_generation, snapshot, committed)


def _load_locked(root: Path) -> tuple[JsonObject, JsonObject]:
    payload: JsonObject = {"project_root": str(root)}
    destination = ledger_path(str(root))
    existed = destination.exists()
    ledger = load_ledger(payload)
    if not existed:
        ledger = default_v2_ledger()
    return ledger, payload


def _recover_locked(
    root: Path,
    ledger: JsonObject,
    payload: JsonObject,
    current: Snapshot | None,
) -> JsonObject:
    raw_pending = ledger.get("manifest_pending")
    if not isinstance(raw_pending, dict):
        return ledger
    before = raw_pending.get("snapshot_before")
    after = raw_pending.get("snapshot_after")
    current_id = _snapshot_id(current)
    if isinstance(after, str) and current_id == after:
        _restore_pending_baseline(root, raw_pending, current)
        committed = _committed_events(raw_pending)
        for event in committed:
            append_agent_event(str(root), _event_agent(event), event)
        _ = _finalize_locked(ledger, raw_pending)
        _save_or_raise(root, payload, ledger)
        return ledger
    if isinstance(before, str) and current_id == before:
        _ = ledger.pop("manifest_pending", None)
        _save_or_raise(root, payload, ledger)
        return ledger
    raise SnapshotStoreError(
        workspace_current_path(root),
        "does not match the pending manifest transition",
    )


def _restore_pending_baseline(
    root: Path,
    pending: JsonObject,
    current: Snapshot | None,
) -> None:
    agent = pending.get("baseline_agent")
    turn_id = pending.get("baseline_turn_id")
    if (
        current is not None
        and isinstance(agent, str)
        and agent
        and isinstance(turn_id, str)
        and turn_id
    ):
        save_turn_baseline_from_current(root, agent, turn_id, current)


def _prepare_events(
    ledger: JsonObject,
    templates: tuple[JsonObject, ...],
    target_generation: int,
) -> tuple[JsonObject, ...]:
    start_seq = sequence_value(ledger.get("event_seq"))
    return tuple(
        template
        | {
            "seq": start_seq + index,
            "manifest_generation": target_generation,
            "commit_state": "uncommitted",
        }
        for index, template in enumerate(templates, start=1)
    )


def _advance_audit_sequence(
    ledger: JsonObject,
    events: tuple[JsonObject, ...],
) -> None:
    for event in events:
        if ledger.get("schema_version") == 2:
            _ = apply_v2_event(ledger, event)
        else:
            ledger["event_seq"] = max(
                sequence_value(ledger.get("event_seq")),
                sequence_value(event.get("seq")),
            )


def _finalize_locked(
    ledger: JsonObject,
    pending: JsonObject,
) -> tuple[JsonObject, ...]:
    target = pending.get("target_generation")
    generation = (
        target if isinstance(target, int) and not isinstance(target, bool) else 0
    )
    committed = _committed_events(pending)
    for event in committed:
        if ledger.get("schema_version") == 2:
            _ = apply_v2_event(ledger, event)
        else:
            _ = apply_v1_event(ledger, event)
    ledger["manifest_generation"] = generation
    after = pending.get("snapshot_after")
    if isinstance(after, str) and after:
        ledger["manifest_snapshot_id"] = after
    _append_journal(ledger, committed)
    _ = ledger.pop("manifest_pending", None)
    return committed


def _committed_events(pending: JsonObject) -> tuple[JsonObject, ...]:
    return tuple(
        event | {"commit_state": "committed"}
        for event in _events(pending.get("events"))
    )


def _append_journal(
    ledger: JsonObject,
    events: tuple[JsonObject, ...],
) -> None:
    current = ledger.get("manifest_event_journal")
    journal = (
        [item for item in current if isinstance(item, dict)]
        if isinstance(current, list)
        else []
    )
    journal.extend(events)
    ledger["manifest_event_journal"] = journal[-JOURNAL_LIMIT:]


def _events(value: JsonValue | None) -> tuple[JsonObject, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _save_or_raise(root: Path, payload: JsonObject, ledger: JsonObject) -> None:
    if not save_ledger(payload, ledger):
        raise SnapshotStoreError(
            ledger_path(str(root)),
            "could not persist manifest transaction",
        )


def _snapshot_id(snapshot: Snapshot | None) -> str:
    return snapshot.snapshot_id if snapshot is not None else UNAVAILABLE_SNAPSHOT


def _generation(ledger: JsonObject) -> int:
    return sequence_value(ledger.get("manifest_generation"))


def _event_agent(event: JsonObject) -> str:
    agent = event.get("agent")
    return agent if isinstance(agent, str) else ""

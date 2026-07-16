"""# noqa: SIZE_OK — snapshot exclusion persistence belongs to this card-approved store module."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import TypeAlias, override
from uuid import uuid4

from .provenance_types import (
    EntryKind,
    ManifestEntry,
    ProvenanceReason,
    ProvenanceStatus,
    ScanIssue,
    Snapshot,
    SnapshotExclusion,
)

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class SnapshotStoreError(ValueError):
    path: Path
    reason: str

    @override
    def __str__(self) -> str:
        return f"invalid provenance snapshot at {self.path}: {self.reason}"


def snapshots_dir(root: Path) -> Path:
    return root.resolve() / ".fable-lite" / "snapshots"


def workspace_current_path(root: Path) -> Path:
    return snapshots_dir(root) / "workspace-current.json"


def turn_baseline_path(root: Path, agent: str, turn_id: str) -> Path:
    return snapshots_dir(root) / "turns" / _safe_key(agent, "agent") / f"{_safe_key(turn_id, 'turn')}-baseline.json"


def save_workspace_current(root: Path, snapshot: Snapshot) -> Path:
    path = workspace_current_path(root)
    _save(path, snapshot)
    return path


def load_workspace_current(root: Path) -> Snapshot | None:
    return _load(workspace_current_path(root))


def save_turn_baseline(root: Path, agent: str, turn_id: str, snapshot: Snapshot) -> Path:
    path = turn_baseline_path(root, agent, turn_id)
    _save(path, snapshot)
    return path


def save_turn_baseline_from_current(root: Path, agent: str, turn_id: str, snapshot: Snapshot) -> Path:
    destination = turn_baseline_path(root, agent, turn_id)
    source = workspace_current_path(root)
    temporary = destination.parent / f"snapshot-{uuid4().hex}.tmp"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, temporary)
        os.replace(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        _save(destination, snapshot)
    return destination


def load_turn_baseline(root: Path, agent: str, turn_id: str) -> Snapshot | None:
    return _load(turn_baseline_path(root, agent, turn_id))


def delete_turn_baseline(root: Path, agent: str, turn_id: str) -> None:
    try:
        turn_baseline_path(root, agent, turn_id).unlink(missing_ok=True)
    except OSError as exc:
        raise SnapshotStoreError(turn_baseline_path(root, agent, turn_id), str(exc)) from exc


def _save(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(_to_value(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix="snapshot-",
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(encoded)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SnapshotStoreError(path, str(exc)) from exc


def _safe_key(value: str, fallback: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or fallback


def _load(path: Path) -> Snapshot | None:
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SnapshotStoreError(path, str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise SnapshotStoreError(path, "must be valid JSON") from exc
    return _from_value(path, raw)


def _to_value(snapshot: Snapshot) -> dict[str, JsonValue]:
    return {
        "root": str(snapshot.root),
        "entries": [_entry_value(entry) for entry in snapshot.entries],
        "reparse_observations": [_entry_value(entry) for entry in snapshot.reparse_observations],
        "issues": [{"path": issue.path, "reason": issue.reason} for issue in snapshot.issues],
        "exclusions": [_exclusion_value(item) for item in snapshot.exclusions],
        "snapshot_id": snapshot.snapshot_id,
        "scope_policy_id": snapshot.scope_policy_id,
        "generated_patterns": list(snapshot.generated_patterns),
        "is_casefolded": snapshot.is_casefolded,
        "platform": snapshot.platform,
        "full_reconciled_at": snapshot.full_reconciled_at,
        "status": snapshot.status.value,
        "status_reason": snapshot.status_reason.value,
    }


def _entry_value(entry: ManifestEntry) -> dict[str, JsonValue]:
    return {
        "path": entry.path,
        "canonical_key": entry.canonical_key,
        "file_type": entry.file_type.value,
        "size": entry.size,
        "mtime_ns": entry.mtime_ns,
        "mode": entry.mode,
        "digest": entry.digest,
    }


def _exclusion_value(item: SnapshotExclusion) -> dict[str, JsonValue]:
    return {
        "path": item.path,
        "reason": item.reason,
        "peer_agent_key": item.peer_agent_key,
        "peer_turn_id": item.peer_turn_id,
        "invocation_id": item.invocation_id,
        "started_seq": item.started_seq,
        "started_at": item.started_at,
        "observer_turn_id": item.observer_turn_id,
    }


def _from_value(path: Path, value: JsonValue) -> Snapshot:
    if not isinstance(value, dict):
        raise SnapshotStoreError(path, "must be an object")
    entries = _entries(path, value.get("entries"), "entries")
    observations = _entries(path, value.get("reparse_observations"), "reparse_observations")
    issues = _issues(path, value.get("issues"))
    exclusions = _exclusions(path, value.get("exclusions"))
    patterns = _strings(path, value.get("generated_patterns"), "generated_patterns")
    return Snapshot(
        root=Path(_string(path, value.get("root"), "root")),
        entries=entries,
        reparse_observations=observations,
        issues=issues,
        snapshot_id=_string(path, value.get("snapshot_id"), "snapshot_id"),
        scope_policy_id=_string(path, value.get("scope_policy_id"), "scope_policy_id"),
        generated_patterns=patterns,
        exclusions=exclusions,
        is_casefolded=_boolean(path, value.get("is_casefolded"), "is_casefolded"),
        platform=_string(path, value.get("platform"), "platform"),
        full_reconciled_at=_optional_string(path, value.get("full_reconciled_at"), "full_reconciled_at"),
        status=_status(path, value.get("status"), issues),
        status_reason=_reason(path, value.get("status_reason")),
    )


def _entries(path: Path, value: JsonValue | None, field: str) -> tuple[ManifestEntry, ...]:
    if not isinstance(value, list):
        raise SnapshotStoreError(path, f"{field} must be a list")
    return tuple(_entry(path, item, field) for item in value)


def _entry(path: Path, value: JsonValue, field: str) -> ManifestEntry:
    if not isinstance(value, dict):
        raise SnapshotStoreError(path, f"{field} item must be an object")
    file_type = _string(path, value.get("file_type"), "file_type")
    try:
        kind = EntryKind(file_type)
    except ValueError as exc:
        raise SnapshotStoreError(path, "file_type is invalid") from exc
    return ManifestEntry(
        path=_string(path, value.get("path"), "path"),
        canonical_key=_string(path, value.get("canonical_key"), "canonical_key"),
        file_type=kind,
        size=_integer(path, value.get("size"), "size"),
        mtime_ns=_integer(path, value.get("mtime_ns"), "mtime_ns"),
        mode=_integer(path, value.get("mode"), "mode"),
        digest=_string(path, value.get("digest"), "digest"),
    )


def _issues(path: Path, value: JsonValue | None) -> tuple[ScanIssue, ...]:
    if not isinstance(value, list):
        raise SnapshotStoreError(path, "issues must be a list")
    issues: list[ScanIssue] = []
    for item in value:
        if not isinstance(item, dict):
            raise SnapshotStoreError(path, "issue must be an object")
        issues.append(
            ScanIssue(
                _string(path, item.get("path"), "issue.path"),
                _string(path, item.get("reason"), "issue.reason"),
            )
        )
    return tuple(issues)


def _exclusions(
    path: Path,
    value: JsonValue | None,
) -> tuple[SnapshotExclusion, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SnapshotStoreError(path, "exclusions must be a list")
    exclusions: list[SnapshotExclusion] = []
    for item in value:
        if not isinstance(item, dict):
            raise SnapshotStoreError(path, "exclusion must be an object")
        exclusions.append(
            SnapshotExclusion(
                path=_string(path, item.get("path"), "exclusion.path"),
                reason=_string(path, item.get("reason"), "exclusion.reason"),
                peer_agent_key=_string(
                    path,
                    item.get("peer_agent_key"),
                    "exclusion.peer_agent_key",
                ),
                peer_turn_id=_string(
                    path,
                    item.get("peer_turn_id"),
                    "exclusion.peer_turn_id",
                ),
                invocation_id=_string(
                    path,
                    item.get("invocation_id"),
                    "exclusion.invocation_id",
                ),
                started_seq=_integer(
                    path,
                    item.get("started_seq"),
                    "exclusion.started_seq",
                ),
                started_at=_string(
                    path,
                    item.get("started_at"),
                    "exclusion.started_at",
                ),
                observer_turn_id=_string(
                    path,
                    item.get("observer_turn_id"),
                    "exclusion.observer_turn_id",
                ),
            )
        )
    return tuple(exclusions)


def _strings(path: Path, value: JsonValue | None, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SnapshotStoreError(path, f"{field} must be a string list")
    strings = [item for item in value if isinstance(item, str)]
    if len(strings) != len(value):
        raise SnapshotStoreError(path, f"{field} must be a string list")
    return tuple(strings)


def _string(path: Path, value: JsonValue | None, field: str) -> str:
    if not isinstance(value, str):
        raise SnapshotStoreError(path, f"{field} must be a string")
    return value


def _optional_string(path: Path, value: JsonValue | None, field: str) -> str | None:
    if value is None:
        return None
    return _string(path, value, field)


def _reason(path: Path, value: JsonValue | None) -> ProvenanceReason:
    raw = _optional_string(path, value, "status_reason") or ""
    try:
        return ProvenanceReason(raw)
    except ValueError as exc:
        raise SnapshotStoreError(path, "status_reason is invalid") from exc


def _integer(path: Path, value: JsonValue | None, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SnapshotStoreError(path, f"{field} must be an integer")
    return value


def _boolean(path: Path, value: JsonValue | None, field: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotStoreError(path, f"{field} must be a boolean")
    return value


def _status(
    path: Path,
    value: JsonValue | None,
    issues: tuple[ScanIssue, ...],
) -> ProvenanceStatus:
    if value is None:
        return ProvenanceStatus.INCOMPLETE if issues else ProvenanceStatus.COMPLETE
    raw = _string(path, value, "status")
    try:
        return ProvenanceStatus(raw)
    except ValueError as exc:
        raise SnapshotStoreError(path, "status is invalid") from exc

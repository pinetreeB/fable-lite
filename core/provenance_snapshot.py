from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Final

from .provenance_policy import canonical_manifest_key
from .provenance_types import (
    ManifestEntry,
    ProvenanceConfig,
    ProvenanceStatus,
    ScanIssue,
    ScanResult,
    Snapshot,
    SnapshotExclusion,
)

SCANNER_SCHEMA_VERSION = 2
DEFAULT_POLICY_REVISION: Final = 1


@dataclass(frozen=True, slots=True)
class SnapshotBuildContext:
    root: Path
    config: ProvenanceConfig
    windows: bool
    platform: str = os.name


def build_snapshot(context: SnapshotBuildContext, result: ScanResult) -> Snapshot:
    collisions = _colliding_keys(result.entries, context.windows)
    entries = tuple(
        sorted(
            (
                entry
                for entry in result.entries
                if canonical_manifest_key(entry.path, context.windows) not in collisions
            ),
            key=lambda entry: (entry.canonical_key, entry.path),
        )
    )
    issues = result.issues + tuple(
        ScanIssue(path, "casefold_collision") for path in sorted(collisions.values())
    )
    status = (
        ProvenanceStatus.INCOMPLETE
        if issues and result.status is ProvenanceStatus.COMPLETE
        else result.status
    )
    observations = tuple(sorted(result.reparse_observations, key=lambda entry: entry.canonical_key))
    return Snapshot(
        root=context.root,
        entries=entries,
        reparse_observations=observations,
        issues=issues,
        snapshot_id=snapshot_id_for(entries),
        scope_policy_id=_scope_policy_id(context),
        generated_patterns=context.config.generated,
        is_casefolded=context.windows,
        platform=context.platform,
        status=status,
        status_reason=result.status_reason,
    )


def _colliding_keys(entries: tuple[ManifestEntry, ...], windows: bool) -> dict[str, str]:
    seen: dict[str, str] = {}
    collisions: dict[str, str] = {}
    for entry in entries:
        key = canonical_manifest_key(entry.path, windows)
        prior = seen.get(key)
        if prior is not None and prior != entry.path:
            collisions[key] = min(prior, entry.path)
        else:
            seen[key] = entry.path
    return collisions


def snapshot_id_for(
    entries: tuple[ManifestEntry, ...],
    exclusions: tuple[SnapshotExclusion, ...] = (),
) -> str:
    encoded = json.dumps(
        {
            "entries": [
                (entry.path, entry.file_type.value, entry.size, entry.mtime_ns, entry.mode, entry.digest)
                for entry in entries
            ],
            "exclusions": [
                (
                    item.path,
                    item.reason,
                    item.peer_agent_key,
                    item.peer_turn_id,
                    item.invocation_id,
                    item.started_seq,
                    item.started_at,
                    item.observer_turn_id,
                )
                for item in exclusions
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _digest_text(encoded)


def _scope_policy_id(context: SnapshotBuildContext) -> str:
    return _scope_policy_id_for_revision(context, DEFAULT_POLICY_REVISION)


def _scope_policy_id_for_revision(
    context: SnapshotBuildContext,
    default_policy_revision: int | None,
) -> str:
    payload = {
        "schema": SCANNER_SCHEMA_VERSION,
        "include": context.config.include,
        "exclude": context.config.exclude,
        "casefolded": context.windows,
    }
    if default_policy_revision is not None:
        payload["default_policy_revision"] = default_policy_revision
    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _digest_text(encoded)


def scope_policy_id(context: SnapshotBuildContext) -> str:
    return _scope_policy_id(context)


def is_trusted_default_policy_migration(
    context: SnapshotBuildContext,
    previous_scope_policy_id: str,
) -> bool:
    previous_policy_ids = {
        _scope_policy_id_for_revision(context, revision)
        for revision in range(1, DEFAULT_POLICY_REVISION)
    }
    previous_policy_ids.add(_scope_policy_id_for_revision(context, None))
    return previous_scope_policy_id in previous_policy_ids


def _digest_text(value: str) -> str:
    digest = hashlib.blake2b(value.encode("utf-8", "surrogateescape"), digest_size=32)
    return f"blake2b-256:{digest.hexdigest()}"

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from .provenance_policy import canonical_manifest_key
from .provenance_types import ManifestEntry, ProvenanceConfig, ScanIssue, ScanResult, Snapshot

SCANNER_SCHEMA_VERSION = 1


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
    observations = tuple(sorted(result.reparse_observations, key=lambda entry: entry.canonical_key))
    return Snapshot(
        root=context.root,
        entries=entries,
        reparse_observations=observations,
        issues=issues,
        snapshot_id=_snapshot_id(entries),
        scope_policy_id=_scope_policy_id(context),
        generated_patterns=context.config.generated,
        is_casefolded=context.windows,
        platform=context.platform,
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


def _snapshot_id(entries: tuple[ManifestEntry, ...]) -> str:
    encoded = json.dumps(
        [
            (entry.path, entry.file_type.value, entry.size, entry.mtime_ns, entry.mode, entry.digest)
            for entry in entries
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _digest_text(encoded)


def _scope_policy_id(context: SnapshotBuildContext) -> str:
    encoded = json.dumps(
        {
            "schema": SCANNER_SCHEMA_VERSION,
            "include": context.config.include,
            "exclude": context.config.exclude,
            "casefolded": context.windows,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return _digest_text(encoded)


def scope_policy_id(context: SnapshotBuildContext) -> str:
    return _scope_policy_id(context)


def _digest_text(value: str) -> str:
    digest = hashlib.blake2b(value.encode("utf-8", "surrogateescape"), digest_size=32)
    return f"blake2b-256:{digest.hexdigest()}"

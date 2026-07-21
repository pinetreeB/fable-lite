# B5 invocation status migration rehearsal

> Date: 2026-07-21 (Asia/Seoul)  
> Branch/base: `worker/v24-b5-status` / `033a721`  
> Scope: require `active_turns.*.invocations.*.status`, classify legacy omissions against the invocation lease, and preserve a fail-closed no-write path when auto migration is off or cannot classify safely.

## Policy decision

Three compatibility paths were evaluated.

1. Requiring `status` without a compatibility boundary was rejected: an old ledger would raise during an adapter read and could escape into a host-level fail-open path.
2. Unconditional persisted backfill was rejected: it would bypass the requested opt-in and would rewrite a live ledger without an auditable rollback source.
3. The implemented path is explicit and fail-closed:
   - `FABLE_LITE_AUTO_MIGRATION=1` plus both packaged green receipts enables migration.
   - ON: copy in memory, set only provably lease-expired omissions to `closed`, preserve recent rows with complete R2 window evidence as `open`, validate the entire v2 schema, preserve immutable `ledger.v2-invocation-status.json.bak`, then atomically replace and re-read. If time/window evidence is incomplete, do not archive or rewrite the ledger; retain the degraded compatibility path.
   - OFF: leave source bytes untouched, expose an in-memory compatibility view with `attribution_degraded=true`, block destructive R2 work, and reject every attempt to persist that view. The non-persistable marker also prevents a stale OFF view from overwriting a concurrent successful migration.
   - ON failure: keep hook execution fail-open and ledger bytes unchanged, but emit one `core.ledger` warning containing the migration stage and detail so operators can distinguish failure from OFF mode.

This makes an old project visibly degraded rather than silently broken or silently rewritten. Normal operation resumes after explicit migration; malformed fields unrelated to `status` are not repaired or archived by this narrow migration.

## Regression-first evidence

Before production changes:

```text
python -X utf8 -m pytest tests/test_w3_legacy_invocation_compat.py::test_invocation_without_status_is_rejected_at_the_schema_boundary -q
FAILED: DID NOT RAISE LedgerSchemaError
```

The full pre-fix B5 selection then reported `7 failed, 3 passed`: schema omission allowed, migration APIs/archive absent, OFF load healthy, and the environment opt-in ignored. A later concurrency regression first failed with `assert True is False`, proving that a stale degraded view could overwrite a ledger after another process migrated it.

After the initial implementation, the focused migration/schema/concurrency selection reported `24 passed`.

P4 then reproduced a block-to-allow R2 bypass by removing `status` from a five-minute-old peer invocation: the original backfill persisted `closed`. The repair tests first failed with recent=`closed`, no `classify` refusal for incomplete R2 evidence, and no migration warning. After the repair, the focused migration selection reports recent=`open` with an actual R2 block, expired-only=`closed`, incomplete recent state unchanged/degraded, and ON failure warning distinct from OFF.

## Real-ledger copy setup

The live source was read only. Both rehearsal files were copied from the same snapshot of:

```text
C:\Users\gustj\fable-lite-dev\.fable-lite\ledger.json
```

PowerShell rehearsal root:

```text
C:\Users\gustj\fable-lite-dev\tmp\b5-status-rehearsal
```

Git Bash equivalent:

```text
/c/Users/gustj/fable-lite-dev/tmp/b5-status-rehearsal
```

The sealed real-ledger copy and initial work copy had the same SHA-256:

```text
D9781D447E073E005DB25A4F5A68ACFD75E413BBC7A2161B85995A9AFC53636C
```

The real snapshot was already strict-valid: schema v2, 71 invocation rows, 70 `closed`, 1 `open`, and 0 missing statuses. To exercise the historical shape, only the isolated work copy had `status` removed from one previously closed row:

```text
agent key:    codex_cli:019f8119-79d8-7f11-8393-784ec2058b26:codex
invocation:   exec-03e89e1b-ab81-4fda-b13a-dcbdc84f951f
seed SHA-256: A2DF163EDAD59619DED2C74410961724A86F4490AF1BA41270732186B0D3E7B3
```

No live source path was passed to the migration function.

## Original `22e1725` observations

The byte/hash observations below document the initial B5 migration mechanics. Their unconditional-`closed` classification conclusion is superseded by the P4 lease-aware repair above; archive, atomicity, idempotence, and rollback evidence remain applicable.

### OFF behavior

The strict schema rejected the seeded copy at the exact `.invocations.<id>.status` field. `load_ledger()` returned `attribution_degraded=true` and an in-memory `status=closed`. The work-copy hash remained `A2DF...E7B3`, confirming zero persistence.

### Backfill and preservation

After `migrate_v2_invocation_statuses()`:

```text
strict_valid:                  true
rows:                          71
missing:                       0
open:                          1
closed:                        70
only_expected_semantic_change: true
```

The comparison added `status=closed` to the archived JSON in memory and then compared the complete object to the migrated ledger. Equality was exact, so every existing row and non-target field was preserved. The migrated ledger hash was `D978...636C`, exactly matching the sealed real-ledger copy.

The immutable archive hash was `A2DF...E7B3`, exactly matching the seeded pre-migration bytes.

### Idempotence

Two additional migration calls produced the same value and left the ledger hash unchanged:

```text
before:             d9781d447e073e005db25a4f5a68acfd75e413bbc7a2161b85995a9afc53636c
after first rerun:  d9781d447e073e005db25a4f5a68acfd75e413bbc7a2161b85995a9afc53636c
after second rerun: d9781d447e073e005db25a4f5a68acfd75e413bbc7a2161b85995a9afc53636c
```

### Rollback and reapply

The rehearsal atomically restored `ledger.json` from the archive. Its SHA-256 became `A2DF...E7B3`, exactly matching the archived legacy bytes. Reapplying the migration restored `D978...636C`, again matching the sealed real-ledger copy.

Fault injection separately confirms that a failed destination replace restores the original bytes, and validation failure on any unrelated malformed field leaves the source untouched without creating a misleading archive.

## Verdict

PASS after P4 repair: strict rejection, lease-aware open/closed classification, incomplete-evidence degraded preservation, opt-in-only persistence, observable ON failure, immutable archive, existing-row preservation, idempotence, atomic failure restoration, byte-exact rollback, and reapply are covered. The original real-ledger copy proves the storage mechanics; focused end-to-end regression proves that a recent peer candidate remains blocked by R2 after migration.

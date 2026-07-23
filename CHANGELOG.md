# Changelog

## [Unreleased]

### Added

- **CI-02 interpreter matrix and tool pins**: CI now runs the full gate on
  Ubuntu Python 3.12/3.13/3.14 and Windows Python 3.12/3.14, and installs
  pytest/ruff/build from pinned `ci/constraints.txt` so runs stay
  reproducible instead of tracking whatever pip resolves that day.
- **CLI-02 operator onboarding**: added secret-safe human/JSON `smtw doctor`
  and concise `smtw status`, explicit non-overwriting `smtw init`, and a
  write-free `smtw migrate --check` with human migration summaries and stable
  healthy/unsafe/action-required exit codes.
- **GOALS-02 identity-aware goals UX**: added canonical `smtw goals plan`, `verify`, and `status` commands with automatic selection of a sole exact active identity, unique host/session environment matching, explicit `--identity` or matching `--host`/`--session-id`/`--agent` selection, and explicit errors for ambiguity, wrong identities, and synthetic-only turns.

### Changed

- **DOC-02 bilingual README realignment**: rebuilt both READMEs around the
  current canonical config and six-state authority model, explicit migration,
  truthful gate failure policies, identity-aware multi-agent operation,
  legacy module execution, performance/privacy limits, and the actual
  cross-platform CI gates. Removed stale single-legacy-state, fixed-contract,
  design-stage alias, and blanket fail-open claims.

### Fixed

- **COMPAT-01/02 source and legacy execution semantics**: source checkouts now
  prefer their adjacent repository version over stale installed metadata,
  `doctor` exposes module/distribution mismatches, and physical legacy thin
  shims support `python -m fable_lite.<public-submodule>` without loader alias
  failures while ordinary legacy imports retain object identity.
- **N2 recovery guidance**: checkpoint denials now include a complete copyable identity-specific plan command. Following that command writes the checkpoint N2 actually checks, immediately clears the gate for that identity, and cannot borrow a foreign identity's checkpoint; legacy single-agent fallback remains available.
- **Quarantine durability (QUAR-01)**: blocked-command records now reserve
  UUID-suffixed destinations with an exclusive create instead of overwriting a
  name selected by an `exists()` check. UUID collisions retry, POSIX files
  request mode `0600`, and a success is returned only after post-GC
  existence, size, and SHA-256 verification. Backup and verification failures
  remain side effects of the unchanged fail-closed R2 block decision.
- **Truthful truncation records (QUAR-02)**: quarantine headers now record
  original/stored byte counts and SHA-256 digests, UTF-8 encoding, truncation
  state, and complete/incomplete status. The 1 MiB limit applies to stored
  command bytes, UTF-8 code points are never split, `quarantine show` warns
  before partial content, and block messages distinguish complete, partial,
  and failed preservation.
- **CONFIG-02 fail-closed config precedence**: malformed `pyproject.toml` files now recognize equivalent quoted and dotted `tool.smtw` declarations without treating comments or basic/literal string contents as config. A possible canonical declaration is `DECLARED_INVALID` and cannot silently fall back to enabled legacy config.
- **MIGRATION-02 rollback archive rotation**: invocation-status backfills now preserve each distinct source in a SHA-256-named immutable archive with a validated schema-v1 index, reuse identical sources, retain the current rollback source, and bound older archives by count and total bytes. A legacy fixed-name archive no longer prevents a later mixed-version backfill.
- **ATTR-02**: invocation candidates now persist separate project-relative lexical keys for PostTool attribution and filesystem-resolved keys for R2 peer matching. Symlink replacement therefore remains attributed to the declared link while destructive access to its current in-project target still sees the open peer window; out-of-root targets retain the existing R2 policy.
- **HINT-02**: inline-Python state-file hints reuse R2's command-position normalization through `env`, environment assignments, and `command`, and track `pathlib.Path` imports including `Path as P` and `pathlib as pl`.

### Compatibility

- The invocation ledger keeps the resolved `candidate_paths` compatibility projection. New readers prefer `candidate_logical_paths` / `candidate_resolved_paths`, while legacy live entries are migrated on read with the previous physical canonicalization rule.

### Known Limitations

- Inline-Python hints are advisory friction, not an authorization boundary. Static AST inspection intentionally does not decode dynamic `exec`/`eval` payloads such as Base64-wrapped source; R2's independent fail-closed command gate remains authoritative and is unchanged.

## [2.6.0] - 2026-07-22 — canonical Python package (`fable_lite` → `smtw`)

### Changed

- Renamed the canonical Python package from `fable_lite` to `smtw`. Both console scripts (`smtw` and `fable-lite`) now enter `smtw.cli`, and user-facing module commands use `python -m smtw`.

### Compatibility

- `fable_lite` remains as a deprecation shim through v3.x. The package and all public submodules are aliases of the corresponding `smtw` module objects, so legacy imports, `python -m fable_lite`, reload, Windows spawn, and old pickle module paths remain supported.
- The shim emits `DeprecationWarning` once per process with the message `fable_lite is deprecated; import smtw instead`. Default warning handling is supported; explicitly promoting the warning to an error with `-W error::DeprecationWarning` or `PYTHONWARNINGS=error` is intentional failure behavior.
- Running this source checkout while an older global `fable-lite` distribution remains installed is unsupported because distribution metadata can resolve to the older version. Use a clean virtual environment.

## [2.5.0] - 2026-07-22 — internal unification (`.fable-lite` → `.smtw`)

Internal identifier unification kept deferred at the v2.0 rebrand (design track "v3": `docs/design/smtw-unification-v3.md`, 3-AI). P4 summary: `docs/reviews/2026-07-22-smtw-unification-v3-p4.md`. Released as a **minor** version because it is fully backward compatible: existing (unmigrated) projects keep running unchanged on the legacy tree, this release only adds an explicit `smtw migrate` path and reads both generations, and nothing auto-migrates. The breaking removal of legacy read paths is reserved for a future major (v3/v4).

### Added

- **State layout & explicit migration (Q1)**: `core/state_layout.py` gives a side-effect-free layout verdict (`EMPTY`/`LEGACY`/`NATIVE`/`MIGRATED`/`MIGRATING`/`CONFLICT`); `core/state_migration.py` + `smtw migrate` perform an **explicit** copy→verify→publish migration (isolated staging copy, pre/post source manifest comparison, atomic marker, `rename`-into-absent-target publish). Invariants: exactly one authoritative tree per project, `state_dir()` never writes, no per-file legacy fallback, no silent fallback after publish. Migration **defers** when the legacy tree has an active turn or open invocation (requires session restart); `.fable-lite/config.json` intentionally stays in the legacy location for config-fallback lifetime.
- **Runtime env unification (Q3)**: `core/runtime_env.py` resolves eight semantic keys with `SMTW_` canonical and `FABLE_LITE_` as a read alias by **presence, not truthiness**. Both generations present with differing values is a **fail-closed** `SmtwEnvConflictError` (blocks silent gate-weakening via the legacy name); adapters convert it to `permissionDecision: deny` (PreToolUse) / `decision: block` (other events) rather than absorbing it into health fail-open.
- **Project config unification (Q4)**: precedence `.smtw.toml` > `pyproject.toml` `[tool.smtw]` > `.fable-lite/config.json`, each source resolving to `ABSENT` / `VALID` / `DECLARED_INVALID`. Only an unparseable TOML is corrupt; a missing `[tool.smtw]` section is `ABSENT` and falls through (a removed section cannot DoS supervision into fail-closed). A malformed-pyproject header probe skips comments/strings/`[tool.smtw.fake]`.
- **Blocked-artifact quarantine (B1)**: when R2 blocks a destructive command, the command is backed up to `.fable-lite/quarantine/` as a best-effort side effect so a worker's shell-written artifact is not lost — R2's block decision and reason code are unchanged (a backup failure never flips block to allow). `smtw quarantine list/show/clear` lets the orchestrator review and recover; there is no `apply` (auto-apply is out of scope). Backups are traversal-safe (sanitized agent key, three CLI path guards), live inside the R2-protected state dir, and are bounded by count/size/age GC.

### Changed

- **Consumer SSOT**: every state consumer (ledger, attribution/snapshot, contract/goals/intent, scorecard/coordination/audit, adapter runtime log) now takes the single tree from `state_dir()`; a regression test forbids direct `.fable-lite` literals outside `core/state_layout.py`.
- **R2 / provenance dual protection**: destructive-guard and provenance-exclude protect `.smtw`, the preserved legacy `.fable-lite`, and migration staging/lock by both lexical and resolved path, so a leftover legacy tree is never left unguarded.

### Known Limitations

- **Content-matched marker forgery**: an attacker who already bypasses R2 (arbitrary code execution) can plant a `.smtw` whose marker digest/count/bytes exactly match the legacy tree and have it accepted as authoritative. This causes no data loss (content is identical to legacy) and cannot launder history (a mismatched digest is rejected as `CONFLICT`); a local marker signature adds no trust because the same ACE could rewrite the live target directly, and forcing legacy presence would violate the no-fallback-after-publish invariant. Runtime full-tree rehashing was rejected as prohibitively expensive. Verdict Low / no fix (codex·agy·orchestrator concur).

### Compatibility / migration

- Legacy read paths (`.fable-lite/` whole-tree mode, `FABLE_LITE_*` env aliases, `.fable-lite/config.json` lowest-precedence fallback) remain through v3.x and are removed no earlier than v4, with `smtw doctor` surfacing the active layout/config/env source. **Python module rename `fable_lite` → `smtw` (Q2) is deliberately deferred** to a later minor with a compatibility shim.

## [2.4.1] - 2026-07-21

### Fixed

- **Invocation status hardening (B5)**: `invocations.status` is required again at the v2 schema boundary. Explicitly opted-in status backfill (`FABLE_LITE_AUTO_MIGRATION=1`) closes only omissions proven older than the invocation lease and preserves recent, fully evidenced peer windows as `open`; unclassifiable rows remain unchanged/degraded. Immutable archive and atomic validation remain intact, and ON-but-failing migration emits stage/detail diagnostics instead of looking identical to OFF mode.
- **Release-gate separation**: `auto_migration_enabled()` (ledger v1→v2 schema migration, unlocked in v2.0.0) is receipts-gated and env-independent again, so a clean wheel keeps schema migration on (fixes a wheel-smoke `assert auto_migration_enabled()` regression). The opt-in invocation-status backfill moved to a distinct `status_backfill_enabled()` gate.

## [2.4.0] - 2026-07-21 — stabilization (no new gate semantics)

Stabilization sweep closing the external audit (`docs/reviews/2026-07-20-sol-stabilization-handoff.md`, P0/P1/P2) plus real-world multi-agent friction. Each item shipped regression-first with adversarial (agy) P4 review; full review-debt closure in `docs/reviews/2026-07-21-v24-review-debt-closure.md` (13/13 FIXED or ALREADY_FIXED).

### Fixed

- **R2-01**: the destructive-command parser inspects every chained shell segment (`&&`, `||`, `;`, `|`), not just the head; a benign leading segment no longer masks a destructive tail, operators inside quotes are never split, and any unresolved destructive segment fails closed.
- **R2-02**: `git checkout -f/--force` is blocked as implicit-scope destruction (discards local work) regardless of target attribution; `-b/-B` and non-forced switches stay allowed.
- **R2-03**: peer open-invocation candidate paths are canonicalized to project-relative keys on both write and read, closing a pre-attribution window where an absolute peer candidate diverged from a relative R2 target (backward-compat migration for live ledgers included).
- **R2-04**: state-dir (`.fable-lite`) protection applies to the lexical project-relative first component, so a symlinked state dir cannot route deletes around protection via out-of-root resolution.
- **ACT-01**: on session-root mismatch, the Claude adapter re-checks the mismatched project's opt-in before using it as the active root; unconfigured projects stay stateless (quiet-opt-in contract preserved).
- **CODEX-01 / CODEX-02**: recovered Codex identities are promoted `legacy_default` → `exact`, and contract authorship is recorded after identity resolution, so namespaced-contract authoring is honored on later high-risk edits.
- **SCORE-01**: coordination view first/last times use `occurred_at` min/max instead of journal append order, fixing time inversion under concurrency.
- **HINT-01**: inline Python write-friction detects `Path.rename/replace` destinations and `open` update/binary write modes while leaving read forms unflagged.
- **NEW-02**: `parse_unable_*` fail-closed reasons are mapped in `R2_COORDINATION_REASON_MAP`, so dynamic-command denials (e.g. PowerShell `$var` idioms) are labeled accurately instead of `unresolvable_target` — diagnostic only, gate decision unchanged.
- **Multi-agent goals (B4)**: `goals.py` checkpoints are namespaced per session identity (`goals/<identity>.json`), preventing worker overwrite of the orchestrator's goals; N2 gate checks the active identity; single-identity sessions stay compatible via legacy fallback + on-write migration.

### Changed

- **REL-01**: removed the unused/stale `uv.lock`; `sync_version.py` fails CI if it returns or any release-version surface drifts.
- **DOC-01**: README/README.ko realigned to actual behavior (plugin install + per-project opt-in; blocks up to twice then fail-opens); stale/overclaimed phrasing removed.
- **PKG-01**: added `smtw` / `fable-lite` console scripts and a `version` subcommand.

## [2.3.1] - 2026-07-21

### Fixed

- Made R2 deny auditing fail fast when the ledger lock is busy, preserving the blocking response's one-second budget while leaving deterministic events retryable through the durable outbox. Malformed outbox entries are now rejected before mutation and persist `coordination_degraded` instead of escaping from ledger serialization.
- Made turn bootstrap recovery atomic under the root ledger lock with first-valid-write-wins baseline creation, CAS baseline advancement, explicit missing/ready/degraded states, crash-residue adoption, and live-owner-safe stale-lock recovery. A READY turn is accepted only when its physical baseline exists and its snapshot ID matches the ledger.
- Made coordination projection durable without changing gate decisions: a bounded 256-entry ledger outbox commits with the authoritative transition, drains after unlock through the strict exact-content writer, acknowledges by CAS, retries crash cuts, and reports overflow/schema/I/O damage through `coordination_degraded`.
- Reconciled `COMPLETE_WITH_EXCLUSIONS` PostTool observations by replaying trustworthy turn deltas in memory while filtering every excluded path under the result snapshot's canonical case policy. Excluded peer changes are never attributed to the caller; non-excluded observer contention remains visible.
- Stopped deterministic probe runs from dirtying tracked files. `eval/results/` is ignored and CI writes probe receipts to runner temporary storage.
- Made benchmark RSS accounting phase-local. Timing samples no longer reuse the process-lifetime high-water mark, and the dedicated memory probe polls current RSS during each action so earlier benchmark phases cannot create false SLO failures.

### Changed

- Moved the two reviewed green migration-gate receipts from mutable `eval/results/` output into package data at `core/release_receipts/`. This also fixes clean wheels silently keeping v1 dual-read because `eval/` was never packaged. Source checkouts and wheel installs now evaluate the same W9/W10 evidence; fresh measurements do not self-promote into release approval.

### Known Limitations

- Atomic replace does not include directory fsync durability across sudden power loss. Stale-lock recovery does not yet compare process start time, so PID reuse remains a hardening backlog.
- A tombstone-free stale turn written by a pre-upgrade version cannot always be distinguished from a live child turn.
- Coordination payloads are count-bounded but have no per-entry byte ceiling; bootstrap timestamp validation is conservative at delivery time, and the journal/parser and ledger validators duplicate part of the schema contract.
- `PEER_EXCLUSION` coordination audit, stronger peer-exclusion lease policy, and durable ledger events for cumulative replay remain follow-up work. The W3 replay fix intentionally changes only in-memory attribution; immediate PostTool deltas remain durably recorded as before.

## [2.3.0] - 2026-07-19

### Fixed

- `resume_turn` bootstrap retry treated `COMPLETE_WITH_EXCLUSIONS` as failure (strict `is COMPLETE` comparison), defeating the F3 peer-activity rescue exactly in its target scenario — concurrent multi-agent boot on a fresh store. Bootstrap success now uses the shared completion helper, and `begin_invocation` no longer drops `candidate_paths` registration on the recovery path. Reproduced 100% by two independent probes before the fix.
- `turn_not_started` recovery is now explicit: an invocation that successfully full-bootstraps a missing baseline records `baseline_status=ready`, `provenance_incomplete=false`, `provenance_status=complete`, and an empty status reason in one ledger write, instead of depending on a later PostToolUse to accidentally overwrite the stale state (which a PostToolUse fail-open could leave behind forever). Failed bootstraps keep the conservative `turn_not_started` state unchanged.
- Windows long-path atomic writes: on hosts with `LongPathsEnabled=0` (the Windows default), session-registry warning paths past the 260-character boundary made `os.replace` fail and silently degraded PostToolUse into a health fail-open, losing scope warnings. `atomic_write` now normalizes to absolute paths and applies the `\\?\` extended-path prefix when either side crosses the boundary, preserving the open→flush→fsync→replace contract. Verified on the reproducing host with default temp paths (704 tests green without workarounds); 259/260/262-boundary and deep-warning regressions added.
- Probe runner now forces UTF-8 on the child pytest process so Korean failure tails are no longer mojibake; PASS/FAIL stays returncode-based.

### Added

- Scorecard coordination journal: a separate append-only `.fable-lite/scorecard/coordination.jsonl` (existing `gates.jsonl`, its closed `ReasonCode` enum, and default CLI output are unchanged). This wave records two categories — `r2_deny` (all eight destructive-guard block sites mapped onto four closed reason codes, plus the pre-resolution parse branch) and `turn_bootstrap` (`entered`/`recovered` pairs closed per actor and turn). Recording is laundering-resistant by construction: the R2 verdict itself stays fail-closed on the raw identity, while the journal entry is written only after `resolve_active_invocation()` settles the real identity, as fire-and-forget I/O that can never affect the gate decision (a read-only journal directory does not bypass R2). Remaining categories are reserved in the enum but not yet recorded.
- Scorecard CLI views: `python -m fable_lite scorecard --view sessions|agents|coordination` — `sessions` is the unchanged default; `agents` compares actors side by side; `coordination` renders the new journal. Root-level cross views stay a CLI read-time join; the core `SessionIdentity` model is unchanged and nothing new is auto-pushed into Stop output (quiet policy preserved).
- READMEs: project-scope plugin install path for true zero cost outside supervised projects (`claude plugin install ... --scope project`, per-project disable via `.claude/settings.local.json`), and the Antigravity host-status wording updated to the current facts (official-schema hooks.json parses and loads on host 1.1.2+; host execution of config-path hooks still unconfirmed).

### Known Limitations

- Turn bootstrap baseline persistence, ledger transition, and coordination observation use separate transactions to avoid lock re-entry; coordination IDs enforce one recovered observation per actor and turn, but the three operations are not a single atomic transaction.
- Coordination `--days`/`--session` filters apply to `entered` and `recovered` events independently; the CLI prints a hint when a counterpart may fall outside the selected window.

## [2.2.0] - 2026-07-17

### Changed

- **Claude Code adapter is now opt-in per project** ("Quiet Opt-in", spec: `docs/specs/v2.2-quiet-optin.md`). Hooks engage only when `<root>/.fable-lite/config.json` declares `{"schema_version": 1, "supervision": true}` (strict boolean). Inactive projects take a stdlib-only fast path: zero core imports, zero file writes, output exactly `{}`. Exact-home sessions are hard-off even with a config; projects under the home directory keep full supervision.
- Project root resolution no longer trusts the drifting tool `cwd`: `CLAUDE_PROJECT_DIR` → write-once session registry latch (atomic, TTL-GC'd, cleaned by SessionEnd) → upward config search fallback. This eliminates the cd-drift false blocks (R1/Stop) observed in live sessions.
- Message diet: informational systemMessages (`observed N change(s)`, `provenance incomplete`, `recorded verification`, `Stop gate allow`, home advisory) are silenced while ledger recording is preserved. Block reasons stay visible; scope warnings dedupe to once per turn; health warnings (fail-open, corrupt registry/config, root mismatch) dedupe to once per session and are never silenced. Scorecard line is display-opt-in.
- Quick mode keeps full PostToolUse/PostToolUseFailure observation and atomically promotes the turn to normal before the first mutation-capable tool runs (promotion is exception-safe; if it cannot be persisted the tool is denied rather than fail-opened). Only provably read-only turns skip the heavy Stop reconcile, and they never claim "clean verified".
- PreToolUse denials emit `hookSpecificOutput.permissionDecision: "deny"` instead of the deprecated top-level `decision: "block"`.
- Test harness: suite is hermetic against shared plugin-data state (`SMTW_TEST_FORCE_ENABLE=1` isolates per test); verified green on two heterogeneous runner environments plus a hostile shared-state environment.

## [2.1.1] - 2026-07-16

### Fixed

- Home-directory sessions (project root exactly equal to `$HOME`/`%USERPROFILE%`) now explicitly skip provenance instead of looping on `scope_too_large`. A home directory's scan volume structurally exceeds any budget (observed 11x: `.claude` alone is 2.8 GB, 61% of it the agent's own session logs), so provenance was blocking every turn and passing only via the fail-open cap without ever verifying anything (observed 9 cap-allows, 0 resolutions). Home root is now reported as `unsupported`/`home_root` with an actionable message (open the session from the project folder), snapshot scans are skipped entirely, and only the `scope_too_large`/`incomplete` provenance blocks are bypassed — investigation, design, and verification gates stay active. Exact home match only (case-folded, path-normalized); projects under the home directory keep full supervision.

## [2.1.0] - 2026-07-15

### Added

- Added an opt-in design gate (default OFF). Enable it with `FABLE_LITE_DESIGN_GATE=1` or a project `design/gate.config` `{"enabled": true}` (project config wins over the environment variable). When enabled, UI-domain or UI-file-creation turns run `design_lint` on changed lines — raw hex/rgb/hsl colors, raw px spacing, and Tailwind arbitrary design literals — with DESIGN-OPS exception boundaries (token source files, `0`/`1px` hairlines, percentages, `currentColor`, SVG internals, and a path+rule+reason+expiry allowlist). Stop blocks a UI-touching turn until the lint passes and a render-verification tool call was observed, with an independent two-block fail-open counter. `fable_lite check --design` runs the lint on demand regardless of the toggle. Editing `design/gate.config` invalidates a prior pass, so an agent cannot launder an allowlist entry to bypass the gate. Adds the `design-review` pack and extends `verification-grounding` with design observation checks.

## [2.0.2] - 2026-07-15

### Fixed

- Fixed provenance false positives that structurally blocked Stop on home-directory, large-repo, and remote-mutation sessions. Shell effects are now classified as proven-read-only, proven-remote-only, or local-or-unknown (default), and a `git status` observation no longer marks a turn mutation-capable on its own.
- Hardened the config self-exemption path: `.fable-lite/provenance-config.json` is always tracked by the snapshot scanner, and git-tracked source paths are force-observed regardless of config `exclude`, so an agent cannot launder a backdoor by excluding a path and committing it.
- Fixed shell tokenization so a mid-word `#` no longer swallows subsequent operators; a change command after `#` is classified local-or-unknown, not read-only.
- Recognized loopback aliases (`127.1`, `0.0.0.0`, integer and short-form IPs, trailing-dot hostnames) so local ssh/scp edits are not misclassified as remote-only.
- Restored fail-closed handling when a post-mutation turn baseline is missing, preventing a concurrent observer from absorbing an unverified change.
- Attributed a deleted path to the current turn only when it existed in the turn baseline, so a pre-turn deletion no longer over-blocks a read-only turn.
- Replaced tracked-path discovery with `git ls-tree HEAD` plus staged additions and applied Windows case-folding, removing an intent-to-add false positive and a casing-based evasion.

## [2.0.1] - 2026-07-14

### Added

- Added Session Quality Scorecard: an append-only gate journal, bounded per-session ledger cache, privacy-preserving CLI summaries, and optional Stop allow summaries for Claude Code, Codex CLI, and Antigravity payloads.
- Added bounded provenance scans for 10,000 tracked entries, 256 MiB, and cooperative full/incremental deadlines. Oversized scopes now return an explicit advisory-only `scope too large` state without committing partial snapshots.
- Added conservative remote mutation epochs for direct `ssh` and local-to-remote `scp`. A separately started successful verification, including a local-only check, must cover the remote epoch; satisfying it does not prove that remote state was observed as clean. Local redirects, pipelines, downloads, command chains, substitutions, and unsafe SSH options do not use this relaxation.
- Expanded CI with Ruff, version synchronization, fresh W9 receipts, wheel build/install smoke, and a Windows nightly/tag workflow for randomized W9, W10, the eight-process Stop race, tag/version matching, and receipt artifacts.

### Fixed

- Removed the `quick`-mode exemption for non-document changes. Fresh successful verification is now required for code and other non-document artifacts in every task mode.
- Replaced substring-only verification recognition with shell-aware tokenization so output-only commands such as `echo pytest`, `printf`, `Write-Output`, comments, and print-only inline Python cannot unlock Stop.
- Tightened high-risk R1 contracts so `evidence` is required, must be a list of non-empty strings, and cannot be replaced with missing, scalar, empty, or whitespace-only values.
- Applied soft provenance exclusions at every path depth so nested dependency and cache directories do not trigger home-root scan blowups.
- Preserved frozen verification epochs and conservative local-mutation handling in oversized scopes, including SSH options that can create local files.
- Cleared the current Ruff findings without adding runtime dependencies.

### Changed

- Updated both READMEs to describe v2 state paths, Scorecard, the current deterministic probe result, the actual host-support matrix, and the limits below.

### Known Limitations

- Stop still fail-opens after two blocks. The harness supervises normal work discipline; it is not a complete defense against a deliberately evasive model.
- Files outside the project root and database or network side effects are not directly observed. Remote epochs prove only that a later verification command ran after the remote mutation, not that remote state was independently observed as clean.
- Full reconciliation near the 10,000-entry/256 MiB envelope can take several seconds. Deadlines are cooperative and cannot preempt one blocked OS call.
- Promise-only completion (`PRB-01`) and independent per-gate toggles (`PRB-11`) remain manual/unimplemented.
- Antigravity conformance is validated by payload injection; live firing on host 1.1.1 remains unconfirmed.

## [2.0.0] - 2026-07-13

### Added - Change provenance

- Renamed the public product and plugin from `fable-lite` to `show-me-the-work` (`smtw`): the project grew from transferring Fable 5 working discipline into evidence-based AI work supervision. Internal `.fable-lite/`, `FABLE_LITE_*`, Python module, CLI launcher, and package identifiers remain compatible in v2.0.

- Added stdlib-only filesystem provenance with BLAKE2b-256 manifests, metadata fast-paths, full Stop reconciliation, Windows casefold collision handling, non-follow symlink/reparse safety, generation rebase, canonical multi-adapter replay, and per-turn verification covers.
- Added the 200-case W9 golden corpus and Git/non-git plus Claude Code/Codex/Antigravity replay receipts.
- Added the W10 1k/10k benchmark (`5` warm-ups + `30` measurements) with optional 50k/2GiB stress, percentile/read/stat/RSS metrics, atomic JSON receipt, and a two-receipt release migration guard.

### Release gate status

- Gates 1-10 and 12-13 in `docs/design/v2-provenance.md` section 17 are covered by the W1-W9 implementation and green W9 receipt: no tool-name-only events, parser-miss loss, no-op false positives, stale verification, Git/non-git drift, migration corruption, concurrency loss, adapter replay mismatch, Windows collision overwrite, or premature migration trigger; runtime remains stdlib-only.
- Gate 11 is green under the rev3 3-AI SLO reconsensus: representative 1k retains 200ms/1,000ms metadata/full p95 budgets, while extreme 10k uses measured 1,000ms/6,000ms budgets after a 1.48s isolated Windows lower-bound probe; native scanning remains rejected and the workload was not reduced.
- The final receipt records independent 1k and 10k hard gates, clean fast-path reads of 0 bytes, and aggregate green status. With the green W9 receipt, `record_event()` now permits the one-shot v1 ledger migration path.

## [1.2.0] - 2026-07-12

### Added — Evidence integrity, host conformance, and release SSOT

- P0 Evidence Integrity adds conservative Antigravity verification-result parsing, monotonic ledger sequences with verification epochs, and a self-locating Codex installer whose four hooks work from projects outside the fable-lite checkout.
- Three-host Stop conformance now replays real Claude Code Stop, Codex CLI Stop, and Antigravity AfterAgent payload shapes for the shared two-block cap, fresh-verification recovery, and N1-marker recovery. Antigravity coverage is payload injection only, not proof that its host hook engine fires.
- `.claude-plugin/plugin.json` is the version SSOT. The stdlib-only `scripts/sync_version.py` synchronizes marketplace metadata, `pyproject.toml`, and both README badges; `--check` reports drift without writing, and release hygiene compares every version surface.
- The classifier corpus distinguishes bare `next.js`/`node.js`/`vue.js` technology names from explicit file references and no longer treats `생성` alone as an observable artifact request. Real filenames and HTML/page/game/chart/SVG/UI targets retain their existing behavior.

### Fixed — P4 review hardening

- Applied nine review fixes: explicit Antigravity result signals now outrank ANSI-stripped text fallback; support checks reuse epoch-aware verification; docs changes do not advance the verification epoch; agent-log locks verify ownership; malformed tool names fall through to the next authoritative payload source; Codex installation refuses to overwrite an existing hooks file; failed Codex verification blocks Stop; fresh Antigravity verification allows Stop; and externally installed Codex hooks exercise the unverified-change block path.
- Antigravity payload handling now covers the observed `tool_response` result shape and string boolean signals while preserving conservative fallback when explicit result fields are absent.

### Known Limitations

- Antigravity 1.1.1 did not invoke the configured hook process in six live installation attempts. Its adapter remains tested through deterministic payload injection until the host exposes a working live hook engine; see `docs/reviews/p9-agy-live-hooks.md`.
- Moving the Stop block-counter update into the shared ledger transaction remains deferred to v2.0. Current per-file lock ownership is hardened, but multi-process Stop-counter serialization is outside v1.2 scope.

## [1.1.3] - 2026-07-08

### Changed — N1 markers only on turns that change files (user feedback)

- The N1 investigation-marker gate now applies only to turns that actually modified files. Answer-only turns ("why doesn't this work?") reply in plain prose — no `가설/증거/기각` block demanded. Rationale: the pack's purpose is "investigate before you modify"; forcing markers on a turn that modifies nothing is friction, not discipline.
- Ledger change/verification records are now reset per prompt (turn-scoped). Without this, one early edit kept `changed=True` for every later question turn in the session — an adversarial review (Critical-1) caught that the relaxation would have been ineffective before it shipped.
- Packs (ko/en) document the exemption, and still recommend recording markers for investigation-only turns so a later fix turn doesn't retro-invent its investigation.
- Known accepted limitation (documented, backlog): file edits made via raw shell commands are not counted as changes, so such turns bypass N1 — fable-lite's threat model is carelessness, not an adversarial model deliberately dodging its own gates.
- pytest 101 (4 new: exemption x2, per-turn reset, changed-turn still blocks), probes 15/15.

## [1.1.2] - 2026-07-08

### Changed — report style guidance (user feedback)

- Investigation packs (ko/en) gained a "Report style: body for humans, markers for the gate" section: the final report must lead with a plain-language body (no code identifiers/paths/line numbers; readers may be non-developers), with the marker block compressed to one line each at the bottom. Rationale: the N1 gate only parses marker existence, but models were letting the marker format drag the whole report into a technical document — forcing non-developer users to ask follow-up questions.
- N1 block message now carries the same hint ("markers are one compact line each at the bottom; keep the body in plain language"), so a blocked model rewrites toward a readable report instead of a more technical one.
- Gate logic unchanged: pytest 97, probes 15/15.

## [1.1.1] - 2026-07-08

### Fixed — briefing false positives (real-world defect)

- Worker boot/role-injection prompts ("[부팅] ... read MEMORY.md and X.md ... then stand by") were classified as multi-story work: the two memory file paths alone tripped `needs_goals`, so the PreToolUse gate blocked the worker's first Bash/Edit calls twice and wasted 1-2 minutes per boot (observed live on a Sonnet worker pane).
- New briefing detection: a boot marker ("[부팅]", "세션 부팅", ...) or a stand-by closing ("대기하라", "standby", ...) suppresses `needs_goals`/multi-story/artifact signals — but only when no imperative action follows. Imperative-suffix matching keeps rule phrases ("...만 수정", "sentinel 파일 생성.") from counting as actions, while "[부팅] ... auth.py 고쳐줘" still gates (adversarial-review bypass guard).
- `is_debug` and `risk_flags` are intentionally NOT suppressed inside briefings: a boot prompt carrying a dangerous command still hits the R1 contract gate.
- Path mention counting no longer counts version strings (`v1.1.0`) or bare domains (`google.com`) as file paths.
- Word-boundary matching for multi-story terms: `and` no longer matches inside `commands`/`sandbox`; `여러분`, `12개월`, `multiply` no longer false-positive.

### Evidence

- Reproduced both reported boot prompts before the fix (`needs_goals=True`, spurious verification pack), confirmed suppression after (`needs_goals=False`, no packs), and confirmed the enumeration regression probe (PRB03) still blocks real multi-story prompts.
- pytest 92 (10 new classification tests), deterministic probes 15/15, live-payload hook E2E: boot prompt → Bash allowed; multi-story prompt → Edit blocked.

## [1.1.0] - 2026-07-07

### Added — Intent Gate ("알" gate)

- Ambiguity scoring (`core/ambiguity.py`): signal-count score (0-4) with a conservative threshold of 2 and hard never-flag guards (questions, explicit paths, existing goals/intent, "그냥 해"). Scoring→threshold-gating methodology adapted from [Yeachan-Heo/gajae-code](https://github.com/Yeachan-Heo/gajae-code) (MIT).
- Intent-interview packs (`packs/intent-interview.ko.md` / `.en.md`): at most 3 questions, one at a time, multiple-choice first; explicit-assumption path (`--assumed`) when the user says "just do it".
- `fable_lite intent set/show/clear` CLI writing `.fable-lite/intent.json`; a new prompt auto-clears the previous intent.
- PreToolUse edit-lock: when a prompt is flagged ambiguous, Edit/Write/patch tools are blocked until `intent.json` exists (2-block cap, fail-open; Bash/read tools stay allowed so investigation can proceed).
- Self-locating launcher `fable-lite-cli.py` + absolute-path command injection: hooks compute their own install location and embed a copy-paste-runnable command into block reasons and injected context, so recovery works in plugin installs without `pip` and without relying on model path-hunting.

### Evidence

- Ambiguity corpus (41 real-style Korean prompts, adversarially authored by a different model): accuracy 100% — 0 false positives, 0 false negatives after a targeted fix round (initial run: FP 0, FN 7).
- Live nested-session E2E: ambiguous prompt → questions first and Edit genuinely blocked; clear prompt → zero added friction; "그냥 알아서" → assumption declared and recorded with `--assumed`; after the launcher fix, the model copy-executed the injected command verbatim and produced a standard-schema `intent.json` with no workarounds.

### Fixed

- BLOCKER (found in live E2E): `python -m fable_lite` failed with `ModuleNotFoundError` from arbitrary project directories under plugin installs — resolved via the self-locating launcher plus injected absolute-path commands.
- Verification OK-signal matching now uses word-boundary regex (`\bok\b`, case-insensitive): recognizes leading `ok ...` and `OK: ...`, with a regression test proving `broken` does not false-positive.

### Known Limitations

- Value-dump verification output with no pass/ok token (e.g. `add(2,3) = 5`) is conservatively treated as unverified — the safe direction (gate gets stricter, never looser).
- `확인질문 N:` marker adherence varies turn-to-turn; the gate is deterministic on `intent.json` file existence, markers are guidance.

## [1.0.0] - 2026-07-07

### Stabilized

- Core discipline engine: Korean-first prompt classification, investigation compliance markers, evidence-gated Stop checks, scope warnings, high-risk contract gating, ledger persistence, and goals checkpoints.
- Three adapter surfaces: Claude Code, Codex CLI, and Antigravity thin wrappers around the shared core behavior.
- Orchestrator CLI: `python -m fable_lite check` and `python -m fable_lite brief`, including task-card based delegation checks.
- Evaluation surface: deterministic probe runner, smoke harness, A/B methodology documents, and summarized review reports.

### Evidence

- Controlled A/B evaluation: fable-lite ON won 5/5 tasks against OFF in the blind-judged report.
- Live 3-CLI observation: Claude Code, Codex CLI, and Antigravity adapter paths were exercised as practical hook surfaces.
- Natural-compliance experiment: pack-only behavior failed to reliably enforce investigation markers, while the Stop gate recovered runs with concrete evidence.

### Known Limitations

- Verification recognition is centered on Python, JavaScript, shell, and common build/test runner patterns. Other ecosystems may need additional adapter policy.
- Evaluation sample sizes are intentionally small; the reports support release readiness, not statistical generalization.
- Sonamu Bot deployment assumes a host with Python available on `PATH` because hooks are stdlib Python scripts.
- fable-lite reproduces procedural discipline only. It does not claim to reproduce Fable 5 model-weight capabilities.

## [0.6.x] - 2026-07

- Added orchestrator `check` and `brief` commands, including task-card integration.
- Added Codex CLI and Antigravity adapters alongside the Claude Code adapter.
- Added deterministic probe runner and JSON result output.

## [0.5.x] - 2026-07

- Hardened adapter payload parsing, fail-open behavior, N1 investigation compliance wiring, and Stop gate sharing.
- Added regression tests for realistic nested tool payloads and malformed hook inputs.

## [0.4.x] - 2026-07

- Added compliance, scope, and high-risk contract modules.
- Added bilingual investigation marker parsing and pack alignment checks.

## [0.3.x] - 2026-07

- Added ledger, verification state, and goals checkpoint CLI behavior.
- Added tests for Stop blocking limits and verification recording.

## [0.2.x] - 2026-07

- Added Korean prompt classification patterns and initial pack routing.
- Added Claude Code hook scaffold and plugin manifest wiring.

## [0.1.x] - 2026-07

- Project bootstrap, research notes, initial architecture, and v1 contract drafts.

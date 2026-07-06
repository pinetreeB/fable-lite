# fable-lite v5 TaskCard Code Quality Re-Review

## Verdict

- codeQualityStatus: CLEAR
- recommendation: APPROVE
- reviewed scope: `fable_lite/card.py`, `fable_lite/check_support.py`, `fable_lite/check.py`, `fable_lite/brief.py`, `fable_lite/cli.py`, `tests/test_fable_lite_cli.py`, `README.ko.md`, `tmp/codex-ulw-done9-notepad.md`
- blockers: none

## Skill Perspective Check

- `code-review` skill loaded.
- `ultrawork` skill loaded because the prompt/hook activated `ulw`; applied as lightweight evidence discipline only because this is read-only review.
- `programming` skill and `references/python/README.md` loaded before judging Python maintainability and test relevance.
- `remove-ai-slops` skill loaded and applied as a read-only slop/overfit pass over production and tests.
- Result: no CRITICAL or HIGH violation. LOW residual slop remains: an unused helper from the previous root-broadening approach, plus a small automated coverage gap noted below.

## Evidence Inspected

- `git status --short`: repository has unrelated dirty state outside the requested review scope; ignored for this review.
- `git diff -- fable_lite/card.py fable_lite/check_support.py fable_lite/check.py fable_lite/brief.py fable_lite/cli.py tests/test_fable_lite_cli.py README.ko.md tmp/codex-ulw-done9-notepad.md`: inspected scoped diff plus full bodies of untracked `card.py` and `check_support.py`.
- `tmp/codex-ulw-done9-notepad.md`: inspected claimed RED/GREEN, review-fix notes, and verification evidence.
- `C:/Users/rotat/.claude/scripts/TaskCard.psm1`: checked TaskCard field names and required fields against parser assumptions.

## Verification Re-Run

- `python -m pytest tests/test_fable_lite_cli.py -q`: PASS, 9 passed.
- `python -m pytest tests/`: PASS, 50 passed.
- `python -m py_compile @files` with PowerShell-expanded `fable_lite/*.py` and `tests/test_fable_lite_cli.py`: PASS.
- `python -m basedpyright --level error fable_lite tests/test_fable_lite_cli.py`: PASS, 0 errors, 0 warnings, 0 notes.
- Manual CLI QA in temp git repo:
  - `python -m fable_lite brief --card <card>` included both `tmp/.done-green` and `tmp/.sentinel-green`.
  - `python -m fable_lite check --card <card>` GREEN case exited 0.
  - case-insensitive forbidden path `Secrets/token.txt` vs `secrets/**` exited 1 and reported forbidden.
  - temp repo cleanup verified.

## Prior Blocker Verification

1. allowed_paths glob broadening: fixed. `check.py:136-140` delegates card scope to `card_scope_findings`; `card.py:98-105` checks changed files against original allowed patterns, and `card.py:179-196` matches normalized POSIX path segments. Regression test: `tests/test_fable_lite_cli.py:193-206`.
2. stale legacy ledger verification: fixed for valid generated cards. `card.py:108-115` disables legacy fallback when `card.verify` is present and requires an agent JSONL; `card.py:118-144` requires a post-card timestamp, exact command, and `success is True`. Regression test for no-agent legacy ledger: `tests/test_fable_lite_cli.py:209-222`.
3. `brief --card` completion paths: fixed. `brief.py:38-56` prints every `card.completion_paths()` item, and `card.py:38-43` deduplicates done artifact and sentinel. Regression test: `tests/test_fable_lite_cli.py:225-245`.

## CRITICAL

None.

## HIGH

None.

## MEDIUM

None.

## LOW

1. `fable_lite/card.py:61` and `fable_lite/card.py:158` - unused `scope_roots()` / `_scope_root()` remain after switching away from root-broadened card scope.
   - Risk: dead code preserves the old mental model that caused blocker 1, so it can confuse future maintenance.
   - Severity rationale: no caller uses it, and runtime behavior is unaffected.

2. `tests/test_fable_lite_cli.py:159-245` - automated card tests cover the fixed RED regressions and `brief --card`, but the card GREEN happy path currently relies on manual CLI QA rather than a committed pytest case.
   - Risk: a future change could make all card checks reject while the current blocker-focused tests still pass.
   - Severity rationale: manual CLI QA was reproduced successfully, and the current blocker tests are meaningful, not tautological.

3. `fable_lite/check.py:184`, `fable_lite/card.py:108` - `ledger` parameters are carried through `verify_findings()` / `card_verify_success()` but are not used.
   - Risk: minor API noise.
   - Severity rationale: typecheck passes and behavior is unaffected.

## Scope And Maintainability Notes

- No deletion-only, removal-only, tautological, or implementation-constant-only tests found in the reviewed test additions.
- Tests exercise observable CLI output and exit codes, not internal helper calls.
- No new dependency, global suppression, skipped test, `xfail`, `type: ignore`, or `Any` escape hatch found in the reviewed scope.
- Production line counts are below the 250 pure-LOC threshold: `card.py` 165, `check_support.py` 79, `check.py` 175, `brief.py` 65, `cli.py` 25, `tests/test_fable_lite_cli.py` 191.
- Actual adapter-side production of agent JSONL was treated as outside this review scope. The reviewed `check --card` path correctly requires that evidence and fails closed without it.

## Final Recommendation

APPROVE. The three prior blockers are fixed in the reviewed scope, verification was reproduced, and remaining concerns are LOW maintainability items only.

# fable-lite v5 TaskCard Context Re-review Gate

recommendation: REJECT

## blockers

- B1. Malformed TaskCards fail open. `TaskCard.psm1` generates mandatory `owner`, `allowed_paths`, `verify`, `done_artifact`, and emitted `sentinel` fields, but `fable_lite/card.py:46-57` converts missing card fields to empty strings/lists. Direct CLI counterexamples returned `GREEN` / exit 0 for a card missing `verify`, and for a card missing `allowed_paths` plus completion fields. This is a boundary parse gap: `check --card` can approve an invalid card instead of failing closed.
- B2. Unresolved production slop remains after the fix. `fable_lite/card.py:61-67` `scope_roots()` and `fable_lite/card.py:158-164` `_scope_root()` are no longer called after direct card-glob matching replaced root broadening. `rg -n "scope_roots|_scope_root" fable_lite tests` found only their definitions/call between themselves. This is dead production code under the `remove-ai-slops` review criterion.
- B3. No post-fix general code-review report artifact supports the current implementation. The available general review report `.omo/evidence/fable-lite-v5-taskcard-code-review.md` is pre-fix and still requests changes. `.omo/evidence/fable-lite-v5-taskcard-security-rereview-code-review.md` is post-fix but security-scoped only.

## originalIntent

Re-review the fable-lite v5 TaskCard integration after fixes, read-only, and decide whether missed local TaskCard/wmux context still invalidates the implementation.

## desiredOutcome

`brief --card` and `check --card` should align with local `TaskCard.psm1`: card allowed paths drive scope, forbidden paths are matched safely, exact post-card verify evidence is required, and both completion paths (`done_artifact` and `sentinel`) are surfaced/checked without treating sentinel presence as verification evidence. The implementation should remain scoped outside core/packs/adapters/eval changes for this task.

## userOutcomeReview

The named prior context blocker is fixed for valid TaskCard-generated cards: `brief --card` now emits both `tmp/.done-card` and a TaskCard-style absolute sentinel path, and `check --card` requires both completion paths. Direct CLI QA also confirms the fixed allowed-glob, casefold-forbidden, and post-card exact-verify semantics.

The shipped artifact still cannot receive a final gate PASS because malformed TaskCards are accepted as green instead of rejected at the boundary. This does not break the happy path for a card produced by `TaskCard.psm1`, but it leaves the TaskCard contract optional in the Python parser and can produce false completion if the card file is hand-written, truncated, or stale.

## checked artifact paths

- `C:/Users/rotat/.claude/scripts/TaskCard.psm1`
- `docs/design/wmux-orchestration.md`
- `README.md`
- `README.ko.md`
- `fable_lite/card.py`
- `fable_lite/check_support.py`
- `fable_lite/check.py`
- `fable_lite/brief.py`
- `fable_lite/cli.py`
- `tests/test_fable_lite_cli.py`
- `tmp/codex-ulw-done9-notepad.md`
- `.omo/evidence/fable-lite-v5-taskcard-code-review.md`
- `.omo/evidence/fable-lite-v5-taskcard-context-gate-review.md`
- `.omo/evidence/fable-lite-v5-taskcard-security-code-review.md`
- `.omo/evidence/fable-lite-v5-taskcard-security-rereview-code-review.md`
- `.omo/evidence/fable-lite-v5-taskcard-cli-gate-review.md`
- `.omo/evidence/fable-lite-v5-taskcard-cli/notepad.md`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-brief-card.txt`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-check-card-red.txt`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-test-pytest.txt`

## direct evidence

- `TaskCard.psm1`: `New-TaskCard` writes `done_artifact` and `sentinel`; `Test-TaskDone` checks the sentinel.
- Current tests: `tests/test_fable_lite_cli.py:177-190` covers case-insensitive forbidden; `193-206` covers allowed glob non-broadening; `209-222` covers pre-card legacy verify rejection; `225-245` asserts `brief --card` includes both done artifact and sentinel.
- Verification rerun:
  - `python -m pytest tests/test_fable_lite_cli.py -q` -> 9 passed.
  - `python -m pytest tests/ -q` -> 50 passed.
  - `python -m basedpyright --level error fable_lite tests/test_fable_lite_cli.py` -> 0 errors, 0 warnings, 0 notes.
  - `python -m py_compile fable_lite/card.py fable_lite/check_support.py fable_lite/check.py fable_lite/brief.py fable_lite/cli.py tests/test_fable_lite_cli.py` -> exit 0.
- Manual valid-card CLI QA in temp git repos:
  - `brief --card` exit 0 and contained `tmp/.done-card`, `C:/Users/rotat/.claude/tmp/.done-card`, allowed/forbidden paths, verify command, and owner.
  - `check --card` GREEN exit 0 with exact post-card verify and both completion files.
  - `check --card` RED exit 1 for `Secrets/token.txt` against `secrets/**`.
  - `check --card` RED exit 1 for `src/secrets.txt` against `allowed_paths=["src/*.py"]`.
  - `check --card` RED exit 1 for pre-card legacy verification.
  - `check --card` RED exit 1 when only `done_artifact` exists and `sentinel` is missing.
- Manual malformed-card counterexamples:
  - Missing `verify` field, with successful verification in the ledger: `check --card` returned `fable-lite check: GREEN`, exit 0.
  - Missing `allowed_paths`, `done_artifact`, and `sentinel`, with changed `outside.py` and exact verification: `check --card` returned `fable-lite check: GREEN`, exit 0.
- Scope check:
  - `git diff --name-only` shows task files plus unrelated dirty files in `adapters/`, `eval/`, and docs/reviews. Per user instruction, those were treated as pre-existing/separate because the stated TaskCard implementation scope did not include them.

## exact evidence gaps

- No current post-fix general code-review artifact shows updated `programming` plus `remove-ai-slops` coverage for the final diff. Existing general review is pre-fix.
- No regression test covers malformed/incomplete TaskCard JSON failing closed.
- No cleanup/removal was done for dead `scope_roots/_scope_root` because this was a read-only review.

## remove-ai-slops and programming pass

- Tests are not deletion-only or tautological for the named fixed cases; they exercise CLI-observable outputs and exit codes.
- The new `card.py` parser does not fully follow parse-don't-validate at the TaskCard boundary because required schema fields become empty values.
- Dead production helper code remains after the allowed-glob fix, creating maintenance burden without behavior.

# fable-lite v5 TaskCard Context Gate Review

recommendation: REJECT

blockers:
- `brief --card` and `check --card` disagree on real TaskCard completion paths. `TaskCard.psm1` writes both `done_artifact` and `sentinel`; `Test-TaskDone` checks the sentinel. The implementation tells the worker to create only `done_artifact` (`fable_lite/brief.py:39`, `fable_lite/brief.py:54-55`, `fable_lite/brief.py:72-75`) while `check --card` requires both `done_artifact` and `sentinel` (`fable_lite/card.py:37-42`, `fable_lite/card.py:77-83`). A worker following the generated brief can fail the card check even after the required verify command succeeds.

originalIntent:
- Add v5 TaskCard integration to the v4 `fable_lite` orchestrator CLI without editing forbidden core/packs/adapters/eval surfaces.
- Align with prior v4 CLI design and `C:/Users/rotat/.claude/scripts/TaskCard.psm1`.

desiredOutcome:
- `brief --card <card.json>` should generate instructions that are sufficient for a worker to satisfy `check --card <card.json>`.
- Card fields should drive scope, forbidden path checks, required verify evidence, and completion artifact checks without treating sentinel presence as verification evidence.

userOutcomeReview:
- The shipped surface does not satisfy the user-visible TaskCard workflow. A real card generated from the local schema contains both `done_artifact` and `sentinel`; the brief currently surfaces only `done_artifact`, but the checker blocks if the omitted sentinel is missing.
- Direct reproduction in a temp repo: with a TaskCard containing both paths, a successful matching verification record, and only the brief-instructed `done_artifact` present, `python -m fable_lite check --card <card> --root <tmp>` returned RED / exit 1 solely for missing sentinel.

checked artifact paths:
- `C:/Users/rotat/.claude/scripts/TaskCard.psm1`
- `docs/design/wmux-orchestration.md`
- `README.ko.md`
- `README.md`
- `fable_lite/card.py`
- `fable_lite/check_support.py`
- `fable_lite/check.py`
- `fable_lite/brief.py`
- `fable_lite/cli.py`
- `tests/test_fable_lite_cli.py`
- `tmp/codex-ulw-done9-notepad.md`
- `.omo/evidence/fable-lite-v5-taskcard-cli/notepad.md`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-brief-card.txt`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-check-card-red.txt`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-test-pytest.txt`
- `.omo/evidence/fable-lite-v5-taskcard-cli/cleanup.txt`

exact evidence gaps:
- No test covers a real TaskCard with both `done_artifact` and `sentinel` where the worker follows `brief --card` output.
- `tests/test_fable_lite_cli.py:211-230` asserts that the brief includes `tmp/.done-card`, but does not assert that the card sentinel path is included.
- Executor evidence `.omo/evidence/fable-lite-v5-taskcard-cli/tc-brief-card.txt` likewise shows only the relative done artifact path being instructed.
- No independent code-review report was found that explicitly covers `remove-ai-slops` overfit/slop criteria and `programming` criteria. Direct pass found a missing non-overfit regression for the sentinel/done-artifact contract.

slop_and_programming_pass:
- Focused pytest evidence exists: `tests/test_fable_lite_cli.py` passed 6 tests.
- Tests are not deletion-only or tautological overall, but the new brief-card test is too weak for the local schema because it does not assert the sentinel path.
- The production split into `card.py` and `check_support.py` is not by itself a blocker, but it did not prevent the key contract mismatch above.

unrelated_dirty_files:
- Git status also shows untracked/dirty `.omo/evidence/fable-lite-v5-taskcard-cli/*`, `adapters/antigravity/*`, `docs/reviews/e1b-repeat.md`, `eval/ab-repeat/*`, and `tests/test_antigravity_adapter.py`. These were not counted as the TaskCard implementation violation because the assignment scoped changed files separately.

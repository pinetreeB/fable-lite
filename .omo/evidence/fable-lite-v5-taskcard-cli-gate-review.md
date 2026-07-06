# fable-lite v5 TaskCard CLI Gate Review

## recommendation

APPROVE

## blockers

None.

## originalIntent

Integrate `fable_lite check` and `brief` with wmux TaskCard JSON from
`TaskCard.psm1`. The requested implementation outcome was:

- add `check --card` and `brief --card`
- use TaskCard `allowed_paths` as the direct scope source
- enforce `forbidden_paths`
- require exact `verify` success for the card
- enforce `done_artifact` and `sentinel` completion paths
- preserve legacy non-card modes
- add regression tests
- add one Korean README usage line
- do not modify `core/`, `packs/`, `adapters/`, or `eval/` for this task

The malformed-card follow-up required incomplete cards to fail closed instead
of silently falling back to legacy behavior. Required fields checked are
`slug`, `owner`, non-empty `allowed_paths`, `verify`, and `done_artifact`.

## desiredOutcome

From the user's perspective, a Claude/Codex/Antigravity worker can be delegated
with a TaskCard, receive a brief generated from that card, and later be gated by
`check --card` so that out-of-scope edits, forbidden edits, missing exact
verification, and missing completion artifacts produce RED. Existing legacy CLI
usage must still work.

## userOutcomeReview

PASS. The scoped implementation now satisfies the requested user-visible
behavior:

- `brief --card` reads the card and prints card allowed paths, forbidden paths,
  verify command, owner-derived target, `done_artifact`, and `sentinel`.
- `check --card` defaults root to cwd, owner to card owner, and since-file to
  the card path.
- Direct card glob matching is used for `allowed_paths`; the stale root
  broadening helper is absent.
- Forbidden matching is case-insensitive after normalized POSIX path handling.
- Card verification requires post-card agent JSONL evidence with the exact
  command and `success is True`; legacy ledger success is not accepted for cards.
- Incomplete cards are RED/rejected through `card_validation_findings`.
- Legacy `check` and `brief` paths are still covered by tests.

Current dirty files outside the requested implementation scope remain an
unrelated residual risk, not an implementation blocker for this review. The
dirty state includes `README.md`, `adapters/`, `docs/reviews/`,
`eval/`, `tests/test_adapters.py`, `tests/test_antigravity_adapter.py`, and
other `.omo` evidence paths. The reviewed implementation scope itself is the
file list requested by the user.

## checkedArtifactPaths

- `fable_lite/card.py`
- `fable_lite/check_support.py`
- `fable_lite/check.py`
- `fable_lite/brief.py`
- `fable_lite/cli.py`
- `tests/test_fable_lite_cli.py`
- `README.ko.md`
- `tmp/codex-ulw-done9-notepad.md`
- `.omo/evidence/fable-lite-v5-taskcard-code-quality-rereview-code-review.md`
- `.omo/evidence/fable-lite-v5-taskcard-cli/notepad.md`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-brief-card.txt`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-check-card-red.txt`
- `.omo/evidence/fable-lite-v5-taskcard-cli/tc-test-pytest.txt`
- `C:/Users/rotat/.claude/scripts/TaskCard.psm1`

## directVerification

- `python -m pytest tests/test_fable_lite_cli.py -q`: PASS, 10 passed.
- `python -m pytest tests/`: PASS, 51 passed.
- `python -m basedpyright --level error fable_lite tests/test_fable_lite_cli.py`:
  PASS, 0 errors, 0 warnings, 0 notes.
- `python -m py_compile @files` after PowerShell file expansion for
  `fable_lite/*.py` and `tests/test_fable_lite_cli.py`: PASS.
- Manual CLI QA in a temp git repo:
  - incomplete card with only `owner`: `check --card` exited 1 and rendered RED
    with task-card errors for missing `slug`, `verify`, `done_artifact`, and
    `allowed_paths`.
  - same incomplete card: `brief --card` exited 1 with a task-card error.
  - valid card: `brief --card` exited 0 and included both `tmp/.done-green` and
    `tmp/.sentinel-green`.
  - valid card with post-card exact agent JSONL verify and completion files:
    `check --card` exited 0/GREEN.
  - casefold forbidden scenario: `Secrets/token.txt` with forbidden
    `secrets/**` exited 1/RED and reported `forbidden_paths`.
  - temp QA directory cleanup was verified.

## skillPerspectiveAndSlopPass

Required review criteria were directly applied:

- `remove-ai-slops`: production and tests were checked for excessive/useless
  tests, deletion-only tests, tests that merely verify removal, tautological
  tests, implementation-mirroring tests, unnecessary extraction, stale helpers,
  and false-confidence coverage. No blocking slop found.
- `programming`: Python code was checked against the loaded Python guidance:
  typed boundary parsing, no `Any`/ignore suppressions, no broad catch in the
  reviewed implementation, file-size limits, test shape, and strict typecheck.
  No blocking issue found.

The code-quality rereview report explicitly documents the same skill coverage:
it states that `programming` plus `references/python/README.md` were loaded, and
that `remove-ai-slops` was applied as a read-only slop/overfit pass over
production and tests. It also records that no deletion-only, removal-only,
tautological, or implementation-constant-only tests were found.

Residual nonblocking notes:

- `card_verify_success` and `verify_findings` retain unused `ledger` parameters.
  This is minor API noise and did not affect behavior, tests, or typecheck.
- Existing saved CLI evidence under
  `.omo/evidence/fable-lite-v5-taskcard-cli/` contains older focused-test counts
  from before the malformed-card fix. This gate review supersedes it with fresh
  direct reruns listed above.

## exactEvidenceGaps

No completion-blocking evidence gap remains for the requested implementation.

Nonblocking gaps:

- The latest manual QA transcript is recorded in this gate review, not in a
  separate per-scenario text file under `.omo/evidence/fable-lite-v5-taskcard-cli/`.
- Current worktree has unrelated dirty files outside the requested scope.

## finalDecision

APPROVE

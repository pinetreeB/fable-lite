# fable-lite v5 TaskCard Code Review

## Review Scope
- Files reviewed: `fable_lite/card.py`, `fable_lite/check_support.py`, `fable_lite/check.py`, `fable_lite/brief.py`, `fable_lite/cli.py`, `tests/test_fable_lite_cli.py`, `README.ko.md`, `tmp/codex-ulw-done9-notepad.md`
- Notepad inspected: `tmp/codex-ulw-done9-notepad.md`
- Out-of-scope dirty state observed but not reviewed for implementation correctness: untracked `adapters/antigravity/`, `eval/ab-repeat/`, `tests/test_antigravity_adapter.py`

## Skill Perspective Check
- `remove-ai-slops`: loaded and applied as a review lens. The tests do not look deletion-only or tautological overall, but they miss the key negative allowed-path and stale-verification cases below.
- `programming`: loaded with Python reference README and applied as a review lens. The implementation has a blocking scope-correctness bug and a timestamp verification bug; no untyped escape hatch was found in the reviewed paths.
- `code-review`: loaded and applied for severity structure.

## Verification Run
- `python -m pytest tests/test_fable_lite_cli.py -q` -> PASS, 6 passed.
- `python -m basedpyright --level error fable_lite tests/test_fable_lite_cli.py` -> PASS, 0 errors.
- `python -m pytest tests/ -q` -> PASS, 46 passed.
- Manual scenario `allowed_paths=["src/*.py"]`, changed `src/secrets.txt`, exact verify and artifacts present -> actual `check --card` exit 0 GREEN. Expected RED.
- Manual scenario stale global ledger verify before card creation, changed `app.py` after card, artifacts present, no post-card verify -> actual `check --card` exit 0 GREEN. Expected RED.

## CRITICAL

(none)

## HIGH

1. `fable_lite/check.py:141` + `fable_lite/card.py:155`
   Issue: Card `allowed_paths` glob patterns are reduced to directory roots before scope evaluation. For example, `src/*.py` becomes `src`, so `src/secrets.txt` is accepted as in scope.
   Risk: A TaskCard can claim a narrow glob/file class while `check --card` permits unrelated files under the same directory. This violates the requirement that the card drive allowed-path scope.
   Evidence: Manual temp repo with `allowed_paths=["src/*.py"]` and changed `src/secrets.txt` returned GREEN with changed count 1.
   Required fix: For card mode, check changed paths against the card patterns directly, using normalized repo-relative glob matching, instead of converting globs to broad roots.

2. `fable_lite/card.py:100`
   Issue: If the card owner has no agent JSONL log, `card_verify_success` falls back to `ledger["verification_results"]`, which has no timestamp. A successful matching verification from before the card was created is accepted for card completion.
   Risk: `check --card` can approve changed files without any post-card execution of the exact verify command. This violates the exact verify and timestamp semantics.
   Evidence: Manual temp repo recorded global verification before card creation, then changed `app.py` after card creation; `check --card` returned GREEN.
   Required fix: In card mode, require a post-card timestamped verification event. If legacy ledger entries lack timestamps, treat them as insufficient for card verify or persist timestamps in the ledger.

## MEDIUM

1. `fable_lite/brief.py:39` + `fable_lite/card.py:37`
   Issue: `brief --card` tells the worker to create only `done_artifact` when both `done_artifact` and `sentinel` exist, while `check --card` requires both completion paths.
   Risk: A worker following the generated brief can still fail the subsequent check for the omitted sentinel. This is a workflow self-inconsistency.
   Evidence: A card with `done_artifact="tmp/.done-card"` and `sentinel="tmp/.sent-card"` produced brief output naming only `tmp/.done-card`.
   Required fix: Either brief both required completion paths or make check require the same single completion path that brief instructs.

## LOW

1. `tests/test_fable_lite_cli.py:173`
   Issue: The new card tests cover forbidden paths and wrong verify command, but not allowed-path negative matching or stale pre-card verification.
   Risk: The current green tests give false confidence over the two blocking paths.
   Required fix: Add focused regression tests for allowed glob rejection and pre-card verification rejection.

## Status
- codeQualityStatus: BLOCK
- recommendation: REQUEST_CHANGES
- blockers:
  - Card allowed-path globs are broadened to directory roots and can be bypassed.
  - Card exact verify can be satisfied by stale untimestamped legacy ledger entries.

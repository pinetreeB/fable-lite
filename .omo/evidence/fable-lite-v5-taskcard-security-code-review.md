# fable-lite v5 TaskCard Security Review

## Skill Perspective Check

- `security-best-practices`: loaded. No Python CLI-specific reference exists in the skill references, so this review applied general Python/security review criteria for local CLI tools.
- `programming`: loaded with Python reference. No security decision depends on style alone; the review used its boundary/parsing and strict-path handling perspective.
- `remove-ai-slops`: loaded. The diff does not need a slop cleanup recommendation for this security verdict, but its overfit/slop pass was applied to avoid treating tautological tests or needless complexity as security proof.
- Violations of skill perspective: no command-execution slop found. A path-normalization gap in forbidden path matching violates the expected boundary-normalization discipline.

## Verdict

- codeQualityStatus: BLOCK
- recommendation: REQUEST_CHANGES
- Security-only verdict: FAIL
- Highest severity: HIGH

## CRITICAL

None.

## HIGH

### H1. `forbidden_paths` can be bypassed by path casing on Windows/case-insensitive filesystems

- File: `fable_lite/card.py:87-93`, `fable_lite/card.py:176-179`
- Impact: a TaskCard that forbids `secrets/**` can still pass `fable_lite check` when the changed path is reported as `Secrets/token.txt`. This undermines the stated security goal to avoid editing forbidden directories.
- Cause: `_matches_card_path()` normalizes slashes only, then uses `fnmatch.fnmatchcase()` without case folding or canonical path normalization. `card_forbidden_findings()` trusts that comparison directly.
- Reproduction evidence:
  - Temp repo TaskCard: `allowed_paths=["**"]`, `forbidden_paths=["secrets/**"]`, exact successful verify ledger entry, `tmp/.done-card` present.
  - Changed file: `Secrets/token.txt`.
  - Command: `python -m fable_lite check --root <tmpRoot> --card <card.json>`.
  - Observed: exit `0`, output `fable-lite check: GREEN`, changed file listed as `Secrets/token.txt`, no `forbidden` finding.

## MEDIUM

None.

## LOW

None.

## Positive Security Checks

- No direct execution of card-provided `verify` strings found. `card_verify_success()` compares ledger/agent-log command strings to `card.verify` at `fable_lite/card.py:97-112` and `fable_lite/card.py:127-141`.
- Dynamic proof: a card `verify` string containing a PowerShell marker-write command was checked with `python -m fable_lite check --root <tmpRoot> --card <card.json>`; the marker file was not created (`MARKER_EXISTS=False`).
- Git invocation in `fable_lite/check_support.py:13-21` uses an argument list with `shell=False` default and only runs `git`, not TaskCard verify commands.
- No network clients, external API calls, `eval`, `exec`, or shell=True usage were found in the scoped production files.

## Blocking Issues

- Fix H1 before approval: normalize/canonicalize card forbidden matching consistently with the repository path model, including case-insensitive comparison on Windows/case-insensitive local filesystems and safe handling of `./` or `..` path spellings from ledger entries.

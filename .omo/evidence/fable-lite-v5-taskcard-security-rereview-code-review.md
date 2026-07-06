# fable-lite v5 TaskCard Security Re-review

## Verdict

- codeQualityStatus: CLEAR
- recommendation: APPROVE
- security verdict: PASS
- highest severity: NONE
- source edits made by reviewer: none

## Scope Reviewed

Changed/added files requested by task:

- fable_lite/card.py
- fable_lite/check_support.py
- fable_lite/check.py
- fable_lite/brief.py
- fable_lite/cli.py
- tests/test_fable_lite_cli.py
- README.ko.md
- tmp/codex-ulw-done9-notepad.md

Context read for called API behavior:

- core/ledger.py, specifically agent log path sanitization and event replay.

## Skill-perspective Check

- remove-ai-slops: consulted. No deletion-only tests, tautological tests, implementation-constant mirroring, or unnecessary production parsing/extraction found in the security-relevant changed files.
- programming: consulted, plus Python README. Security-relevant code keeps subprocess use bounded to list-form git invocation and avoids untyped command execution paths. No security-significant untyped escape hatch or brittle prompt-test blocker found.
- security-best-practices: consulted. The available references are web-framework-specific (Django/FastAPI/Flask) and do not directly apply to this local Python CLI; general local-tool security criteria were applied.
- Diff does not violate the remove-ai-slops or programming perspectives for the reviewed security/high-risk local-tool behavior.

## Evidence Inspected

- `fable_lite/card.py:46` reads the card as local JSON via `json.loads(path.read_text(...))`; no dynamic import, eval, exec, shell, network, or external process call is introduced by card loading.
- `fable_lite/card.py:108` through `fable_lite/card.py:115` routes card verification to agent-log lookup only.
- `fable_lite/card.py:130` through `fable_lite/card.py:144` compares `raw.get("command") == card.verify` and `success is True`; the card verify string is not executed.
- `fable_lite/card.py:179` through `fable_lite/card.py:196` normalizes paths with POSIX separators, strips boundary separators, casefolds, and matches segment-by-segment including `**`; this addresses the prior Windows `Secrets/token.txt` vs `secrets/**` blocker.
- `fable_lite/check_support.py:13` through `fable_lite/check_support.py:21` invokes git as `subprocess.run(["git", "-C", str(root), *args], ...)` with `shell=False`; reviewed card fields do not enter a shell command.
- `fable_lite/brief.py:34` through `fable_lite/brief.py:63` renders card fields into delegation text only; it does not execute verify strings.
- `core/ledger.py:56` through `core/ledger.py:61` sanitizes agent names before building `.fable-lite/agents/<agent>.jsonl`, avoiding owner-based path traversal for agent logs.

## Verification Commands Run

- `python -m pytest tests/test_fable_lite_cli.py -q`
  - Result: PASS, 9 passed.
- `python -m pytest tests/`
  - Result: PASS, 50 passed.
- `python -m basedpyright --level error fable_lite tests/test_fable_lite_cli.py`
  - Result: PASS, 0 errors, 0 warnings, 0 notes.
- Manual CLI QA in a temp git repo:
  - Card had `forbidden_paths=["secrets/**"]`; changed file was `Secrets/token.txt`.
  - `python -m fable_lite check --card <card.json>` from the temp repo returned RED with exit 1 and listed `Secrets/token.txt: forbidden_paths`.
  - Separate card used a verify string that would create a marker file if executed. After `brief --card` and `check --card`, marker existence was `False`; check returned exit 1 because only exact log evidence counts.

## Findings by Severity

### CRITICAL

None.

### HIGH

None.

### MEDIUM

None.

### LOW

None.

## Blocking Issues

None.


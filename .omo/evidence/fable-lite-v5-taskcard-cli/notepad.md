# fable-lite v5 TaskCard CLI QA Notepad

Tier: LIGHT - executable read-only CLI QA over an existing implementation; no product edits.
Skills used:
- ultrawork: user prompt included ulw keyword; using lightweight evidence discipline.
- manual QA executor contract: using faithful CLI surface and non-empty artifacts.
Relevant skill note: docs/shared/agent-tiers.md was referenced by ultrawork but not present under the installed skill tree, so no delegation was used.

Acceptance criteria:
1. `python -m pytest tests/test_fable_lite_cli.py -q` passes, covering legacy no-card and card paths.
2. Real CLI `python -m fable_lite brief --card <card.json>` exits 0 and prints card fields.
3. Real CLI `python -m fable_lite check --card <card.json>` exits 1/RED when a forbidden path is touched and required verify command is missing/wrong.

Scenario surfaces:
- CLI test suite: PowerShell invoking Python/pytest.
- CLI manual brief: PowerShell invoking `python -m fable_lite brief --card tmp/qa-taskcard-cli/brief-card.json`.
- CLI manual check RED: PowerShell invoking `python -m fable_lite check --card tmp/qa-taskcard-cli/red-repo/card.json` from the repository root.

Results:
- TC-TEST PASS: pytest focused CLI suite exited 0 with 6 passed.
- TC-BRIEF-CARD PASS: brief --card exited 0 and printed card-derived allowed/forbidden/verify/sentinel/owner fields.
- TC-CHECK-CARD-RED PASS: check --card exited 1/RED for forbidden path touch plus wrong/missing required verify command.
Self-review: LIGHT tier held because this was read-only CLI QA; evidence covers focused regression tests plus actual card CLI surfaces and the riskiest RED case. No product files were edited.
Cleanup: Removed temporary directory: C:\Users\rotat\fable-lite\tmp\qa-taskcard-cli

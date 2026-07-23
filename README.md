# show-me-the-work

**show-me-the-work** (`smtw`) is a local, hook-based work supervisor that checks for actually executed evidence before an AI calls a task done.

[![version](https://img.shields.io/badge/version-2.6.0-brightgreen.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> 🇰🇷 Korean is the primary project language: [README.ko.md](README.ko.md)

## 1. Product in one minute

AI coding agents are capable, but they can edit code without running it, stop
after promising a next step, or report a visual result they never observed.
show-me-the-work watches local hook events, records work evidence, and challenges
an unsupported completion claim.

It makes unverified completion materially harder and observable; it blocks up
to twice, then records a fail-open escape to avoid deadlock. It does not make a
model smarter or turn evidence into proof that the code is correct.

### Why the name?

The project began as **fable-lite**, an experiment in transferring Fable 5's
working discipline to smaller models. Model capability could not be
transplanted, but investigation, verification, and completion discipline could
be implemented as procedure. The v2.0 name describes that narrower product:
when an agent says “done,” **show me the work**—the run, observation, and
evidence.

## 2. Guarantees and non-guarantees

### What it is designed to provide

- Observation of verification commands that actually ran, including their
  success or failure.
- Detection when relevant files changed after the verification being credited.
- Identity-scoped contracts for selected high-risk work.
- R2 protection against destructive work that may erase a peer's unsettled
  changes or protected state.
- At most two completion bounces for ordinary evidence gates, followed by an
  explicit audited fail-open escape.
- Local audit evidence for turns, invocations, paths, goals, scorecards, and
  blocked destructive commands.

### What it does not guarantee

- That code, research content, or a visual result is correct.
- Complete observation of databases, services, network state, or remote side
  effects.
- Complete defense against deliberate arbitrary-code evasion.
- Replacement of human review or operational authorization.
- Live support for every AI host.
- Perfect parsing of every shell, PowerShell, or dynamic language grammar.

The distinction matters: prompts alone produced 0/3 natural compliance in one
live study, while the Stop gate produced 3/3 blocked-then-recovered runs with
real evidence ([report](docs/reviews/p5b-n1-natural.md)). In a five-task blinded
comparison, ON won 5/5 on verification discipline while task correctness was
unchanged ([A/B report](docs/reviews/e1-ab-report.md)). These are small
measurements of behavior, not a general correctness guarantee.

### Host support

| Host | Current status |
|---|---|
| Claude Code | Live hook chain confirmed |
| Codex CLI | Live hook chain confirmed |
| Antigravity | Payload and config-load conformance confirmed on host 1.1.2+; live execution of config-path hooks remains unconfirmed |

## 3. Names, packages, and paths

| Surface | Canonical | Legacy / compatibility |
|---|---|---|
| Product | show-me-the-work | fable-lite (former name) |
| CLI | `smtw` | `fable-lite` |
| Python import | `smtw` | `fable_lite` |
| Distribution | `fable-lite` for now | Same distribution |
| Runtime state | `.smtw/` for new or migrated projects | `.fable-lite/` for unmigrated projects |
| Project config | `.smtw.toml`, then `pyproject.toml` `[tool.smtw]` | `.fable-lite/config.json` fallback |
| Runtime environment | `SMTW_*` | `FABLE_LITE_*` read aliases |

There is one authoritative state tree at a time. A legacy activation config may
remain a fallback config source during the compatibility window even after the
runtime authority has migrated.

## 4. One-minute install

Requirements: Python 3.12+ must be on `PATH`. The runtime is standard-library
only. Project scope is recommended so unrelated projects do not pay Python
startup cost on every host hook event.

```bash
git clone https://github.com/pinetreeB/show-me-the-work
cd show-me-the-work
claude plugin marketplace add .
claude plugin install show-me-the-work@show-me-the-work --scope project
smtw init --root .
smtw doctor --root .
```

`smtw init` refuses the exact user home, never overwrites an existing canonical
or legacy config, does not create runtime state, and does not migrate legacy
state. By default it creates `.smtw.toml`, adds runtime patterns to
`.gitignore`, and points to `smtw doctor`. Use `--config pyproject` to place the
canonical table in `pyproject.toml`, or `--no-gitignore` to leave ignore rules
unchanged.

For a personal uncommitted plugin registration, use Claude's `--scope local`.
A user-scope install is also possible, but inactive projects still start a
Python process before returning an empty hook result. The exact user home is
always inactive even if a config is present.

The initial `cwd fallback is best-effort`, not a security trust boundary.
Before Claude provides `CLAUDE_PROJECT_DIR` or the session root is latched, a
forged hook payload or working directory can steer the initial upward config
search. A mismatched environment root is effective for that hook only when the
selected project has its own exact opt-in config.

## 5. Project configuration

The preferred dedicated config is:

```toml
# .smtw.toml
schema_version = 1
supervision = true
```

The canonical alternative is:

```toml
# pyproject.toml
[tool.smtw]
schema_version = 1
supervision = true
```

Config precedence is strict:

1. `.smtw.toml`
2. `pyproject.toml` `[tool.smtw]`
3. legacy `.fable-lite/config.json`

A declared but invalid higher-precedence config is an error; it does not fall
through to an enabled legacy config. Canonical and legacy runtime environment
variables may coexist only when their values agree. Conflicting values are
fail-closed and `smtw doctor` reports the key names without printing secret raw
values.

### Existing legacy projects

An existing project may keep this compatibility activation file:

```json
{"schema_version": 1, "supervision": true}
```

`smtw init` preserves it and suggests an explicit migration check. Setting
`supervision = false` in canonical config, or removing all activation config,
disables supervision; disabling does not delete existing state.
`SMTW_TEST_FORCE_ENABLE=1` is test-only and must not be used for normal
sessions.

## 6. State authority and migration

`smtw status` and `smtw doctor` expose the current authority:

| Layout | Meaning | Authority / action |
|---|---|---|
| `EMPTY` | No state tree exists | A future active write uses `.smtw/` |
| `LEGACY` | Only an unmigrated legacy tree exists | `.fable-lite/`; migration is optional and explicit |
| `NATIVE` | A native canonical tree exists without a migration marker | `.smtw/` |
| `MIGRATING` | Legacy source plus owned staging state exists | `.fable-lite/`; wait for or investigate the migration |
| `MIGRATED` | A verified published canonical tree exists | `.smtw/`; preserved legacy is not a fallback authority |
| `CONFLICT` | The layout cannot prove one safe authority | No authority; related work blocks or reports degraded |

```bash
smtw status --root .
smtw migrate --root . --check
smtw migrate --root .
smtw doctor --root .
```

Layout migration never runs automatically. `--check` is write-free. Migration
copies, verifies, and atomically publishes a new authority; it defers while an
active turn or open invocation exists. It keeps the source tree after success
for explicit rollback analysis and never silently falls back to it after
publication. State writers share a layout barrier so successful writes are not
lost across the publish boundary.

This layout migration is separate from versioned ledger backfills. Do not use a
ledger migration environment switch as a substitute for `smtw migrate`.

## 7. Quick operational commands

```bash
smtw doctor --root .
smtw doctor --root . --json
smtw status --root .
smtw migrate --root . --check
smtw quarantine list --root .
smtw scorecard --root . --view coordination
smtw goals status --root . --identity <host:session-id:agent>
```

`doctor` reports tool/distribution/module versions, Python, host/plugin/config,
environment conflicts, state authority, migration readiness, active work,
ledger/provenance health, quarantine usage, and probe/host status. Exit codes
are `0` healthy, `1` unsafe/error, and `2` inactive/deferred/action required.
`status` is the short runtime view.

For a multi-story task:

```bash
smtw goals plan --root . --goal "release" --story "verify Windows" --verify-cmd "python -m pytest"
smtw goals verify --root . --story "verify Windows" --evidence "pytest green"
```

For wmux-style delegation, `brief` creates the task discipline block and
`check` compares the ledger with the worktree:

```bash
smtw brief --paths "core/**,tests/**" --verify-cmd "python -m pytest tests/" --sentinel tmp/.done --target codex
smtw check --root . --agent codex --since-file tmp/.delegation-start
```

## 8. Gate behavior

| Gate / boundary | Event | Evidence used | Block cap | Failure policy | Known limitation |
|---|---|---|---|---|---|
| N1 investigation | Prompt routing and Stop | Bilingual hypothesis, evidence, and rejection markers | Shares the Stop cap of 2 | Audited fail-open after cap | Markers prove report structure, not that a hypothesis is true |
| N2 goals / intent / design | Prompt and completion checkpoints | Identity-scoped plan, verification evidence, or clarified intent | 2 per gate | Audited fail-open after cap | A synthetic or foreign identity cannot satisfy another active identity |
| Verification completion | Stop / AfterAgent | Successful command observation covering current changes | 2 | Audited fail-open after cap | A successful test can still be the wrong test |
| R1 high-risk contract | PreToolUse | Evidence-bearing, identity-scoped contract below the authoritative tree | No ordinary cap | Hard block until contract exists | Covers selected risk families; it is not external approval |
| R2 destructive protection | PreToolUse | Parsed targets, logical/resolved candidates, peer ownership, protected state | No cap | Ambiguity and peer risk fail closed | Dynamic execution cannot always be statically decoded |
| Scope drift | PostToolUse | Requested scope versus observed paths | Advisory | Warning, deduplicated per turn | It does not undo an edit |
| Stale mutation | PreToolUse | Current turn identity and lifecycle | No cap | Mutation denies; proven read-only work keeps its existing policy | Old pre-upgrade turns can be hard to distinguish from abandoned work |
| Runtime env conflict | Any activated hook | Canonical and legacy variable presence/value agreement | No cap | Fail closed | Reports names, never secret raw values |
| State layout conflict | State consumer / mutation | Validated layout and migration marker | No cap | No authority; block or degraded report | Recovery may require operator inspection |

General observer and health-recording failures are usually fail-open with a
warning. That does not weaken R2, environment-conflict, or authority-conflict
boundaries. Inline Python path hints are friction, not authorization; R2 remains
the independent boundary.

## 9. Multi-agent operation

- **Identity:** exact identities use `host:session-id:agent`. A sole exact active
  identity can be selected automatically; ambiguity requires `--identity` or
  matching `--host`, `--session-id`, and `--agent`. Synthetic identities are
  never presented as successful ownership.
- **Candidate ownership:** the path declared by a tool is retained as a logical
  project-relative candidate for attribution, while the current resolved path
  is tracked separately for R2 peer matching. Symlink replacement does not
  erase the declared ownership record.
- **Settlement:** a peer's open invocation or unsettled revision must be closed
  or explicitly settled before destructive work can consume it. Ownership is
  not cleared merely by age.
- **R2:** destructive commands are checked against peer candidates and both
  canonical and legacy state paths. An unparseable destructive target is
  denied rather than guessed safe.
- **Goals:** checkpoints live in an identity namespace. Planning for one
  identity cannot recover another identity's N2 gate.
- **Quarantine:** a blocked destructive command is preserved best-effort in the
  authoritative state tree. Preservation success or failure never changes the
  R2 deny decision; operators can list, show, or clear records, but there is no
  automatic apply.
- **Stale turns:** mutation-capable work from a stale turn is denied before an
  invocation is registered. Submit a current prompt to start a current turn.
- **Migration:** do not manually mutate state during migration. The layout
  barrier serializes supported writers, and migration defers while live work is
  visible.

[LazyCodex/OmO](https://github.com/code-yeongyu/oh-my-openagent)'s `ulw` can
drive a task toward completion while show-me-the-work inspects completion
evidence. They are complementary, but neither expands the other's
authorization.

## 10. Compatibility

Legacy state/config/environment reads and the `fable_lite` Python shim remain
available through the v3.x compatibility window. Removal is planned no earlier
than v4; consult the changelog before upgrading. After a state migration is
published, compatibility does not mean per-file fallback to the old authority.

| Legacy surface | Status | Canonical replacement |
|---|---|---|
| `fable-lite` console script | Supported with compatibility status | `smtw` |
| `import fable_lite` and public submodules | Supported aliases with one `DeprecationWarning` per process | `import smtw` |
| `python -m fable_lite` | Supported, deprecated | `python -m smtw` |
| `python -m fable_lite.cli` | Supported physical thin shim | `python -m smtw` |
| `python -m fable_lite.scorecard` | Supported physical thin shim | `smtw scorecard` |
| `python -m fable_lite.migrate` | Supported physical thin shim | `smtw migrate` |
| Other public `fable_lite.<module>` execution | Physical compatibility shim present; CLI-bearing modules delegate to `smtw` | Prefer the top-level `smtw` CLI |

Default warning handling is supported. Explicitly promoting
`DeprecationWarning` to an error with `-W error::DeprecationWarning` or
`PYTHONWARNINGS=error` is intentionally outside the compatibility contract.

In a source checkout, an adjacent `pyproject.toml` plus `.git` makes the source
version authoritative even if an older global distribution is installed.
`smtw doctor` reports module and distribution paths/versions and warns on a
mismatch. In a wheel install, distribution metadata remains authoritative.

## 11. Performance and scope

- Prefer project-scope plugin installation. A global inactive install performs
  no project writes, but Python interpreter startup still occurs for each hook.
- The release envelope is 10,000 observed entries and 256 MiB of regular-file
  content. Full reconciliation has an 8-second cooperative deadline;
  incremental observation has a 2-second deadline.
- Full Stop reconciliation near the envelope can take several seconds. If an
  entry, byte, or time budget is exceeded, the partial snapshot is discarded
  and the turn reports `scope_too_large` instead of claiming complete
  observation.
- Deadlines are checked between filesystem calls and hash chunks. A single
  blocked OS call cannot be preempted by this in-process scanner.
- Layout inspection and `status` are local filesystem operations. `doctor`
  additionally reads local config, ledger, quarantine inventory, and the last
  probe receipt; it performs no network call.

Use focused project roots and explicit provenance exclusions for generated or
vendored trees. An exclusion reduces observation and must be reviewed as a
trust decision.

## 12. Privacy, retention, and deletion

State is local to the project authority, but it can contain sensitive work
context:

- commands and affected paths;
- file digests, invocation and turn metadata;
- prompt-derived intent, goals, and high-risk contract evidence;
- per-agent logs, verification observations, gate journals, and scorecards;
- blocked-command quarantine content.

Do not commit or transmit runtime state by default. `smtw init` proposes ignore
patterns for both canonical and legacy trees. Quarantine is bounded to 64
records, 16 MiB total, and seven days; each stored command is capped at 1 MiB
with original/stored byte counts and SHA-256 metadata when truncated. Other
state remains until project policy or an operator removes it.

```bash
smtw status --root .
smtw quarantine list --root .
smtw quarantine clear --root . --all
```

There is no automatic “reset all state” command. Before manual deletion,
disable supervision, stop active agents, use `status`/`doctor` to identify the
authority, preserve any audit or rollback material you need, and treat
canonical and preserved legacy trees separately. Deleting the wrong tree can
remove evidence or rollback data; disabling supervision alone does not delete
anything.

## 13. Development and verification

The blocking CI matrix runs on Ubuntu and Windows with Python 3.12:

```bash
python scripts/sync_version.py --check
ruff check core adapters fable_lite goals tests eval contrib scripts smtw --exclude eval/ab
python -m pytest tests/ -q
python eval/run_probes.py --strict --output tmp/smtw-probes.json
python eval/e2e_smoke.py
python -m compileall -q core adapters fable_lite goals eval contrib scripts smtw
python -m eval.provenance.run --output tmp/smtw-provenance.json
python -m build --wheel --outdir dist
python scripts/check_wheel_contents.py --wheel-dir dist
```

CI then installs the wheel into a clean Python 3.12 virtual environment and
smokes canonical and legacy module/console entry points. Release-quality also
runs randomized provenance, the 1k/10k performance receipt (non-blocking
because shared runners are noisy), and an eight-process Stop-counter race.

The deterministic probe runner reports automatic pass/fail cases and leaves
model-judged probes as `manual`. Use `--strict` for a non-zero exit on failure
and a scratch `--output` path to avoid replacing a local receipt.

### Credits and license

The investigation, verification, decomposition, and early-stop procedures were
adapted from ideas validated in
[fivetaku/fablize](https://github.com/fivetaku/fablize) (MIT). The intent
interview method was adapted from
[Yeachan-Heo/gajae-code](https://github.com/Yeachan-Heo/gajae-code) (MIT).
Evaluation-loop ideas were informed by
[rennf93/opus-fable-playbook](https://github.com/rennf93/opus-fable-playbook)
and [elon-choo/fablever](https://github.com/elon-choo/fablever). All prose and
code in this repository were rewritten.

MIT © pinetreeB

# show-me-the-work

**show-me-the-work** (`smtw`, Korean: **쇼미더워크**) is evidence-based AI work supervision: no executed proof, no credible "done."

[![version](https://img.shields.io/badge/version-2.0.0-brightgreen.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> 🇰🇷 **한국어가 1차 문서입니다**: [`README.ko.md`](README.ko.md)

## 🎯 What is this? (for non-developers / vibe coders)

When you ask an AI (Claude, Codex…) to write code, it's capable but sometimes **careless**:

- It changes code and says "done" **without actually running it**
- It builds a page and ends with "just open it in a browser" (never looked itself)
- It picks **one guess** for a bug and fixes that (maybe the wrong spot)
- It says "I'll do X next" and just **stops**

`show-me-the-work` is an **automatic quality inspector** that sits next to the AI. When the AI tries to call something "done" without checking, it **blocks and demands evidence**. You do nothing — install once, and it works on every task.

> Think of it as a flexible QA supervisor assigned to a skilled-but-careless worker. Silent most of the time; it only steps in the moment someone says "done" without proof.

**One honest caveat**: show-me-the-work doesn't make the AI *smarter* — it just makes cutting corners *impossible to finish*. (In a real on/off comparison, correctness was identical; what differed was how rigorously the work got verified.)

## 📖 Why "show-me-the-work"

This project was renamed from **fable-lite** in v2.0. It began by asking whether Fable 5's working discipline could be transferred to lower models. The answer was useful but narrower than imitation: model capability cannot be transplanted, while verification, investigation, and completion discipline can be enforced as procedure.

By v2.0, the product had grown into **evidence-based AI work supervision** across Claude Code, Codex, and Antigravity. The old name described the starting experiment; the new name describes the product.

The name reverses two familiar cheat-code memes: StarCraft's `show me the money` and the broader "show me" challenge. This is the anti-cheat version: **Show me the work.** Don't tell the user it passed — show the run, the observation, and the evidence.

## 🤝 Synergy with LazyCodex (ulw)

[LazyCodex/OmO](https://github.com/code-yeongyu/oh-my-openagent)'s `ulw` drives tasks to completion; show-me-the-work inspects evidence at every completion attempt — the roles don't overlap. Run both on Codex CLI (`adapters/codex_cli/INSTALL.ko.md`) and you get runs that **push all the way through, but can't finish with an unverified "done"**. This repository itself was built with that combo (implemented under ulw, inspected by smtw).

## 💬 Common objections (FAQ)

### "Hooks don't actually force anything — the AI can just ignore them."

**Asking in words and blocking in code are different things.** That objection is true for the first half only.

- Writing "always verify before finishing" in a prompt is a **request**. The AI can ignore it — in our measurement, instruction-only compliance was **0/3**.
- show-me-the-work is not a request; it is a **lock**. Until the condition (evidence of an actually-executed verification) is met, the "done" declaration and tool calls are **rejected at the program level**. There is no layer where the model gets to "decide not to comply" — the same measurement showed hard gates converging to **3/3** blocked-then-recovered-with-real-evidence ([experiment report](docs/reviews/p5b-n1-natural.md)).

One fun piece of evidence: while building this repository, **even a frontier model (Fable 5) got blocked by this gate and had to rewrite its report.** Hooks don't rely on the model's goodwill.

### "Verification is the human's job. Why build this at all?"

Agreed — **final responsibility stays with the human, and show-me-the-work doesn't replace that.** What it blocks is the step before: **the AI claiming "I verified it" while having executed nothing.**

- People who can't read code (a core audience of this tool) have **no way to tell** that claim is fake.
- People who can read code still can't personally verify the thousands of lines an AI produces per day.

show-me-the-work is a **first-pass filter**: no executed evidence, no "done". It doesn't remove human verification — it raises the trustworthiness of what reaches the human.

### "Can't the AI just fill in the format and slip through?"

**In theory, yes. We can't fully prevent it and don't claim to.** However:

1. The gate **doesn't trust the AI's words — it reads tool results**: not the sentence "tests passed", but whether a test command actually ran and succeeded.
2. The behavior change is **measured, blind-judged**: ON won 5/5 tasks, and every gap appeared exactly where verification gets tedious ([A/B report](docs/reviews/e1-ab-report.md)).
3. It doesn't block forever — after 2 blocks it lets the run proceed (no deadlocks). That's a safety valve, and an honest limitation we document.

In short: **show-me-the-work is not a tool that makes you trust the AI — it's a tool that lets you trust it less.**

---

## Technical summary

A Korean-first, evidence-based AI work supervisor with live hook receipts for Claude Code and Codex CLI, plus an Antigravity adapter validated by payload injection. It enforces investigation, verification grounding, evidence-gated completion, scope control, and high-risk contracts as **deterministic hooks**, not suggestions.

**A procedure transplant, not a capability transplant.** Weight-level abilities (out-of-spec defect discovery, self-driven implication depth) are explicitly out of scope; the harness escalates honestly instead of pretending.

## Why hooks, not prompts

In a live 3-run experiment with unrestricted tools ([report](docs/reviews/p5b-n1-natural.md)), pack instructions alone produced **0/3** natural compliance; the deterministic Stop gate converged all three runs to *one block → one full recovery* with real evidence (file:line citations, re-run outputs). Discipline packs tell the model *what* to do; hooks make skipping it impossible to finish.

In a controlled ON-vs-OFF A/B (5 tasks, blind-judged by a different model — [report](docs/reviews/e1-ab-report.md)): **ON won 5/5 (137 vs 109 rubric points)**. Correctness was identical; the gap opens exactly where verification gets inconvenient — the render task (OFF: *"just open it in a browser"* vs ON: real browser observation) and the multi-story task (OFF made zero verification attempts).

The qualitative gap **held across 3 repeat runs** (OFF verified 0/3, ON 3/3), though the *cost multiplier* swings too much to quote as a single number ([repeat study](docs/reviews/e1b-repeat.md)) — which also surfaced and fixed a real verification-crediting bug in the harness. We report what replicates and flag what doesn't.

## What's inside

| Gate | Mechanism |
|------|-----------|
| Investigation protocol (3+ hypotheses, evidence, rejections) | pack + **N1 compliance parser** on Stop (bilingual markers) |
| Verification grounding (RUN→OBSERVE→FIX→RE-RUN) | pack + evidence ledger |
| Evidence-gated completion | Stop hook blocks changed-but-unverified turns (max 2, fail-open) |
| Filesystem provenance | bounded snapshots, per-turn baselines, and full Stop reconciliation |
| Scope drift detection | PostToolUse warning |
| High-risk contract (auth/migration/payment/mass-delete) | PreToolUse **hard block** until `.fable-lite/contract.json` exists |
| Korean-first routing | "버그 고쳐줘", "왜 안 돼" → investigation pack |
| Goals checkpoints | `goals/goals.py` CLI, verify-with-evidence required |
| Session Scorecard | per-session block, recovery, and cap counts from an append-only gate journal |

Pure-stdlib Python core (zero Claude Code imports — platform-neutral, adapters are thin wrappers), single state dir `.fable-lite/`, every hook fail-open, Windows-native.

> Compatibility note: the internal state path remains `.fable-lite/` in v2.0 to avoid breaking existing installations. A public alias is planned for v2.1.

Non-document file changes require a fresh successful verification in every task mode (`quick`, `normal`, and `deep`). No change and documentation-only turns retain their existing allow behavior.

### Host support

| Host | Current status |
|---|---|
| Claude Code | live hook chain confirmed |
| Codex CLI | live hook chain confirmed |
| Antigravity | payload-injection conformance confirmed; live firing on host 1.1.1 is not confirmed |

### State and honest limits

User state remains under `.fable-lite/`. The main ledger, goals, intent/contract files, per-agent JSONL logs, bounded session-scorecard cache, provenance configuration, snapshots, turn baselines, gate journal, locks, and recovery backups all live there. Important paths include `ledger.json`, `agents/*.jsonl`, `snapshots/workspace-current.json`, `snapshots/turns/**`, `scorecard/gates.jsonl`, and `provenance-config.json`.

- Stop blocks at most twice and then fail-opens; this prevents deadlocks but is not an adversarial-model security boundary.
- Files outside the project root and database or network side effects are not directly observed.
- Provenance supports up to 10,000 tracked entries and 256 MiB. A full Stop reconciliation near that envelope can take several seconds. Larger or slower scopes return an explicit advisory-only `scope too large` state instead of committing a partial snapshot. Time limits are cooperative between filesystem calls and hash chunks; one blocked OS call cannot be preempted.
- Direct `ssh` and local-to-remote `scp` are tracked as remote mutation epochs. A later, separately started successful verification can satisfy the epoch; this does not prove that remote state was observed as clean. Commands with local redirects, pipelines, downloads, chains, substitutions, or unsafe SSH options stay on the conservative local-mutation path.
- Promise-only text completion remains manual probe `PRB-01`; there is no dedicated blocking rule. Independent per-gate toggles remain manual probe `PRB-11` and are not implemented.
- The harness improves work discipline and evidence quality. It does not make a model more capable or guarantee complete defense against deliberate evasion.

## Install

**Requires Python 3.12+ on PATH in the target environment** — the hooks are stdlib-Python scripts, so a host without a resolvable `python` (e.g. a fresh worker box) must install it first. No third-party packages.

Recommended local-clone install:

```
git clone https://github.com/pinetreeB/show-me-the-work
claude plugin marketplace add <path-to-show-me-the-work>
/plugin install show-me-the-work@show-me-the-work
```

After the plugin is registered in a marketplace, `/plugin marketplace add pinetreeB/show-me-the-work` can replace the local path step.

## Verify

```
python -m pytest tests/ -q      # unit tests
python eval/run_probes.py --strict  # deterministic probe suite; 15 pass, 0 fail, 3 manual
python eval/e2e_smoke.py        # full hook-chain smoke (real CC payload schema)
```

## Credits

Procedural design (verification grounding, decomposition/evidence gates, investigation loop, early-stop prevention) adapted from [fivetaku/fablize](https://github.com/fivetaku/fablize) (MIT) — all prose and code rewritten. The intent-gate interview methodology (ambiguity scoring → threshold gating → one-question-at-a-time confirmation) is adapted from [Yeachan-Heo/gajae-code](https://github.com/Yeachan-Heo/gajae-code) (MIT). Evaluation-loop ideas informed by [rennf93/opus-fable-playbook](https://github.com/rennf93/opus-fable-playbook) and [elon-choo/fablever](https://github.com/elon-choo/fablever).

MIT © pinetreeB

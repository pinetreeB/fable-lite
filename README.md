# fable-lite

[![version](https://img.shields.io/badge/version-1.0.0-brightgreen.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> 🇰🇷 **한국어가 1차 문서입니다**: [`README.ko.md`](README.ko.md)

## 🎯 What is this? (for non-developers / vibe coders)

When you ask an AI (Claude, Codex…) to write code, it's capable but sometimes **careless**:

- It changes code and says "done" **without actually running it**
- It builds a page and ends with "just open it in a browser" (never looked itself)
- It picks **one guess** for a bug and fixes that (maybe the wrong spot)
- It says "I'll do X next" and just **stops**

`fable-lite` is an **automatic quality inspector** that sits next to the AI. When the AI tries to call something "done" without checking, it **blocks and demands evidence**. You do nothing — install once, and it works on every task.

> Think of it as a flexible QA supervisor assigned to a skilled-but-careless worker. Silent most of the time; it only steps in the moment someone says "done" without proof.

**One honest caveat**: fable-lite doesn't make the AI *smarter* — it just makes cutting corners *impossible to finish*. (In a real on/off comparison, correctness was identical; what differed was how rigorously the work got verified.)

## 📖 Why the name "fable-lite"

This project began with a question: **could Fable 5 — Anthropic's top-tier model — be recreated on lower models?** After surveying prior attempts and controlled experiments, the answer was clear: the model's raw capability (spotting problems nobody asked about, the depth to crack hard problems) obviously cannot be transplanted.

But what people who use Fable actually feel isn't just capability. It's **the force of pushing through to the end** — never calling something "done" before verifying it, running what it builds with its own hands, not stopping halfway. That part turned out to be **procedure, not capability** — which means hooks can enforce it.

So we dropped Fable's weight (model capability) and carried over only its way of working — hence **fable-lite**.

## 🤝 Synergy with LazyCodex (ulw)

[LazyCodex/OmO](https://github.com/code-yeongyu/oh-my-openagent)'s `ulw` drives tasks to completion; fable-lite inspects evidence at every completion attempt — the roles don't overlap. Run both on Codex CLI (`adapters/codex_cli/INSTALL.ko.md`) and you get runs that **push all the way through, but can't finish with an unverified "done"**. This repository itself was built with that combo (implemented under ulw, inspected by fable-lite).

---

## Technical summary

A Korean-first Claude Code harness that makes lower Claude models (Opus, Sonnet) follow **Fable 5's working discipline** — investigation, verification grounding, evidence-gated completion, scope control, and high-risk contracts — enforced as **deterministic hooks**, not suggestions.

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
| Scope drift detection | PostToolUse warning |
| High-risk contract (auth/migration/payment/mass-delete) | PreToolUse **hard block** until `.fable-lite/contract.json` exists |
| Korean-first routing | "버그 고쳐줘", "왜 안 돼" → investigation pack |
| Goals checkpoints | `goals/goals.py` CLI, verify-with-evidence required |

Pure-stdlib Python core (zero Claude Code imports — platform-neutral, adapters are thin wrappers), single state dir `.fable-lite/`, every hook fail-open, Windows-native.

## Install

**Requires Python 3.12+ on PATH in the target environment** — the hooks are stdlib-Python scripts, so a host without a resolvable `python` (e.g. a fresh worker box) must install it first. No third-party packages.

Recommended local-clone install:

```
git clone https://github.com/pinetreeB/fable-lite
claude plugin marketplace add <path-to-fable-lite>
/plugin install fable-lite@fable-lite
```

After the plugin is registered in a marketplace, `/plugin marketplace add pinetreeB/fable-lite` can replace the local path step.

## Verify

```
python -m pytest tests/ -q      # unit tests
python eval/run_probes.py       # deterministic probe suite
python eval/e2e_smoke.py        # full hook-chain smoke (real CC payload schema)
```

## Credits

Procedural design (verification grounding, decomposition/evidence gates, investigation loop, early-stop prevention) adapted from [fivetaku/fablize](https://github.com/fivetaku/fablize) (MIT) — all prose and code rewritten. The intent-gate interview methodology (ambiguity scoring → threshold gating → one-question-at-a-time confirmation) is adapted from [Yeachan-Heo/gajae-code](https://github.com/Yeachan-Heo/gajae-code) (MIT). Evaluation-loop ideas informed by [rennf93/opus-fable-playbook](https://github.com/rennf93/opus-fable-playbook) and [elon-choo/fablever](https://github.com/elon-choo/fablever).

MIT © pinetreeB

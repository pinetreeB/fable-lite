# fable-lite

[![ci](https://github.com/pinetreeB/fable-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/pinetreeB/fable-lite/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> 🇰🇷 **한국어가 1차 문서입니다**: [`README.ko.md`](README.ko.md)

A Korean-first Claude Code harness that makes lower Claude models (Opus, Sonnet) follow **Fable 5's working discipline** — investigation, verification grounding, evidence-gated completion, scope control, and high-risk contracts — enforced as **deterministic hooks**, not suggestions.

**A procedure transplant, not a capability transplant.** Weight-level abilities (out-of-spec defect discovery, self-driven implication depth) are explicitly out of scope; the harness escalates honestly instead of pretending.

## Why hooks, not prompts

In a live 3-run experiment with unrestricted tools ([report](docs/reviews/p5b-n1-natural.md)), pack instructions alone produced **0/3** natural compliance; the deterministic Stop gate converged all three runs to *one block → one full recovery* with real evidence (file:line citations, re-run outputs). Discipline packs tell the model *what* to do; hooks make skipping it impossible to finish.

In a controlled ON-vs-OFF A/B (5 tasks, blind-judged by a different model — [report](docs/reviews/e1-ab-report.md)): **ON won 5/5 (137 vs 109 rubric points)**. Correctness was identical; the gap opens exactly where verification gets inconvenient — the render task (OFF: *"just open it in a browser"* vs ON: headless-browser clicks + screenshot evidence) and the multi-story task (unverified completion claims **3 vs 0**).

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

```
/plugin marketplace add pinetreeB/fable-lite
/plugin install fable-lite@fable-lite
```

Or from a local clone: `claude plugin marketplace add <path>` → `claude plugin install fable-lite@fable-lite`.

## Verify

```
python -m pytest tests/ -q      # unit tests
python eval/run_probes.py       # deterministic probe suite
python eval/e2e_smoke.py        # full hook-chain smoke (real CC payload schema)
```

## Credits

Procedural design (verification grounding, decomposition/evidence gates, investigation loop, early-stop prevention) adapted from [fivetaku/fablize](https://github.com/fivetaku/fablize) (MIT) — all prose and code rewritten. Evaluation-loop ideas informed by [rennf93/opus-fable-playbook](https://github.com/rennf93/opus-fable-playbook) and [elon-choo/fablever](https://github.com/elon-choo/fablever).

MIT © pinetreeB

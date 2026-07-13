from __future__ import annotations

import argparse

from .brief import run_brief
from .check import run_check
from .intent import add_intent_parser
from .scorecard import add_scorecard_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fable_lite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="오케스트레이터 사후 게이트를 실행합니다.")
    check.add_argument("--root")
    check.add_argument("--agent")
    check.add_argument("--since-file")
    check.add_argument("--card")
    check.set_defaults(func=run_check)

    brief = subparsers.add_parser("brief", help="위임 프롬프트 규율 블록을 생성합니다.")
    brief.add_argument("--paths")
    brief.add_argument("--verify-cmd")
    brief.add_argument("--sentinel")
    brief.add_argument("--card")
    brief.add_argument("--target", choices=("codex", "claude", "agy"))
    brief.set_defaults(func=run_brief)

    add_intent_parser(subparsers)
    add_scorecard_parser(subparsers)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))

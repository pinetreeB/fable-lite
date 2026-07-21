from __future__ import annotations

import argparse
import json

from core.state_migration import migrate_state


def add_migrate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "migrate",
        help="legacy .fable-lite 상태를 검증 후 .smtw로 명시적으로 복사합니다.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--lock-wait-seconds", type=float, default=15.0)
    parser.set_defaults(func=run_migrate)


def run_migrate(args: argparse.Namespace) -> int:
    result = migrate_state(
        args.root,
        activation=True,
        lock_wait_seconds=args.lock_wait_seconds,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return result.exit_code

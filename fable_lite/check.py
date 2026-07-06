from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from core.classify import classify_prompt
from core.contract import evaluate_r1_contract
from core.ledger import JsonObject, load_agent_ledger, load_ledger
from core.scope_guard import evaluate_scope
from .card import TaskCard, card_changed_excludes, card_completion_findings, card_forbidden_findings, card_scope_findings, card_validation_findings, card_verify_success, load_task_card
from .check_support import (
    changed_since,
    git,
    has_successful_verification,
    is_state_path,
    merge,
    non_docs,
    parse_porcelain,
    path_evidence,
    relative_to_root,
    sentinels,
    string,
    string_list,
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    root: Path
    agent: str
    changed_files: list[str]
    unverified: list[str]
    scope_messages: list[str]
    r1_messages: list[str]
    promise_messages: list[str]
    forbidden_messages: list[str]
    verify_messages: list[str]
    card_messages: list[str]
    git_warnings: list[str]
    card_path: str

    def is_green(self) -> bool:
        return not (
            self.unverified
            or self.scope_messages
            or self.r1_messages
            or self.promise_messages
            or self.forbidden_messages
            or self.verify_messages
            or self.card_messages
        )


def run_check(args: Namespace) -> int:
    card = load_task_card(Path(str(args.card))) if args.card else None
    root = Path(str(args.root or Path.cwd())).resolve()
    agent = str(args.agent or (card.owner if card else ""))
    since_file = Path(str(args.since_file)).resolve() if args.since_file else (card.path if card else None)
    result = evaluate(root, agent, since_file, card)
    print(render(result))
    return 0 if result.is_green() else 1


def evaluate(root: Path, agent: str, since_file: Path | None, card: TaskCard | None = None) -> CheckResult:
    ledger_payload = {"project_root": str(root), "agent": agent} if agent else {"project_root": str(root)}
    ledger = load_agent_ledger(ledger_payload) if agent else load_ledger(ledger_payload)
    changed_files, warnings = changed_paths(root, ledger, since_file)
    changed_files = [path for path in changed_files if path not in card_changed_excludes(root, card)]
    prompt = string(ledger.get("prompt"))
    verified = card_verify_success(root, agent, ledger, card, has_successful_verification(ledger))
    unverified = unverified_changes(changed_files, ledger, verified)
    scope_messages = scope_findings(root, prompt, changed_files, card)
    r1_messages = r1_findings(root, prompt, changed_files)
    promise_messages = sentinel_findings(root, prompt, card)
    forbidden_messages = card_forbidden_findings(changed_files, card)
    verify_messages = verify_findings(ledger, card, verified)
    card_messages = card_validation_findings(card)
    return CheckResult(
        root=root,
        agent=agent,
        changed_files=changed_files,
        unverified=unverified,
        scope_messages=scope_messages,
        r1_messages=r1_messages,
        promise_messages=promise_messages,
        forbidden_messages=forbidden_messages,
        verify_messages=verify_messages,
        card_messages=card_messages,
        git_warnings=warnings,
        card_path=str(card.path) if card else "",
    )


def render(result: CheckResult) -> str:
    status = "GREEN" if result.is_green() else "RED"
    lines = [
        f"fable-lite check: {status}",
        f"- root: {result.root}",
        f"- agent: {result.agent or '(all)'}",
        f"- changed: {len(result.changed_files)}",
    ]
    if result.card_path:
        lines.append(f"- card: {result.card_path}")
    if result.changed_files:
        lines.extend(f"  - {path}" for path in result.changed_files)
    _section(lines, "미검증 변경", result.unverified)
    _section(lines, "scope 이탈", result.scope_messages)
    _section(lines, "forbidden 침범", result.forbidden_messages)
    _section(lines, "verify 요구", result.verify_messages)
    _section(lines, "작업카드 오류", result.card_messages)
    _section(lines, "R1 위반", result.r1_messages)
    _section(lines, "미이행 약속", result.promise_messages)
    _section(lines, "git 경고", result.git_warnings)
    return "\n".join(lines)


def changed_paths(root: Path, ledger: Mapping[str, object], since_file: Path | None) -> tuple[list[str], list[str]]:
    paths = string_list(ledger.get("changed_files_seen"))
    warnings: list[str] = []
    git_result = git(root, "status", "--porcelain=v1", "-uall")
    if git_result.returncode == 0:
        paths = merge(paths, parse_porcelain(git_result.stdout))
    else:
        warnings.append("git status 실행 실패: ledger 기준으로만 판정")
    paths = [path for path in paths if not is_state_path(path)]
    if since_file is not None:
        marker = relative_to_root(root, since_file)
        paths = [path for path in paths if path != marker]
        paths = [path for path in paths if changed_since(root, path, since_file)]
    return paths, warnings


def unverified_changes(changed_files: list[str], ledger: Mapping[str, object], verified: bool | None = None) -> list[str]:
    if not changed_files or (verified if verified is not None else has_successful_verification(ledger)):
        return []
    return non_docs(changed_files)


def scope_findings(root: Path, prompt: str, changed_files: list[str], card: TaskCard | None = None) -> list[str]:
    if not changed_files:
        return []
    if card and card.allowed_paths:
        return card_scope_findings(changed_files, card)
    classified = classify_prompt({"prompt": prompt})
    requested_paths = classified.get("requested_paths")
    result = evaluate_scope(
        {
            "project_root": str(root),
            "prompt": prompt,
            "requested_paths": requested_paths if isinstance(requested_paths, list) else [],
            "changed_files": changed_files,
        }
    )
    if result.get("decision") != "warn":
        return []
    out_of_scope = string_list(result.get("out_of_scope"))
    message = string(result.get("message")) or "범위 이탈 가능성"
    return [f"{path}: {message}" for path in out_of_scope]


def r1_findings(root: Path, prompt: str, changed_files: list[str]) -> list[str]:
    findings: list[str] = []
    for path in changed_files:
        risk_text = "\n".join([prompt, path, path_evidence(root, path)])
        result = evaluate_r1_contract(
            {
                "project_root": str(root),
                "tool_name": "Edit",
                "file_paths": [path],
                "prompt": risk_text,
            }
        )
        if result.get("decision") == "block":
            reason = string(result.get("reason")) or "R1 contract required"
            findings.append(f"{path}: {reason}")
    return findings


def sentinel_findings(root: Path, prompt: str, card: TaskCard | None = None) -> list[str]:
    missing: list[str] = []
    for sentinel in sentinels(prompt):
        _add_missing_path(missing, root, sentinel, "sentinel")
    missing.extend(card_completion_findings(root, card))
    return missing


def verify_findings(ledger: Mapping[str, object], card: TaskCard | None, verified: bool) -> list[str]:
    if card is None or verified:
        return []
    return [f"verify `{card.verify}` 성공 기록이 없습니다"]


def _add_missing_path(missing: list[str], root: Path, path: str, label: str) -> None:
    if path and not _path_for(root, path).exists():
        missing.append(f"{path}: {label} 파일이 없습니다")


def _path_for(root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _section(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.append(f"- {title}:")
    lines.extend(f"  - {item}" for item in items)

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

from .design_gate import DESIGN_CONFIG_PATH, is_ui_path
from .ledger_schema import JsonObject, JsonValue

DOC_EXTS: Final = (".md", ".txt", ".rst", ".adoc")
CODE_EXTS: Final = (
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c",
    ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".sql", ".ps1",
)
V1_PROJECTION_FIELDS: Final = (
    "task_mode", "prompt", "packs", "changed_files_seen", "change_kinds",
    "verification_commands", "verification_results", "event_seq", "last_change_seq",
    "stop_blocks", "goals_blocks", "intent_blocks", "requires_investigation_compliance",
    "needs_goals", "intent_required", "ambiguity_score", "scope_warnings", "agent",
    "design_required", "design_touched", "design_blocks", "design_last_change_seq",
    "design_check_passed", "design_check_seq", "design_violations", "design_baseline_revision",
    "design_dirty_baseline",
)
DESIGN_FIELDS: Final = tuple(field for field in V1_PROJECTION_FIELDS if field.startswith("design_"))


def default_ledger() -> JsonObject:
    return {
        "task_mode": "quick",
        "prompt": "",
        "packs": [],
        "changed_files_seen": [],
        "change_kinds": [],
        "verification_commands": [],
        "verification_results": [],
        "event_seq": 0,
        "last_change_seq": 0,
        "stop_blocks": 0,
        "goals_blocks": 0,
        "intent_blocks": 0,
        "requires_investigation_compliance": False,
        "needs_goals": False,
        "intent_required": False,
        "ambiguity_score": 0,
        "scope_warnings": [],
        "agent": "",
    }


def classify_change_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in DOC_EXTS:
        return "docs"
    if suffix in CODE_EXTS:
        return "code"
    return "artifact"


def _as_str_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_result_list(value: JsonValue | None) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _append_unique(items: list[str], item: str) -> list[str]:
    if item and item not in items:
        items.append(item)
    return items


def _bool_value(value: JsonValue | None) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _bounded_score(value: JsonValue | None) -> int:
    if not isinstance(value, int):
        return 0
    return max(0, min(4, value))


def sequence_value(value: JsonValue | None) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def apply_v1_event(ledger: JsonObject, payload: Mapping[str, JsonValue]) -> JsonObject:
    event = payload.get("event")
    event_seq = sequence_value(payload.get("seq"))
    ledger["event_seq"] = max(sequence_value(ledger.get("event_seq")), event_seq)
    agent = payload.get("agent")
    if isinstance(agent, str) and agent:
        ledger["agent"] = agent

    if event == "prompt":
        mode = payload.get("task_mode")
        prompt = payload.get("prompt")
        packs = payload.get("packs")
        ledger["task_mode"] = mode if isinstance(mode, str) else "quick"
        ledger["prompt"] = prompt if isinstance(prompt, str) else ""
        ledger["packs"] = [item for item in packs if isinstance(item, str)] if isinstance(packs, list) else []
        ledger["needs_goals"] = payload.get("needs_goals") is True
        ledger["intent_required"] = payload.get("intent_required") is True
        ledger["ambiguity_score"] = _bounded_score(payload.get("ambiguity_score"))
        ledger["requires_investigation_compliance"] = payload.get("requires_investigation_compliance") is True
        ledger["stop_blocks"] = 0
        ledger["goals_blocks"] = 0
        ledger["intent_blocks"] = 0
        ledger["changed_files_seen"] = []
        ledger["change_kinds"] = []
        ledger["verification_commands"] = []
        ledger["verification_results"] = []
        ledger["last_change_seq"] = 0
        ledger["scope_warnings"] = []
        _reset_design_state(ledger, payload)
    elif event == "change":
        path = payload.get("path")
        if isinstance(path, str) and path:
            changed = _as_str_list(ledger.get("changed_files_seen"))
            kinds = _as_str_list(ledger.get("change_kinds"))
            kind_value = payload.get("kind")
            kind = kind_value if isinstance(kind_value, str) else classify_change_kind(path)
            ledger["changed_files_seen"] = _append_unique(changed, path)
            ledger["change_kinds"] = _append_unique(kinds, kind)
            if kind != "docs":
                ledger["last_change_seq"] = event_seq
            normalized = Path(path.replace("\\", "/")).as_posix().removeprefix("./").casefold()
            policy_changed = normalized == DESIGN_CONFIG_PATH.as_posix().casefold()
            if ledger.get("design_required") is True and (is_ui_path(path) or policy_changed):
                ledger["design_touched"] = True
                ledger["design_last_change_seq"] = event_seq
                ledger["design_check_passed"] = False
    elif event == "verification":
        command_value = payload.get("command")
        evidence_value = payload.get("evidence")
        command = command_value if isinstance(command_value, str) else ""
        evidence = evidence_value if isinstance(evidence_value, str) else ""
        commands = _as_str_list(ledger.get("verification_commands"))
        results = _as_result_list(ledger.get("verification_results"))
        ledger["verification_commands"] = _append_unique(commands, command)
        result: JsonObject = {"command": command, "success": _bool_value(payload.get("success")), "evidence": evidence}
        if event_seq > 0:
            result["seq"] = event_seq
        results.append(result)
        ledger["verification_results"] = results
    elif event == "scope_warning":
        warning_value = payload.get("message")
        warning = warning_value if isinstance(warning_value, str) else ""
        warnings = _as_str_list(ledger.get("scope_warnings"))
        ledger["scope_warnings"] = _append_unique(warnings, warning)
    elif event == "design_check" and ledger.get("design_required") is True:
        ledger["design_check_passed"] = payload.get("passed") is True
        ledger["design_check_seq"] = event_seq
        violations = payload.get("violations")
        ledger["design_violations"] = (
            [item for item in violations if isinstance(item, dict)]
            if isinstance(violations, list)
            else []
        )
    return ledger


def _reset_design_state(ledger: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    if payload.get("design_required") is not True:
        for field in DESIGN_FIELDS:
            _ = ledger.pop(field, None)
        return
    ledger["design_required"] = True
    ledger["design_touched"] = False
    ledger["design_blocks"] = 0
    ledger["design_last_change_seq"] = 0
    ledger["design_check_passed"] = False
    ledger["design_check_seq"] = 0
    ledger["design_violations"] = []
    baseline = payload.get("design_baseline_revision")
    ledger["design_baseline_revision"] = baseline if isinstance(baseline, str) else ""
    dirty_baseline = payload.get("design_dirty_baseline")
    ledger["design_dirty_baseline"] = dirty_baseline if isinstance(dirty_baseline, dict) else {}

from __future__ import annotations

import sys
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 주의: core.* import는 여기(모듈 최상단)에 두지 않는다 — main()의 try 블록
# 밖에서 실행되면 core에 문제가 생겼을 때 fail-open 없이 훅 전체가 죽는다
# (v1 릴리스 심사 B1). 각 handle_* 함수 안에서 필요한 것만 지역 import한다
# — claude_code/codex_cli의 8개 훅 파일과 동일하게, import 실패도
# main()의 try/except가 잡아 fail_open으로 처리되도록 한다.

def read_payload() -> dict[str, object]:
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        raw = cast(object, json.loads(text))
    except json.JSONDecodeError as exc:
        raise ValueError("malformed JSON payload") from exc
    if not isinstance(raw, dict):
        raise ValueError("payload must be a JSON object")
    return dict(cast(Mapping[str, object], raw))


def _common():
    from adapters.antigravity import hook_common

    return hook_common


def emit(payload: Mapping[str, object]) -> int:
    data = json.dumps(payload, ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def fail_open(event_name: str, msg: str) -> int:
    reason = f"[smtw] fail-open: {msg}"
    if event_name in {"PreToolUse", "BeforeTool"}:
        return emit({"decision": "allow", "reason": reason})
    return emit({})

def handle_pre_tool_use(payload: Mapping[str, object]) -> int:
    from adapters.antigravity.tool_io import extract_command, extract_paths_from_input, extract_tool_info, tool_family
    from adapters.intent_command import intent_set_command
    from core.classify import classify_prompt
    from core.contract import evaluate_pretool_contract
    from core.destructive_guard import evaluate_r2_destructive_gate

    common = _common()
    tool_name, tool_input = extract_tool_info(payload)
    paths = extract_paths_from_input(tool_input)
    cmd = extract_command(tool_input)
    family = tool_family(tool_name)
    root = common.project_root(payload)
    prompt_hint = " ".join(paths) if family == "edit" else ""
    invocation = common.canonical_invocation(payload, "pre_tool", family, paths, cmd, False, "")
    # R2 first: run before any other ledger read such as resolve_active_invocation.
    # Any exception raised here is absorbed inside destructive_guard as "degraded"
    # so a later broad except fail-open cannot undo this decision.
    r2_result = evaluate_r2_destructive_gate(
        {
            "project_root": root,
            "tool_name": "Bash" if family == "shell" else tool_name,
            "command": cmd,
            "host": invocation.host,
            "agent": invocation.agent,
            "session_id": invocation.session_id,
        }
    )
    if r2_result.get("decision") == "block":
        return emit({"decision": "deny", "reason": str(r2_result.get("reason", ""))})

    from core.adapter_observation import begin_invocation, resolve_active_invocation

    invocation = resolve_active_invocation(Path(root), invocation)
    turn_payload = dict(payload)
    turn_payload["agent"] = invocation.agent
    turn_payload["session_id"] = invocation.session_id
    turn_payload["turn_id"] = invocation.turn_id
    _ = common.prepare_turn(turn_payload, prompt_hint, __file__, classify_prompt)

    result = evaluate_pretool_contract({
        "project_root": common.project_root(payload),
        "tool_name": tool_name,
        "file_paths": paths,
        "command": cmd,
        "prompt": json.dumps(tool_input, ensure_ascii=False),
        "intent_set_command": intent_set_command(__file__),
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "attribution": invocation.scorecard_attribution,
    })
    
    if result.get("decision") == "block":
        return emit({"decision": "deny", "reason": str(result.get("reason", ""))})
    _ = begin_invocation(Path(common.project_root(payload)), invocation)
    return emit({"decision": "allow"})

def handle_post_tool_use(payload: Mapping[str, object]) -> int:
    from adapters.antigravity.tool_io import (
        extract_command,
        extract_paths_from_input,
        extract_tool_info,
        tool_family,
        verification_result,
    )

    common = _common()
    root = common.project_root(payload)
    tool_name, tool_input = extract_tool_info(payload)

    from core.adapter_observation import observe_post_tool, resolve_active_invocation, verification_covers
    from core.classify import classify_prompt
    from core.contract import record_contract_authored_event
    from core.ledger import JsonObject, load_ledger, record_event
    from core.provenance_types import ProvenanceStatus
    from core.scope_guard import evaluate_scope
    from core.verification import is_verification_command

    family = tool_family(tool_name)
    if family in {"edit", "shell"}:
        command = extract_command(tool_input)
        success, evidence = verification_result(payload)
        candidate_paths = extract_paths_from_input(tool_input)
        invocation = common.canonical_invocation(
            payload,
            "post_tool",
            family,
            candidate_paths,
            command,
            success,
            evidence,
        )
        if family == "edit":
            record_contract_authored_event(
                {
                    "project_root": root,
                    "file_paths": candidate_paths,
                    "host": invocation.host,
                    "agent": invocation.agent,
                    "session_id": invocation.session_id,
                    "turn_id": invocation.turn_id,
                    "attribution": invocation.scorecard_attribution,
                }
            )
        invocation = resolve_active_invocation(Path(root), invocation)
        observation = observe_post_tool(Path(root), invocation)
        verification_command = family == "shell" and is_verification_command(command)
        if verification_command:
            covers = verification_covers(Path(root), invocation)
            verification: JsonObject = {
                "project_root": root,
                "event": "verification",
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
                "turn_id": invocation.turn_id,
                "invocation_id": invocation.invocation_id,
                "command": command,
                "success": invocation.success,
                "evidence": invocation.evidence,
            }
            if covers is not None:
                verification["covers"] = covers
            _ = record_event(verification)
            return emit({})
        if observation.status is ProvenanceStatus.SCOPE_TOO_LARGE:
            return emit({})
        if observation.incomplete and not verification_command:
            return emit({})
        paths = list(observation.changed_paths)
        ledger = load_ledger({"project_root": root})
        prompt = ledger.get("prompt", "")
        if not isinstance(prompt, str):
            prompt = ""
        
        req = classify_prompt({"prompt": prompt})["requested_paths"] if prompt else []
        scope = evaluate_scope({
            "project_root": root,
            "prompt": prompt,
            "requested_paths": req,
            "changed_files": paths
        })
        if scope.get("decision") == "warn":
            _ = record_event({
                "project_root": root,
                "event": "scope_warning",
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
                "turn_id": invocation.turn_id,
                "message": scope.get("message"),
            })
            return emit({})
        if family == "edit":
            return emit({})
        return emit({})
    return emit({})

def handle_pre_invocation(payload: Mapping[str, object]) -> int:
    from core.classify import classify_prompt

    common = _common()
    lines = common.prepare_turn(payload, "", __file__, classify_prompt, force=True)
    return emit({
        "injectSteps": [{"ephemeralMessage": "\n".join(lines)}],
    })


def handle_stop(payload: Mapping[str, object]) -> int:
    from core.verify_state import evaluate_stop

    common = _common()
    root, invocation, stop_payload = common.stop_context(payload)
    result = evaluate_stop(stop_payload)
    return common.emit_stop_result(root, invocation, result)

def main() -> int:
    event_name = sys.argv[1] if len(sys.argv) >= 2 else ""
    try:
        if not event_name:
            return fail_open(event_name, "No event specified")

        payload = read_payload()
        
        handlers = {
            "PreInvocation": handle_pre_invocation,
            "PreToolUse": handle_pre_tool_use,
            "PostToolUse": handle_post_tool_use,
            "Stop": handle_stop,
            "BeforeModel": handle_pre_invocation,
            "BeforeTool": handle_pre_tool_use,
            "AfterTool": handle_post_tool_use,
            "AfterAgent": handle_stop,
        }
        handler = handlers.get(event_name)
        return handler(payload) if handler is not None else emit({})
            
    except Exception as e:
        return fail_open(event_name, str(e))

if __name__ == "__main__":
    sys.exit(main())

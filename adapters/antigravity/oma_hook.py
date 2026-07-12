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


def fail_open(msg: str) -> int:
    return emit({"decision": "allow", "systemMessage": f"fable-lite fail-open: {msg}"})

def handle_before_tool(payload: Mapping[str, object]) -> int:
    from adapters.antigravity.tool_io import extract_command, extract_paths_from_input, extract_tool_info
    from adapters.intent_command import intent_set_command
    from core.adapter_observation import begin_invocation, resolve_active_invocation
    from core.contract import EDIT_TOOLS, SHELL_TOOLS, evaluate_pretool_contract

    common = _common()
    tool_name, tool_input = extract_tool_info(payload)
    paths = extract_paths_from_input(tool_input)
    cmd = extract_command(tool_input)
    family = "edit" if tool_name in EDIT_TOOLS else "shell" if tool_name in SHELL_TOOLS else "other"
    invocation = common.canonical_invocation(payload, "pre_tool", family, paths, cmd, False, "")
    invocation = resolve_active_invocation(Path(common.project_root(payload)), invocation)

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
    })
    
    if result.get("decision") == "block":
        return emit({"decision": "block", "reason": str(result.get("reason", ""))})
    _ = begin_invocation(Path(common.project_root(payload)), invocation)
    return emit({"decision": "allow"})

def handle_after_tool(payload: Mapping[str, object]) -> int:
    from adapters.antigravity.tool_io import (
        extract_command,
        extract_paths_from_input,
        extract_tool_info,
        verification_result,
    )

    common = _common()
    root = common.project_root(payload)
    tool_name, tool_input = extract_tool_info(payload)

    from core.adapter_observation import observe_post_tool, resolve_active_invocation, verification_covers
    from core.classify import classify_prompt
    from core.contract import EDIT_TOOLS, SHELL_TOOLS
    from core.ledger import JsonObject, load_ledger, record_event
    from core.scope_guard import evaluate_scope
    from core.verification import is_verification_command

    family = "edit" if tool_name in EDIT_TOOLS else "shell" if tool_name in SHELL_TOOLS else "other"
    if family != "other":
        command = extract_command(tool_input)
        success, evidence = verification_result(payload)
        invocation = common.canonical_invocation(
            payload,
            "post_tool",
            family,
            extract_paths_from_input(tool_input),
            command,
            success,
            evidence,
        )
        invocation = resolve_active_invocation(Path(root), invocation)
        observation = observe_post_tool(Path(root), invocation)
        verification_command = family == "shell" and is_verification_command(command)
        if observation.incomplete and not verification_command:
            return emit({"decision": "allow", "systemMessage": "fable-lite provenance incomplete; fail-open observation."})
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
            return emit({
                "decision": "allow",
                "systemMessage": str(scope.get("message")),
                "hookSpecificOutput": {"additionalContext": str(scope.get("message"))}
            })
        if family == "edit":
            return emit({"decision": "allow", "systemMessage": f"fable-lite provenance: observed {len(paths)} change(s)."})
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
            _ = record_event({
                **verification,
            })
            return emit({"decision": "allow", "systemMessage": "fable-lite 원장: 검증 기록."})
        return emit({"decision": "allow", "systemMessage": f"fable-lite provenance: observed {len(paths)} change(s)."})
    return emit({"decision": "allow"})

def handle_before_model(payload: Mapping[str, object]) -> int:
    from adapters.intent_command import intent_set_command
    from core.ambiguity import evaluate_ambiguity
    from core.classify import classify_prompt
    from core.intent import clear_intent
    from core.ledger import record_event
    from core.adapter_observation import start_turn

    common = _common()
    prompt_value = payload.get("prompt", "")
    if not isinstance(prompt_value, str):
        prompt_value = ""
    
    if not prompt_value:
        req = common.mapping(payload.get("llm_request"))
        for msg in reversed(common.mapping_sequence(req.get("messages"))):
            if msg.get("role") == "user":
                prompt_value = str(msg.get("content", ""))
                break

    root = common.project_root(payload)
    invocation = common.canonical_invocation(payload, "turn_start", "other", [], "", True, "")
    observation = start_turn(Path(root), invocation)
    _ = clear_intent(root)
    result = classify_prompt({"prompt": prompt_value})
    ambiguity = evaluate_ambiguity({
        "project_root": root,
        "prompt": prompt_value,
        "requested_paths": common.string_list(result.get("requested_paths")),
    })
    intent_required = ambiguity.get("ambiguous") is True
    command_template = intent_set_command(__file__)
    packs = common.packs_with_intent(result.get("packs", []), intent_required)
    
    _ = record_event({
        "project_root": root,
        "event": "prompt",
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "baseline_snapshot_id": observation.baseline_snapshot_id,
        "current_snapshot_id": observation.snapshot_id,
        "provenance_incomplete": observation.incomplete,
        "task_mode": result.get("mode", "quick"),
        "prompt": prompt_value,
        "packs": packs,
        "needs_goals": result.get("needs_goals", False),
        "intent_required": intent_required,
        "ambiguity_score": ambiguity.get("ambiguity_score") if isinstance(ambiguity.get("ambiguity_score"), int) else 0,
        "requires_investigation_compliance": "investigation" in packs
    })
    
    lines = [
        "fable-lite 활성화: 작업 규율을 절차로 적용하세요.",
        f"mode={result.get('mode', 'quick')}"
    ]
    if "investigation" in packs:
        lines.extend([
            "조사 팩 준수 필수: 출력에 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:`를 포함하세요.",
            "수정 전 재현과 경쟁 가설을 먼저 기록하세요."
        ])
    if "verification-grounding" in packs:
        lines.append("렌더/실행 산출물은 RUN→OBSERVE→FIX→RE-RUN 증거 없이는 완료하지 마세요.")
    if result.get("needs_goals"):
        lines.append("2+ 스토리 작업입니다. goals 체크포인트를 만들거나 사용자에게 명시 확인을 받으세요.")
    common.append_intent_context(lines, intent_required, command_template)
        
    return emit({
        "decision": "allow",
        "hookSpecificOutput": {
            "additionalContext": "\n".join(lines)
        }
    })


def handle_after_agent(payload: Mapping[str, object]) -> int:
    from core.adapter_observation import finish_turn, resolve_active_invocation
    from core.verify_state import evaluate_stop

    _ = (finish_turn, resolve_active_invocation, evaluate_stop)
    return _common().handle_after_agent(payload)

def main() -> int:
    try:
        if len(sys.argv) < 2:
            return fail_open("No event specified")
            
        event_name = sys.argv[1]
        payload = read_payload()
        
        if event_name == "BeforeModel":
            return handle_before_model(payload)
        elif event_name == "BeforeTool":
            return handle_before_tool(payload)
        elif event_name == "AfterTool":
            return handle_after_tool(payload)
        elif event_name == "AfterAgent":
            return handle_after_agent(payload)
        else:
            return emit({"decision": "allow"})
            
    except Exception as e:
        return fail_open(str(e))

if __name__ == "__main__":
    sys.exit(main())

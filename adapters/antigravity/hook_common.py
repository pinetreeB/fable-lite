from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from core.adapter_observation import CanonicalInvocation

def mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def mapping_sequence(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if isinstance(item, str) and item]


def project_root(payload: Mapping[str, object]) -> str:
    cwd = payload.get("cwd") or os.getcwd()
    return str(cwd)


def canonical_invocation(
    payload: Mapping[str, object],
    phase: str,
    family: str,
    paths: list[str],
    command: str,
    success: bool,
    evidence: str,
) -> CanonicalInvocation:
    from core.adapter_observation import CanonicalInvocation

    session = payload.get("session_id")
    identity_synthetic = not isinstance(session, str) or not session
    session_id = session if isinstance(session, str) and session else "default"
    agent_value = payload.get("agent")
    agent = agent_value if isinstance(agent_value, str) and agent_value else "antigravity"
    turn_value = payload.get("turn_id")
    turn_id = turn_value if isinstance(turn_value, str) and turn_value else f"turn:{session_id}"
    invocation_value = payload.get("tool_use_id") or payload.get("invocation_id") or payload.get("tool_call_id")
    invocation_id = invocation_value if isinstance(invocation_value, str) and invocation_value else f"{phase}:{session_id}:{family}"
    return CanonicalInvocation(
        "antigravity",
        agent,
        session_id,
        turn_id,
        invocation_id,
        phase,
        family,
        tuple(sorted({path.replace("\\", "/") for path in paths if path})),
        command,
        success,
        evidence,
        identity_synthetic,
    )


def packs_with_intent(packs_value: object, intent_required: bool) -> list[str]:
    packs = string_list(packs_value)
    if intent_required and "intent-interview" not in packs:
        packs.append("intent-interview")
    return packs


def append_intent_context(lines: list[str], intent_required: bool, intent_command: str) -> None:
    if not intent_required:
        return
    lines.extend([
        "의도 확인 필요: 수정 전 `확인질문 N:` 형식으로 목표/범위/비목표를 최대 3개만 물어보세요.",
        f"확인되면 정확히 이 명령을 그대로 실행하세요: `{intent_command}`",
        "저장소 루트에서 직접 실행 중이면 `python -m fable_lite intent set ...`도 가능하지만, 플러그인 사용 중에는 위 절대경로 명령을 우선하세요.",
        "사용자가 묻지 말라고 한 경우에만 합리적 가정을 기록하고 명령 끝에 `--assumed`를 붙이세요.",
    ])


def emit(payload: Mapping[str, object]) -> int:
    data = json.dumps(payload, ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def handle_after_agent(payload: Mapping[str, object]) -> int:
    from core.adapter_observation import finish_turn, resolve_active_invocation, restart_blocked_turn
    from core.verify_state import evaluate_stop

    root = project_root(payload)
    invocation = canonical_invocation(payload, "stop", "other", [], "", True, "")
    invocation = resolve_active_invocation(Path(root), invocation)
    _ = finish_turn(Path(root), invocation)
    assistant_text = ""
    request = mapping(payload.get("llm_request"))
    for message in reversed(mapping_sequence(request.get("messages"))):
        if message.get("role") in ["assistant", "model"]:
            assistant_text = str(message.get("content", ""))
            break
    stop_payload = {
        "project_root": root,
        "stop_hook_active": False,
        "assistant_text": assistant_text,
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "attribution": invocation.scorecard_attribution,
    }
    result = evaluate_stop(stop_payload)
    if result.get("decision") == "block":
        restart_blocked_turn(Path(root), invocation)
        return emit({"decision": "block", "reason": str(result.get("reason", ""))})
    return emit({"decision": "allow", "systemMessage": str(result.get("message", "[smtw] Stop gate allow."))})

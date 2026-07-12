from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import sys
from typing import TypeGuard


def _string_list(value: object) -> list[str]:
    if not _string_sequence(value):
        return []
    return [item for item in value if item]


def _string_sequence(value: object) -> TypeGuard[Sequence[str]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes) and all(isinstance(item, str) for item in value)


def _context(result: Mapping[str, object], ambiguity: Mapping[str, object], intent_command: str) -> str:
    packs = result.get("packs")
    needs_goals = result.get("needs_goals") is True
    intent_required = ambiguity.get("ambiguous") is True
    lines = [
        "fable-lite 활성화: 작업 규율을 절차로 적용하세요.",
        f"mode={result.get('mode', 'quick')}",
    ]
    if isinstance(packs, list) and "investigation" in packs:
        lines.extend(
            [
                "조사 팩 준수 필수: 출력에 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:`를 포함하세요.",
                "수정 전 재현과 경쟁 가설을 먼저 기록하세요.",
            ]
        )
    if isinstance(packs, list) and "verification-grounding" in packs:
        lines.append("렌더/실행 산출물은 RUN→OBSERVE→FIX→RE-RUN 증거 없이는 완료하지 마세요.")
    if needs_goals:
        lines.append("2+ 스토리 작업입니다. goals 체크포인트를 만들거나 사용자에게 명시 확인을 받으세요.")
    if intent_required:
        lines.extend(
            [
                "의도 확인 필요: 수정 전 `확인질문 N:` 형식으로 목표/범위/비목표를 최대 3개만 물어보세요.",
                f"확인되면 정확히 이 명령을 그대로 실행하세요: `{intent_command}`",
                "저장소 루트에서 직접 실행 중이면 `python -m fable_lite intent set ...`도 가능하지만, 플러그인 사용 중에는 위 절대경로 명령을 우선하세요.",
                "사용자가 묻지 말라고 한 경우에만 합리적 가정을 기록하고 명령 끝에 `--assumed`를 붙이세요.",
            ]
        )
    return "\n".join(lines)


def _packs_with_intent(packs_value: object, intent_required: bool) -> list[str]:
    packs = _string_list(packs_value)
    if intent_required and "intent-interview" not in packs:
        packs.append("intent-interview")
    return packs


def _score(value: object) -> int:
    return value if isinstance(value, int) else 0


def _fail_open(message: str) -> int:
    data = json.dumps({"systemMessage": f"fable-lite fail-open: {message}"}, ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def main() -> int:
    try:
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from adapters.claude_code.common import canonical_invocation, emit, project_root, read_payload
        from adapters.intent_command import intent_set_command
        payload = read_payload()
        from core.ambiguity import evaluate_ambiguity
        from core.classify import classify_prompt
        from core.intent import clear_intent
        from core.ledger import record_event
        from core.adapter_observation import start_turn

        prompt_value = payload.get("prompt")
        prompt = prompt_value if isinstance(prompt_value, str) else ""
        root_value = project_root(payload)
        invocation = canonical_invocation(payload, "turn_start", "other", [], "", True, "")
        observation = start_turn(Path(root_value), invocation)
        _ = clear_intent(root_value)
        result = classify_prompt({"prompt": prompt})
        command_template = intent_set_command(__file__)
        ambiguity = evaluate_ambiguity(
            {
                "project_root": root_value,
                "prompt": prompt,
                "requested_paths": _string_list(result.get("requested_paths")),
            }
        )
        intent_required = ambiguity.get("ambiguous") is True
        packs = _packs_with_intent(result.get("packs"), intent_required)
        result_for_context = {**result, "packs": packs}
        requires_compliance = "investigation" in packs
        _ = record_event(
            {
                "project_root": root_value,
                "event": "prompt",
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
                "turn_id": invocation.turn_id,
                "baseline_snapshot_id": observation.baseline_snapshot_id,
                "current_snapshot_id": observation.snapshot_id,
                "provenance_incomplete": observation.incomplete,
                "task_mode": result["mode"],
                "prompt": prompt,
                "packs": packs,
                "needs_goals": result["needs_goals"],
                "intent_required": intent_required,
                "ambiguity_score": _score(ambiguity.get("ambiguity_score")),
                "requires_investigation_compliance": requires_compliance,
            }
        )
        return emit(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": _context(result_for_context, ambiguity, command_template),
                }
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

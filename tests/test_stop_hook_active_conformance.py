from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import TypeAlias, cast

from core.ledger import load_ledger, record_event


ROOT = Path(__file__).resolve().parents[1]
HOSTS = ("claude_code", "codex_cli", "antigravity")

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _run_adapter(args: list[str], payload: JsonObject) -> JsonObject:
    process = subprocess.run(
        [sys.executable, *args],
        input=json.dumps(payload, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert process.returncode == 0, process.stderr
    raw = cast(object, json.loads(process.stdout))
    assert isinstance(raw, dict)
    return cast(JsonObject, raw)


def _write_claude_transcript(case_root: Path, assistant_text: str) -> Path:
    transcript = case_root.parent / f"{case_root.name}-claude-transcript.jsonl"
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": assistant_text}],
        },
    }
    _ = transcript.write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return transcript


def _run_stop(host: str, case_root: Path, assistant_text: str, *, active: bool) -> JsonObject:
    if host == "claude_code":
        transcript = _write_claude_transcript(case_root, assistant_text)
        return _run_adapter(
            [str(ROOT / "adapters" / "claude_code" / "stop.py")],
            {
                "hook_event_name": "Stop",
                "cwd": str(case_root),
                "transcript_path": str(transcript),
                "stop_hook_active": active,
                "session_id": "claude-stop-conformance",
            },
        )
    if host == "codex_cli":
        return _run_adapter(
            [str(ROOT / "adapters" / "codex_cli" / "stop.py")],
            {
                "hook_event_name": "Stop",
                "cwd": str(case_root),
                "last_assistant_message": assistant_text,
                "stop_hook_active": active,
                "session_id": "codex-stop-conformance",
                "turn_id": "turn-1",
            },
        )

    assert host == "antigravity"
    # Antigravity 1.1.1 did not fire hooks live (docs/reviews/p9-agy-live-hooks.md).
    # This case proves payload-injection conformance only, not host-engine activation.
    return _run_adapter(
        [str(ROOT / "adapters" / "antigravity" / "oma_hook.py"), "AfterAgent"],
        {
            "cwd": str(case_root),
            "termination_reason": "completed",
            "llm_request": {
                "messages": [{"role": "assistant", "content": assistant_text}],
            },
            "session_id": "agy-stop-conformance",
        },
    )


def _decision(result: JsonObject) -> str:
    encoded = json.dumps(result, ensure_ascii=False).casefold()
    assert "fail-open" not in encoded
    return "block" if result.get("decision") in {"block", "continue"} else "allow"


def _seed_changed_turn(
    case_root: Path,
    *,
    requires_investigation: bool = False,
    path: str = "app.py",
    kind: str = "code",
) -> None:
    packs = ["investigation"] if requires_investigation else []
    identity = _turn_identity(case_root)
    _ = record_event(
        identity
        | {
            "project_root": str(case_root),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": f"{path} 수정해줘",
            "packs": packs,
            "requires_investigation_compliance": requires_investigation,
        }
    )
    _ = record_event(
        identity
        | {
            "project_root": str(case_root),
            "event": "change",
            "path": path,
            "kind": kind,
        }
    )


def _turn_identity(case_root: Path) -> JsonObject:
    identities: dict[str, JsonObject] = {
        "claude_code": {
            "host": "claude_code",
            "session_id": "claude-stop-conformance",
            "agent": "claude",
        },
        "codex_cli": {
            "host": "codex_cli",
            "session_id": "codex-stop-conformance",
            "agent": "codex",
            "turn_id": "turn-1",
        },
        "antigravity": {
            "host": "antigravity",
            "session_id": "agy-stop-conformance",
            "agent": "antigravity",
        },
    }
    return identities[case_root.name]


def test_unverified_change_blocks_twice_then_allows_across_stop_payloads(
    tmp_path: Path,
) -> None:
    for host in HOSTS:
        case_root = tmp_path / host
        case_root.mkdir()
        # Given: every host sees the same deep turn with an unverified code change.
        _seed_changed_turn(case_root)

        # When: the initial Stop and two follow-up Stop attempts are injected.
        decisions = [
            _decision(_run_stop(host, case_root, "작업 완료", active=False)),
            _decision(_run_stop(host, case_root, "작업 완료", active=True)),
            _decision(_run_stop(host, case_root, "작업 완료", active=True)),
        ]

        # Then: all hosts honor the shared two-block fail-open cap.
        assert decisions == ["block", "block", "allow"]
        assert load_ledger({"project_root": str(case_root)})["stop_blocks"] == 2


def test_fresh_verification_allows_immediately_after_first_stop_block(
    tmp_path: Path,
) -> None:
    for host in HOSTS:
        case_root = tmp_path / host
        case_root.mkdir()
        # Given: an unverified change has already caused the first Stop block.
        _seed_changed_turn(case_root)
        first = _run_stop(host, case_root, "작업 완료", active=False)
        assert _decision(first) == "block"

        # When: a successful verification is recorded after the change epoch.
        _ = record_event(
            _turn_identity(case_root)
            | {
                "project_root": str(case_root),
                "event": "verification",
                "command": "python -m pytest tests/ -q",
                "success": True,
                "evidence": "1 passed",
            }
        )
        second = _run_stop(host, case_root, "검증 완료", active=True)

        # Then: the next Stop allows instead of consuming the second block.
        assert _decision(second) == "allow"
        ledger = load_ledger({"project_root": str(case_root)})
        results = cast(list[JsonObject], ledger["verification_results"])
        assert cast(int, results[-1]["seq"]) > cast(int, ledger["last_change_seq"])
        assert ledger["stop_blocks"] == 1


def test_n1_marker_block_recovers_with_compliant_stop_payload(
    tmp_path: Path,
) -> None:
    for host in HOSTS:
        case_root = tmp_path / host
        case_root.mkdir()
        # Given: a docs-only investigation turn changed a file but omitted N1 markers.
        _seed_changed_turn(
            case_root,
            requires_investigation=True,
            path="README.md",
            kind="docs",
        )
        first = _run_stop(host, case_root, "원인을 찾아 수정했습니다.", active=False)
        assert _decision(first) == "block"

        # When: the follow-up response supplies all required investigation markers.
        compliant = "\n".join(
            (
                "가설 1: 입력 경계 문제",
                "가설 2: 상태 갱신 문제",
                "가설 3: 호스트 payload 문제",
                "기각: 상태 갱신 문제는 재현되지 않음",
                "증거: 동일 fixture 재실행 통과",
            )
        )
        second = _run_stop(host, case_root, compliant, active=True)

        # Then: N1 recovers immediately; docs-only stays exempt from verification.
        assert _decision(second) == "allow"
        assert load_ledger({"project_root": str(case_root)})["stop_blocks"] == 1

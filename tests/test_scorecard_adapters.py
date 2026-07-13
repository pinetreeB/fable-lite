from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Literal, TypeAlias, assert_never
from unittest.mock import patch

from core.ledger import save_ledger
from core.ledger_v2 import default_v2_ledger


ROOT = Path(__file__).resolve().parents[1]
SCORECARD_ENV = "FABLE_LITE_SCORECARD"
SCORECARD_LINE = "[smtw] 이번 세션 · 차단 시도 2 · 회복 턴 1 · cap 통과 0"
BASE_ALLOW_LINE = "[smtw] Stop gate allow."

HostName: TypeAlias = Literal["claude_code", "codex_cli", "antigravity"]
CacheState: TypeAlias = Literal["missing", "no_activity", "incomplete", "malformed"]
HOSTS: tuple[HostName, ...] = ("claude_code", "codex_cli", "antigravity")
CACHE_STATES: tuple[CacheState, ...] = ("missing", "no_activity", "incomplete", "malformed")
JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _agent(host: HostName) -> str:
    match host:
        case "claude_code":
            return "claude"
        case "codex_cli":
            return "codex"
        case "antigravity":
            return "antigravity"
        case unreachable:
            assert_never(unreachable)


def _session_id(host: HostName) -> str:
    return f"{host}-scorecard-session"


def _payload(host: HostName, root: Path) -> JsonObject:
    common: JsonObject = {
        "cwd": str(root),
        "session_id": _session_id(host),
        "turn_id": "turn-scorecard",
    }
    match host:
        case "claude_code":
            common["hook_event_name"] = "Stop"
            common["stop_hook_active"] = False
            return common
        case "codex_cli":
            common["hook_event_name"] = "Stop"
            common["last_assistant_message"] = "완료"
            common["stop_hook_active"] = False
            return common
        case "antigravity":
            common["termination_reason"] = "completed"
            common["llm_request"] = {
                "messages": [{"role": "assistant", "content": "완료"}],
            }
            return common
        case unreachable:
            assert_never(unreachable)


def _adapter_args(host: HostName) -> list[str]:
    match host:
        case "claude_code":
            return [str(ROOT / "adapters" / "claude_code" / "stop.py")]
        case "codex_cli":
            return [str(ROOT / "adapters" / "codex_cli" / "stop.py")]
        case "antigravity":
            return [
                str(ROOT / "adapters" / "antigravity" / "oma_hook.py"),
                "AfterAgent",
            ]
        case unreachable:
            assert_never(unreachable)


def _run_stop(
    host: HostName,
    root: Path,
    env_override: Mapping[str, str] | None = None,
) -> JsonObject:
    env = os.environ.copy()
    env.pop(SCORECARD_ENV, None)
    if env_override is not None:
        env.update(env_override)
    process = subprocess.run(
        [sys.executable, *_adapter_args(host)],
        input=json.dumps(_payload(host, root), ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert isinstance(result, dict)
    return result


def _cache_entry(host: HostName, *, complete: bool = True, activity: bool = True) -> JsonObject:
    return {
        "host": host,
        "session_id": _session_id(host),
        "agent": _agent(host),
        "activated_at": "2026-07-13T12:00:00+00:00",
        "observed": True,
        "complete": complete,
        "blocked_attempts": 2 if activity else 0,
        "recovered_scopes": 1 if activity else 0,
        "resolved_attempts": 2 if activity else 0,
        "cap_allows": 0,
        "unresolved_block_ids": [],
        "latest_turn_id": "turn-scorecard",
        "first_occurred_at": "2026-07-13T12:00:01+00:00" if activity else None,
        "last_occurred_at": "2026-07-13T12:00:02+00:00" if activity else None,
        "by_reason": {},
    }


def _seed_cache(root: Path, host: HostName, entry: JsonValue) -> None:
    ledger = default_v2_ledger()
    ledger["prompt"] = "scorecard adapter test"
    ledger["agent"] = "default"
    key = f"{host}:{_session_id(host)}:{_agent(host)}"
    ledger["scorecard_cache"] = {key: entry}
    save_ledger({"project_root": str(root)}, ledger)


def _seed_without_cache(root: Path) -> None:
    ledger = default_v2_ledger()
    ledger["prompt"] = "scorecard adapter test"
    ledger["agent"] = "default"
    save_ledger({"project_root": str(root)}, ledger)


def _system_message(result: JsonObject) -> str:
    message = result.get("systemMessage")
    assert isinstance(message, str)
    return message


def _scorecard_lines(result: JsonObject) -> list[str]:
    return [
        line
        for line in _system_message(result).splitlines()
        if line.startswith("[smtw] 이번 세션")
    ]


def _captured_stop_payload(host: HostName, root: Path) -> JsonObject:
    from core import adapter_observation, verify_state

    captured: list[JsonObject] = []
    emitted: list[JsonObject] = []

    def evaluate_stop(payload: Mapping[str, JsonValue]) -> JsonObject:
        captured.append(dict(payload))
        return {"decision": "allow", "message": BASE_ALLOW_LINE}

    def emit(payload: Mapping[str, JsonValue]) -> int:
        emitted.append(dict(payload))
        return 0

    payload = _payload(host, root)
    with ExitStack() as stack:
        stack.enter_context(patch.object(verify_state, "evaluate_stop", evaluate_stop))
        stack.enter_context(
            patch.object(adapter_observation, "finish_turn", return_value=None)
        )
        stack.enter_context(
            patch.object(
                adapter_observation,
                "resolve_active_invocation",
                side_effect=lambda _root, invocation: invocation,
            )
        )
        match host:
            case "claude_code":
                from adapters.claude_code import common, stop

                stack.enter_context(patch.object(common, "read_payload", return_value=payload))
                stack.enter_context(patch.object(common, "emit", emit))
                assert stop.main() == 0
            case "codex_cli":
                from adapters.codex_cli import common, stop

                stack.enter_context(patch.object(common, "read_payload", return_value=payload))
                stack.enter_context(patch.object(common, "emit", emit))
                stack.enter_context(
                    patch.object(stop, "_run_process_reaper", return_value=None)
                )
                assert stop.main() == 0
            case "antigravity":
                from adapters.antigravity import hook_common

                stack.enter_context(patch.object(hook_common, "emit", emit))
                assert hook_common.handle_after_agent(payload) == 0
            case unreachable:
                assert_never(unreachable)
    assert emitted
    assert len(captured) == 1
    return captured[0]


def test_stop_when_raw_payload_has_no_agent_passes_canonical_identity(tmp_path: Path) -> None:
    # Given: each real host Stop payload has session/turn IDs but no agent field.
    roots: dict[HostName, Path] = {host: tmp_path / host for host in HOSTS}
    for root in roots.values():
        root.mkdir()

    # When: each Stop handler normalizes and evaluates its payload.
    captured: dict[HostName, JsonObject] = {
        host: _captured_stop_payload(host, roots[host]) for host in HOSTS
    }

    # Then: the canonical host/session/default-agent/turn identity is always supplied.
    assert captured == {
        host: {
            "project_root": str(roots[host]),
            "stop_hook_active": False,
            "assistant_text": "" if host == "claude_code" else "완료",
            "host": host,
            "agent": _agent(host),
            "session_id": _session_id(host),
            "turn_id": "turn-scorecard",
            "attribution": "exact",
        }
        for host in HOSTS
    }


def test_canonical_invocation_marks_synthesized_identity_for_all_hosts() -> None:
    # Given: each host receives one raw payload without host/agent/session identity.
    from adapters.antigravity.hook_common import canonical_invocation as antigravity
    from adapters.claude_code.common import canonical_invocation as claude
    from adapters.codex_cli.common import canonical_invocation as codex

    factories = (claude, codex, antigravity)

    # When/Then: fallback identity is explicitly attributable as legacy_default.
    for factory in factories:
        invocation = factory({}, "stop", "other", [], "", True, "")
        assert invocation.identity_synthetic is True
        assert invocation.scorecard_attribution == "legacy_default"
    real_session_id = "92d09f11-808b-474c-9d48-d173b207ab4b"
    exact_invocations = tuple(
        factory(
            {"session_id": real_session_id},
            "stop",
            "other",
            [],
            "",
            True,
            "",
        )
        for factory in factories
    )
    assert [item.identity_synthetic for item in exact_invocations] == [False] * 3
    assert [item.scorecard_attribution for item in exact_invocations] == ["exact"] * 3


def test_stop_allow_when_cache_has_activity_appends_exact_shared_line(tmp_path: Path) -> None:
    # Given: every current canonical session has two blocks and one recovery scope.
    roots: dict[HostName, Path] = {host: tmp_path / host for host in HOSTS}
    for host, root in roots.items():
        root.mkdir()
        _seed_cache(root, host, _cache_entry(host))

    # When: each host Stop allow path renders its cached summary.
    results: dict[HostName, JsonObject] = {
        host: _run_stop(host, roots[host]) for host in HOSTS
    }

    # Then: all hosts expose the same exact count-only line in their native shape.
    assert {
        host: _scorecard_lines(result) for host, result in results.items()
    } == {host: [SCORECARD_LINE] for host in HOSTS}
    assert set(results["claude_code"]) == {"systemMessage"}
    assert set(results["codex_cli"]) == {"systemMessage"}
    assert results["antigravity"].get("decision") == "allow"
    assert set(results["antigravity"]) == {"decision", "systemMessage"}
    messages = [_system_message(results[host]) for host in HOSTS]
    assert messages == [messages[0]] * len(HOSTS)


def test_stop_allow_when_scorecard_is_disabled_omits_line(tmp_path: Path) -> None:
    # Given: active caches exist but the compatibility env opt-out is exact zero.
    roots: dict[HostName, Path] = {host: tmp_path / host for host in HOSTS}
    for host, root in roots.items():
        root.mkdir()
        _seed_cache(root, host, _cache_entry(host))

    # When: every Stop host runs with Scorecard disabled.
    results: dict[HostName, JsonObject] = {
        host: _run_stop(host, roots[host], {SCORECARD_ENV: "0"})
        for host in HOSTS
    }

    # Then: gate output remains available without a Scorecard line.
    assert all(_scorecard_lines(result) == [] for result in results.values())


def test_stop_allow_when_cache_is_not_renderable_omits_line_fail_open(
    tmp_path: Path,
) -> None:
    # Given: each host has missing, inactive, incomplete, and malformed cache truth in turn.
    results: dict[str, JsonObject] = {}
    for host in HOSTS:
        for cache_state in CACHE_STATES:
            root = tmp_path / f"{host}-{cache_state}"
            root.mkdir()
            match cache_state:
                case "missing":
                    _seed_without_cache(root)
                case "no_activity":
                    _seed_cache(root, host, _cache_entry(host, activity=False))
                case "incomplete":
                    _seed_cache(root, host, _cache_entry(host, complete=False))
                case "malformed":
                    malformed = _cache_entry(host)
                    malformed["activated_at"] = "malformed"
                    _seed_cache(root, host, malformed)
                case unreachable:
                    assert_never(unreachable)

            # When: Stop evaluates the otherwise routine allow path.
            results[f"{host}:{cache_state}"] = _run_stop(host, root)

    # Then: no invented zero/count is shown and malformed Scorecard data cannot block Stop.
    assert all(_scorecard_lines(result) == [] for result in results.values())
    assert all(result.get("decision") != "block" for result in results.values())

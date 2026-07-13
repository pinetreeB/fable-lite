from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import TypeAlias

import pytest


ROOT = Path(__file__).resolve().parents[1]

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _run_scorecard(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "fable_lite", "scorecard", "--root", str(root), *args],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONPATH": python_path,
        },
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _transition(
    event_id: str,
    occurred_at: datetime,
    *,
    session_id: str,
    agent: str,
    action: str = "block",
    resolves: tuple[str, ...] = (),
    attribution: str = "exact",
    extra: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    return {
        "scorecard_schema_version": 1,
        "event": "gate_transition",
        "event_id": event_id,
        "host": "codex_cli",
        "session_id": session_id,
        "agent": agent,
        "turn_id": f"turn-{event_id}",
        "reason_code": "stop.verification_missing",
        "action": action,
        "resolves": list(resolves),
        "resolution": "verification" if action == "recover" else "none",
        "attribution": attribution,
        "occurred_at": occurred_at.isoformat(),
        **(extra or {}),
    }


def _write_journal(
    root: Path, events: list[dict[str, JsonValue]], *, partial_tail: bool = False
) -> None:
    path = root / ".fable-lite" / "scorecard" / "gates.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False) for event in events]
    suffix = "\n{\"partial\":" if partial_tail else "\n"
    path.write_text("\n".join(lines) + suffix, encoding="utf-8", newline="\n")


def _write_agent_events(
    root: Path, agent: str, events: list[dict[str, JsonValue]]
) -> None:
    path = root / ".fable-lite" / "agents" / f"{agent}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in events),
        encoding="utf-8",
        newline="\n",
    )


def _verification(
    occurred_at: datetime,
    *,
    session_id: str,
    agent: str,
    success: bool,
    extra: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    return {
        "event": "verification",
        "host": "codex_cli",
        "session_id": session_id,
        "agent": agent,
        "turn_id": f"turn-{session_id}",
        "timestamp": occurred_at.isoformat(),
        "success": success,
        "command": "pytest",
        "evidence": "tests completed",
        **(extra or {}),
    }


def _json_result(result: subprocess.CompletedProcess[str]) -> JsonValue:
    assert result.returncode == 0, result.stderr
    parsed: JsonValue = json.loads(result.stdout)
    return parsed


def _objects(value: JsonValue) -> list[dict[str, JsonValue]]:
    if isinstance(value, dict):
        return [value, *(item for child in value.values() for item in _objects(child))]
    if isinstance(value, list):
        return [item for child in value for item in _objects(child)]
    return []


def _session_row(
    value: JsonValue, *, session_id: str, agent: str
) -> dict[str, JsonValue]:
    rows = [
        item
        for item in _objects(value)
        if item.get("session_id") == session_id
        and item.get("agent") == agent
        and "blocked_attempts" in item
    ]
    assert len(rows) == 1, rows
    return rows[0]


def test_scorecard_cli_sc_cli_01_help_exposes_documented_options(tmp_path: Path) -> None:
    # Given: the installed module entry point.
    # When: scorecard help is requested through the real subprocess surface.
    result = _run_scorecard(tmp_path, "--help")

    # Then: every SSOT option is registered on the scorecard parser.
    assert result.returncode == 0, result.stderr
    assert all(option in result.stdout for option in ("--root", "--session", "--days", "--all", "--json"))


def test_scorecard_cli_sc_cli_02_separates_agents_unattributed_and_cap(
    tmp_path: Path,
) -> None:
    # Given: two agents share a session id, while legacy and cap events also exist.
    now = datetime.now(UTC)
    events = [
        _transition("c-block-1", now, session_id="shared", agent="codex"),
        _transition("c-block-2", now, session_id="shared", agent="codex"),
        _transition(
            "c-recover",
            now,
            session_id="shared",
            agent="codex",
            action="recover",
            resolves=("c-block-1", "c-block-2"),
        ),
        _transition("c-block-3", now, session_id="shared", agent="codex"),
        _transition(
            "c-cap",
            now,
            session_id="shared",
            agent="codex",
            action="cap_allow",
            resolves=("c-block-3",),
        ),
        _transition("a-block", now, session_id="shared", agent="agy"),
        _transition(
            "legacy-block",
            now,
            session_id="legacy-session",
            agent="default",
            attribution="legacy_default",
        ),
    ]
    _write_journal(tmp_path, events)

    # When: all scorecard data is rendered as machine-readable output.
    result = _run_scorecard(tmp_path, "--all", "--json")
    data = _json_result(result)

    # Then: units stay distinct, agents do not merge, and legacy is unattributed.
    codex = _session_row(data, session_id="shared", agent="codex")
    agy = _session_row(data, session_id="shared", agent="agy")
    legacy = _session_row(data, session_id="legacy-session", agent="default")
    assert (codex["blocked_attempts"], codex["recovered_scopes"]) == (3, 1)
    assert (codex["resolved_attempts"], codex["cap_allows"]) == (2, 1)
    assert (agy["blocked_attempts"], agy["recovered_scopes"], agy["cap_allows"]) == (1, 0, 0)
    assert legacy["blocked_attempts"] == 1
    assert "unattributed" in result.stdout.lower() or "미귀속" in result.stdout


def _filter_fixture(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _write_journal(
        tmp_path,
        [
            _transition("recent-a", now - timedelta(hours=2), session_id="recent-a", agent="codex"),
            _transition("recent-b", now - timedelta(days=1), session_id="recent-b", agent="agy"),
            _transition("old", now - timedelta(days=10), session_id="old", agent="codex"),
        ],
    )


def _session_ids(data: JsonValue) -> set[str]:
    return {
        str(item["session_id"])
        for item in _objects(data)
        if "blocked_attempts" in item and "session_id" in item
    }


def test_scorecard_cli_sc_cli_03_filters_one_session(tmp_path: Path) -> None:
    # Given: two recent sessions and one old session.
    _filter_fixture(tmp_path)
    # When: one canonical session is selected.
    selected = _session_ids(_json_result(_run_scorecard(tmp_path, "--session", "recent-a", "--json")))
    # Then: no other session is rendered.
    assert selected == {"recent-a"}


def test_scorecard_cli_sc_cli_03_filters_days(tmp_path: Path) -> None:
    # Given: two recent sessions and one old session.
    _filter_fixture(tmp_path)
    # When: the two-day view is selected.
    selected = _session_ids(_json_result(_run_scorecard(tmp_path, "--days", "2", "--json")))
    # Then: only recent sessions are rendered.
    assert selected == {"recent-a", "recent-b"}


def test_scorecard_cli_sc_cli_03_all_disables_day_filter(tmp_path: Path) -> None:
    # Given: two recent sessions and one old session.
    _filter_fixture(tmp_path)
    # When: the all-time view is selected.
    selected = _session_ids(_json_result(_run_scorecard(tmp_path, "--all", "--json")))
    # Then: all canonical sessions are rendered.
    assert selected == {"recent-a", "recent-b", "old"}


def _observation_fixture(tmp_path: Path) -> None:
    activated_at = datetime(2026, 7, 13, 12, tzinfo=UTC)
    _write_journal(
        tmp_path,
        [_transition("activation", activated_at, session_id="activation", agent="codex")],
    )
    _write_agent_events(
        tmp_path,
        "codex",
        [
            _verification(activated_at - timedelta(hours=1), session_id="before", agent="codex", success=True),
            _verification(activated_at + timedelta(hours=1), session_id="after", agent="codex", success=True),
        ],
    )


def _assert_empty_session(tmp_path: Path, session_id: str, *, observed: bool) -> None:
    data = _json_result(_run_scorecard(tmp_path, "--session", session_id, "--json"))
    row = _session_row(data, session_id=session_id, agent="codex")
    assert (row["observed"], row["blocked_attempts"]) == (observed, 0)


def test_scorecard_cli_sc_cli_04_pre_activation_is_na(tmp_path: Path) -> None:
    # Given: an exact session before scorecard activation.
    _observation_fixture(tmp_path)
    # When: the historical session is queried.
    _assert_empty_session(tmp_path, "before", observed=False)
    # Then: the helper asserts N/A instead of a fabricated observed zero.


def test_scorecard_cli_sc_cli_04_post_activation_is_zero(tmp_path: Path) -> None:
    # Given: an exact session after scorecard activation.
    _observation_fixture(tmp_path)
    # When: the observed no-activity session is queried.
    _assert_empty_session(tmp_path, "after", observed=True)
    # Then: the helper asserts a truthful zero.


def test_scorecard_cli_sc_cli_05_marks_partial_journal_incomplete(tmp_path: Path) -> None:
    # Given: a valid event followed by a writer-crash tail.
    _write_journal(
        tmp_path,
        [_transition("valid", datetime.now(UTC), session_id="partial", agent="codex")],
        partial_tail=True,
    )

    # When: the journal is rebuilt through the CLI.
    data = _json_result(_run_scorecard(tmp_path, "--all", "--json"))

    # Then: the valid prefix survives without claiming completeness.
    row = _session_row(data, session_id="partial", agent="codex")
    assert row["blocked_attempts"] == 1
    assert any(item.get("complete") is False for item in _objects(data))


def _private_result(tmp_path: Path, *output_mode: str) -> tuple[subprocess.CompletedProcess[str], set[str]]:
    project = tmp_path / "PRIVATE_PROJECT_PATH_TOKEN"
    now = datetime.now(UTC)
    secrets: dict[str, JsonValue] = {
        "project_path": "PRIVATE_PROJECT_PATH_TOKEN",
        "path": "SECRET_FILE_PATH_TOKEN.py",
        "file": "SECRET_FILE_NAME_TOKEN.txt",
        "prompt": "SECRET_PROMPT_TOKEN",
        "message": "SECRET_MESSAGE_TOKEN",
    }
    _write_journal(
        project,
        [_transition("private", now, session_id="private", agent="codex", extra=secrets)],
    )
    _write_agent_events(
        project,
        "codex",
        [_verification(now, session_id="private", agent="codex", success=False, extra=secrets)],
    )
    return _run_scorecard(project, "--all", *output_mode), {str(value) for value in secrets.values()}


def _assert_private(result: subprocess.CompletedProcess[str], secrets: set[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert all(secret not in f"{result.stdout}\n{result.stderr}" for secret in secrets)


def test_scorecard_cli_sc_cli_06_human_never_leaks_sensitive_context(tmp_path: Path) -> None:
    # Given: hostile journal/log extras containing share-unsafe context.
    # When: human output is rendered.
    result, secrets = _private_result(tmp_path)
    # Then: only counts and statistics are exposed.
    _assert_private(result, secrets)


def test_scorecard_cli_sc_cli_06_json_never_leaks_sensitive_context(tmp_path: Path) -> None:
    # Given: hostile journal/log extras containing share-unsafe context.
    # When: JSON output is rendered.
    result, secrets = _private_result(tmp_path, "--json")
    # Then: only counts and statistics are exposed.
    _assert_private(result, secrets)


def test_scorecard_cli_human_renders_period_reasons_cap_and_na_without_zero(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    _write_journal(
        tmp_path,
        [
            _transition("activation", now, session_id="active", agent="codex"),
            _transition(
                "cap",
                now,
                session_id="active",
                agent="codex",
                action="cap_allow",
                resolves=("activation",),
            ),
        ],
    )
    _write_agent_events(
        tmp_path,
        "codex",
        [
            _verification(
                now - timedelta(hours=1),
                session_id="before",
                agent="codex",
                success=True,
            )
        ],
    )

    active = _run_scorecard(tmp_path, "--session", "active")
    before = _run_scorecard(tmp_path, "--session", "before")

    assert active.returncode == 0, active.stderr
    assert "기간 · session=active" in active.stdout
    assert "stop.verification_missing" in active.stdout
    assert "cap 통과(미해결) 1" in active.stdout
    assert before.returncode == 0, before.stderr
    assert "미관측(N/A)" in before.stdout
    assert "차단 0" not in before.stdout


def test_scorecard_cli_malformed_agent_log_marks_output_incomplete(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    _write_journal(
        tmp_path,
        [_transition("valid", now, session_id="valid", agent="codex")],
    )
    path = tmp_path / ".fable-lite" / "agents" / "codex.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event":"verification"\n', encoding="utf-8")

    data = _json_result(_run_scorecard(tmp_path, "--all", "--json"))

    assert isinstance(data, dict)
    assert data["complete"] is False
    row = _session_row(data, session_id="valid", agent="codex")
    assert row["complete"] is False


@pytest.mark.parametrize(("event_count", "budget_seconds"), ((10_000, 10), (100_000, 30)))
def test_scorecard_cli_large_journal_latency(
    tmp_path: Path, event_count: int, budget_seconds: int
) -> None:
    occurred_at = datetime.now(UTC)
    path = tmp_path / ".fable-lite" / "scorecard" / "gates.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for index in range(event_count):
            event_id = f"event-{index}"
            action = "block" if index % 2 == 0 else "recover"
            resolves = () if action == "block" else (f"event-{index - 1}",)
            event = _transition(
                event_id,
                occurred_at,
                session_id="large",
                agent="codex",
                action=action,
                resolves=resolves,
            )
            _ = handle.write(json.dumps(event, separators=(",", ":")) + "\n")

    started = time.perf_counter()
    result = _run_scorecard(tmp_path, "--all", "--json")
    elapsed = time.perf_counter() - started

    assert result.returncode == 0, result.stderr
    assert elapsed <= budget_seconds
    row = _session_row(_json_result(result), session_id="large", agent="codex")
    assert row["blocked_attempts"] == event_count // 2
    assert row["recovered_scopes"] == event_count // 2

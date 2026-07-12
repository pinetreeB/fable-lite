from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock, skipUnless

from contrib.codex_process_reaper import decision
from contrib.codex_process_reaper import windows_runtime
from core.ledger import record_event


ROOT = Path(__file__).resolve().parents[1]
REAPER_ROOT = ROOT / "contrib" / "codex_process_reaper"
CODEX_STOP = ROOT / "adapters" / "codex_cli" / "stop.py"


def test_codex_process_reaper_contract_files_exist() -> None:
    # Given: the design requires a Windows-only contrib implementation and operator guide.
    expected = (
        REAPER_ROOT / "decision.py",
        REAPER_ROOT / "reaper.py",
        REAPER_ROOT / "README.ko.md",
    )

    # When: the contribution layout is inspected.
    missing = [path.name for path in expected if not path.is_file()]

    # Then: every contract surface is present under contrib, outside core.
    assert missing == []


def test_candidate_patterns_match_only_the_v2_whitelist() -> None:
    # Given: process records that cover every v2 whitelist family and adjacent negatives.
    cases = (
        ("node.exe", "node context7-mcp/index.js", True),
        ("node.exe", "node codegraph/server.js", True),
        ("node.exe", "node chrome-devtools-mcp/index.js", True),
        ("node.exe", r"node @modelcontextprotocol\server-memory\dist\index.js", True),
        ("node.exe", "node mcp-bundle/index.js", True),
        ("node.exe", "node lsp-daemon/index.js", True),
        ("node.exe", "node git-bash-mcp/index.js", True),
        ("node.exe", r"node sisyphuslabs\omo\dist\mcp.js", True),
        ("node_repl.exe", "", True),
        ("node.exe", "node unrelated-server/index.js", False),
        ("python.exe", "python context7-mcp.py", False),
    )

    # When: the v2-compatible candidate matcher evaluates each record.
    actual = tuple(
        decision.is_reaper_candidate(
            decision.ProcessRecord(
                pid=index,
                parent_pid=1,
                name=name,
                command_line=command_line,
                created_at=datetime(2026, 7, 12, tzinfo=UTC),
            )
        )
        for index, (name, command_line, _) in enumerate(cases, start=10)
    )

    # Then: only whitelisted MCP node processes and node_repl qualify.
    assert actual == tuple(expected for _, _, expected in cases)


def test_pid_scope_and_initial_window_select_only_current_session_residue() -> None:
    # Given: nested Codex roots, a live hook chain, early tools, late residue, and other panes.
    first = datetime(2026, 7, 12, 1, 0, tzinfo=UTC)
    records = (
        decision.ProcessRecord(
            90, 1, "codex.exe", "outer", first - timedelta(minutes=1)
        ),
        decision.ProcessRecord(100, 90, "codex.exe", "inner", first),
        decision.ProcessRecord(110, 100, "powershell.exe", "hook shell", first),
        decision.ProcessRecord(111, 110, "python.exe", "stop.py", first),
        decision.ProcessRecord(120, 100, "node.exe", "node context7-mcp", first),
        decision.ProcessRecord(
            121, 100, "node_repl.exe", "", first + timedelta(minutes=5)
        ),
        decision.ProcessRecord(
            125, 100, "cmd.exe", "bridge", first + timedelta(minutes=6)
        ),
        decision.ProcessRecord(
            130, 125, "node.exe", "node codegraph", first + timedelta(minutes=6)
        ),
        decision.ProcessRecord(
            131, 130, "node_repl.exe", "", first + timedelta(minutes=7)
        ),
        decision.ProcessRecord(140, 100, "node.exe", "node lsp-daemon", None),
        decision.ProcessRecord(200, 1, "codex.exe", "other pane", first),
        decision.ProcessRecord(
            210, 200, "node.exe", "node context7-mcp", first + timedelta(minutes=8)
        ),
        decision.ProcessRecord(300, 1, "claude.exe", "claude pane", first),
        decision.ProcessRecord(
            310, 300, "node.exe", "node codegraph", first + timedelta(minutes=8)
        ),
    )

    # When: selection starts from the running Stop hook PID.
    result = decision.select_reap_decision(records, hook_pid=111)

    # Then: nearest Codex scope wins, unknown timestamps stay protected, and taskkill roots collapse.
    assert result.session_pid == 100
    assert result.scoped_candidate_pids == (120, 121, 130, 131, 140)
    assert result.protected_pids == (120, 121, 140)
    assert result.target_pids == (130, 131)
    assert result.termination_pids == (130,)
    assert result.outside_scope_candidate_pids == (210, 310)


def test_missing_or_cyclic_parent_chain_fails_closed_without_targets() -> None:
    # Given: a hook chain cycle with no reachable Codex process.
    records = (
        decision.ProcessRecord(10, 11, "python.exe", "stop.py", None),
        decision.ProcessRecord(11, 10, "powershell.exe", "hook", None),
        decision.ProcessRecord(20, 1, "node.exe", "node context7-mcp", None),
    )

    # When: the selector cannot prove session ownership.
    result = decision.select_reap_decision(records, hook_pid=10)

    # Then: fail-closed selection returns no target for the fail-open runner to kill.
    assert result.session_pid is None
    assert result.scoped_candidate_pids == ()
    assert result.target_pids == ()
    assert result.termination_pids == ()


@skipUnless(os.name == "nt", "Windows-only taskkill contract")
def test_windows_termination_uses_taskkill_tree_flag() -> None:
    # Given: taskkill reports a successful tree termination.
    completed = subprocess.CompletedProcess(
        args=["taskkill.exe"],
        returncode=0,
        stdout="SUCCESS",
        stderr="",
    )

    # When: one residue root is terminated.
    with mock.patch("subprocess.run", return_value=completed) as run:
        succeeded = windows_runtime.terminate_process_tree(42)

    # Then: the Windows tree flag is mandatory so child conhost/processes are included.
    assert succeeded is True
    assert run.call_args.args[0] == ["taskkill.exe", "/PID", "42", "/T", "/F"]


def test_reaper_is_disabled_by_default_without_log_side_effect(tmp_path: Path) -> None:
    # Given: no opt-in environment variable is present.
    log_path = tmp_path / "reaper.log"
    env = os.environ.copy()
    _ = env.pop("FABLE_LITE_CODEX_REAPER", None)
    env["FABLE_LITE_CODEX_REAPER_LOG"] = str(log_path)

    # When: the contrib entrypoint is invoked directly.
    result = subprocess.run(
        [sys.executable, "-m", "contrib.codex_process_reaper.reaper"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # Then: default-off is a silent no-op.
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert not log_path.exists()


@skipUnless(os.name == "nt", "Windows-only process snapshot")
def test_runtime_snapshot_failure_logs_error_and_exits_zero(tmp_path: Path) -> None:
    # Given: opt-in is enabled but the Windows process snapshot launcher is unavailable.
    log_path = tmp_path / "reaper.log"
    env = os.environ.copy()
    env.update(
        {
            "FABLE_LITE_CODEX_REAPER": "1",
            "FABLE_LITE_CODEX_REAPER_LOG": str(log_path),
            "FABLE_LITE_CODEX_REAPER_POWERSHELL": str(
                tmp_path / "missing-powershell.exe"
            ),
        }
    )

    # When: the real entrypoint hits the runtime failure boundary.
    result = subprocess.run(
        [sys.executable, "-m", "contrib.codex_process_reaper.reaper"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # Then: the Stop-compatible process remains fail-open and records only a log event.
    event = log_path.read_text(encoding="utf-8").splitlines()[-1]
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert '"event": "codex_process_reaper"' in event
    assert '"status": "error"' in event


@skipUnless(os.name == "nt", "Windows-only Stop/reaper integration")
def test_codex_stop_invokes_reaper_only_after_gate_allows(tmp_path: Path) -> None:
    # Given: one blocked project, one allowed project, and a harmless forced snapshot failure.
    blocked_root = tmp_path / "blocked"
    allowed_root = tmp_path / "allowed"
    blocked_root.mkdir()
    allowed_root.mkdir()
    log_path = tmp_path / "reaper.log"
    env = os.environ.copy()
    env.update(
        {
            "FABLE_LITE_CODEX_REAPER": "1",
            "FABLE_LITE_CODEX_REAPER_LOG": str(log_path),
            "FABLE_LITE_CODEX_REAPER_POWERSHELL": str(
                tmp_path / "missing-powershell.exe"
            ),
        }
    )
    _ = record_event(
        {
            "project_root": str(blocked_root),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "app.py 수정",
        }
    )
    _ = record_event(
        {
            "project_root": str(blocked_root),
            "event": "change",
            "path": "app.py",
            "kind": "code",
        }
    )

    # When: the unverified Stop blocks, followed by an answer-only Stop that allows.
    blocked = subprocess.run(
        [sys.executable, str(CODEX_STOP)],
        cwd=ROOT,
        env=env,
        input=json.dumps(
            {
                "cwd": str(blocked_root),
                "last_assistant_message": "완료",
                "stop_hook_active": False,
            },
            ensure_ascii=False,
        ),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_after_block = log_path.exists()
    allowed = subprocess.run(
        [sys.executable, str(CODEX_STOP)],
        cwd=ROOT,
        env=env,
        input=json.dumps(
            {
                "cwd": str(allowed_root),
                "last_assistant_message": "답변 완료",
                "stop_hook_active": False,
            },
            ensure_ascii=False,
        ),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # Then: blocked Stop never reaps; allowed Stop invokes fail-open contrib and still exits zero.
    assert '"decision": "block"' in blocked.stdout
    assert log_after_block is False
    assert allowed.returncode == 0
    assert '"systemMessage"' in allowed.stdout
    assert '"status": "error"' in log_path.read_text(encoding="utf-8").splitlines()[-1]


def test_operator_guide_documents_opt_in_disable_and_hourly_backstop() -> None:
    # Given/When: the Korean operator guide is read as the activation contract.
    guide = (REAPER_ROOT / "README.ko.md").read_text(encoding="utf-8")

    # Then: it names default-off, both env controls, disable, and the untouched hourly backstop.
    assert "기본 OFF" in guide
    assert "FABLE_LITE_CODEX_REAPER" in guide
    assert "FABLE_LITE_CODEX_REAPER_LOG" in guide
    assert "비활성화" in guide
    assert "mcp-reaper.ps1" in guide
    assert "백스톱" in guide

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _plant_entry(root: Path, *, name: str, command: str, now: float) -> Path:
    from core.quarantine import backup_blocked_command

    saved = backup_blocked_command(
        str(root),
        command=command,
        agent_key="host:sess:agent",
        reason_code="state_dir_protected",
        target=name,
        now=now,
    )
    assert saved is not None
    return saved


def _run_cli(argv: list[str]) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout

    from fable_lite.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(argv)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = int(args.func(args))
    return exit_code, buffer.getvalue()


def test_quarantine_subcommand_is_registered() -> None:
    from fable_lite.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["quarantine", "list", "--root", "."])
    assert args.command == "quarantine"


def test_cli_list_reports_planted_entries(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _plant_entry(root, name="a.txt", command="rm a.txt", now=1_700_000_000.0)
    _plant_entry(root, name="b.txt", command="rm b.txt", now=1_700_000_010.0)

    exit_code, output = _run_cli(["quarantine", "list", "--root", str(root)])

    assert exit_code == 0
    payload = json.loads(output)
    assert isinstance(payload, list)
    assert len(payload) == 2
    ids = {item["id"] for item in payload}
    assert len(ids) == 2
    for item in payload:
        assert item["reason_code"] == "state_dir_protected"
        assert "size_bytes" in item


def test_cli_list_on_empty_project_returns_empty_list(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    exit_code, output = _run_cli(["quarantine", "list", "--root", str(root)])

    assert exit_code == 0
    assert json.loads(output) == []


def test_cli_show_prints_full_original_command(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    saved = _plant_entry(
        root, name="a.txt", command="cat <<'EOF' > a.txt\nhello world\nEOF\n", now=1_700_000_000.0
    )

    exit_code, output = _run_cli(
        ["quarantine", "show", saved.name, "--root", str(root)]
    )

    assert exit_code == 0
    assert "hello world" in output


def test_cli_show_unknown_id_fails_without_raising(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    exit_code, _output = _run_cli(
        ["quarantine", "show", "does-not-exist.txt", "--root", str(root)]
    )

    assert exit_code != 0


def test_cli_show_rejects_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("do-not-leak", encoding="utf-8")

    exit_code, output = _run_cli(
        [
            "quarantine",
            "show",
            "../../secret.txt",
            "--root",
            str(root),
        ]
    )

    assert exit_code != 0
    assert "do-not-leak" not in output


def test_cli_clear_single_id_removes_only_that_entry(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    first = _plant_entry(root, name="a.txt", command="rm a.txt", now=1_700_000_000.0)
    _plant_entry(root, name="b.txt", command="rm b.txt", now=1_700_000_010.0)

    exit_code, _output = _run_cli(
        ["quarantine", "clear", first.name, "--root", str(root)]
    )
    assert exit_code == 0

    _exit_code, output = _run_cli(["quarantine", "list", "--root", str(root)])
    payload = json.loads(output)
    assert len(payload) == 1
    assert payload[0]["id"] != first.name


def test_cli_clear_all_removes_every_entry(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _plant_entry(root, name="a.txt", command="rm a.txt", now=1_700_000_000.0)
    _plant_entry(root, name="b.txt", command="rm b.txt", now=1_700_000_010.0)

    exit_code, _output = _run_cli(
        ["quarantine", "clear", "--all", "--root", str(root)]
    )
    assert exit_code == 0

    _exit_code, output = _run_cli(["quarantine", "list", "--root", str(root)])
    assert json.loads(output) == []


def test_cli_has_no_apply_subcommand() -> None:
    from fable_lite.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["quarantine", "apply", "--root", "."])

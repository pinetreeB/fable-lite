from __future__ import annotations

import json
from pathlib import Path

from core.adapter_observation import CanonicalInvocation, begin_invocation, observe_post_tool, start_turn
from core.ledger import load_agent_events
from core.ledger_event_schema import validate_v2_event
from core.provenance_lifecycle import ProvenanceLifecycle
from core.shell_hints import shell_candidate_paths


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_bash_shell_hints_extract_mutation_destinations() -> None:
    # Given: Bash commands write quoted, Unicode, and glob paths.
    command = "echo x > '한글 파일.txt'; tee -a 'logs/append file.txt'; cp src/a.py 'build/결과.py'; mv build/결과.py out/final.py; rm tmp/*.tmp; sed -i 's/a/b/' 'src/space file.py'"

    # When: shell candidate paths are extracted.
    paths = shell_candidate_paths(command)

    # Then: only mutation destinations and deletion candidates are retained.
    assert paths == (
        "한글 파일.txt",
        "logs/append file.txt",
        "build/결과.py",
        "out/final.py",
        "tmp/*.tmp",
        "src/space file.py",
    )


def test_powershell_shell_hints_extract_content_paths() -> None:
    # Given: PowerShell content commands use path switches and quoted whitespace.
    command = "Set-Content -LiteralPath 'out/한글 파일.txt' -Value x; Add-Content -Path logs/*.log -Value x; Out-File -FilePath 'reports/out file.txt'"

    # When: shell candidate paths are extracted.
    paths = shell_candidate_paths(command)

    # Then: each PowerShell output target remains a normalized hint.
    assert paths == ("out/한글 파일.txt", "logs/*.log", "reports/out file.txt")


def test_heredoc_redirect_extracts_its_output_path() -> None:
    command = "cat <<'EOF' > 'out/heredoc file.txt'\nbody\nEOF"

    assert shell_candidate_paths(command) == ("out/heredoc file.txt",)


def test_inline_runtime_shell_hints_extract_write_paths() -> None:
    # Given: Python and Node inline snippets contain filesystem write calls.
    python_command = "python -c \"from pathlib import Path; Path('build/out file.txt').write_text('x')\""
    node_command = "node -e \"fs.writeFileSync('dist/app.js', 'x'); fs.rmSync('tmp/*.tmp')\""

    # When: shell candidate paths are extracted.
    python_paths = shell_candidate_paths(python_command)
    node_paths = shell_candidate_paths(node_command)

    # Then: inline writes supply only the target filesystem hints.
    assert python_paths == ("build/out file.txt",)
    assert node_paths == ("dist/app.js", "tmp/*.tmp")


def test_inline_path_reads_are_not_treated_as_write_targets() -> None:
    # Given: inline Python only reads/inspects a path (no write method chained).
    # Path('x') 뒤에 쓰기 메서드가 없으면 후보 경로로 추출하지 않는다 — read_text·exists
    # 같은 읽기가 R2-friction에 걸리던 오탐 제거.
    read_commands = (
        "python -c \"from pathlib import Path; Path('.fable-lite/gates.jsonl').read_text()\"",
        "python -c \"Path('.fable-lite/ledger.json').exists()\"",
        "python -c \"print(Path('.fable-lite/x').read_bytes())\"",
    )
    for command in read_commands:
        assert shell_candidate_paths(command) == (), command


def test_inline_path_writes_remain_write_targets() -> None:
    # Given: inline Python chains a write/delete method on Path(...).
    write_commands = (
        ("python -c \"Path('.fable-lite/ledger.json').write_text('x')\"", ".fable-lite/ledger.json"),
        ("python -c \"Path('build/note.txt').write_bytes(b'x')\"", "build/note.txt"),
        ("python -c \"Path('tmp/scratch').unlink()\"", "tmp/scratch"),
    )
    for command, expected in write_commands:
        assert shell_candidate_paths(command) == (expected,), command


def test_parser_candidates_without_a_delta_create_no_change(tmp_path: Path) -> None:
    # Given: a shell invocation advertises a target but does not alter bytes.
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("shell", "turn")
    invocation = lifecycle.begin_invocation(
        "shell", "turn", "write", shell_candidate_paths("echo x > app.py")
    )

    # When: PostTool observes the unchanged workspace.
    result = lifecycle.post_tool(invocation, source="shell")

    # Then: parser-only hints do not create a provenance change.
    assert result.changes == ()


def test_generated_shell_delta_has_generated_source_and_contended_falls_external(tmp_path: Path) -> None:
    # Given: two shell producers overlap on a configured generated output.
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({"version": 1, "generated": ["dist/**"]}),
    )
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("agent-a", "turn-a")
    lifecycle.start_turn("agent-b", "turn-b")
    first = lifecycle.begin_invocation("agent-a", "turn-a", "one", ("dist/app.js",))
    second = lifecycle.begin_invocation("agent-b", "turn-b", "two", ("dist/app.js",))
    _write(tmp_path / "dist" / "app.js", "generated")

    # When: both active producers observe the same physical generated delta.
    first_result = lifecycle.post_tool(first, source="shell")
    _ = lifecycle.post_tool(second, source="shell")

    # Then: initial attribution is generated and the overlap resolves to external.
    assert first_result.changes[0].source == "generated"
    assert lifecycle.changes[0].source == "external"


def test_generated_shell_change_is_recorded_as_a_v2_source_event(tmp_path: Path) -> None:
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({"version": 1, "generated": ["dist/**"]}),
    )
    invocation = CanonicalInvocation(
        "host", "agent", "session", "turn", "shell", "post_tool", "shell", (), "echo x > dist/app.js", True, ""
    )
    _ = start_turn(tmp_path, invocation)
    _ = begin_invocation(tmp_path, invocation)
    _write(tmp_path / "dist" / "app.js", "generated")

    _ = observe_post_tool(tmp_path, invocation)

    events = load_agent_events(str(tmp_path), "agent")
    assert events is not None
    changes = [event for event in events if event.get("event") == "change"]
    assert changes[-1]["source"] == "generated"
    assert validate_v2_event(changes[-1]) == changes[-1]

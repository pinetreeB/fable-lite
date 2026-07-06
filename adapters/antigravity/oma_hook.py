import sys
import json
import os
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.classify import classify_prompt
from core.ledger import classify_change_kind, load_ledger, record_event
from core.scope_guard import evaluate_scope
from core.contract import evaluate_pretool_contract
from core.verify_state import evaluate_stop

def read_payload():
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("malformed JSON payload") from exc

def emit(payload):
    data = json.dumps(payload, ensure_ascii=False)
    sys.stdout.buffer.write(data.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    return 0

def fail_open(msg):
    return emit({"decision": "allow", "systemMessage": f"fable-lite fail-open: {msg}"})

def extract_tool_info(payload):
    tool_name = ""
    tool_input = {}
    
    metadata = payload.get("metadata", {})
    if isinstance(metadata, dict) and "tool_name" in metadata:
        tool_name = metadata.get("tool_name", "")
        tool_input = metadata.get("tool_input", {})
    
    req = payload.get("llm_request", {})
    if isinstance(req, dict):
        tool_calls = req.get("tool_calls", [])
        if isinstance(tool_calls, list) and len(tool_calls) > 0:
            tc = tool_calls[0]
            if isinstance(tc, dict):
                tool_name = tc.get("name", tool_name)
                args = tc.get("args") or tc.get("input") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except:
                        pass
    
                if isinstance(args, dict):
                    tool_input = args
    
    # Map OmA tools to CC tools
    tool_name_map = {
        "write_to_file": "Edit",
        "replace_file_content": "Edit",
        "multi_replace_file_content": "Edit",
        "run_command": "Bash",
        "write_file": "Edit",
        "edit_file": "Edit"
    }
    mapped_name = tool_name_map.get(str(tool_name), str(tool_name))
    return mapped_name, tool_input

def extract_paths_from_input(tool_input):
    paths = []
    if "file_paths" in tool_input and isinstance(tool_input["file_paths"], list):
        paths.extend([str(p) for p in tool_input["file_paths"]])
    for key in ["file_path", "path", "notebook_path", "TargetPath", "AbsolutePath", "TargetFile"]:
        if key in tool_input and isinstance(tool_input[key], str):
            paths.append(tool_input[key])
    return paths

def extract_command(tool_input):
    for key in ["command", "CommandLine"]:
        if key in tool_input and isinstance(tool_input[key], str):
            return tool_input[key]
    return ""

def _get_project_root(payload):
    cwd = payload.get("cwd") or os.getcwd()
    return str(cwd)

def handle_before_tool(payload):
    tool_name, tool_input = extract_tool_info(payload)
    paths = extract_paths_from_input(tool_input)
    cmd = extract_command(tool_input)
    
    result = evaluate_pretool_contract({
        "project_root": _get_project_root(payload),
        "tool_name": tool_name,
        "file_paths": paths,
        "command": cmd,
        "prompt": json.dumps(tool_input, ensure_ascii=False)
    })
    
    if result.get("decision") == "block":
        return emit({"decision": "block", "reason": str(result.get("reason", ""))})
    return emit({"decision": "allow"})

def handle_after_tool(payload):
    root = _get_project_root(payload)
    tool_name, tool_input = extract_tool_info(payload)
    
    from core.contract import EDIT_TOOLS, SHELL_TOOLS
    TEST_TERMS = ("pytest", "npm test", "go test", "cargo test", "node --test")
    
    if tool_name in EDIT_TOOLS:
        paths = extract_paths_from_input(tool_input)
        for path in paths:
            record_event({
                "project_root": root,
                "event": "change",
                "path": path,
                "kind": classify_change_kind(path)
            })
        ledger = load_ledger({"project_root": root})
        prompt = ledger.get("prompt", "")
        if not isinstance(prompt, str): prompt = ""
        
        req = classify_prompt({"prompt": prompt})["requested_paths"] if prompt else []
        scope = evaluate_scope({
            "project_root": root,
            "prompt": prompt,
            "requested_paths": req,
            "changed_files": paths
        })
        if scope.get("decision") == "warn":
            record_event({"project_root": root, "event": "scope_warning", "message": scope.get("message")})
            return emit({
                "decision": "allow",
                "systemMessage": str(scope.get("message")),
                "hookSpecificOutput": {"additionalContext": str(scope.get("message"))}
            })
        return emit({"decision": "allow", "systemMessage": f"fable-lite 원장: 변경 {len(paths)}건 기록."})
        
    if tool_name in SHELL_TOOLS:
        cmd = extract_command(tool_input)
        if any(term in cmd.lower() for term in TEST_TERMS):
            record_event({
                "project_root": root,
                "event": "verification",
                "command": cmd,
                "success": True,
                "evidence": "tool output"
            })
            return emit({"decision": "allow", "systemMessage": "fable-lite 원장: 검증 기록."})
            
    return emit({"decision": "allow"})

def handle_after_agent(payload):
    root = _get_project_root(payload)
    term_reason = payload.get("termination_reason", "")
    
    assistant_text = ""
    req = payload.get("llm_request", {})
    if isinstance(req, dict) and "messages" in req and isinstance(req["messages"], list):
        for msg in reversed(req["messages"]):
            if msg.get("role") in ["assistant", "model"]:
                assistant_text = str(msg.get("content", ""))
                break
                
    # OmA doesn't have stop_hook_active natively, so assume False for normal evaluation
    result = evaluate_stop({
        "project_root": root,
        "stop_hook_active": False,
        "assistant_text": assistant_text
    })
    
    if result.get("decision") == "block":
        return emit({"decision": "block", "reason": str(result.get("reason", ""))})
        
    return emit({"decision": "allow", "systemMessage": str(result.get("message", "fable-lite Stop gate allow."))})

def handle_before_model(payload):
    prompt_value = payload.get("prompt", "")
    if not isinstance(prompt_value, str): prompt_value = ""
    
    if not prompt_value:
        req = payload.get("llm_request", {})
        if isinstance(req, dict) and "messages" in req and isinstance(req["messages"], list):
            for msg in reversed(req["messages"]):
                if msg.get("role") == "user":
                    prompt_value = str(msg.get("content", ""))
                    break

    result = classify_prompt({"prompt": prompt_value})
    packs = result.get("packs", [])
    
    record_event({
        "project_root": _get_project_root(payload),
        "event": "prompt",
        "task_mode": result.get("mode", "quick"),
        "prompt": prompt_value,
        "packs": packs,
        "needs_goals": result.get("needs_goals", False),
        "requires_investigation_compliance": isinstance(packs, list) and "investigation" in packs
    })
    
    lines = [
        "fable-lite 활성화: 작업 규율을 절차로 적용하세요.",
        f"mode={result.get('mode', 'quick')}"
    ]
    if isinstance(packs, list):
        if "investigation" in packs:
            lines.extend([
                "조사 팩 준수 필수: 출력에 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:`를 포함하세요.",
                "수정 전 재현과 경쟁 가설을 먼저 기록하세요."
            ])
        if "verification-grounding" in packs:
            lines.append("렌더/실행 산출물은 RUN→OBSERVE→FIX→RE-RUN 증거 없이는 완료하지 마세요.")
    if result.get("needs_goals"):
        lines.append("2+ 스토리 작업입니다. goals 체크포인트를 만들거나 사용자에게 명시 확인을 받으세요.")
        
    return emit({
        "decision": "allow",
        "hookSpecificOutput": {
            "additionalContext": "\n".join(lines)
        }
    })

def main():
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

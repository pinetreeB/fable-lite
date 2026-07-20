# v2.3.1 안정화 지시서(sol-stabilization-handoff.md) 트리아지 결과

> **작성자**: agy (리뷰·분석 워커)
> **대상 코드**: `main` (533df02)
> **판정 요약**: P0 5건, P1 6건 모두 현재 코드베이스에서 성립하는 실결함으로 **CONFIRMED** (확인)되었습니다.

---

## 1. P0 (Critical/High)

### 1-1. R2-01: 체인된 셸 명령의 모든 segment 검사
- **판정: CONFIRMED**
- **근거**: `core/destructive_guard.py` (line 436 `parse_destructive_command`)
  - 현재 파서는 `&&`, `||`, `;` 등으로 연결된 셸 segment를 분리하지 않고 전체 문자열을 `_tokenize` 한 뒤 첫 부분만 `_DETECTORS`로 검사합니다.
  - 따라서 `echo ok && rm peer-owned.py` 와 같이 앞 segment가 무해한 명령어이면 뒤의 파괴 명령어를 통과시키는 치명적 우회(fail-open)가 존재합니다.

### 1-2. R2-02: `git checkout -f/--force` 통과
- **판정: CONFIRMED**
- **근거**: `core/destructive_guard.py` (line 214-218 `_detect_git`)
  - `git checkout` 처리 시 `-b`, `-B`만 걸러내고 나머지 옵션은 무시한 채 `_parse_git_pathspec`으로 넘깁니다. 
  - `git checkout -f main` 입력 시 `-f`는 버려지고 `main`이 target으로 간주되어, 귀속 데이터가 없는 경로로 판단해 그대로 통과(allow)됩니다. 이는 로컬 작업 트리를 모두 폐기할 수 있는 심각한 implicit scope 우회입니다.

### 1-3. AUDIT-01: R2 deny가 ledger lock 15초 대기로 hook timeout 10초 위협
- **판정: CONFIRMED**
- **근거**: `core/adapter_observation.py` (line 339) → `core/scorecard_coordination.py` (line 284-288 `try_record_coordination_event` → line 270 `with ledger_transaction(root):`)
  - R2 deny 결정 후 감사를 남길 때, 타 프로세스가 ledger lock을 점유 중이면 최대 15초(`LOCK_WAIT_SECONDS`) 대기합니다.
  - Claude Code hook timeout이 10초이므로, 감사 기록 대기 때문에 R2 차단 응답 자체가 timeout으로 유실되는 치명적 문제가 발생합니다.

### 1-4. ACT-01: root mismatch 시 opt-in 미재검사
- **판정: CONFIRMED**
- **근거**: `adapters/claude_code/bootstrap.py` (line 105-117)
  - 기존 A 프로젝트로 bind된 `record`가 있으면, 새 훅이 B 프로젝트(`env_root`)에서 들어올 때 `bind_session`에서 `root_mismatch`를 띄우지만 B 프로젝트의 `config.json` (opt-in 여부)을 전혀 검사하지 않습니다.
  - 그리고 바로 `env_root`를 활성 루트로 사용하여 `_active`를 반환하므로, B 프로젝트에 무단으로 상태가 기록되고 게이트가 활성화되는 계약 위반이 발생합니다.

### 1-5. R2-03: absolute peer candidate 키 불일치
- **판정: CONFIRMED**
- **근거**: 
  - `core/adapter_observation.py` (line 588): peer의 `candidate_paths` (절대 경로 포함)를 원장에 기록할 때 프로젝트 상대 경로로 변환(canonicalize)하지 않고 원본 그대로 저장합니다.
  - `core/ledger_v2.py` (line 111): `open_peer_invocation_candidates`에서 이 경로를 읽을 때 `canonical_manifest_key`를 쓰지만, 이는 단순히 대소문자(casefold)와 슬래시만 통일할 뿐 절대경로를 상대경로로 바꾸지 못합니다.
  - 반면 `core/destructive_guard.py` (line 498 `_canonicalize_target`)는 R2 검사 대상 경로를 철저히 상대경로로 변환합니다. 따라서 두 키가 엇갈려 peer 작업 보호가 우회됩니다.

---

## 2. P1 (Medium/Low)

### 2-1. CODEX-01: identity authorship 순서 (recovered exact 승격 누락)
- **판정: CONFIRMED**
- **근거**: `adapters/codex_cli/pre_tool_use.py` (line 77)
  - Claude 어댑터와 달리, session_id 없이 복구된 단일 active turn에 대해 `legacy_default`를 `exact`로 승격하는 보정 로직(`if attribution == "legacy_default" and invocation.session_id != "default": attribution = "exact"`)이 완전히 누락되어 있습니다.

### 2-2. CODEX-02: identity authorship 기록 전에 resolve 수행
- **판정: CONFIRMED**
- **근거**: `adapters/codex_cli/post_tool_use.py` (line 45-56)
  - 편집(edit) 성공 후 `record_contract_authored_event`를 호출하여 원장에 기록한 **직후**에 `resolve_active_invocation`을 호출하여 신원을 복원합니다.
  - 순서가 뒤집혀 있어 계약 파일이 항상 미복원 상태(`legacy_default`)로 기록되므로 서명 감사가 실패합니다.

### 2-3. SCORE-01: 시간 min/max 계산
- **판정: CONFIRMED**
- **근거**: `fable_lite/scorecard.py` (line 354)
  - 원장 순회 중 `row["last_observed_at"] = event.occurred_at.isoformat()` 형태로 단순히 가장 마지막 줄에 적힌 시간으로 덮어씁니다. 동시성으로 인해 과거 시간 이벤트가 나중에 append될 경우 시간 역전이 발생합니다.

### 2-4. R2-04: symlink 상태디렉토리
- **판정: CONFIRMED**
- **근거**: `core/destructive_guard.py` (line 499)
  - `_canonicalize_target` 수행 시 `.fable-lite`가 외부를 가리키는 symlink라면 `resolved.relative_to(base)`에서 `ValueError`가 발생해 `_CANON_OUT_OF_ROOT`를 반환합니다.
  - 이 경우 경로 기반 보호인 `_is_state_dir_key` 층에 도달하기도 전에 외부 디렉토리로 간주되어 파괴 명령이 무사통과됩니다.

### 2-5. HINT-01: Path.rename/replace/open
- **판정: CONFIRMED**
- **근거**: `core/shell_hints.py` (line 13-18 `_INLINE` 정규식)
  - `replace()`나 `rename()` 메서드의 괄호 안(destination 인자)은 정규식 그룹 캡처에서 완전히 누락되어 목적지 경로가 보호 마찰을 받지 않습니다.
  - `open(...)` 의 모드 검사도 `['\"][wax]`만 잡기 때문에 `r+`, `w+`, `a+`, `rb+` 같은 업데이트/바이너리 쓰기 모드는 모두 놓칩니다.

### 2-6. REL-01: 버전 동기화
- **판정: CONFIRMED**
- **근거**: `scripts/sync_version.py` (line 118-132 `_plan_updates`)
  - `plugin.json`, `marketplace.json`, `pyproject.toml`, 리드미, 체인지로그 등은 동기화하지만 `uv.lock`은 체크 및 수정 대상에서 아예 누락되어 있어 lockfile 버전 표류(drift)를 탐지할 수 없습니다.

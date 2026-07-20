# show-me-the-work v2.3.x 안정화 작업 지시서

> 대상 저장소: [pinetreeB/show-me-the-work](https://github.com/pinetreeB/show-me-the-work)  
> 작성 목적: 현재 `main`의 안전성·동시성·릴리스 품질 문제를 AI 개발 에이전트들이 재현하고 수정할 수 있도록 작업 범위, 우선순위, 회귀 테스트, 완료 조건을 명시한다.  
> 기준 상태: `v2.3.0` 계열. 작업 시작 시 반드시 최신 `main`을 다시 확인하고, 이미 해결된 항목은 재현 테스트로 해결 여부를 증명한 뒤 체크한다.

---

## 0. 최상위 지시

이번 작업은 **기능 추가가 아니라 안정화 릴리스**다.

권장 릴리스 이름:

```text
v2.3.1 — No new features. Safety, concurrency, review-debt, and release-hygiene fixes.
```

다음 원칙을 지킨다.

1. **수정 전에 재현한다.** 각 결함마다 실패하는 회귀 테스트를 먼저 추가한다.
2. **한 PR에 한 문제군만 다룬다.** R2 파서, opt-in, journal lock, packaging을 한 PR에 섞지 않는다.
3. **게이트를 약화해 테스트를 통과시키지 않는다.**
   - 단순히 예외를 더 삼키지 않는다.
   - `fail-open` 범위를 넓히지 않는다.
   - 기존 `R2 fail-closed` invariant를 깨지 않는다.
   - hook timeout을 늘리는 것을 근본 해결책으로 삼지 않는다.
4. **호스트별 실제 경계를 테스트한다.** 함수 테스트만 추가하지 말고 가능하면 hook subprocess 테스트도 추가한다.
5. **Windows와 POSIX를 함께 고려한다.**
6. **문서 주장과 실제 보장 범위를 일치시킨다.**
7. 변경 후 다음 기본 게이트를 모두 통과시킨다.

```powershell
ruff check core adapters fable_lite goals tests eval contrib scripts --exclude eval/ab
python -m pytest tests/ -q
python eval/run_probes.py --strict
python eval/e2e_smoke.py
python -m compileall -q core adapters fable_lite goals eval contrib scripts
python scripts/sync_version.py --check
python -m build --wheel --outdir dist
```

---

# 1. 완료 정의

각 작업은 아래 조건을 모두 만족해야 완료다.

- [ ] 결함을 재현하는 테스트가 수정 전 실패한다.
- [ ] 수정 후 해당 테스트가 통과한다.
- [ ] 전체 테스트·프로브·E2E·Ruff가 통과한다.
- [ ] Windows/Ubuntu CI가 모두 green이다.
- [ ] 기존 fail-open/fail-closed 정책을 임의로 바꾸지 않았다.
- [ ] PR 본문에 원인, 재현 명령, 수정 원리, 비보장 범위를 적었다.
- [ ] 관련 GitHub review thread에 수정 커밋 또는 반박 근거를 연결한다.
- [ ] 새 Known Limitation이 생기면 README와 CHANGELOG에 동시에 적는다.
- [ ] 동일 문제를 다시 막는 회귀 테스트 이름이 명확하다.
- [ ] “AI 여러 명이 승인했다”가 아니라 실행 결과와 테스트 증거를 남긴다.

---

# 2. 우선순위 요약

| ID | 우선순위 | 항목 | 위험 |
|---|---:|---|---|
| R2-01 | P0 | 체인된 셸 명령의 모든 segment 검사 | 동료 작업 삭제 우회 |
| R2-02 | P0 | `git checkout -f/--force` 차단 | 브랜치 전환 중 미커밋 변경 폐기 |
| AUDIT-01 | P0 | R2 deny 감사 기록을 진짜 nonblocking으로 변경 | 이미 계산한 deny가 hook timeout에 묶임 |
| ACT-01 | P0 | session root mismatch 시 프로젝트별 opt-in 재검사 | 비활성 프로젝트에 상태 기록·감독 |
| R2-03 | P0/P1 | absolute peer candidate 경로 canonicalization | pre-attribution 창에서 동료 파일 삭제 |
| CODEX-01 | P1 | recovered Codex identity를 exact로 승격 | 자기 계약 authoring 오차단 |
| CODEX-02 | P1 | contract authorship 기록 전에 identity resolve | 유효 계약이 감사 근거 없이 거절됨 |
| SCORE-01 | P1 | coordination first/last 시간 `min/max` 계산 | 동시성에서 보고 시간 역전 |
| R2-04 | P1 | symlink된 `.fable-lite` lexical 보호 | 실제 상태·원장 삭제 우회 |
| HINT-01 | P1 | inline `Path.rename/replace/open` 쓰기 탐지 보강 | 상태 파일 직접 쓰기 마찰 장치 우회 |
| REL-01 | P1 | `uv.lock`, manifest, changelog, release 버전 동기화 | 빌드·배포 재현성 저하 |
| DOC-01 | P2 | README의 설치·보장 문구 정합성 수정 | 사용자 기대와 실제 동작 불일치 |
| PKG-01 | P2 | `smtw` console script 및 doctor/init UX | 제품명·명령명 혼란 |
| STATE-01 | P2 | 공유 config와 runtime state 분리 설계 | 팀 설정 공유·개인 기록 보호 충돌 |

---

# 3. P0 작업

## R2-01 — 체인된 셸 명령의 모든 segment 검사

### 문제

현재 파괴 명령 파서는 전체 command를 한 번 tokenize한 뒤 detector를 적용한다. `_command_segments()`는 동적 command head 검사에만 사용되는 구조이므로, 뒤쪽 segment의 파괴 명령을 놓칠 수 있다.

대표 우회:

```bash
echo ok && rm peer-owned.py
python -V ; git restore peer-owned.py
rm own.py && rm peer-owned.py
echo harmless | tee out.txt ; Remove-Item peer-owned.py
```

첫 command가 정상 명령이면 뒤쪽 `rm`, `git restore`, `Remove-Item`이 R2 판정에서 빠질 수 있다.

### 관련 코드

```text
core/destructive_guard.py
- _command_segments
- parse_destructive_command
- _DETECTORS
- evaluate_r2_destructive_gate
```

### 수정 요구

단일 결과가 아니라 복수 segment 결과를 안전하게 다룰 수 있어야 한다.

권장 API 예시:

```python
def parse_destructive_commands(command: str) -> tuple[ParsedDestructiveCommand, ...]:
    ...
```

정책:

1. quote 안의 `;`, `|`, `&&`, `||`는 분리하지 않는다.
2. 모든 shell segment를 독립적으로 파싱한다.
3. 파괴형 segment가 하나라도 `resolved=False`이면 전체 command를 fail-closed 차단한다.
4. 파괴형 segment가 여러 개면 모든 target을 평가한다.
5. 한 segment의 self-owned target이 통과해도 뒤 segment의 peer-owned target이 있으면 차단한다.
6. non-destructive segment는 무시한다.
7. wrapper 안의 nested command 처리와 중복 검사로 recursion loop가 생기지 않아야 한다.

### 필수 회귀 테스트

```python
def test_r2_inspects_destructive_command_after_benign_segment():
    command = "echo ok && rm peer-owned.py"
    # peer-owned.py가 타 에이전트 미정산이면 block

def test_r2_inspects_every_destructive_segment():
    command = "rm own.py && rm peer-owned.py"
    # 첫 대상이 self-owned여도 두 번째 peer-owned 때문에 block

def test_r2_does_not_split_operators_inside_quotes():
    command = 'python -c "print(\'a && rm x\')"'
    # 실제 파괴 command가 아니면 allow

def test_r2_handles_semicolon_and_pipeline_boundaries():
    ...

def test_r2_parse_unable_later_segment_fails_closed():
    ...
```

subprocess adapter 테스트도 최소 1개 추가한다.

### 완료 조건

- 체인 뒤쪽의 파괴 명령이 더 이상 누락되지 않는다.
- 기존 benign corpus가 오탐 없이 유지된다.
- quote·escape·PowerShell segment에 대한 회귀가 없다.

참고 리뷰:

- [PR #1 — Inspect every shell segment for R2 destructive commands](https://github.com/pinetreeB/show-me-the-work/pull/1#discussion_r3594375772)

---

## R2-02 — `git checkout -f/--force`를 implicit-scope 파괴로 차단

### 문제

현재 checkout 파서는 옵션을 건너뛰고 남은 위치 인자를 path처럼 attribution 조회에 넘길 수 있다.

```bash
git checkout -f main
git checkout --force release/v2
```

`main` 또는 `release/v2`라는 경로에 귀속 정보가 없으면 allow될 수 있지만, Git의 force checkout은 작업 트리의 로컬 변경을 버릴 수 있다.

### 관련 코드

```text
core/destructive_guard.py
- _detect_git
- _parse_git_pathspec
```

### 수정 요구

checkout의 discard semantics를 별도로 분류한다.

최소 차단 대상:

```text
-f
--force
```

Git 버전별 alias 또는 결합 옵션이 있다면 함께 확인한다.

정책:

- `git checkout -b/-B <branch>`의 기존 허용 동작은 유지한다.
- 일반 `git checkout main`은 기존 정책대로 branch switch로 허용할 수 있다.
- `git checkout -f main`은 target attribution과 무관하게 `implicit_scope`로 차단한다.
- `git switch --discard-changes`는 기존 차단을 유지한다.

### 필수 테스트

```python
@pytest.mark.parametrize(
    "command",
    [
        "git checkout -f main",
        "git checkout --force main",
        "git checkout -f release/v2",
    ],
)
def test_r2_blocks_forced_checkout_branch_switch(command):
    ...

def test_r2_still_allows_nonforced_branch_switch():
    ...

def test_r2_still_allows_checkout_branch_creation():
    ...
```

참고 리뷰:

- [PR #2 — Block forced checkout branch switches](https://github.com/pinetreeB/show-me-the-work/pull/2#discussion_r3594928069)

---

## AUDIT-01 — R2 deny 감사 기록을 진짜 nonblocking으로 변경

### 문제

R2 차단 결정 후 coordination journal을 동기적으로 기록한다. 기록 실패는 예외를 삼키지만, `ledger_transaction()` lock을 얻기 위해 기본 최대 15초 기다릴 수 있다.

Claude Code hook timeout은 10초다.

즉:

1. R2 deny는 이미 계산됨.
2. deny를 출력하기 전에 감사 기록을 시도함.
3. 다른 프로세스가 `.fable-lite/ledger.lock`을 보유하면 최대 15초 대기.
4. 호스트 hook timeout 10초와 충돌 가능.
5. “관측 실패가 gate decision을 바꾸지 않는다”는 의도와 달리, 응답 전달 자체를 지연시킨다.

### 관련 코드

```text
core/adapter_observation.py
- record_r2_deny_after_resolution

core/scorecard_coordination.py
- record_coordination_event
- try_record_coordination_event

core/agent_log.py
- ledger_transaction
- LOCK_WAIT_SECONDS

adapters/*/pre_tool_use.py
```

### 수정 요구

R2 denial의 응답 경로에서 장기 대기를 제거한다.

가능한 설계:

1. coordination journal 전용 lock 파일 사용
2. 즉시 획득 가능한 try-lock 사용
3. lock을 즉시 얻지 못하면 기록을 포기
4. event ID가 deterministic하므로 다음 기회에 재기록 가능한 구조 검토
5. ledger lock과 coordination append lock을 분리
6. 감사 기록 실패가 deny 결과·latency에 영향을 주지 않음

금지:

- hook timeout을 20초 이상으로 올려 문제를 숨기기
- background thread를 생성하고 프로세스 종료 안정성을 검증하지 않기
- lock 없이 여러 프로세스가 JSONL line을 섞어 쓰게 만들기
- deny보다 journal durability를 우선하기

### 성능 완료 조건

다른 프로세스가 ledger lock을 보유한 상태에서 R2 deny hook이:

```text
권장: 250 ms 이내
최대 허용: 1초 이내
절대 금지: 10초 hook timeout에 근접
```

### 필수 테스트

- 별도 프로세스가 ledger lock을 보유
- PreToolUse R2 denial subprocess 실행
- 반환 시간이 내부 budget 이하인지 측정
- decision이 항상 deny/block인지 확인
- journal이 기록되지 않아도 gate decision은 유지됨
- lock 해제 후 정상 기록 가능
- concurrent journal append가 line atomicity를 유지

참고 리뷰:

- [PR #5 — Make R2 audit recording nonblocking](https://github.com/pinetreeB/show-me-the-work/pull/5#discussion_r3610775644)

---

## ACT-01 — session root mismatch에서 프로젝트별 opt-in 재검사

### 문제

활성 프로젝트 A에 session registry가 latch된 뒤 후속 hook payload의 `CLAUDE_PROJECT_DIR`이 프로젝트 B를 가리키면, B의 config를 다시 검사하지 않고 B를 active root로 사용할 수 있다.

재현 시나리오:

```text
A/.fable-lite/config.json = supervision true
B에는 config 없음
같은 session_id
첫 UserPromptSubmit: CLAUDE_PROJECT_DIR=A
다음 hook: CLAUDE_PROJECT_DIR=B
```

예상 위험:

- B가 opt-in하지 않았는데 ledger/snapshot이 기록됨
- B에 감독·차단이 적용됨
- “프로젝트별 quiet opt-in” 제품 계약 위반

### 관련 코드

```text
adapters/claude_code/bootstrap.py
- bootstrap
- record is not None branch
- env_root / fixed_root / bind_session
```

### 정책을 먼저 명시

권장 정책 중 하나를 선택하고 문서화한다.

#### 정책 A — mismatch env root를 새 프로젝트로 취급

- B의 config를 즉시 검사
- B가 정확히 opt-in이면 이번 hook만 B 사용 가능
- B가 opt-in이 아니면 inactive
- registry latch는 기존 A 유지

#### 정책 B — latch root만 권위로 사용

- mismatch 시 경고
- 모든 hook은 A에만 적용
- B에는 절대 상태를 쓰지 않음

현재 README의 “env가 있으면 이번 훅의 유효 루트는 env” 문구를 유지하려면 정책 A가 더 자연스럽다.

### 필수 테스트

```python
def test_latched_enabled_project_does_not_activate_unconfigured_env_root():
    ...

def test_latched_enabled_project_can_use_separately_opted_in_env_root_if_policy_allows():
    ...

def test_root_mismatch_warning_is_emitted_once():
    ...

def test_unconfigured_mismatch_root_remains_project_stateless():
    ...
```

B 아래에 `.fable-lite/ledger.json`, snapshot, config 이외 상태가 생기지 않는지 반드시 확인한다.

참고 리뷰:

- [PR #4 — Re-check opt-in for a mismatched environment root](https://github.com/pinetreeB/show-me-the-work/pull/4#discussion_r3601687453)

---

## R2-03 — peer open invocation candidate 경로를 project-relative canonical key로 저장

### 문제

peer adapter가 absolute `candidate_paths`를 기록하면, open invocation candidate key가 절대경로 형태로 남을 수 있다. 반면 R2 caller의 상대 target은 프로젝트 상대 canonical key로 변환된다.

예:

```text
peer candidate: /repo/peer-new.py
caller target: peer-new.py
```

두 key가 불일치하면 change event가 생기기 전의 pre-attribution window에서 R2가 peer 작업을 보호하지 못한다.

### 관련 코드

```text
core/ledger_v2.py
- open_peer_invocation_candidates
- candidate_paths 저장·조회

core/adapter_observation.py
- _record_invocation
- begin_invocation
- _with_shell_candidates

core/provenance_lifecycle_start.py
- candidate_paths
```

### 수정 요구

candidate path는 ledger에 기록하기 전에 다음 형태로 정규화한다.

```text
project root 내부 absolute path -> project-relative canonical key
project-relative path -> canonical key
root 밖 path -> 별도 정책에 따라 제외 또는 명시 상태
resolve 불가 -> 완화 근거로 사용하지 않음
Windows -> casefold 규칙 일치
```

동일 canonicalization helper를 R2 target과 invocation candidate 양쪽에서 재사용하는 것이 바람직하다.

### 필수 테스트

```python
def test_peer_absolute_candidate_blocks_relative_destructive_target():
    ...

def test_peer_relative_candidate_blocks_absolute_destructive_target():
    ...

def test_windows_casefold_candidate_matches_target():
    ...

def test_out_of_root_candidate_is_not_misclassified_as_project_owned():
    ...
```

참고 리뷰:

- [PR #1 전체 리뷰](https://github.com/pinetreeB/show-me-the-work/pull/1)

---

# 4. P1 작업

## CODEX-01 — recovered Codex identity를 contract 판정에서 exact로 취급

### 문제

Codex payload가 `session_id`를 생략했을 때 `resolve_active_invocation()`이 유일한 active turn의 실제 session을 복구할 수 있다. 그러나 `identity_synthetic=True`가 남아 `scorecard_attribution`이 `legacy_default`로 유지될 수 있다.

Claude adapter에는 다음 보정이 존재한다.

```python
if attribution == "legacy_default" and invocation.session_id != "default":
    attribution = "exact"
```

Codex adapter에도 동일 의미의 보정이 필요하다.

그렇지 않으면 자기 namespaced contract 파일 편집이 일반 state-file 직접 편집으로 취급돼 차단될 수 있다.

### 관련 코드

```text
adapters/codex_cli/pre_tool_use.py
core/adapter_observation.py
core/contract.py
```

### 필수 테스트

- session_id 없는 Codex payload
- active turn 정확히 1개
- identity resolve 후 실제 session 복구
- 자기 namespaced contract authoring 허용
- 다른 identity contract는 거절

---

## CODEX-02 — contract authorship 기록 전에 Codex identity resolve

### 문제

현재 Codex PostToolUse 흐름에서 contract authorship 기록이 `resolve_active_invocation()`보다 먼저 수행될 수 있다.

그 결과 recovered session에서:

1. namespaced contract edit 성공
2. 기록 시 attribution이 `legacy_default`
3. `record_contract_authored_event()`가 early return
4. digest 감사 이벤트가 없음
5. 다음 high-risk 작업에서 contract가 불인정

### 관련 코드

```text
adapters/codex_cli/post_tool_use.py
- record_contract_authored_event
- resolve_active_invocation
```

### 수정 요구

순서를 다음처럼 통일한다.

```text
canonical_invocation
-> resolve_active_invocation
-> attribution 보정
-> record_contract_authored_event
-> observe_post_tool
```

Claude, Codex, Antigravity adapter가 동일 invariant를 지키는지 함께 감사한다.

### 필수 테스트

- recovered Codex exact identity
- namespaced contract 편집
- agent journal에 `contract_authored` 이벤트와 content digest 존재
- 그 contract로 후속 high-risk edit 통과
- 타 identity contract 복사 시 거절

---

## SCORE-01 — coordination 시간 범위를 journal append 순서가 아닌 timestamp로 계산

### 문제

동시 이벤트는 생성 시각과 append 순서가 다를 수 있다.

현재 집계가:

```python
first_observed_at = first appended event
last_observed_at = every later appended event
```

형태이면, 나중에 append된 이벤트의 `occurred_at`이 더 과거일 때:

```text
last_observed_at < first_observed_at
```

가 될 수 있다.

### 관련 코드

```text
fable_lite/scorecard.py
- _run_coordination_view
```

### 수정 요구

각 group마다 ISO 문자열 덮어쓰기가 아니라 datetime 기준 `min/max`를 사용한다.

```python
first = min(first, event.occurred_at)
last = max(last, event.occurred_at)
```

### 필수 테스트

- journal append 순서와 occurred_at 순서를 반대로 구성
- JSON output의 first/last가 chronological min/max인지 확인
- 동일 timestamp
- filter 적용 후에도 min/max 정확성 유지

참고 리뷰:

- [PR #5 — Derive coordination time bounds from timestamps](https://github.com/pinetreeB/show-me-the-work/pull/5#discussion_r3610775648)

---

## R2-04 — symlink된 `.fable-lite`를 resolve 결과와 무관하게 보호

### 문제

`.fable-lite`가 프로젝트 밖 디렉터리를 가리키는 symlink라면:

```bash
rm .fable-lite/ledger.json
```

의 target이 resolve 후 out-of-root로 분류될 수 있다. 현재 state-dir check 전에 out-of-root를 skip하면 실제로 사용 중인 원장·감사 상태가 삭제될 수 있다.

### 관련 코드

```text
core/destructive_guard.py
- _canonicalize_target
- _is_state_dir_key
- evaluate_r2_destructive_gate
```

### 수정 요구

state-dir 보호는 resolved target뿐 아니라 **lexical project-relative path**에도 적용한다.

정책:

- target의 정규화된 첫 component가 `.fable-lite`이면 symlink 여부와 무관하게 hard block
- `./.fable-lite/...`, `src/../.fable-lite/...`도 차단
- 이름이 비슷한 `.fable-lite-backup`은 오탐하지 않음
- 프로젝트 밖의 일반 경로는 기존 R2 비범위 정책 유지

### 필수 테스트

- `.fable-lite`가 외부 dir symlink
- ledger 삭제 command block
- nested traversal로 state dir 도달 block
- prefix 유사 경로는 allow

참고 리뷰:

- [PR #2 — Protect symlinked state directories](https://github.com/pinetreeB/show-me-the-work/pull/2#discussion_r3594928078)

---

## HINT-01 — inline Python `Path` 쓰기 탐지 보강

### 문제

현재 state-file friction용 inline regex가 다음 형태를 놓칠 수 있다.

```python
Path("tmp/ledger.json").replace(Path(".fable-lite/ledger.json"))
Path("tmp/ledger.json").rename(Path(".fable-lite/ledger.json"))
Path(".fable-lite/ledger.json").open(mode="w").write("x")
Path(".fable-lite/ledger.json").open("r+").write("x")
Path(".fable-lite/ledger.json").open("rb+")
```

주의: 이 friction은 강한 보안 경계가 아니라 우발적 직접 편집을 막는 보조 장치다. 따라서 정규식 하나를 “완전한 방어”로 주장하지 않는다.

### 관련 코드

```text
core/shell_hints.py
- _INLINE
- _inline_paths
```

### 수정 요구

가능하면 단순 regex 확장보다 `python -c` 소스에 한정한 AST 분석을 검토한다.

최소 지원:

- `Path(...).write_text/write_bytes/unlink/touch/...`
- `rename/replace` receiver와 destination 모두
- `open(mode=...)`
- writable update mode: `r+`, `rb+`, `w+`, `a+`, `x+`
- plain built-in `open(path, writable_mode)`도 가능하면 지원

읽기 오탐 금지:

```python
Path(".fable-lite/x").read_text()
Path(".fable-lite/x").exists()
Path(".fable-lite/x").open("r")
```

### 필수 테스트

참고 리뷰:

- [PR #3 — Path rename/replace destination](https://github.com/pinetreeB/show-me-the-work/pull/3#discussion_r3595749863)
- [PR #3 — writable Path.open modes](https://github.com/pinetreeB/show-me-the-work/pull/3#discussion_r3595749868)

---

## REL-01 — 릴리스 버전과 lockfile 동기화

### 문제

확인할 버전 표면:

```text
.claude-plugin/plugin.json
.claude-plugin/marketplace.json
pyproject.toml
README.md badge
README.ko.md badge
CHANGELOG.md
uv.lock
Git tag
GitHub Release
wheel metadata
```

현재 version sync script가 `uv.lock`이나 공개 GitHub Release까지 검증하지 않을 수 있다.

### 수정 요구

1. `uv.lock`을 실제 개발 워크플로에서 사용한다면 version sync 검사에 포함
2. 사용하지 않는 lockfile이라면 삭제하고 CI·문서에서 명시
3. release workflow에서 tag와 package/plugin version 일치 검증 유지
4. GitHub Release 생성 절차를 릴리스 체크리스트에 추가
5. clean wheel install로 metadata 확인
6. `smtw --version` 또는 동등한 version 출력 경로 추가 검토

### 필수 테스트

```python
def test_all_release_version_surfaces_are_synchronized():
    ...
```

CI에서 lock drift도 잡아야 한다.

---

# 5. P2 제품·문서 작업

## DOC-01 — README 주장과 실제 동작 정합성

### 수정할 핵심 문구

#### 현재 기대 충돌 1

```text
한 번 설치하면 모든 작업에서 알아서 작동
```

실제 동작:

```text
플러그인 설치 + 각 프로젝트의 명시적 opt-in 필요
```

권장:

> 플러그인을 한 번 설치한 뒤, 감독할 프로젝트에서만 config를 켜면 자동으로 작동합니다.

#### 현재 기대 충돌 2

```text
AI가 무시할 수 있는 층이 아예 없음
```

실제 동작:

```text
Stop은 최대 2회 차단 후 교착 방지를 위해 fail-open
```

권장:

> 검증 없는 완료를 최대 두 번 기계적으로 되돌려 보내며, 교착을 막기 위해 그 이후에는 감사 기록과 경고를 남기고 통과시킵니다.

#### stale 문구

현재 버전이 2.3.x인데 다음과 같은 과거 예정 문구가 남아 있지 않은지 검색한다.

```text
v2.1에서 추가할 예정
```

### README 권장 구조

1. 3줄 제품 설명
2. 보장하는 것 / 보장하지 않는 것
3. 1분 설치
4. 프로젝트 opt-in
5. 실행 예시
6. 상태·개인정보·삭제 방법
7. 호스트 지원표
8. 아키텍처
9. 실험 결과와 방법론
10. Known Limitations

### 실험 문구 권장

> 소규모 통제 실험에서 검증 비용이 있는 작업은 smtw ON 조건이 OFF보다 실제 검증을 시도하는 행동을 일관되게 증가시켰다. 정확성 향상은 관측되지 않았고, 비용·시간 증가폭은 작업과 실행마다 크게 달랐다.

단일 배율을 대표 수치로 강조하지 않는다.

---

## PKG-01 — `smtw` console script와 기본 운영 명령

`pyproject.toml`에 하위 호환 alias를 추가한다.

```toml
[project.scripts]
smtw = "fable_lite.cli:main"
fable-lite = "fable_lite.cli:main"
```

권장 CLI:

```text
smtw init
smtw doctor
smtw status
smtw enable
smtw disable
smtw scorecard
smtw version
```

### `smtw doctor` 최소 검사

- Python 실행 경로와 버전
- plugin manifest와 hook file 존재
- 프로젝트 opt-in config
- project root 판정
- `.fable-lite` 쓰기 권한
- hook command가 실제 Python을 찾는지
- mock JSON payload 왕복
- lock contention latency
- corrupt state backup 존재 여부
- host 지원 상태
- 현재 fail-open health warning

---

## STATE-01 — 공유 config와 runtime state 분리 설계

현재 `.fable-lite/`에 공유 설정과 로컬 runtime 상태가 섞여 있으면 다음 요구가 충돌한다.

- 팀과 supervision 설정 공유
- prompt·command·path·agent log는 commit하지 않기
- `.fable-lite/` 전체 ignore
- config만 선택적으로 commit

후속 설계 후보:

```text
.smtw.toml                       # 공유 config
.git/.smtw/                      # Git 저장소 로컬 runtime state
또는
<OS data dir>/smtw/<project-id>/ # 사용자별 runtime state
```

이 작업은 v2.3.1 필수 수정과 섞지 말고 ADR부터 작성한다.

---

# 6. 기존 GitHub 리뷰 debt 전수 확인

아래 finding은 thread가 unresolved로 남아 있거나 current code와 다시 대조할 가치가 있다. 각 finding마다 다음 중 하나로 종결한다.

```text
FIXED      — 재현 테스트 + 수정 커밋
NOT_REPRO  — 최신 main에서 실패하지 않는 테스트와 원인 설명
ACCEPTED   — Known Limitation + 위험 평가 + 후속 issue
DUPLICATE  — 동일 회귀 테스트/수정 링크
```

## PR #1

- [ ] 모든 shell segment 검사
- [ ] absolute peer candidate project-relative 정규화
- [ ] recovered Codex attribution exact 승격
- [ ] Codex contract authorship 전에 identity resolve

## PR #2

- [ ] force checkout 차단
- [ ] symlink `.fable-lite` 보호

## PR #3

- [ ] `Path.rename/replace` destination 탐지
- [ ] keyword/update `Path.open` writable mode 탐지

## PR #4

- [ ] root mismatch project opt-in 재검사

## PR #5

- [ ] R2 audit write nonblocking
- [ ] coordination first/last timestamp min/max

---

# 7. 추가 테스트 전략

## 7.1 Property-based / grammar fuzz

대상:

```text
core/destructive_guard.py
core/shell_command.py
core/shell_hints.py
path canonicalization
```

생성할 입력:

- quotes
- escaped operators
- nested wrappers
- PowerShell flags
- Windows drive paths
- UNC paths
- relative traversal
- symlink/reparse
- chained commands
- empty/invalid tokens
- environment references
- pathspec magic

불변식:

- benign command가 파괴형으로 오탐되지 않음
- known destructive corpus가 누락되지 않음
- parse 불능 파괴형은 fail-closed
- state dir은 lexical/resolved 어느 경로에서도 보호
- 같은 파일의 absolute/relative/case variant가 같은 canonical key

## 7.2 상태 기계 모델 테스트

턴 상태:

```text
turn_not_started
active
finish_requested
blocked/restarted
turn_finished
stale/GC
```

invocation 상태:

```text
open
completed
lease expired
```

검증할 불변식:

- block 전에 active turn이 사라지지 않음
- allow 이후에만 finished 전이
- missing baseline mutation-capable은 clean claim 불가
- read-only missing baseline은 clean 주장 없이 allow
- verification은 last change 이후여야 함
- peer change exemption과 remote epoch는 섞이지 않음
- crash recovery event가 귀속 authority로 조기 승격되지 않음

## 7.3 Hook latency budget

각 hook에 내부 budget을 둔다.

예시:

```text
inactive fast path       p95 < 100 ms
ordinary PreToolUse      p95 < 500 ms
R2 deny under contention < 1 s
Stop 1k normal scope     기존 SLO 유지
```

shared runner의 전체 benchmark는 measure-only로 둘 수 있지만, lock contention 같은 결정론적 timeout 회귀는 blocking CI로 만든다.

---

# 8. PR 분할 권장안

## PR A — R2 chained segments + force checkout

변경 범위:

```text
core/destructive_guard.py
tests/test_multiagent_f2*.py
adapter subprocess regression
```

## PR B — R2 nonblocking coordination audit

변경 범위:

```text
core/scorecard_coordination.py
core/agent_log.py 또는 전용 lock module
core/adapter_observation.py
tests/test_scorecard_coordination.py
```

## PR C — opt-in root mismatch

변경 범위:

```text
adapters/claude_code/bootstrap.py
tests/test_claude_quiet_optin.py
README
```

## PR D — Codex recovered identity

변경 범위:

```text
adapters/codex_cli/pre_tool_use.py
adapters/codex_cli/post_tool_use.py
tests/*codex*
```

## PR E — path/state-dir hardening

변경 범위:

```text
core/destructive_guard.py
core/shell_hints.py
tests
```

## PR F — scorecard timestamps + release hygiene

서로 독립 커밋 또는 별도 PR 권장.

---

# 9. 각 AI 에이전트의 보고 형식

각 작업 에이전트는 최종 보고를 다음 형식으로 제출한다.

```markdown
## 작업 ID

R2-01

## 재현

- 실패 테스트:
- 수정 전 결과:
- 실제 원인:

## 수정

- 변경 파일:
- 핵심 알고리즘:
- 기존 invariant 보존 방법:

## 검증

- focused test:
- full pytest:
- probes:
- e2e:
- ruff:
- Windows:
- Ubuntu:

## 위험·잔여 한계

- 새 오탐 가능성:
- 새 미탐 가능성:
- 의도적으로 비범위로 둔 항목:

## 리뷰 debt 처리

- 관련 GitHub thread:
- 상태: FIXED / NOT_REPRO / ACCEPTED / DUPLICATE
- 근거:
```

---

# 10. 오케스트레이터용 실행 프롬프트

아래 블록을 그대로 상위 AI에게 전달할 수 있다.

```text
당신은 pinetreeB/show-me-the-work v2.3.1 안정화 책임자다.

목표는 새 기능 추가가 아니라 첨부된 안정화 지시서의 P0/P1 결함을 재현 테스트와 함께 폐쇄하는 것이다.

규칙:
1. 최신 main을 기준으로 각 finding을 먼저 재현한다.
2. 한 PR에 한 문제군만 다룬다.
3. 테스트를 약화하거나 fail-open 범위를 넓혀 통과시키지 않는다.
4. R2 deny 결정은 audit I/O와 무관하게 빠르게 반환되어야 한다.
5. 프로젝트별 opt-in 계약을 깨지 않는다.
6. 모든 수정은 focused regression + full pytest + probes + E2E + Ruff 증거를 남긴다.
7. 과거 GitHub review finding은 FIXED/NOT_REPRO/ACCEPTED/DUPLICATE 중 하나로 명시적으로 닫는다.
8. 코드 변경 전에 작업 계획과 영향 invariant를 보고한다.
9. 기존 코드가 이미 수정된 경우 변경하지 말고 그 사실을 증명하는 테스트와 근거를 제출한다.
10. 완료 보고는 지시서의 보고 템플릿을 따른다.

작업 순서:
A. R2-01
B. R2-02
C. AUDIT-01
D. ACT-01
E. R2-03
F. CODEX-01/CODEX-02
G. SCORE-01
H. R2-04/HINT-01
I. REL-01/DOC-01

P0가 모두 green이 되기 전에는 v2.4 기능을 시작하지 않는다.
```

---

# 11. 릴리스 체크리스트

- [ ] P0 전부 폐쇄
- [ ] P1 전부 폐쇄 또는 명시적 Known Limitation
- [ ] unresolved review thread 정리
- [ ] Ubuntu CI green
- [ ] Windows CI green
- [ ] clean wheel install
- [ ] `uv.lock` 정책 확정
- [ ] plugin/marketplace/pyproject/README/changelog/tag version 일치
- [ ] GitHub Release 생성
- [ ] README의 opt-in·2회 cap·비보장 문구 수정
- [ ] `probes-latest.json` fresh 생성
- [ ] provenance receipt fresh 생성
- [ ] 실제 Claude Code sandbox smoke
- [ ] 실제 Codex CLI sandbox smoke
- [ ] Antigravity는 live 확인 여부를 과장 없이 표기
- [ ] CHANGELOG 제목에 “No new features” 명시
- [ ] 릴리스 후 최소 1개 외부 테스트 프로젝트에서 dogfood

---

## 최종 목표

이 안정화 작업이 끝난 뒤 저장소가 전달해야 할 메시지는 다음과 같다.

> show-me-the-work는 AI를 더 똑똑하게 만드는 도구가 아니다.  
> 변경과 검증의 실행 증거를 관측하고, 검증 없는 완료를 제한적으로 되돌려 보내는 작업 규율 하네스다.  
> 보안 경계나 완전한 적대 모델 방어를 주장하지 않으며, 동시성·오류·성능 한계를 테스트와 문서로 공개한다.

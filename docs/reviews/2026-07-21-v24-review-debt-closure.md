# v2.4 안정화 트랙 리뷰 debt 종결표

> 대상: `docs/reviews/2026-07-20-sol-stabilization-handoff.md` §2 우선순위 표 + §6 GitHub 리뷰 debt(PR #1~#5) 전 항목.
> 기준: `main` d678f40(이 PR H가 분기한 시점). 각 항목을 FIXED(v2.4 PR 링크) / ALREADY_FIXED(v2.4 트랙 이전에 이미 해소) / NOT_REPRO 중 하나로 종결한다.
> 본 PR H 자체가 다루는 DOC-01·PKG-01·STATE-01(ADR 단계, 비범위)은 이 표에서 제외한다 — 별도로 처리 중이거나 이번 트랙 범위 밖이다.

## 요약

Sol 지시서 §2가 나열한 12개 P0/P1 결함(R2-01, R2-02, AUDIT-01, ACT-01, R2-03, CODEX-01, CODEX-02, SCORE-01, R2-04, HINT-01, REL-01) 전부와, 지시서에는 없던 신규 발견 1건(NEW-01, coordination reason map 받이통 문제)까지 총 13건 모두 **main에 실제로 반영되어 있음을 코드·테스트·커밋으로 직접 확인**했다. NOT_REPRO 판정은 없다 — 전부 실결함이었고 전부 닫혔다.

| ID | 판정 | PR | Merge 커밋 |
|---|---|---|---|
| R2-01 | FIXED | #7 (worker/v24-pr-a) | b059f1a |
| R2-02 | FIXED | #7 (worker/v24-pr-a) | b059f1a |
| AUDIT-01 | ALREADY_FIXED (v2.3.1) | — | a4774e6 |
| ACT-01 | FIXED | #9 (worker/v24-pr-c) | 8d69b09 |
| R2-03 | FIXED | #11 (worker/v24-r2-03) | d678f40 |
| CODEX-01 | FIXED | #10 (worker/v24-pr-d) | 166ccbe |
| CODEX-02 | FIXED | #10 (worker/v24-pr-d) | 166ccbe |
| SCORE-01 | FIXED | #8 (worker/v24-pr-f) | 0451e7b |
| R2-04 | FIXED | #12 (worker/v24-pr-e) | 7230de4 |
| HINT-01 | FIXED | #12 (worker/v24-pr-e) | 7230de4 |
| REL-01 | FIXED | #8 (worker/v24-pr-f) | 0451e7b |
| NEW-01 (신규) | FIXED | #12 (worker/v24-pr-e) | 7230de4 |

---

## PR #1 (R2-01, R2-03, CODEX-01, CODEX-02)

### R2-01 — 체인된 셸 명령의 모든 segment 검사
**FIXED.** `core/destructive_guard.py`의 `parse_destructive_commands`가 `_command_segments()`로 나눈 `&&`/`;`/`|` 각 segment를 독립 파싱해, 앞선 무해 segment 뒤의 파괴 명령(`echo ok && rm peer-owned.py`류)도 잡는다. 커밋 `e03b321` "fix(r2): inspect every chained shell segment", 회귀 테스트 `tests/test_multiagent_f4_stabilization_r2_01.py` 신설. PR #7 merge `b059f1a`.

### R2-03 — absolute peer candidate project-relative 정규화
**FIXED.** 1차 수정(쓰기 경로 정규화) 커밋 `9b43e5a`(worker/v24-r2-03 최초 커밋) + agy Critical 적발 대응 2차 수정(읽기 경로 `open_peer_invocation_candidates`가 저장된 legacy 절대경로 candidate도 `canonicalize_project_path`로 마이그레이션) 커밋 `f9f04ed`. 재현 테스트 `tests/test_multiagent_r2_03.py`(6개, 신규 1개 포함) 전부 green. PR #11 merge `d678f40`.

### CODEX-01 — recovered Codex identity를 contract 판정에서 exact로 취급
**FIXED.** `adapters/codex_cli/pre_tool_use.py`에 claude_code와 동일한 `legacy_default→exact` 승격 로직 추가. 커밋 `75a51cd`. 주의: 지시서가 서술한 런타임 실패 자체는 조사 결과 NOT_REPRO였다 — 트리아지 기준 시점(533df02) 이후 무관한 F2 커밋(`591eedd`)이 `_active_invocation`의 복구 분기에서 `identity_synthetic=False`를 부수적으로 이미 세팅하기 시작해 실질 결함은 이미 해소된 상태였다. 다만 코드 자체(보정 스니펫 부재)는 실재하는 구조적 결함이라 수정하고 회귀 테스트로 고정했다(`tests/test_codex_recovered_identity.py`). PR #10 merge `166ccbe`.

### CODEX-02 — contract authorship 기록 전에 identity resolve
**FIXED.** 동일 커밋 `75a51cd`가 `adapters/codex_cli/post_tool_use.py`에도 같은 보정을 추가. 지시서의 "record가 resolve보다 먼저 실행된다"는 순서 서술도 조사 결과 현재 코드와 불일치(resolve가 이미 record보다 먼저 실행됨) — 순서는 NOT_REPRO, 보정 누락만 실재해 수정. PR #10 merge `166ccbe`.

## PR #2 (R2-02, R2-04)

### R2-02 — `git checkout -f/--force` 통과
**FIXED.** `core/destructive_guard.py`에 `GIT_CHECKOUT_DISCARD_FLAGS = frozenset({"-f", "--force"})` 신설, 해당 플래그가 있으면 target attribution 조회와 무관하게 즉시 `implicit_scope` 차단. 커밋 `5ddd317` "fix(r2): block forced checkout branch switches", 회귀 테스트 `tests/test_multiagent_f4_stabilization_r2_02.py` 신설. PR #7 merge `b059f1a`.

### R2-04 — symlink된 `.fable-lite` lexical 보호
**FIXED.** `_canonicalize_lexical_target` 신설(symlink resolve 없이 project-relative key 산출) — `.fable-lite` 자체가 외부를 가리키는 symlink여도 resolve 결과가 아닌 lexical 경로로 `_is_state_dir_key` 판정을 먼저 수행. 커밋 `de4f6f8` "fix(r2): protect symlinked state directory lexically", 회귀 테스트 `tests/test_multiagent_f4_stabilization_r2_04.py` 신설. PR #12 merge `7230de4`.

## PR #3 (HINT-01)

### HINT-01 — inline `Path.rename/replace/open` 쓰기 탐지 보강
**FIXED.** `core/shell_hints.py`의 `_INLINE` 정규식을 확장해 `rename`/`replace` 호출의 destination 인자와 `r+`/`w+`/`a+`/`rb+` 등 갱신 모드 `open()` 호출까지 포괄. 커밋 `502c689` "fix(hints): detect inline Python write destinations", 회귀 테스트 `tests/test_shell_hints_stabilization.py` 신설. PR #12 merge `7230de4`.

## PR #4 (ACT-01)

### ACT-01 — session root mismatch 시 프로젝트별 opt-in 재검사
**FIXED.** `adapters/claude_code/bootstrap.py`에 mismatch 시 새 env root의 `config.json`을 즉시 재검사하는 로직(정책 A: mismatch root를 새 프로젝트로 취급, 그 프로젝트가 정확히 opt-in일 때만 해당 훅에서 사용) 추가. 커밋 `197353c` "fix(claude): recheck opt-in on root mismatch", 회귀 테스트 `tests/test_claude_activation_mismatch.py`(140줄) 신설. PR #9 merge `8d69b09`.

## PR #5 (AUDIT-01, SCORE-01)

### AUDIT-01 — R2 deny 감사 기록을 진짜 nonblocking으로 변경
**ALREADY_FIXED — v2.4 트랙 이전(v2.3.1)에 이미 해소.** 커밋 `a4774e6` "Fix fail-fast R2 coordination auditing"(v2.3.1 버전범프 커밋 `7057ee7`보다 앞선 시점)이 `core/agent_log.py`·`core/scorecard_coordination.py`·`core/ledger.py` 총 219줄 변경 + `tests/test_scorecard_coordination.py` 252줄 보강으로 이미 해결했다. v2.4 PR 어디에도 포함되지 않는다(포함될 필요가 없다).

### SCORE-01 — coordination 시간 범위를 timestamp로 계산
**FIXED.** `fable_lite/scorecard.py`의 `_run_coordination_view`가 append 순서 덮어쓰기 대신 `occurred_at` datetime의 `min`/`max`로 first/last를 계산하도록 수정. 커밋 `f49d008`, 재현 테스트 3종(역순 append/동일 timestamp/session 필터 후) `tests/test_scorecard_cli.py`에 추가. PR #8 merge `0451e7b`.

## §2 표에만 있고 §6 PR 목록엔 없는 항목

### REL-01 — `uv.lock`, manifest, changelog, release 버전 동기화
**FIXED.** 조사 결과 `uv.lock`은 CI·문서 어디서도 실사용되지 않고(CONTRIBUTING.md는 plain `pip`/`pytest`만 문서화) 자체 버전이 2.1.1로 38커밋·2개 마이너버전 정체돼 있어 정책=삭제로 확정. 삭제+`.gitignore`+`scripts/sync_version.py` 가드(재도입 시 CI에서 즉시 실패) 추가, 버전 표면 동기화 테스트를 `test_all_release_version_surfaces_are_synchronized`로 확장. 커밋 `5d4c918`. PR #8 merge `0451e7b`(SCORE-01과 동일 PR).

### NEW-01 — coordination reason map이 여러 실패 사유를 `unresolvable_target` 받이통으로 뭉갬 (Sol 지시서에 없는 신규 발견)
**FIXED.** 이 대화 세션 초반에 material-erp 실사고를 조사하며(`tmp/r2-unresolvable-probe.md`, 이후 정리됨) 직접 발견한 문제 — `R2_COORDINATION_REASON_MAP`이 8개 사유만 명시적으로 매핑하고 `parse_unable_*` 계열 전부를 fallback 기본값 `UNRESOLVABLE_TARGET`으로 뭉뚱그려, 실제로는 "명령 파싱 불가"인 차단이 사람에게는 "경로를 찾을 수 없음"으로 잘못 보고되던 것과 정확히 같은 코드 경로다. 커밋 `c844898` "fix(r2): label parser fail-closed denials accurately"가 `CoordinationReason`에 `COMMAND_PARSE_UNAVAILABLE` 등 신규 값을 추가해 `parse_unable_*` 전부를 개별 정확한 사유로 라벨링(현재 맵 20개 항목, 기존 8개 대비 확장). 회귀 테스트 `tests/test_destructive_guard_new_02.py` 신설 + `test_scorecard_coordination.py` 보강. PR #12 merge `7230de4`(R2-04·HINT-01과 동일 PR).

---

## 결론

Sol 지시서 §2의 P0/P1 12개 항목 + 신규 발견 1건, 총 13건 모두 main(d678f40)에 반영 완료다. 지시서 §6의 FIXED/NOT_REPRO/ACCEPTED/DUPLICATE 종결 기준으로 보면 전부 FIXED(또는 v2.3.1에서 ALREADY_FIXED)이며, ACCEPTED나 DUPLICATE로 남긴 항목은 없다. CODEX-01/CODEX-02는 지시서가 서술한 정확한 실패 시나리오 자체는 재현되지 않았으나(NOT_REPRO), 구조적 원인(보정 코드 부재)은 실재해 수정했다는 점을 위 각 절에 명시했다.

남은 것은 이 표에서 제외한 P2 항목(DOC-01·PKG-01·STATE-01)뿐이며, DOC-01·PKG-01은 이 PR H가 같은 커밋 묶음에서 처리한다. STATE-01은 지시서 원문대로 "v2.3.1 필수 수정과 섞지 말고 ADR부터 작성"할 별도 트랙으로 남겨둔다.

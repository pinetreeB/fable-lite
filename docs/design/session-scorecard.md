# Session Quality Scorecard — 설계 SSOT (3-AI 합의 확정)

> 2026-07-13 확정. 입력: tmp/scorecard/spec-draft-claude.md(좌상 원안) + tmp/scorecard/design-agy.md(제품·리스크) + tmp/scorecard/design-codex.md(구현 실측, W10 벤치 재실행 + Windows I/O 200회 계측 포함).
> 쟁점은 좌상이 코드 직접 검증으로 판정. 원안의 기록 위치·캐시·recovered 파생 규칙은 **기각**되고 codex 수정안 구조로 확정됐다.

## 1. 목표

세션 동안 smtw 게이트가 **무엇을 막았고(차단), 무엇을 시켰는지(회복), 무엇을 포기했는지(cap 통과)** 를 성적표로 보여줘 도구 가치를 매 세션 체감시킨다. README "실측 근거"의 데이터 원천.

### 수용 기준 (GWT)
1. Given 게이트 차단이 발생한 세션, When Stop allow, Then 그 세션의 `차단 시도 N · 회복 턴 N · cap 통과 N` 1줄이 systemMessage에 부착된다.
2. Given 차단/회복/cap 활동이 전무한 세션, When Stop allow, Then Scorecard 줄은 부착되지 않는다(노이즈 0). CLI에서는 활성화 이후 세션의 "차단 0"을 정직 표시한다.
3. Given cap 도달로 fail-open된 차단, When 성적표 조회, Then `cap 통과(미해결)`로 별도 표기되고 회복에 절대 합산되지 않는다.
4. Given 기능 도입(activated_at) 이전 구간, When 조회, Then `미관측(N/A)`으로 표시된다 — 0으로 표시하지 않는다.
5. Given Scorecard 저장/집계 실패, When 게이트 판정, Then 판정은 변하지 않고(fail-open) 성적표는 `incomplete`를 노출한다.
6. 성능: 기존 W10 1k/10k SLO 전체 통과 + 증분 SLO(§7) 통과.

## 2. 합의 경위 — 쟁점 판정

| # | 쟁점 | 원안(Claude) | agy | codex | **판정** |
|---|------|--------------|-----|-------|----------|
| 1 | 세션 경계 | session_id | 오늘/기간 누적 | canonical identity 정본 | **codex**: `(host, session_id, agent)` 정본(verification_covers.agent_key 재사용). `--days`는 CLI 뷰 필터. Stop 1줄=현재 세션만. legacy는 `미귀속` bucket 분리 |
| 2 | 기록 위치 | agents jsonl 혼입 | 혼입+v2 validator 추가 | **별도 journal** | **codex**: agents jsonl은 replay 입력이라 혼입 시 유령 turn 생성(ledger_v2.py:35-58 — unknown 이벤트도 turn 생성/갱신). `.fable-lite/scorecard/gates.jsonl` + 독립 `scorecard_schema_version=1` |
| 3 | Stop 1줄 기본값 | 미정 | 기본 ON+옵트아웃, 0은 dim | 기본 ON, 활동 시만 표시 | **codex+agy 수렴**: 기본 ON, `block/recover/cap_allow` 있는 세션만 부착. env `FABLE_LITE_SCORECARD=0` 옵트아웃 |
| 4 | recovered 판정 | 파생 계산 | 명시 집계 | 명시 transition+resolves | **만장일치(원안 기각)**: reason_code별 회복 조건 상이(N1은 마커만으로 회복 — conformance 테스트가 고정). 명시 `recover` transition + `resolves[]` |
| 5 | cap_allow 노출 | 노출(기움) | 전적 찬성 | 찬성, 사실형 문구 | **만장일치**: `cap 통과(미해결)` 표기, recovered 불합산 |
| 6 | 성능·원자성 | 락 보유+append 무시 가능 | scorecard.json 기각 | 실측 기각+증분 SLO | **codex**: record_event 재사용 금지(비재진입 락 중첩→15s timeout 경로), 별도 캐시 파일 기각(atomic replace 2/200 WinError 5 실측), R1은 락 신규 획득 비용 별도 측정 |
| 7 | 하위 호환 | 무마이그레이션 | 찬성 | 찬성+N/A 의미론 | **codex 보강**: 무이벤트 과거≠차단 0. `activated_at` 이후 exact-attribution 세션만 0 표시 |

agy 고유 보강(채택): 성적표 렌더에 **경로·파일명·메시지 등 민감정보 금지**(순수 카운트·통계만) / 멀티에이전트 오귀속 방지 위해 에이전트별 분리 표시.

## 3. 데이터 모델

### 3.1 Gate transition journal — `.fable-lite/scorecard/gates.jsonl` (진실 원천)

```json
{
  "scorecard_schema_version": 1,
  "event": "gate_transition",
  "event_id": "uuid",
  "host": "claude_code|codex_cli|antigravity",
  "session_id": "...",
  "agent": "...",
  "turn_id": "...",
  "reason_code": "stop.verification_missing",
  "action": "block|recover|cap_allow",
  "resolves": ["block-event-id", "..."],
  "resolution": "verification|observation|markers|goals_checkpoint|intent_checkpoint|contract|none",
  "attribution": "exact|legacy_default",
  "occurred_at": "UTC ISO-8601"
}
```

의미 규칙:
- `cap_allow`는 절대 `recover`로 집계하지 않는다.
- routine allow는 기록하지 않는다. **이전 unresolved block을 닫는 allow만** `recover`로 기록하고 `resolves`에 닫은 block event_id를 명시한다.
- 집계 단위 분리: `blocked_attempts` / `recovered_scopes` / `resolved_attempts` — "차단 2→회복 2"처럼 다른 단위를 같은 숫자로 보이지 않는다.
- recovery scope = `(host, session_id, agent, turn_id, reason_code)`.
- malformed/부분 마지막 줄은 무시하되 결과에 `complete=false` 노출.
- v2 ledger 이벤트 스키마와 버전을 공유하지 않는다(독립 진화).

### 3.2 reason_code (내부 결정 코드 — 어댑터 노출 문구와 분리)

| code | 발원 |
|------|------|
| `stop.provenance_incomplete` | verify_state.evaluate_without_io 첫 분기 |
| `stop.investigation_markers` | N1 조사 마커 부족 분기 |
| `stop.verification_missing` | 최종 미검증 분기 |
| `pretool.goals_missing` | gate_counters.block_goals_once |
| `pretool.intent_missing` | gate_counters.block_intent_once |
| `pretool.contract_missing` | contract.py R1 |

현재 `_record_stop_block` 합류 지점에서는 원인 구분이 불가하므로, 내부 Decision에 `reason_code`를 추가한다(외부 어댑터 응답 문구는 불변).

### 3.3 Stop O(1) 캐시 — `ledger.json` 내 `scorecard_cache` (optional·bounded·재생성 가능)

- 키: `agent_key()`와 동일한 `host:session_id:agent`
- 값: 현재 세션 totals, unresolved block event_id 목록, 최근 turn, `activated_at`, `complete`
- **bound**: 최근 64 세션 초과분 제거(ledger 비대화 방지)
- v2 스키마에 **optional** 필드로 검증(기존 ledger에 필수 필드 추가 금지 — 무마이그레이션)
- Stop은 이미 로드한 ledger 스냅샷에서 O(1)로 1줄 생성. **추가 파일 read/scan 0회.**
- CLI 정본은 journal 재집계. 캐시 삭제/오염 시 journal에서 재구축 가능해야 한다.

## 4. 기록 지점과 락 규율

- **`record_event()` 재사용 금지** — 비재진입 owner-file 락(agent_log.py:106-149)을 이미 보유한 차단 경로에서 재획득하면 15s timeout 경로가 된다.
- 신규 primitive: `record_gate_transition_locked(ledger, payload, transition)` — **이미 락을 보유한 호출자용**. gate 판정 + 기존 block counter + scorecard_cache 증분을 한 RMW 안에서 처리하고, journal append는 같은 임계구역에서 수행하되 실패해도 gate decision과 분리(캐시에 `complete=false`).
- R1(contract.py)은 현재 락이 없다 → 별도 owning wrapper가 한 번만 락을 잡는다. R1 경로 신규 락 비용은 증분 SLO로 별도 측정.
- hook hot path에 retry loop 금지. 실패는 기록하고 CLI rebuild/진단으로 넘긴다.

## 5. 선행 수정 — 3-host canonical identity 상시 전달

현재 세 Stop 어댑터는 원본 payload에 `agent`가 있을 때만 identity를 `evaluate_stop()`에 전달한다(adapters/claude_code/stop.py:29-42, adapters/codex_cli/stop.py:109-122, adapters/antigravity/hook_common.py:107-120). 어댑터가 이미 계산한 `CanonicalInvocation` identity(host/agent/session_id/turn_id)를 **항상** 전달하도록 수정한다. v1 ledger는 active_turn이 없어 기존 top-level fallback을 그대로 타므로 agent-less v1 호환과 양립한다.

## 6. 표시

### 6.1 Stop allow 1줄 (기본 ON, 활동 시만)
- 문구: `[smtw] 이번 세션 · 차단 시도 2 · 회복 턴 1 · cap 통과 0`
- 부착 조건: 현재 canonical 세션에 block/recover/cap_allow ≥1. 없으면 기존 allow 메시지 그대로(줄 미부착).
- 캐시 부재 또는 `complete=false`면 **잘못된 0 대신 줄 생략**.
- 옵트아웃: `FABLE_LITE_SCORECARD=0`.
- Claude 어댑터는 allow에서 `systemMessage`만 사용(additionalContext 금지 — 재호출 사이클, stop.py:44-50 주석 준수). Codex/Antigravity는 공용 렌더 문자열을 host shape에만 매핑.

### 6.2 CLI 상세 — `python -m fable_lite scorecard`
- 옵션: `--root`(기본 .), `--session <id>`, `--days N`(뷰 필터), `--all`, `--json`
- 렌더(한/영): 게이트별 표(blocked_attempts/recovered_scopes/resolved_attempts/cap_allows) + verification ok/fail + 기간 + completeness + `미귀속`/`미관측(N/A)` 구분
- **민감정보 금지**: 경로·파일명·프롬프트·메시지 렌더 금지. 순수 카운트·통계만(공유 캡처 안전).
- 에이전트별 분리 표시(멀티에이전트 오귀속 방지).

## 7. 성능 하드게이트 (구현 완료 조건)

1. Stop allow 경로: 새 glob/JSONL scan/stat/hash **0회**.
2. 현재 세션 요약: 이미 로드한 ledger 캐시에서 O(1).
3. gate journal append 증분: p95 ≤ 100ms, p99 ≤ 250ms (별도 bench phase).
4. 기존 W10 1k/10k 전체 SLO 동시 통과 (baseline: tmp/scorecard/bench-codex.json — 1k Stop p95 427ms, 10k Stop p95 4,462ms/budget 6,000ms).
5. fault(락 timeout·PermissionError·read-only·disk-full)에서 gate decision 불변 + Scorecard만 incomplete.
6. 새 bench phase: `stop_allow_scorecard` / `gate_block_scorecard` / `r1_block_scorecard`, on/off A/B 동일 조건(synthetic repo/seed/warm-up/30회), percentile hard gate.

## 8. 모듈 경계

| 모듈 | 책임 |
|------|------|
| `core/scorecard.py` | I/O 없는 typed transition 집계(순수 함수) |
| `core/scorecard_store.py` | journal/cache 읽기·쓰기, completeness 판정, `record_gate_transition_locked` |
| `fable_lite/scorecard.py` | argparse 서브커맨드 + 한/영 렌더러 |
| 3 어댑터 | 공용 core 결과를 host 출력 shape로 매핑만 |

## 9. 테스트 전략 (codex §5 채택)

- 단위/스키마: transition 필수 필드·enum·UTC·unknown version 거부 / block→recover→cap 전이 / 중복 event_id idempotency / "2 block 1 recovery = attempts 2·scopes 1·resolved 2" / cap_allow 불합산 / malformed 줄 / pre-activation N/A vs post-activation 0
- 동시성: Win/POSIX 8·32 subprocess, lost update 0, event_id 중복 0, writer crash 후 지속, fault injection에서 gate decision 불변
- 3-host: block→recover / block→cap_allow / no-activity 동일 시나리오, agent-less payload에서도 canonical identity 전달, Claude allow에 additionalContext 부재, malformed state fail-open
- CLI/E2E: 실행 실측, 대형 journal(10k/100k events) latency, 캐시 재구축 동일성
- 성능 회귀: §7

⚠️ Antigravity는 payload-injection conformance만 유효(라이브 훅 미발동 — docs/reviews/p9-agy-live-hooks.md). README 3-host 주장은 live E2E 확보 전 보류 유지.

## 10. 비(非)스코프

README 자동 갱신(후속 스토리), 외부 텔레메트리 서버(금지 합의 유지), wmux 대시보드, 등급화(A+/B 점수), 시간 근접성 기반 세션 자동 병합.

## 11. 구현 승인 조건 (codex §6 — 본 문서로 충족)

1. ✅ agents JSONL 혼입·별도 scorecard.json 제거 → gate journal + bounded ledger cache 확정
2. ✅ reason_code + 명시 recover/resolves transition 확정
3. ✅ canonical 3-host identity 상시 전달 계약 확정(§5)
4. ✅ 과거 무이벤트 = N/A + completeness 노출 확정
5. ✅ W10 전체 SLO + 증분 SLO + Win/POSIX 동시성 fault test를 완료 기준에 포함(§7·§9)

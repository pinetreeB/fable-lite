# Session Quality Scorecard S2 — R2 수리 표적 재검 (ultracode)

> 재검일: 2026-07-14 · 검증자: 우하 pane (implementation-ultracode, Opus 4.8) 오케스트레이션 + 4표적 병렬 검증 + 적대적 회귀 헌팅
> 대상: P4 리뷰(docs/reviews/session-scorecard-ultracode.md)가 확정한 결함 중 codex가 수리한 4건 — COR-1/CON-1, PERF-1, CON-2, COR-2
> 수리 보고: tmp/scorecard/repair-report-codex.md · 방법: 각 결함을 코드 트레이스 + 표적 회귀 테스트 실행 + 격리 재현으로 검증 → 각 판정을 적대적으로 공격(잔여 구멍 + fix가 낳은 신규 결함 헌팅)
> 검증 규율: 전체 pytest 스위트·eval 벤치 미재실행(표적 테스트만), 격리 재현은 저장소 무변형

---

## 1. 한 줄 판정 (쉬운 말)

4건의 수리 중 **2건은 깨끗하게 고쳐졌고(COR-1, PERF-1), 2건은 원래 결함은 고쳤지만 그 과정에서 새 문제를 만들었습니다(CON-2, COR-2).** 새로 생긴 문제 둘 다 **성적표에 보이는 라벨·표시에만 영향**을 주고 게이트 안전(막느냐 마느냐)이나 데이터에는 영향이 없어 심각도는 낮습니다. 다만 그중 **COR-2 수리는 사실상 방향을 반대로 뒤집어서**, 이제 정상적인 모든 세션이 성적표에서 "미귀속(누구 건지 모름)"으로 잘못 표시됩니다 — 원래 문제(라벨이 아예 안 생김)보다 오히려 더 넓게 틀립니다. **STILL-BROKEN(원 결함 잔존)은 0건.**

**하드게이트: 치명 결함 0 유지.** 단 COR-2는 "고쳤다"기보다 "다른 방향으로 틀어졌다"에 가까워 재수리를 권합니다.

---

## 2. 결과 요약

| 결함 | 원 결함 봉합 | 신규 결함 | 종합 판정 | 심각도 |
|------|:---:|:---:|------|------|
| **COR-1/CON-1** (reason-shift cap 유실) | ✅ 봉합 | 없음 | **CONFIRMED-FIXED** | — |
| **PERF-1** (벤치 JSONL scan 맹점) | ✅ 봉합 | 없음 | **CONFIRMED-FIXED** | — |
| **CON-2** (축출 세션 과소집계) | ✅ 봉합 | ⚠️ 있음(회귀) | **FIXED + NEW-DEFECT** | low |
| **COR-2** (legacy_default 죽은 계약) | ✅ 봉합(문자 그대로) | ⚠️ 있음(반전) | **NEW-DEFECT** | low(~medium) |

검증자 판정(4표적): FIXED 3 · STILL-BROKEN 0 · 신규결함 3건(CON-2 회귀, COR-2 verify+adversarial 이중). 적대 검증에서 **COR-1·PERF-1은 VERDICT-UPHELD**, **CON-2는 NEW-DEFECT-FOUND**(1차 verdict가 "트레이드오프"로 관대 처리한 것을 적대 검증이 회귀로 승격 — 오케스트레이터 재현이 지지), **COR-2는 verify·adversarial 양쪽 NEW-DEFECT 일치**.

---

## 3. 표적별 상세

### COR-1/CON-1 → CONFIRMED-FIXED (신규 결함 없음)

**수리:** codex는 권고 ①안을 `verify_state.py:298-299`에 구현 — cap 시 현재 사유의 미해결 block이 없으면 **세션 전체 미해결 block ID를 `cap_allow.resolves`로 fallback**.
```python
if action is GateAction.CAP_ALLOW and not resolves:
    resolves = unresolved_block_ids(ledger, payload)   # 사유 불문 전체
```

**검증 (오케스트레이터 직접 + 워크플로우):**
- 2-reason 재현 경로(provenance block ×2 → verification_missing 사유전환 → cap)가 이제 cap_allow를 `{p1,p2}` resolves로 기록 → 침묵 드롭 소멸(GWT#3 충족).
- **★불변식 무파손 확인(2 anchor 코드 트레이스):** ① `scorecard.py:275` `if transition.action is not GateAction.RECOVER: continue` → 집계가 CAP_ALLOW의 resolves를 **완전 skip** ② `scorecard_cache.py:79-81` CAP_ALLOW case는 `cap_allows`만 +1, unresolved **미소비**. → cap_allow에 실린 cross-reason block id는 집계에서 **inert**(RECOVER 집계·CLI 재집계·recovered_scopes/resolved_attempts 어디서도 resolved로 세어지지 않음). 워크플로우 격리 재현 5시나리오(후속 RECOVER·3+사유·반복 재기록·오염 시도)가 pure/cache 양경로 수치 완전일치로 실증.
- 표적 회귀 2건(`test_stop_reason_shift_records_cap_allow_for_all_unresolved_blocks`, `test_stop_records_cap_allow_without_recovery`) + 오케스트레이터 실행 `pytest gate_integration+store+review_regressions` **28 passed**.

**잔여 구멍(수리가 만든 게 아님):** 한 턴에서 상한 도달 후 **미해결 block을 전부 회복(unresolved 완전 비움)**한 뒤 같은 턴에 재차 cap이 발동하면, fallback도 빈 튜플이라 cap_allow가 여전히 드롭된다. 이는 권고①의 본질적 한계(미해결 block이 존재해야만 동작)이자 수리 전에도 드롭되던 자매 케이스이며, 관측 전용·트리거 좁음(전량 회복 후 동일 턴 재cap). 원 COR-1 보고 경로(미해결 잔존)는 정확히 닫혔다.

```
증거:
- core/verify_state.py:298-299 (fallback), :300-301 (드롭 가드 미발동)
- core/scorecard.py:275 (_resolved_blocks RECOVER-only), core/scorecard_cache.py:79-81 (CAP_ALLOW 미소비)
- 오케스트레이터: unresolved_for_entry(None)→전체반환(cache:175-176), 28 회귀테스트 PASS
- 기각: cross-reason resolves가 집계 오염 → 기각(양경로 inert 확정, 적대 5시나리오 UPHELD)
```

---

### PERF-1 → CONFIRMED-FIXED (신규 결함 없음)

**수리:** Scorecard 벤치에 `gates.jsonl`의 `Path.read_text`/읽기용 `Path.open` 계측 추가 + stop_allow on-arm journal read>0을 하드게이트 실패로 배선.
- `provenance_bench_scorecard.py`: `counting_read_text`/`counting_open`이 `_is_scorecard_journal(path)`(name=='gates.jsonl' AND parent=='scorecard') 접근을 `journal_reads`로 계수.
- `provenance_bench_metrics.py:193-194`: `evaluate_scorecard_slo`가 `if enabled.journal_read_count: failures.append("stop_allow_scorecard_new_journal_read")` — on-arm **절대 >0 실패**.

**검증:**
- 프로덕션 읽기 경로(`load_scorecard_journal` → `scorecard_store.py:101` `read_text`)가 실제로 계측됨. ledger.json 읽기(parent='.fable-lite')는 정확히 제외 → off-arm 무관 read 오염 없음(A/B 유효).
- 하드게이트 배선 실효: on-arm journal_read_count=1 → `evaluate_scorecard_slo` passed=False → `bench_provenance.main()` exit 1 → receipt hard_gate.passed=False(적대 검증이 exit-code 배선까지 라인 추적 + 회귀 주입으로 red 확인).
- 표적 회귀 2건(instrumentation 절반 + SLO 절반 각각 방어) PASS. **strawman 아님** — `_stop_allow_phase`가 실제 `evaluate_stop`을 계측 래퍼로 감쌈.

**잔여 구멍(수리와 무관):** 계측이 `Path.read_text`/`Path.open`만 패치 → 미래에 `builtins.open`/`os.open`로 저널을 읽는 코드가 생기면 우회 가능(적대 검증 실증). 단 **현행 프로덕션 리더는 `Path.read_text` 사용이라 잡히며, 이는 원 PERF-1 구멍(read_text/open scan 불가시)이 아니라 mock 기반 접근의 미래 견고성 한계**. 라이브 결함 아님.

```
증거:
- eval/provenance_bench_scorecard.py:178-200 (계측), :234-235 (_is_scorecard_journal)
- eval/provenance_bench_metrics.py:193-194 (SLO 절대 실패 배선)
- 적대: ledger/decoy/non-journal read → journal_read_count=0 (범위 정확), on-arm=1 → exit1 (배선 실효)
- 기각: 무관 read 오염으로 A/B 무의미 → 기각(_is_scorecard_journal 범위 정확)
```

---

### CON-2 → CONFIRMED-FIXED (원 결함) + NEW-DEFECT (low, 수리가 낳은 회귀)

**수리:** `scorecard_store.py:133,159` — 캐시 포화(64) + `existing=None`이면 `complete=False`로 보수 처리.
```python
cache_saturated = len(cache) >= MAX_CACHED_SESSIONS   # 64
complete = appended and consistent and (existing is not None or (not observed_before and not cache_saturated))
```

**원 결함 봉합 확인 (오케스트레이터 재현):** `con2_repro.py`를 수리 코드에 재실행 →
```
[T2] cache[K]: blocked_attempts=1 complete=False   (수리 전엔 complete=True)
[Stop 렌더 입력] cached_session_summary: None(줄 생략)
```
과소집계를 complete=True 권위값으로 노출하던 원 결함 소멸. 신규 회귀 테스트 `test_reactivated_evicted_session_is_not_exposed_as_complete`가 실제 `apply_v2_event` 턴 경계로 complete=False·summary=None·저널진실=3을 강하게 단언, PASS. **원 결함은 확실히 고쳐졌다.**

**그러나 — 신규 결함(경쟁 가설 판정):** 수리가 "진짜 축출"과 "그냥 신규 세션"을 구분하지 못한다.

**가설 1 (1차 verdict — 승인된 트레이드오프):** SSOT §6.1은 "불확실하면 잘못된 숫자 대신 줄 생략"을 인가된 폴백으로 규정한다. 신규 세션 줄이 생략돼도 잘못된 숫자가 아니라 정직한 생략이고, CLI journal 재집계는 정확값을 준다. 리뷰 권고안(축출 판별 불가 시 보수적 complete=False)을 그대로 구현했으니 결함 아님.

**가설 2 (적대 검증 + 오케스트레이터 재현 — fix-introduced 회귀):** 포화 시 산식이 `existing is None` 단일 판별자로 붕괴한다. `existing is None`은 축출과 신규 **양쪽 모두 참** → 포화 후 **모든** missing-key를 incomplete로 뭉갠다. §6.1의 "줄 생략"은 진짜 불완전(offset 불일치·malformed·append 실패=데이터 손실)용이지, **손실 없는 완전한 데이터에 거짓 불완전을 제조하라는 허가가 아니다.**

**증거 (오케스트레이터 격리 재현 `con2_adv_repro.py`, 저장소 무변형):**
```
[포화] cache 크기=64
[신규 N 첫 block] cache[N]: blocked_attempts=1 complete=False   ← 축출 아닌 '진짜 신규'인데 강등
[Stop 렌더 입력] cached_session_summary: None(줄 생략)
[journal 진실] N blocked_attempts=1 (완전·손실 없음)
```
수리 전 산식 `(existing is not None or not observed_before)`에서는 동일 케이스가 `complete=True`로 **정확히 줄을 표시**했다 → 이전에 정상이던 동작을 깬 **명백한 회귀**. 게다가 `updated_entry`의 `complete AND` 누적 때문에 이후 어떤 transition으로도 True 회복 불가(N의 3번째 block까지 False 고정).

**판정: 가설 2 우세.** 트리거는 "프로젝트 ledger에 게이트-활성 세션 64개 누적(단조·영구) 이후 모든 신규 세션" — CON-2 원결함(좁은 재활동)보다 오히려 넓고, 헤드라인 수용기준 **GWT#1(활동≥1 세션은 Stop 1줄)을 흔한 포화 상태에서 무력화**한다. 다만 완화요소(① 잘못된 숫자 아닌 정직한 생략 = §6.1 인가 방향 ② 게이트 판정·데이터 무결성 무영향 ③ CLI `scorecard --all` 재집계로 정확값 항상 조회 가능)로 **severity=low**. 수리 자체 회귀테스트가 session-64(포화 신규)를 기록하면서 complete를 단언하지 않아 이 회귀가 무테스트로 잠복.

**권고:** 포화 시 "진짜 축출"과 "신규"를 구분 — 예: 축출 시 bounded 링버퍼에 evicted key 기록 → 재등장 key만 incomplete, 미기록 key는 genuinely-new로 complete=True 유지. 또는 journal offset이 시사하는 하한과 대조.

```
증거:
- core/scorecard_store.py:133,155-161 (cache_saturated 산식 붕괴), scorecard_cache.py:42 (complete AND 고정)
- 오케스트레이터 재현: con2_repro(원결함 소멸) + con2_adv_repro(신규세션 억제 회귀)
- 기각: '리뷰가 승인한 트레이드오프' → 기각(리뷰는 축출 보수처리만 옵션1로 제시, 순수 신규 억제는 미승인; 옵션2/3 제시=옵션1 부정확 인지)
```

---

### COR-2 → NEW-DEFECT (문자 그대로의 원 결함은 봉합, 그러나 방향 반전 + 프로덕션 전면 오라벨)

**수리:** `CanonicalInvocation.identity_synthetic` + `scorecard_attribution` 추가. raw payload의 host/agent/session_id 중 하나라도 없으면 3어댑터 Stop·PreTool·관측 payload가 `legacy_default` 전달, 셋 다 있으면 `exact`.

**원 결함 봉합 확인:** `new_transition`이 이제 `payload["attribution"]`를 저널에 반영하고, 어댑터가 `invocation.scorecard_attribution`를 채운다 → `legacy_default`가 실제 런타임에서 발원(원 COR-2의 "legacy_default 절대 미발원"은 확정 해소).

**그러나 — 신규 결함(경쟁 가설 판정):**

**가설 1 (봉합됨):** legacy_default가 런타임 발원하니 원 죽은 계약 해소, CONFIRMED-FIXED.

**가설 2 (반전 — 거울상 죽은 계약 + 100% 오라벨):** 판정 코드가 raw payload의 `host` 존재를 검사하는데, **3어댑터 모두 host를 상수로 공급**하므로 raw payload엔 `host`가 구조적으로 절대 없다.

**증거 (오케스트레이터 직접 코드 리딩 — `adapters/claude_code/common.py:137-147`):**
```python
identity_synthetic = any(
    not _string(payload.get(field)) for field in ("host", "agent", "session_id")
)
...
return CanonicalInvocation("claude_code", agent, session_id, ...)   # host = 상수, payload에 없음
```
`payload.get("host")` → 항상 None → `not ""` = 항상 True → **`any(...)`가 host 항에서 항상 단락 True → `identity_synthetic`이 프로덕션 100% True.** 진짜 UUID session_id를 가진 well-formed Claude Stop 세션까지 전부 `attribution=legacy_default` → CLI가 "미귀속"으로 렌더. `exact`는 host+agent+session_id 3필드 동시 존재(=어느 실 호스트도 안 보내는 test-only triple)로만 도달하는 **새 죽은 경로**. 워크플로우 verify·adversarial·오케스트레이터 코드 리딩 **3중 일치**, 격리 재현으로 실 3호스트 payload 전부 legacy_default 실측.

**판정: NEW-DEFECT.** 원 죽은 계약(`exact→legacy` 미발원)을 봉합한 게 아니라 **`exact↔legacy_default`로 반전**시켰다. 설계 SSOT 쟁점7(activated_at 이후 정상 세션=exact-attribution)·§5(어댑터 계산 canonical identity 정본)·원 리뷰("Claude는 session_id 항상 제공→exact 정확")와 정면 충돌. **정상 세션을 능동적으로 "미귀속"으로 오표기**하는 것은 원 결함(정상 세션을 exact로 표기)보다 오히려 넓은 오라벨이다. 심각도: attribution은 표시 전용(게이트·버킷·데이터 무영향, CLI 재집계 카운트는 정확)이라 **low**(오라벨 폭·"실측 근거" 신뢰성 훼손으로 medium 방어 가능). 회귀 테스트 `test_stop_when_raw_payload_has_no_agent_passes_canonical_identity`가 "session_id 보유 payload→legacy_default"를 단언해 **과대판정을 화석화**, 올바른 수정을 능동 차단.

**권고 (재수리):** synthetic 판정에서 **`host` 항 제거**(host는 어댑터가 아는 상수, 판별에 무의미). 실제 식별 정보인 **`session_id` 부재**를 기준으로 판정(agent도 Claude는 기본값이라 포함 시 정상 세션이 여전히 synthetic이 됨 — session_id 단독 기준 권장). 회귀 테스트를 "session_id 보유→exact"로 교정.

```
증거:
- adapters/claude_code/common.py:137-139 (host 검사), :146-147 (host 상수 공급) — 오케스트레이터 직접 확인
- 격리 재현: 실 UUID session_id Claude/codex/antigravity payload 전부 legacy_default, triple만 exact
- 기각: 'exact가 정상 발원한다' → 기각(host 항 상시 True로 exact 도달 불가; test triple만)
```

---

## 4. 오케스트레이터 독립 검증 (first-hand)

- **CON-2 원결함 봉합:** `con2_repro.py`를 수리 코드에 재실행 → `complete=True→False`, summary=None (RUN→OBSERVE).
- **CON-2 신규 결함:** `con2_adv_repro.py` 격리 재현 → 포화 시 신규 세션 첫 block `complete=False`·줄 생략 (RUN→OBSERVE, 저장소 무변형).
- **COR-1 불변식:** `scorecard.py:275`(RECOVER-only)·`scorecard_cache.py:79-81`(CAP_ALLOW 미소비)·`unresolved_for_entry(None)`(전체 반환) 코드 트레이스 + `pytest` **28 passed**(gate_integration+store+review_regressions).
- **COR-2 반전:** `adapters/claude_code/common.py:137-147` 직접 리딩 — host 상수 공급 → synthetic 100% → legacy_default 100% 확정.

**검증 인프라:** 워크플로우 8에이전트 전원 성공(무오류). 방법론상 전체 pytest 스위트·bench_provenance는 재실행하지 않음(표적 테스트·격리 재현·코드 트레이스로 갈음) — codex 수리 보고의 "pytest 312·bench PASS"는 값-로직 정합만 확인, 재생성 확증은 미수행.

---

## 5. 권고 (우선순위)

| 우선 | 항목 | 근거 |
|------|------|------|
| **P1 (재수리)** | COR-2 synthetic 판정에서 `host` 항 제거 → session_id 기준 | 현재 프로덕션 100% 세션이 "미귀속" 오라벨 — 원 결함보다 넓게 틀림. 회귀테스트도 교정 |
| **P2** | CON-2 포화 시 축출/신규 구분(evicted-key 링버퍼 등) | 포화 후 신규 세션 Stop 줄(GWT#1) 영구 생략 회귀 |
| **P3 (백로그)** | COR-1 자매 케이스(전량 회복 후 동일 턴 재cap) | 권고①의 본질적 한계, 관측 전용·트리거 좁음 |
| **P3 (백로그)** | PERF-1 계측을 builtins.open/os.open까지 확장 | 미래 리더 견고성(현행 라이브 결함 아님) |
| **참고** | PERF-2·REG-1은 수리 범위 밖(원 리뷰 헤더 백로그로 명시) | 지시대로 미변경 |

---

## 6. 결론

**STILL-BROKEN 0 · 원 결함 4/4 봉합 · 치명 결함 0.** COR-1·PERF-1은 불변식·하드게이트 배선까지 견고한 **깨끗한 수리**(적대 검증 UPHELD). CON-2·COR-2는 **원 결함은 고쳤으나 각각 low 신규 결함을 유발** — 둘 다 표시·관측 전용이라 게이트 안전·데이터에는 무해하다.

주의를 요하는 것은 **COR-2**다: "죽은 계약 봉합"이라는 문자 그대로의 목표는 달성했으나 판정 로직이 `host`(어댑터 상수)를 검사하는 탓에 **정상 세션 100%를 "미귀속"으로 오표기**하는 반대편 죽은 계약으로 뒤집혔다. "실측 근거"를 표방하는 도구가 모든 세션을 "누구 건지 모름"으로 렌더하는 것은 신뢰성 관점에서 원 결함보다 눈에 띄므로, **COR-2 재수리(host 항 제거)를 P1로 권한다.** CON-2 신규 결함은 정직한 줄 생략(§6.1 인가 방향)이라 P2.

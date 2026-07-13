# Session Quality Scorecard S2 — 다관점 병렬 P4 리뷰 (ultracode)

> 리뷰일: 2026-07-14 · 리뷰어: 우하 pane (implementation-ultracode, Opus 4.8) 오케스트레이션 + 5관점 병렬 서브에이전트 + 적대적 2중 검증
> 대상: git 미커밋 변경 전체 (신규 scorecard 모듈군 8 + 어댑터 Stop 3종 + core 게이트 수정 21파일)
> 설계 SSOT: `docs/design/session-scorecard.md` · 구현 보고: `tmp/scorecard/s2-report-codex.md` · 성능 receipt: `tmp/scorecard/bench-s2-codex.json`
> 방법: 5관점(정확성·동시성·성능·계약·회귀) 정적 리뷰 → 각 결함 refute/confirm 2관점 독립 적대 검증 → 오케스트레이터 직접 재확인(코드 트레이스 + 격리 재현 + 표적 테스트)
> P4 repair 백로그: PERF-2(후속 상태별 벤치 확장), REG-1(cap 반복 기록 정책)는 이번 수리 범위에서 변경하지 않음.

---

## 1. 한 줄 판정 (쉬운 말)

이 기능은 "게이트가 무엇을 막고·시키고·포기했는지"를 세션 성적표로 보여줍니다. **게이트의 안전 동작(막을 건 막고, 실패해도 판정을 안 바꾸는 것)은 튼튼하게 잘 지켜집니다.** 하지만 **성적표에 찍히는 숫자가 특정 상황에서 틀리는** 결함이 몇 개 발견됐습니다. 그중 가장 중요한 것은 "게이트가 상한에 도달해 포기(cap 통과)한 사실이 성적표에서 사라지는" 경우로, 이건 이 도구가 명시적으로 약속한 수용 기준(GWT#3)을 어깁니다. 다만 이 결함들은 전부 **성적표(관측)에만 영향을 주고, 게이트가 실제로 막느냐 마느냐(안전)에는 영향이 없습니다.** 즉시 배포를 막을 치명 결함은 없으나, 넓게 배포하기 전 최소 1건(cap 유실)은 고칠 것을 권합니다.

**하드게이트 판정: 치명(critical) 결함 0건 → 안전축 통과.** 단 codex 보고서의 "UNCONDITIONAL APPROVE / 모든 수용기준 충족" 함의는 부정확 — **수용기준 GWT#3가 도달 가능한 경로에서 미충족(무테스트)**임을 아래에서 실증합니다.

---

## 2. 결함 요약

| ID | 관점 | 원안 심각도 | 검증 후 | 판정 | 한 줄 |
|----|------|------|------|------|------|
| **COR-1 / CON-1** | 정확성·동시성 (동일 결함, 2관점 독립 재발견) | medium / low | **medium** | ✅ CONFIRMED (검증자 4/4) | 한 턴 안에서 차단 사유(reason)가 바뀌면 cap 통과(fail-open)가 성적표에서 유실 — GWT#3 위반 |
| **CON-2** | 동시성 | low | **low** | ✅ CONFIRMED (오케스트레이터 격리 재현) | 64세션 초과로 축출된 세션이 새 턴에 재활동 시 과소집계를 complete=true로 노출 |
| **COR-2** | 정확성 | medium | **low~medium** | ✅ CONFIRMED (검증자 2/2, 심각도 논쟁) | `legacy_default`(미귀속) 귀속이 런타임에서 절대 발원 안 됨 — 죽은 계약 |
| **PERF-1** | 성능 | medium | **low~medium** | ✅ CONFIRMED (검증자 2/2) | §7.1 "JSONL scan 0회" 하드게이트를 벤치 계측이 실제로 검출 못함(read_text 미계측) |
| **PERF-2** | 성능 | medium | **low** | ✅ CONFIRMED (하향, 검증자 2/2) | 증분 하드게이트가 세션 첫 block만 측정 — recover/cap/2nd-block 미계측 |
| **REG-1** | 회귀 | low | **low (논쟁)** | ⚖️ SPLIT (refute 1 / confirm 1) | cap 도달 후 매 호출마다 cap_allow 재기록 → 저널 선형 증가·cap 통과 수 인플레이션 |
| ~~COR-3~~ | 정확성 | low | — | ❌ REFUTED (검증자 2/2 기각) | "lost-commit 후 저널 중복 block 과대계상" 주장 — 저널 카운트는 정직 |

**확정 결함 6건(전부 medium/low) · 치명 0 · 기각 1.** 확정 결함은 **전부 성적표(관측) 정확성** 영역이며, **게이트 판정(allow/block)·데이터 무결성·크래시에는 어느 것도 영향 없음**(모든 검증자 공통 확인).

---

## 3. 확정 결함 상세

### COR-1 / CON-1 (medium) — Stop cap 통과가 reason-shift 시 성적표에서 사라짐

**쉬운 말:** Stop 게이트가 같은 턴에서 2번 막은 뒤(상한 도달) 3번째에 "포기하고 통과(fail-open)"시키는데, 그 3번째의 차단 사유가 앞 2번과 다르면 "포기했다"는 기록(cap 통과)이 성적표에 안 남습니다. 성적표는 "차단 2·회복 0·cap 0"으로 보여서, 실제로 게이트가 손든 사실을 숨깁니다. 이건 정확성·동시성 두 관점이 **독립적으로 같은 결함을 재발견**했고, 4명의 검증자가 전원 CONFIRMED한 이 배치의 헤드라인 결함입니다.

**재현 (2-reason 최소 경로):**
1. 변경파일 존재·deep 모드 세션. Stop#1: provenance 미완 → block(사유=provenance), 카운터 0→1, 미해결{p1}.
2. Stop#2: 여전히 provenance 미완 → block(provenance), 카운터 1→2, 미해결{p1,p2}.
3. 관측 추가로 provenance는 완료됐으나 검증 없음 → Stop#3: block(사유=verification_missing). 카운터 2≥상한(2) → cap 분기 진입. cap 기록이 요구하는 "현재 사유(verification)의 미해결 block"이 없음(미해결은 provenance뿐) → cap_allow 침묵 드롭.
4. 결과: 게이트는 정상 fail-open(allow)하지만 저널·캐시에 cap 흔적 0 → 성적표 `cap 통과 0`.

**왜 이렇게 되나 (근본 원인):** cap **상한 카운터**는 사유 무관 턴 단위 단일 카운터인데, cap **기록**은 사유별(동일 사유의 미해결 block 필수)이라 단위가 어긋납니다. goals/intent 게이트는 사유별 전용 카운터를 써서 이 문제가 없음 → **Stop 전용 결함**.

**권고 (택1):** ① cap 시 현재 사유에 미해결 block이 없으면 미해결 block 전체(사유 불문)를 cap resolves로 사용 — 집계측 부작용 없음(RECOVER만 resolves를 소비, CAP_ALLOW는 resolves를 무시) ② 사유별 미해결 그룹마다 cap_allow 1건 기록 ③ `_stop_blocks`를 사유별 카운터로 분리해 상한·기록 단위 일치.

```
증거:
- core/verify_state.py:138  `if _stop_blocks(...) >= MAX_STOP_BLOCKS`  (사유 무관 단일 카운터)
- core/verify_state.py:113-131  _stop_blocks = blocks["stop"] (3개 stop reason 공유)
- core/verify_state.py:142  cap_allow reason = _decision_reason(현재 3번째 결정)
- core/verify_state.py:298-299  `if action is not BLOCK and not resolves: return False`  (침묵 드롭)
- core/scorecard.py:345-346  _validate_action: resolves 없는 cap_allow는 ScorecardSchemaError (드롭 이중화)
- core/scorecard_cache.py:79-81  CAP_ALLOW case는 unresolved를 소비하지 않음
- core/scorecard_cache.py:171-177  unresolved_for_entry가 reason_code로 필터
- 오케스트레이터 코드 트레이스로 scorecard_cache.py 메커니즘 직접 확인
- 테스트 공백: test_scorecard_gate_integration.py:154-168 _assert_cap는 동일 reason 3연속만 단언 → reason-shift 무테스트
- SSOT 위반: docs/design/session-scorecard.md:13 (GWT#3), :26 (쟁점5 "cap 통과 별도 표기·recover 불합산")
```

---

### CON-2 (low) — 축출된 세션이 새 턴에 재활동하면 과소집계를 "정확(complete)"으로 노출

**쉬운 말:** 성적표 O(1) 캐시는 최근 64개 세션만 들고 있습니다. 한 프로젝트에서 서로 다른 세션이 64개를 넘으면 오래된 세션이 캐시에서 빠집니다. 그 빠진 세션이 나중에 새 턴에서 다시 차단을 겪으면, 캐시가 그 세션을 **빈 상태에서 새로 만들며 "완전(complete=true)"하다고 표시**합니다. 그래서 실제로 3번 막힌 세션의 Stop 줄이 "차단 1"로 나오고, 그 값을 **의심 없는 확정값으로 노출**합니다. SSOT가 요구하는 "정확하거나, 아니면 줄을 생략(잘못된 숫자 금지)" 계약을 어깁니다.

**이 결함은 병렬 검증에서 검증자 2명이 모두 StructuredOutput 실패로 사망**하여 미판정 상태였습니다. **오케스트레이터가 직접 코드 트레이스 + 격리 재현으로 확정**했습니다:

```
격리 재현 결과 (scratchpad/con2_repro.py, 저장소 무변형):
  [T1]  cache[K]: blocked_attempts=2 complete=True
  [evict] K 축출됨=True  cache 크기=64  (서로 다른 64세션이 뒤이어 기록)
  [T2]  cache[K]: blocked_attempts=1 complete=True   ← 빈 entry 재구성
  [Stop 렌더 입력] cached_session_summary: blocked_attempts=1 (권위값으로 노출)
  [journal 진실] K 실제 blocked_attempts=3
  → 과소집계(1)를 complete=True로 노출 = accurate-or-incomplete 계약 위반
```

**핵심 전제 확정:** `core/ledger_v2.py:41-52` — `prompt` 이벤트(새 턴)가 `_new_turn`으로 fresh turn dict를 만들어 `active_turns[agent_key]`를 교체 → `scorecard_observed` 플래그 리셋 → `observed_before=False`. 축출은 저널/offset을 안 건드려 `consistent=True` 유지. `empty_entry`가 `complete=True` 초기값 → `complete = appended(T) and consistent(T) and (existing is None → not observed_before=T) = True`.

**트리거 폭:** 좁음 — 한 프로젝트 ledger에 64개+ 서로 다른 canonical 세션(host:session_id:agent) + 그중 하나가 새 턴에 재활동. wmux 다중 에이전트 장기 프로젝트에서 도달 가능하나 일상적이진 않음. **관측 전용, 게이트 판정 무영향** → low.

**권고:** `existing is None`인데 축출 여부를 판별할 수 없으면 보수적으로 `complete=False`(줄 생략); 또는 재구성 시 journal 재집계로 백필; 또는 세션 단위 "관측 이후 축출됨" 마커를 ledger에 유지.

```
증거:
- core/scorecard_store.py:148-151  complete = appended and consistent and (existing is not None or not observed_before)
- core/scorecard_cache.py:90-98  empty_entry complete=True 초기값
- core/scorecard_cache.py:150-154  bounded_cache = last_occurred_at 상위 64만
- core/scorecard_store.py:260-265  _turn_scorecard_observed → active_turn 플래그(신규 턴서 리셋)
- core/ledger_v2.py:41-52  prompt 이벤트가 turn dict 교체 (전제 확정)
- 테스트 공백: test_deleted_cache_for_observed_turn_...는 '동일 턴 + offset 잔존'만 커버, 축출+턴경계 사각
- SSOT 위반: docs/design/session-scorecard.md:79 (bound 64), :100 (complete=false면 줄 생략)
```

---

### COR-2 (low~medium) — `attribution=legacy_default`가 런타임에서 절대 발원되지 않음 (죽은 계약)

**쉬운 말:** 설계는 "정확히 귀속되는 세션은 exact, 그렇지 않은 레거시 세션은 미귀속(legacy_default)으로 분리"하기로 합의했습니다. 그런데 실제 코드에서 성적표를 기록하는 모든 경로가 항상 `exact`로만 남깁니다. `legacy_default`를 만드는 경로가 하나도 없어서, CLI의 "미귀속" 표시 분기와 스키마 variant가 **프로덕션에서 죽은 코드**입니다. 유일한 커버리지가 손으로 만든 테스트 데이터라, "테스트됨"이 기능 동작을 보증한다는 거짓 확신을 줍니다.

**심각도 논쟁 (검증자 간):**
- **low 근거:** attribution은 표시 전용이라 게이트 판정·버킷 카운트 불변식에 영향 없음. 세션 병합 위해(害)는 attribution이 아니라 `agent_key`(선행 identity 합성, S2가 도입한 게 아님)에서 비롯 — `legacy_default`를 세팅해도 병합은 안 고쳐짐(버킷 키는 agent_key). 트리거도 좁음(Claude는 session_id 항상 제공→exact 정확, antigravity 라이브 미발동, session_id 없는 codex_cli만 사변적).
- **medium 근거:** 합의로 못박은 스펙 항목 미충족 + 정확 귀속 불가 세션을 침묵으로 `exact`로 오표기 = 단순 미관측(N/A)과 달리 적극적 오표기라 도구 신뢰성 핵심을 건드림.

**권고 (택1):** ① `CanonicalInvocation`에 "합성 identity" 플래그를 두어 raw payload에 host/agent/session_id가 없으면 `attribution=legacy_default` 전달; ② 미귀속 요구가 불필요하다고 재확정하면 enum variant·CLI 분기·손수 테스트를 제거해 **죽은 계약을 정리**(설계-구현 불일치를 문서로 봉인).

```
증거:
- core/scorecard_store.py:54  new_transition attribution 기본값 = Attribution.EXACT
- 런타임 발원 3함수 전부 attribution 미전달 → EXACT 고정:
  core/verify_state.py:301, core/gate_counters.py:222, core/contract.py:318 (+ eval/provenance_bench_scorecard.py:54)
- grep LEGACY_DEFAULT 사용처: core/scorecard.py:54(enum), fable_lite/scorecard.py:137(CLI판정), tests/test_scorecard_cli.py:182(손수 데이터) — 기록 경로 0
- 어댑터 합성: adapters/claude_code/common.py:137-139 (session_id or "default", agent or "claude")
- SSOT: docs/design/session-scorecard.md:22 (쟁점1 "legacy는 미귀속 bucket 분리"), :28 (쟁점7), :49 (스키마 attribution)
```

---

### PERF-1 (low~medium) — §7.1 "JSONL scan 0회" 하드게이트를 벤치가 실제로 검출 못함

**쉬운 말:** 설계는 "Stop allow 경로에서 새 파일 스캔·stat·hash 0회"를 P0 하드게이트로 명시합니다. 벤치는 이걸 증명하려고 `Path.stat/glob/rglob`과 해시 4개만 감시합니다. 그런데 성적표 저널(`gates.jsonl`)을 읽는 실제 방식은 `read_text()→open()`이라 감시하는 4개 중 어디에도 안 걸립니다. 즉 **누군가 Stop allow에 저널 전량 스캔을 실수로 넣어도, 카운터가 90/0/0/0 그대로라 하드게이트가 그냥 통과**합니다. receipt의 "scan 0→0"은 성적표 코드가 애초에 하지도 않는 연산을 재는 **약한(strawman) 가드**입니다.

**중요:** 현재 배포 코드는 순수 in-memory 캐시(`cached_session_summary`)만 읽어 **실제 §7.1 위반은 없습니다.** 이건 라이브 버그가 아니라 **가드·증거 품질 결함**입니다 — 그러나 §7.1이 P0 하드게이트라는 점에서 그 하드게이트가 명명된 불변식을 방어하지 못하는 구조적 맹점입니다. 오케스트레이터 재현: 격리 폴더에서 1000줄 `gates.jsonl`을 `read_text` 방식으로 읽으면 stat/glob/rglob 카운터가 실제로 0으로 남음을 확인.

**권고:** `_measure_action`에 `Path.read_text`/`Path.open`(또는 `io.open`) 카운터를 추가하고, `evaluate_scorecard_slo`가 stop_allow on-arm의 journal-read>0을 **실패로 판정**하도록 보강.

```
증거:
- eval/provenance_bench_scorecard.py:154-186  patch.object(Path,'stat'/'glob'/'rglob') + capture._digest_stream 만 설치
- core/scorecard_store.py:96  load_scorecard_journal = read_text().splitlines() (위 4개 미호출)
- eval/provenance_bench_metrics.py:184-189  stop_allow 판정은 hash/stat/full_scan만 비교, content_read_bytes·timing delta 미비교
- receipt: bench-s2-codex.json:275-304  stop_allow off/on = stat 90/90·hash 0/0·scan 0/0
- SSOT: docs/design/session-scorecard.md:112 (§7.1 "새 glob/JSONL scan/stat/hash 0회")
```

---

### PERF-2 (low) — 증분 하드게이트가 세션 첫 block만 측정 (recover/cap/2nd-block 미계측)

**쉬운 말:** 증분 성능 하드게이트(§7.3)는 세 벤치 phase 모두 매번 **새 세션의 첫 block 1건**만 잽니다. 그런데 실제로 더 무거운 경로 — 회복(recover) 턴, cap 통과, 세션의 2번째 이상 transition(이때만 `_persist_incomplete_before_append`가 ledger를 한 번 더 통째로 씀) — 은 어느 phase도 재지 않습니다. 그래서 측정된 12ms/16ms는 가장 가벼운 첫-block 경로만 반영합니다.

**하향 근거 (검증자 공통):** SSOT §7.6이 벤치 phase를 정확히 `stop_allow`/`gate_block`/`r1_block` 3종으로 **명시·한정**하고 구현이 이를 전부 충족 → 스펙 위반은 아님. §7.3 대상인 "증분 journal append"는 block/recover/cap이 바이트 동일한 동일 연산이라 append 증분은 block phase가 대표 측정. 추가 비용은 append가 아니라 ledger `save_ledger`(§7.4 W10 영역). 측정 12/16ms 대 예산 100ms로 6-8배 여유 → 실제 초과 가능성 낮음. **벤치 대표성 공백이지 코드 결함 아님** → low, 후속 하드닝.

**권고:** gate_block/r1 phase에 "기존 complete 세션 위 2번째 transition"(block→recover, block→2nd→cap_allow) A/B arm 추가로 `_persist_incomplete_before_append` 추가 save·RECOVER·CAP_ALLOW 증분을 하드게이트에 포함.

```
증거:
- eval/provenance_bench_scorecard.py:124-131  _identity가 session_id에 index → 매 iteration 신규 세션 → existing 항상 None
- core/scorecard_store.py:130-135  _persist_incomplete_before_append는 existing.complete is True일 때만(=2번째+ transition)
- eval/provenance_bench_scorecard.py:47-70  stop_allow는 회복 조건 미충족으로 RECOVER 미측정
- receipt: gate on p99 25.5ms / r1 on p99 24.7ms vs 예산 p95 100ms/p99 250ms (여유 큼)
```

---

## 4. 경쟁 가설 판정 (적대적 검증 — 조사 프로토콜)

두 개의 논쟁적 finding(1건 기각, 1건 split)을 경쟁 가설로 정면 판정한다.

### COR-3 "lost-commit 후 저널 중복 block 과대계상" → **기각**

**가설 1 (finding 원안):** `save_ledger`가 저널 append 성공 뒤 실패하면, 저널엔 event-A가 남고 ledger는 미커밋 → 다음 턴 재시도 event-B → CLI가 A·B를 둘 다 세어 `blocked_attempts=2 complete=true`로 **과대계상**한다.

**가설 2 (반증):** `save_ledger` 실패는 에이전트를 un-block하지 않는다 — block 결정은 항상 반환된다. 따라서 event-A는 실제로 반환된 진짜 차단이고, event-B도 진짜 차단이다. 저널은 실제 반환된 모든 block을 기록하는 append-only 정본이므로 카운트 2는 **정직한 값**이다.

**가설 3 (설계 의도):** 캐시(incomplete) vs CLI(2/complete)의 "갈림"은 결함이 아니라 SSOT §8이 문자 그대로 요구한 fault 동작 — 캐시는 offset 불일치를 감지해 보수적으로 줄을 생략하고, CLI는 정본 저널을 재집계해 진값을 낸다. "신뢰가능한 두 상충 숫자 동시 노출"은 발생하지 않는다.

```
증거:
- core/ledger.py:83-87  save_ledger는 atomic_write_text를 try/except OSError로 감싸 disk-full/PermissionError(OSError 하위)에서 예외 삼키고 False 반환 — raise 안 함(finding의 '예외' 분기 도달 불가)
- core/verify_state.py:151-155  save 실패해도 block decision 반환 (test_lost_cache_commit_...가 evaluate_stop==block 실증)
- BLOCK 저널 엔트리는 evaluate_stop:225가 decision=='block'일 때만 기록 → 팬텀 block 구조적 불가

기각: 저널 blocked_attempts는 실제 반환된 차단 수를 절대 초과할 수 없다. event-A를 '논리적 중복/orphan'으로 규정한 전제가 틀렸다.
     (검증자 2명 모두 REFUTED / is_real_defect=false. 부수적으로 관측된 'save 실패 시 stop_blocks cap 카운터 유실→캡 늦게 걸림'은
      차단이 느슨해지는 게 아니라 강화되는 안전측 실패이며 본 finding 청구 범위 밖.)
```

### REG-1 "cap 도달 후 매 호출 cap_allow 재기록" → **SPLIT (유효 low, 스펙 위반 아님)**

**가설 1 (confirm):** cap 도달 후 매 도구호출마다 새 cap_allow가 저널에 append되고 `cap_allows +1` → 성적표의 "cap 통과 N"이 별개 cap 사건 수가 아니라 cap 이후 통과된 도구호출 수에 비례. 저널이 세션 도구호출에 **선형 증가**(캐시는 64 bound지만 저널은 무바운드, CLI 정본은 전체 재집계). 형제 지표 `recovered_scopes`는 turn당 dedup하는데 `cap_allow`만 dedup 없음.

**가설 2 (refute):** SSOT(AC3·§3.1)가 요구하는 건 오직 "cap을 별도 표기·recover 불합산"이며 코드가 정확히 지킴. SSOT가 명시 의무화한 유일한 idempotency는 **중복 event_id** dedup뿐(§9, line 130)이고, 별개 cap 통과를 하나로 합치라는 규정은 **없다**(finding 자신도 인정). `blocked_attempts`가 시도당(per-attempt)이니 `cap_allows`가 fail-open-통과당인 것은 **대칭**이다.

**판정:** 두 가설 모두 사실은 정확하나(재현 성립), **스펙 위반은 아니다**. 게이트 판정 불변([block,block,allow,allow]), 성능 하드게이트 무위반(append O(1), Stop 줄은 O(1) 캐시만 읽음, 저널은 핫패스 미도달), 데이터 무결성 무영향. 다만 "cap 통과 40"이 한 개의 capped 게이트를 오해시키고 저널이 선형 성장하는 **유효한 low 설계 개선점**이다. 팀이 per-capped-gate 시맨틱을 선호하면 turn 단위 "이미 capped된 reason" 플래그로 해소 가능(선호 사항이지 버그 픽스 아님).

```
증거:
- core/gate_counters.py:80-91 / core/verify_state.py:138-151  cap 분기가 block 카운터·needs_goals·goals.json 미변경 → post-cap 매 호출이 cap 분기 재진입
- core/scorecard_cache.py:79-81  CAP_ALLOW는 unresolved 미소비 → resolves 계속 non-empty → 가드 미발동
- core/scorecard_store.py:61  new_transition이 매번 새 uuid4 → seen_event_ids dedup 무력
- 격리 재현: N회 호출 → cap_allows=N-2 (goals·intent·stop 3경로 동일)
- SSOT: docs/design/session-scorecard.md:13(AC3), :55(§3.1), :130(dedup은 event_id만) — cap dedup 미의무
```

---

## 5. 확정된 견고함 (P4는 결함만이 아니라 견고성도 판정한다)

5관점 리뷰어가 **실코드로 확인한 올바른 구현** — 핵심만 발췌:

**집계 정확성 (정확성 관점):**
- 3단위 분리(`blocked_attempts`/`recovered_scopes`/`resolved_attempts`)가 순수집계·증분캐시 **양 경로 동일** 계산. SSOT 예시 "2 block 1 recovery = attempts 2·scopes 1·resolved 2" 성립.
- `recovery scope` = (host,session_id,agent,turn_id,reason_code) 5-튜플 정확. cap_allow가 recover로 새지 않음(양 경로 보장). routine allow 미기록. resolves가 직전 미해결·동일 reason만 닫음(이중닫힘·엉뚱닫힘 없음). 중복 event_id idempotency. pre-activation N/A vs post-activation 0 구분.

**동시성 (동시성 관점):**
- **락 규율 §4 준수:** `record_gate_transition_locked`/`_append_transition`의 **모든 프로덕션 call site가 `ledger_transaction` 임계구역 안**(verify_state:217, gate_counters:40/67/114, contract:284) — 락 밖 저널 append 경로 0. `record_event` 재사용·락 재진입 없음(15s timeout 경로 없음). R1 owning wrapper가 락 정확히 1회.
- offset watermark + precommit의 lost-update 방지 정확. fault 격리(append OSError→캐시만 incomplete, gate decision byte-equal). 8/32 subprocess 교차프로세스 writer 회귀 유지(event_id 유일·찢김/중복/유실 0). malformed tail 견고. bounded 64. **기존 F6 원자 카운터·owner-lock 임계구역 미확장·미파손.**

**계약 (계약 관점 — 결함 0):**
- v2 ledger 하위호환 **무마이그레이션 준수**(scorecard_cache/journal_offset를 존재 가드 optional로만 추가, 구 v2 ledger 무변경 통과). `scorecard_schema_version=1` **독립 진화**(ledger schema_version=2와 교차참조 0, 물리 분리 파일). unknown scorecard version 거부. load 시 파생 캐시 fail-open(권위 판정 보존, 비-scorecard 오류는 re-raise로 마스킹 불가).
- **3어댑터 canonical identity 상시 전달(§5) 확정**(agent-less raw payload에서도). 3어댑터 "매핑만"(집계/판정 분기 부재, systemMessage 문자열 동일성 실증). Claude allow는 systemMessage만(additionalContext 금지). PreTool 3어댑터도 full identity 전달.

**회귀 (회귀 관점):**
- **게이트 판정 불변:** evaluate_without_io는 additive `reason_code`만 추가, allow/block 임계·조건 불변. 2회 Stop cap 보존. R1(rm-rf) 판정 위임 불변(root/home/wildcard/absolute/traversal 차단·상대 단일파일 통과 미변경). `restart_blocked_turn`이 stop cap 카운터 미리셋. **기존 회귀 테스트 약화 없음**(test_core_contracts 변경=mkdir exist_ok 1줄, e2e_smoke는 PreToolUse 추가만·준수-Stop 단언 유지).

**성능 (성능 관점):**
- §7.1/§7.2 O(1) 렌더 확인(cached_session_summary는 in-memory dict만, 파일 I/O 0). stop_allow A/B strawman 아님(실제 block 기록 후 렌더 비용 격리). gate/r1 on-arm이 **실제 production entry** 밟음. W10 1k/10k 회귀 없음(s2 1k stop 331ms<baseline 428ms, 10k 3812ms<4462ms). **보고서 성능 수치가 receipt와 정확히 일치(과장 없음).**

---

## 6. 검증 방법론 · 한계

**방법:** 5관점 병렬 정적 리뷰(파일 Read + git diff + grep, 전체 pytest/eval 벤치 미실행 — 공유 상태 간섭 회피) → 각 결함 refute/confirm 2관점 독립 적대 검증 → 오케스트레이터 직접 재확인.

**오케스트레이터 독립 검증 (first-hand):**
- `pytest tests/test_scorecard_core.py tests/test_scorecard_store.py` → **25 passed** (codex 보고서 "pytest 307" 핵심부 corroboration).
- **CON-2 격리 재현** → blocked=1/complete=True vs 진실 3 확정 (검증자 전원 사망 finding을 오케스트레이터가 실증).
- **COR-1/CON-1** scorecard_cache.py 메커니즘 코드 트레이스로 직접 확인.

**검증 인프라 한계 (정직 기록):**
- **적대 검증 에이전트 2명이 StructuredOutput 재시도 상한(5) 초과로 사망** — 이들이 CON-2의 refute/confirm 담당이었음. CON-2는 병렬 검증 미완 → 오케스트레이터 격리 재현으로 대체 확정.
- 총 21에이전트 중 19 완료·2 사망. 리뷰어 방법론상 전체 스위트·벤치 미재실행 → **receipt 수치가 현재 체크인 코드로 재생성됐는지는 재실행 없이 확증 불가**(값-로직 정합성만 확인).

**남은 사각 (후속 검토 권장):**
- `restart_blocked_turn`을 참조하는 테스트 0건(grep) — 어댑터 Stop 다회 호출 루프의 cap 동작은 정적분석으로만 확인(정확성 관점 권고).
- Stop의 `provenance_incomplete`/`investigation_markers` reason_code가 실제 저널에 그 reason으로 기록되는지 dedicated 통합테스트 없음(현 통합테스트는 verification_missing만 seed).
- `_record_scorecard` 예외 핸들러가 (OSError, ScorecardSchemaError)만 포착 — 캐시 계층 비-OSError(ValueError/TypeError 등)가 나오면 evaluate_stop 밖 전파로 fail-open(block→allow) 가능성. 스키마유효 ledger 하 구체 트리거 미발견으로 finding 미제출(추정 금지 준수)이나 방어적 except 광역화 검토 권장.
- 성적표 렌더의 민감정보 금지(§9, `fable_lite/scorecard.py`) 정독은 이번 5관점 분할에 미포함 — 별도 보안 관점 권고.

---

## 7. 권고 (우선순위)

| 우선 | 항목 | 근거 |
|------|------|------|
| **P1 (넓은 배포 전 수정 권장)** | COR-1/CON-1 cap 유실 | 명시 수용기준 GWT#3를 도달 가능 경로에서 위반·무테스트. 도구 핵심 가치("무엇을 포기했는지") 훼손 |
| **P2 (곧 수정)** | PERF-1 벤치 맹점 | P0 하드게이트(§7.1)가 명명된 불변식을 실제로 방어 못함 — 미래 회귀 무방비 |
| **P2** | CON-2 축출 과소집계 | accurate-or-incomplete 계약 위반(실증). 트리거 좁으나 확정 결함 |
| **P3 (설계 결정)** | COR-2 죽은 계약 | legacy_default 배선 or enum/CLI/테스트 제거 중 택1(설계-구현 불일치 봉인) |
| **P3 (백로그)** | PERF-2 벤치 대표성 | 회복/cap/2nd-block A/B arm 추가 |
| **P3 (선호 결정)** | REG-1 cap dedup | per-capped-gate 시맨틱 원하면 turn 플래그 도입(스펙 의무 아님) |
| **기각** | COR-3 | 저널 카운트 정직 — 수정 불요 |

---

## 8. 결론

**하드게이트(치명결함 0) 통과.** 게이트 안전성(판정 불변·fault 격리·락 규율·하위호환)은 5관점 전반에서 견고하게 확인됐고, 계약 관점은 결함 0이다. **확정 결함 6건은 전부 성적표(관측) 정확성 영역이며 게이트 판정에 영향이 없다.**

단 codex 보고서의 **"UNCONDITIONAL APPROVE / 모든 수용기준 충족"은 정정이 필요**하다 — 수용기준 GWT#3(cap 통과 별도 표기)가 도달 가능한 reason-shift 경로에서 미충족이며 무테스트다(COR-1/CON-1). 이는 치명 결함은 아니나, "실측 근거"를 표방하는 도구가 fail-open을 성적표에서 숨기는 것은 제품 신뢰성의 핵심을 건드린다. **P1 1건(cap 유실) 수정 후 넓은 배포**를 권한다.

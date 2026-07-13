# Session Quality Scorecard S2 — R3 재수리(repair2) 표적 확인 (ultracode)

> 확인일: 2026-07-14 · 검증자: 우하 pane (implementation-ultracode, Opus 4.8) 오케스트레이션 + 2표적 병렬 검증 + 적대적 신규결함 헌팅
> 대상: R2 재검(docs/reviews/session-scorecard-ultracode-r2.md)이 확정한 신규 결함 2건을 codex가 repair2로 재수리한 것 — COR-2(반전), CON-2(포화 신규세션 회귀)
> 수리 보고: tmp/scorecard/repair2-report-codex.md · 방법: 코드 트레이스 + 표적 회귀 테스트 + 격리 재현(오케스트레이터 3종 + 워크플로우) + eviction-history 신규결함 헌팅

---

## 1. 한 줄 판정 (쉬운 말)

R2에서 지적한 "새로 생긴 문제 2건"을 codex가 다시 고쳤고, **둘 다 목표대로 해소됐습니다(STILL-BROKEN 0).**
- **COR-2:** "모든 세션이 미귀속으로 잘못 표시되던" 반전 결함이 **깨끗하게 고쳐졌습니다** — 이제 진짜 세션은 "정확(exact)"으로, 세션ID가 없을 때만 "미귀속"으로 표시됩니다.
- **CON-2:** "포화 후 신규 세션 줄이 사라지던" 회귀가 **고쳐졌습니다**(축출된 세션과 진짜 신규 세션을 이제 구분). **단** 그 구분에 쓰는 "축출 기록"이 64개까지만 저장되는 한계 때문에, **아주 오래 잠들었던(총 128개+ 세션이 지나간) 세션이 다시 나타나면** 원래의 과소집계 문제가 그 좁은 구석에서 되살아납니다 — 두 개의 독립 재현으로 실측 확인했습니다. 표시 전용·게이트 무관이라 심각도는 낮고, 이 방식(제한된 기록)의 본질적 한계입니다.

**하드게이트: 치명 결함 0.** R2 신규결함 2/2 해소. CON-2에 confirmed low 잔여 1건(aging tail).

---

## 2. 결과 요약

| 결함 | R2 신규결함 해소 | 잔여/신규 | 판정 | 심각도 |
|------|:---:|:---:|------|------|
| **COR-2** (반전 → session_id 단독 synthetic) | ✅ 해소 | 없음(잔여는 전부 pre-existing) | **CONFIRMED-FIXED** | — |
| **CON-2** (evicted_keys FIFO로 축출/신규 구분) | ✅ 해소 | ⚠️ aging deep-tail | **CONFIRMED-FIXED + 잔여(low)** | low |

워크플로우 판정: FIXED 2/2 · 신규결함 후보 1(CON-2 aging). COR-2 verify·adversarial 모두 **VERDICT-UPHELD**. CON-2 verify=CONFIRMED-FIXED, adversarial=VERDICT-UPHELD(단 aging을 "문서화된 잔여"→"confirmed low 결함"으로 교정). **오케스트레이터 독립 재현이 두 판정의 사실관계를 모두 지지.**

---

## 3. COR-2 → CONFIRMED-FIXED (신규 결함 없음)

**수리:** `identity_synthetic` 판정을 raw payload의 **session_id 부재 단독** 기준으로 변경(host=어댑터 상수·agent=기본값 합성이라 판정에서 제외). R2가 지적한 근본원인(항상 없는 `host` 필드 검사 → 100% synthetic)을 제거.

**검증 (오케스트레이터 직접 + 워크플로우):**
- 3어댑터 실코드 확인:
  - `adapters/claude_code/common.py:137` · `codex_cli/common.py:156`: `identity_synthetic = not _string(payload.get("session_id"))`
  - `antigravity/hook_common.py:46`: `not isinstance(session, str) or not session` (동등)
  - grep: 구 `any(... for field in ("host","agent","session_id"))` 패턴 **0 매치**(잔재 없음).
- 격리 재현(3어댑터 직접 호출): 실 UUID session_id payload(host/agent 없음) → 3어댑터 전부 **synthetic=False, attribution=exact**. session_id 없는/빈 payload → legacy_default. **R2의 "실 3호스트 payload 100% legacy_default 오라벨" 반전 결함 소멸.**
- 회귀 테스트 교정: R2가 "과대판정 화석화"로 지목한 `test_stop_when_raw_payload_has_no_agent_passes_canonical_identity`가 이제 "session_id 보유 → exact"를 3어댑터 강한 등가단언으로 방어. `pytest tests/test_scorecard_adapters.py` **5 passed**.

**계약 무파손:** attribution은 표시 전용(집계·게이트·버킷 무참여), new_transition이 attribution 부재 시 EXACT 폴백(무마이그레이션), session_id 부재는 라벨만 바꿀 뿐 allow/차단 안 함(fail-open). SSOT 쟁점7(정상 세션=exact)과 정합 복원.

**잔여 구멍(전부 pre-existing, repair2 무관·비차단):** ① 공백/제어문자 session_id는 truthy라 exact(실 호스트는 UUID만 전송, 도달 불가) ② `resolve_active_invocation`가 payload 기반 identity_synthetic 보존(보수적 방향, 기존 구조) ③ 2차 병합문제(session_id-less→host:default:agent)는 attribution과 직교하게 잔존(R2 기확인, agent_key 소관). 셋 다 표시 전용·CLI 정확.

```
증거:
- adapters/{claude_code,codex_cli}/common.py:137/156 (session_id-only), antigravity/hook_common.py:46
- 격리 재현: 실 UUID 3호스트 → exact, sessionless → legacy_default
- test_scorecard_adapters.py 5 passed (회귀테스트 exact로 교정)
- 기각: 'exact가 여전히 미발원' → 기각(host 항 제거로 정상 발원, 재현 실측)
```

---

## 4. CON-2 → CONFIRMED-FIXED (실용 케이스) + 확정 잔여 aging deep-tail (low)

**수리:** 캐시 bounding에서 **실제 제거된 canonical key만** `scorecard_evicted_keys`에 기록(FIFO 64). missing key completeness를 "포화 여부"가 아니라 **이 eviction 이력 membership**으로 판정. (`scorecard_store.py:135,159,165`; `scorecard_cache.py:154-163 bounded_cache_with_evictions`)

**실용 케이스 해소 확인 (오케스트레이터 재현 2종, repair2 코드):**
- `con2_repro.py`(축출 재등장): `complete=False`·줄 생략 유지 → 원 CON-2 소멸.
- `con2_adv_repro.py`(포화+**순수 신규**): **`complete=True`·줄 표시** → **R2가 확정한 신규결함(포화 시 신규세션 줄 생략)이 해소됨.**

**계약 무파손:** ① `scorecard_evicted_keys`는 `ledger_schema.py:181` 조건부 검증 = **optional·무마이그레이션** ② `ledger.py:64` 오류 시 pop = **fail-open**(파생상태 폐기·권위 판정 보존) ③ 스키마가 >64·중복 거부(`ledger_schema.py:251-254`), `bounded_cache_with_evictions`는 `dict.fromkeys` dedup으로 중복 미생성 ④ evicted_keys는 complete 플래그에만 관여, 카운트·저널(진실원천) 불침습.

**그러나 — 확정 잔여(경쟁 가설 판정):**

**가설 1 (verdict — 문서화된 잔여):** evicted_keys FIFO 64는 R2가 직접 권고한 링버퍼 방식의 본질적 memory bound다. baseline(수리 전 재등장 전량 오라벨)보다 엄격히 개선이고 트리거 극협·표시 전용이라 신규 결함으로 승격하지 않고 "문서화된 잔여".

**가설 2 (adversarial — confirmed low §6.1 위반):** FIFO 64 상한이 aged-out 세션에 §6.1 accurate-or-incomplete를 위반한다. 손실 없는 완전 데이터에 거짓 complete를 부여하므로 "잔여 한계"가 아니라 실재하는 low 결함이다.

**증거 (두 독립 재현 일치):**
```
오케스트레이터 con2_aging_repro.py:
  [1차축출] K∈evicted_keys (수=1)
  [aging]   K aged out=True (다른 64개 축출로 이력에서 밀려남, 수=64)
  [T2 재등장] cache[K]: blocked_attempts=1 complete=True   ← 원 CON-2 부활
  [journal 진실] K blocked_attempts=3
워크플로우 aged_out_repro.py: 동일 (blocked=1 complete=True vs 진실 4)
```
`scorecard_store.py:159` `identity_known_new = eviction_history_present or ...` (True) + `:165` `key not in evicted_keys` (aged out → True) → `complete=True`. **축출됐으나 이력에서 밀려난 key가 "순수 신규"로 오판돼 과소집계를 권위 노출** = 원 CON-2가 이 좁은 부분집합에서 재출현. 신규 회귀 테스트(`test_reactivated_evicted_session...`)는 filler 64개만 써서 **재등장 시점에 K가 아직 이력에 있는 in-window 케이스만** 커버 → aging 무테스트.

**판정: 가설 2 우세(그러나 net 개선).** 두 독립 재현이 §6.1 위반을 실측했으므로 "문제 없음"이 아니라 **confirmed low 잔여**다. 단 (a) 트리거 극협 — 단일 session_id가 **128+ distinct 게이트-활성 세션에 걸쳐 휴면 후 재등장**해야 성립(실사용 미발생 수준) (b) 표시 전용·게이트/저널 데이터 무손상 (c) CLI `scorecard --all` 저널 재집계로 항상 정확값 조회 (d) **원 CON-2(재등장 전량 오라벨)보다 엄격히 개선** → **severity=low**. 프레이밍 논쟁("repair2가 도입" vs "원 CON-2의 deep-tail 잔여")은 라벨 문제일 뿐 — 실체는 **bounded-history 접근의 본질적 tail**이며, repair2는 실용 범위를 확실히 개선했다.

**완전 봉합 옵션(원하면):** evicted-key를 bounded FIFO 대신 **unbounded seen-set 또는 bloom filter**로 추적(ledger 성장 vs 완전성 트레이드오프) — 또는 현행을 문서화된 known-limitation으로 수용.

```
증거:
- core/scorecard_cache.py:154-163 (bounded_cache_with_evictions, history[-64:] FIFO)
- core/scorecard_store.py:159,165 (identity_known_new + key not in evicted_keys → complete)
- 오케스트레이터 con2_aging_repro + 워크플로우 aged_out_repro (두 독립 재현 일치)
- 기각: 'aged-out도 incomplete로 보수처리됨' → 기각(재현 2종 모두 complete=True 실측)
```

---

## 5. 오케스트레이터 독립 검증 (first-hand)

- **COR-2:** `adapters/{claude_code,codex_cli}/common.py:137/156` + `antigravity/hook_common.py:46` 직접 리딩 → session_id-only 확인. 재현 2종(con2_repro/con2_adv_repro)은 CON-2용이나 COR-2 코드 경로도 정합.
- **CON-2 실용 케이스:** `con2_repro`(축출→incomplete) + `con2_adv_repro`(신규→complete) 재실행, 둘 다 기대 일치 (RUN→OBSERVE).
- **CON-2 aging 잔여:** `con2_aging_repro.py` 격리 재현 — aged-out 재등장 `blocked=1/complete=True` vs 진실 3 실측(RUN→OBSERVE). 워크플로우 `aged_out_repro.py`가 독립 재현으로 동일 확인.
- **계약:** `ledger_schema.py:248-254`(>64·중복 거부), `ledger_schema.py:181`(optional), `ledger.py:64`(fail-open pop) 직접 확인.

**검증 인프라:** 워크플로우 4에이전트 전원 성공(무오류). 전체 pytest·벤치 미재실행(표적 테스트·격리 재현·코드 트레이스로 갈음).

---

## 6. 권고

| 우선 | 항목 | 근거 |
|------|------|------|
| **P3 (선택)** | CON-2 aging tail: 문서화된 known-limitation으로 수용 or unbounded seen-set으로 완전 봉합 | 트리거 극협(128+ 세션)·표시 전용·CLI 정확 → 수용 가능. 완전 봉합은 ledger 성장 트레이드오프 |
| **P3 (백로그)** | CON-2 aging 케이스 회귀 테스트 추가 | 현행 테스트는 in-window만 커버 |
| **참고** | COR-2 pre-existing 잔여(공백 session_id·2차 병합)는 별개·비차단 | repair2 무관 |
| **참고** | COR-1 자매 케이스·PERF-1 builtins.open은 R2 P3 백로그 유지 | 미변경 |

---

## 7. 결론

**STILL-BROKEN 0 · R2 신규결함 2/2 해소 · 치명 결함 0.** **COR-2는 깨끗한 재수리**(반전 결함 완전 소멸, 정상 세션 exact 복원, 회귀 테스트 교정, verify·adversarial UPHELD). **CON-2는 실용 범위에서 재수리 성공**(R2의 포화-신규세션 회귀 해소, 계약 견고) — 다만 evicted_keys의 bounded-64 한계로 **aged-out 재등장(128+ 세션)에서 원 CON-2 과소집계가 되살아나는 confirmed low 잔여**가 두 독립 재현으로 확인됐다.

이 잔여는 **bounded-history 접근의 본질적 tail**이며 repair2가 실용 범위를 확실히 개선했으므로, **known-limitation 문서화로 수용하거나 unbounded seen-set으로 완전 봉합**하는 선택을 권한다(둘 다 low 우선). 3라운드(P4→R2→R3)에 걸친 수렴: 헤드라인 결함들(COR-1 cap 유실·PERF-1 벤치 맹점·COR-2 오라벨·CON-2 과소집계)은 모두 봉합됐고, 남은 것은 극협 트리거의 low 잔여뿐이다.

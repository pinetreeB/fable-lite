# 사건 기록: 진짜 프로젝트 루트에서 빌드 산출물이 provenance 예산을 소진 (2026-07-16)

> 기록 목적: **홈 루트가 아닌 정상 프로젝트 루트**(`material-erp`)에서 Stop 게이트가 `scope_too_large`/`byte_limit`으로 차단된 실측 사례.
> 07-15 사건([[2026-07-15-home-root-provenance-scope-incident]])의 권고 A(홈 루트 → `unsupported` skip)는 **이미 구현·작동 중**이며, 본 건은 그 뒤에서 드러난 **별개 경로**다.
> 발생 세션: wmux 좌상 Claude(Opus 4.8 1M), `/wmux-project-start`로 ERP 4-pane 셋업 중. **파일 변경 0건인 턴에서 차단**.
> 성격: 코드 결함 아님. **스캔 예산과 빌드 산출물의 불일치** — 설정(`provenance-config.json`)으로 해소 가능.

## 07-15 사건과의 관계 (핵심 차이)

| | 07-15 (홈 루트) | **07-16 (본 건, 프로젝트 루트)** |
|---|---|---|
| 루트 | `C:\Users\rotat` (홈) | `C:\Users\rotat\material-erp` (**정상 프로젝트**) |
| 게이트 판정 | `scope_too_large` | `scope_too_large` (동일 증상) |
| 현재 상태 | **해소됨** — A안 구현으로 `unsupported`/`home_root` 판정 → 게이트 통과 | **차단 지속** |
| 예산 초과 주체 | `.claude/projects` 세션 로그 (61%) | **`.next` 빌드 산출물 (73%)** |
| 07-15 문서의 처방 | A안 = 홈이면 skip | A안 무효 (홈 아님). **B안(제외 목록)이 정답** |
| 해결 가능성 | 어떤 예산 상향으로도 불가 | **설정 1개로 완전 해소** (1,112MB → 92.5MB) |

**07-15 문서 함의 5의 정정**: 당시 "에이전트 로그 제외해도 4.3배 잔존 → 제외 목록은 단독 해결책이 **아님**(부분 완화)"으로 격하했다. 이는 **홈 루트 한정 판단**이었다. 프로젝트 루트에서는 제외 목록이 **완전한 해결책**이다. 격하 표현이 B안을 과소평가하게 만들었고, 본 건이 그 반례다.

## 증상 (실측)

1. `/wmux-project-start` 셋업 턴 종료 시 Stop 훅 차단:
   `[smtw] Stop gate: provenance 범위가 너무 커서 local-or-unknown 변경 가능성을 안전하게 관측할 수 없습니다. 프로젝트 루트를 좁히고 다시 관측하세요.`
2. **해당 턴의 파일 변경은 0건**이었다 (셋업 스킬 = pane 기동·메타데이터·읽기 전용 조회뿐, Write/Edit 호출 0회).
3. 증거를 제시하며 재응답해도 동일 차단 반복 — 게이트는 어시스턴트 텍스트가 아니라 ledger 상태만 판정하므로 예상된 동작(07-15 문서 §증상 3과 동일).
4. 게이트 메시지의 지시("루트를 좁히세요")를 **문자 그대로 따를 수 없다** — 루트는 이미 정상 프로젝트 폴더이며, 더 좁힐 대상이 아니다. **본 건에서 이 메시지는 오진단을 유도한다**(§오진단 유발 참조).

## 원인 (ledger 실측)

같은 세션 ID가 **두 루트의 ledger에 동시 등재**된다.

```
# 홈 ledger: C:\Users\rotat\.fable-lite\ledger.json
active_turns["claude_code:e9f5b954-…:claude"]
  provenance_status        = 'unsupported'
  provenance_status_reason = 'home_root'        ← A안 작동 (차단 안 함)

# 프로젝트 ledger: C:\Users\rotat\material-erp\.fable-lite\ledger.json
active_turns["claude_code:e9f5b954-…:claude"]
  provenance_status        = 'scope_too_large'
  provenance_status_reason = 'byte_limit'       ← 실제 차단 주체
  provenance_mutation_capable = True
  provenance_incomplete    = False
```

세션 cwd는 홈이지만 **Bash 도구의 작업 디렉토리가 `material-erp`**였다. 어댑터가 그 경로를 루트로 관측하여 프로젝트 ledger를 갱신하고, 거기서 `byte_limit`이 발생해 차단된다. 홈 쪽은 A안 덕에 조용히 통과하므로, **차단 원인이 홈이라고 오인하기 쉽다**(실제로 1차 조사에서 그렇게 오판했다 — §오진단 유발).

게이트(`core/verify_state.py:236`)는 `status == SCOPE_TOO_LARGE AND mutation_capable is True`이면 **변경 0건이어도 무조건 block**. clean을 증명할 수 없으니 clean을 주장하지 않는다는 설계로, 동작 자체는 정직하다.

### 예산 대비 실측 (`material-erp`, PowerShell 실측)

예산: `DEFAULT_MAX_SCAN_BYTES` = **256MB** (`core/provenance_types.py:11`)

| 항목 | 용량(MB) | 파일수 | 스캔 대상? | 비고 |
|---|---:|---:|---|---|
| `.next` | **812.5** | 212 | ✅ 대상 | **단독으로 예산 3.2배**. 빌드 산출물 |
| `dist` | 121.3 | 1 | ✅ 대상 | 릴리스 번들 ZIP (온프렘 배포용) |
| `tmp` | 84.9 | 2,151 | ✅ 대상 | 작업 임시물 |
| `customer-data` | 75.1 | 39 | ✅ 대상 | 고객 실자료 (감독 가치 있음 → 유지) |
| `.codegraph` | 44.0 | 7 | ❌ 제외됨 | HARD_EXCLUDES |
| `docs` | 4.0 | 128 | ✅ 대상 | |
| `research` | 3.2 | 26 | ✅ 대상 | |
| `public`/`lib`/`app`/`tests`/`output` | ~7.3 | 760 | ✅ 대상 | **실제 감독해야 할 소스** |
| **스캔 총계** | **≈1,112** | | | **예산 4.3배** |

`node_modules`·`.git`은 SOFT/HARD excludes로 이미 제외되어 위 수치에 없다. 즉 **기존 제외 목록이 커버하지 못하는 산출물**(`.next`, `dist`, `tmp`)이 예산의 91%를 차지한다.

정작 감독 가치가 있는 소스(`app`·`lib`·`tests`·`docs`·`research`)는 **합쳐서 약 11MB**로 예산의 4%다.

## ★ 오진단 유발 — 게이트 메시지가 조사를 잘못된 방향으로 밀었다

본 건에서 실제로 발생한 조사 궤적:

1. 메시지("루트를 좁히세요")를 그대로 수용 → **"홈 루트가 원인"이라고 판단** → 사용자에게 "세션을 `material-erp`에서 재시작하자"고 제안.
2. 그 제안은 **틀렸다**. 루트는 이미 `material-erp`로 관측되고 있었고, 재시작해도 `.next` 812MB는 그대로라 차단이 계속됐을 것이다.
3. ledger를 직접 읽고 나서야 `byte_limit`·프로젝트 루트가 드러남.

**교훈**: 게이트 메시지는 `scope_too_large`의 **가장 흔한 원인(홈 루트)을 단정**해 안내한다. 07-15 사건 이후 홈 루트는 `unsupported`로 분기됐으므로, **남은 `scope_too_large`는 정의상 홈 루트가 아니다**. 그런데 메시지는 여전히 홈 루트 시절의 처방("루트를 좁히세요")을 준다. 이는 실행 불가능한 지시일 뿐 아니라 **적극적으로 오답을 가리킨다**.

메시지는 실제 `status_reason`을 반영해야 한다:
- `byte_limit`/`entry_limit` → "다음 경로가 예산의 N%를 차지합니다: … . `.fable-lite/provenance-config.json`의 `exclude`에 추가하세요."
- `deadline` → 스캔 시간 초과 안내.

07-15 문서 함의 2("게이트 메시지가 실행 불가능한 지시를 준다")는 홈 루트에서 제기됐고 A안으로 우회됐으나, **메시지 자체는 고쳐지지 않아** 본 건에서 더 나쁜 형태(오답 유도)로 재발했다.

## 경쟁 가설과 증거

- **가설 1**: 해당 턴의 변경량이 커서 예산 초과 → **기각**. 변경 0건. `git status --porcelain` = `?? docs/answers/` 한 줄(세션 시작 전부터 존재), `HEAD`=`f2a0f17` 불변, `find docs/answers -newermt "06:05"` = 0건.
- **가설 2**: 홈 루트가 원인(07-15와 동일 사건) → **기각**. 홈 ledger는 `unsupported`/`home_root`로 **차단하지 않는다**. 차단은 프로젝트 ledger의 `byte_limit`에서 발생. A안은 정상 작동 중이며 본 건과 무관하다.
- **가설 3**(채택): 프로젝트 루트의 빌드 산출물이 스캔 예산을 초과 → **지지**. 실측 1,112MB / 256MB = 4.3배, `.next` 단독 812.5MB(73%). 기존 제외 목록(`node_modules` 등)이 `.next`를 커버하지 않음을 소스에서 확인(`core/provenance_policy.py:24-32`).
- **가설 4**: provenance 구현 결함 → **기각**. 예산 초과 시 clean을 주장하지 않고 `SCOPE_TOO_LARGE`를 선언하는 것은 설계된 안전 동작(`core/provenance.py:302-304`). 결함은 **기본 제외 목록의 커버리지**와 **메시지 문구**에 있다.
- **기각**: 예산(256MB) 상향 → 증상만 미룬다. `.next`는 빌드마다 재생성되고 커질 수 있으며, 애초에 **감독 가치가 0인 산출물**을 스캔할 이유가 없다. 예산은 감독 대상 소스(11MB)에 비해 이미 23배 여유롭다.
- **기각**: 훅/게이트 비활성화 → 경보를 끄는 조치. ERP 실구현이 시작되면 감독이 실제로 필요하다.

## 해결 (본 건)

`material-erp/.fable-lite/provenance-config.json` 신설:

```json
{
  "version": 1,
  "exclude": [".next/**", "dist/**", "tmp/**", "output/**"]
}
```

스키마: `core/provenance_policy.py:57-78` (`version`은 반드시 `1`, 패턴은 root-relative).
효과: 1,112MB → **약 92.5MB** (예산 256MB의 36%). `customer-data`(75.1MB)는 **의도적으로 유지** — 고객 실자료는 AI가 변조하면 안 되는 감독 대상이다.

제외 대상의 정당성: `.next`(빌드 산출물)·`dist`(릴리스 번들)·`tmp`(작업 임시물)·`output`은 모두 **생성물**이며 provenance 감독 가치가 없다. 소스 감독은 100% 유지된다.

### 해결 검증 (실 스캐너 구동 — 정적 검사 아님)

```python
from core.provenance_lifecycle_scope import scan_snapshot
snap = scan_snapshot(Path(r'C:\Users\rotat\material-erp'), None, frozenset(), True)
```

| 항목 | 적용 전 | **적용 후 (실측)** |
|---|---|---|
| `status` | `scope_too_large` | **`complete`** |
| `status_reason` | `byte_limit` | *(없음)* |
| `incomplete` | False | False |
| entries | — (예산 초과로 중단) | **1,205** (상한 10,000의 12%) |
| 소요 | — | **0.52초** (예산 8.0초의 6.5%) |

예산 3축(bytes·entries·seconds) 모두 여유롭게 통과. 감독 대상 파일 1,205개가 정상 관측된다.

재현 가능한 검증 스크립트: `tmp/verify-2026-07-16-scope-incident.py` (exit 0 / ALL PASS 4-4).
`is_path_in_scope`로 **감독 유지도 함께 단언**한다 — `app/`·`lib/`·`docs/answers/`·`customer-data/`는 스코프 안, `.next/`·`dist/`·`tmp/`·`output/`은 스코프 밖.

### ⚠️ config 적용 후에도 ledger의 `active_turn`은 stale하게 남는다

설정을 넣고 스캐너가 `complete`을 반환해도, `material-erp/.fable-lite/ledger.json`의 해당 턴은 **`scope_too_large`/`byte_limit` 그대로 유지**된다(실측: `blocks.stop=2`로 cap에 도달해 멈춘 턴).

- ledger 항목은 **그 루트에서 어댑터 훅이 다시 돌 때만** 갱신된다. 라이브러리를 직접 호출한 스캔(`scan_snapshot`)은 관측 경로가 아니므로 기록을 갱신하지 않는다.
- 세션 cwd가 다른 곳(예: 홈)이면 **Bash의 `cd`만으로는 그 루트에서 훅이 돌지 않는다** — 루트는 훅 payload의 `project_root`/cwd에서 결정된다(`adapters/claude_code/common.py:42-52`).
- 따라서 **"ledger에 아직 scope_too_large가 보인다"는 수정 실패의 증거가 아니다.** 수정 여부는 스캐너 실구동으로 판정할 것. 조사 시 이 둘을 혼동하면 멀쩡한 수정을 되돌리게 된다.

## fable-lite 관점 함의 (v-next 백로그)

1. **기본 SOFT_EXCLUDES에 프레임워크 빌드 산출물 추가 검토** (`core/provenance_policy.py:24-32`). 현재 목록은 Python/Node 의존성 위주(`node_modules`, `.venv`, `__pycache__`)이고 **빌드 출력이 없다**. 후보: `.next/**`, `dist/**`, `build/**`, `out/**`, `target/**`, `.turbo/**`, `.svelte-kit/**`, `coverage/**`. Next.js 프로젝트에서 `.next`는 사실상 100% 존재하므로, 기본 제외가 없으면 **모든 Next 프로젝트가 본 건을 겪는다**. ★ 우선순위 높음 — 재현성이 높고 사용자가 원인을 찾기 어렵다.
2. **`scope_too_large` 메시지를 `status_reason`별로 분기** (§오진단 유발). 최소한 홈 루트 시절 문구("루트를 좁히세요")를 `byte_limit`에 그대로 쓰지 말 것. 예산을 소진한 상위 경로를 함께 보고하면 사용자가 즉시 `exclude`에 넣을 수 있다.
3. **`provenance-config.json`의 발견 가능성**. 해결책이 설정 파일 하나인데, 게이트 메시지·문서 어디에도 그 존재가 안내되지 않는다. 본 건에서도 소스를 읽고서야 찾았다. 차단 메시지에 경로와 예시를 넣을 것.
4. **동일 세션의 다중 루트 등재**(홈 `unsupported` + 프로젝트 `scope_too_large`)는 조사를 어렵게 한다. 세션 cwd와 도구 cwd가 다를 때 어느 루트가 게이트를 지배하는지 관측 가능해야 한다. 07-15 함의 3(상태 디렉토리 세션 격리)과 같은 뿌리.
5. **변경 0건 턴의 차단 비용**. 본 건은 셋업(파일 미변경) 턴이었는데도 2회 차단 + 턴 재생성(Opus 1M 컨텍스트)이 부과됐다. `mutation_capable=True`만으로 차단하는 현 규칙은 정직하지만, **관측 실패의 비용이 무변경 턴에도 동일**하다는 점은 Scorecard 무력화 신호(07-15 함의 4)와 함께 볼 것.

## 재현 절차

```bash
# 1. Next.js 프로젝트에서 pnpm build 실행 (.next 생성, 수백 MB)
# 2. 해당 프로젝트를 루트로 하는 세션에서 아무 턴이나 종료
# 3. Stop 게이트가 scope_too_large/byte_limit으로 차단
python -c "
import json; d=json.load(open('.fable-lite/ledger.json',encoding='utf-8'))
for k,v in d['active_turns'].items():
    print(k, v.get('provenance_status'), v.get('provenance_status_reason'))
"

# 스캔 대상 용량 실측 (PowerShell, node_modules/.git 제외)
# → .next 단독으로 256MB 예산 초과 확인
```

## 관련

- [[2026-07-15-home-root-provenance-scope-incident]] — 선행 사건(홈 루트). 본 건은 그 A안 구현 **이후** 남은 경로이며, 당시 격하된 B안(제외 목록)이 본 건의 정답이다.
- `core/provenance_policy.py:13-34` (제외 목록), `:57-78` (config 스키마)
- `core/provenance_types.py:10-12` (예산 기본값), `core/provenance.py:297-308` (예산 판정)
- `core/verify_state.py:236` (게이트 차단 조건), `core/adapter_observation.py:290` (홈 루트 unsupported 분기)

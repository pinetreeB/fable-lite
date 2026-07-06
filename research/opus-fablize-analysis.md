# fablize 2.1.0 심층 역공학 분석

> **분석자**: Claude Opus 4.6 (Thinking) — fable-lite 우하 구현보조 pane  
> **분석 대상**: `C:\Users\rotat\.claude\plugins\cache\fablize\fablize\2.1.0\`  
> **일시**: 2026-07-06  
> **목적**: fablize가 Fable 5의 어떤 행동 특성을 어떤 메커니즘으로 재현하는지 역공학하고, fable-lite의 개선 기회를 도출한다.

---

## 1. 구성요소 전수 목록

### 1-1. 핵심 정보

| 항목 | 값 |
|---|---|
| 버전 | 2.1.0 (2026-06-18) |
| 저자 | fivetaku |
| 라이선스 | MIT |
| 타겟 플랫폼 | Claude Code 플러그인 시스템 |
| 의존성 | Python 3 (stdlib only), bash |

### 1-2. 디렉토리 구조 및 파일별 기능

```
fablize/2.1.0/
├── .claude-plugin/
│   ├── plugin.json            ← 플러그인 메타데이터 (name, version, skills 경로)
│   └── marketplace.json       ← 마켓플레이스 등록 메타
├── packs/                     ← 조건부 로딩되는 행동 규칙 팩
│   ├── investigation-protocol.txt    ← [팩A] 디버깅 조사 프로토콜 (6단계)
│   └── verification-grounding-pack.txt ← [팩B] 렌더/실행 산출물 검증 루프 (3단계)
├── scripts/
│   ├── goals.py               ← 멀티스토리 체크포인트 엔진 (.fablize/ 상태 파일)
│   ├── gate/                  ← 관측 게이트 라이브러리
│   │   ├── classify_task.py   ← 프롬프트 → quick/normal/deep 분류 + risk flags
│   │   ├── ledger.py          ← JSON ledger CRUD (~/.fablize/ledgers/)
│   │   ├── parse_tool_result.py ← 도구 결과 → 변경/검증/실패 사실 추출
│   │   └── verify_state.py    ← Stop 시점 차단 판정 로직 (deep-only)
│   └── shadow/                ← out-of-band 측정 인프라
│       ├── shadow_logger.py   ← events.jsonl 로거 + holdout arm 해시
│       ├── shadow_collect.py  ← ledger → events.jsonl 사후 수집 (M2)
│       ├── outcome_collect.py ← git/transcript → 결과신호 수집 (M3)
│       └── analyze.py         ← 층화 비교 + sunset 판정 (M4)
├── hooks/                     ← Claude Code 훅 (실시간 개입)
│   ├── hooks.json             ← 훅 등록 매니페스트 (3개 이벤트 × 5개 훅)
│   ├── router.sh              ← [UserPromptSubmit] 키워드 → 팩 라우팅
│   ├── gate_prompt.py         ← [UserPromptSubmit] ledger 리셋 + 모드 컨텍스트 주입
│   ├── gate_post_tool.py      ← [PostToolUse] 변경/검증/실패 관측 기록
│   ├── gate_stop.py           ← [Stop] deep 미검증 차단 + holdout 분기
│   └── finish-the-work.sh     ← [Stop] "하겠다"만 하고 안 하는 조기 종료 차단
├── skills/fablize/
│   └── SKILL.md               ← 스킬 정의 (§0~§4, 온디맨드 트리거)
├── commands/
│   └── setup.md               ← `/fablize:setup` 명령 정의
├── setup/
│   ├── setup.sh               ← CLAUDE.md에 상시 블록 주입 (idempotent)
│   ├── fablize-block.md       ← 주입되는 상시 규칙 블록 템플릿
│   └── uninstall.sh           ← 상시 블록 제거
├── docs/
│   └── MEASUREMENT_PROTOCOL.md ← 측정 프로토콜 설계 (9개 섹션)
├── tests/                     ← 게이트/shadow 단위 테스트 (6파일)
│   ├── test_gate.py           ← should_block_stop 기본 테스트
│   ├── test_gate_robustness.py ← 16/16 회귀 시나리오
│   ├── test_recovery.py       ← silent-recovery 가드 테스트
│   ├── test_shadow.py         ← shadow 로거/수집 테스트
│   ├── test_shadow_m3.py      ← outcome_collect 테스트
│   └── test_shadow_m4.py      ← stratified_compare + sunset 테스트
├── README.md / README.ko.md   ← 영/한 문서
├── CHANGELOG.md               ← 변경 이력
└── .gitignore                 ← 내부 R&D 문서 배포 제외
```

**총계**: 파일 30개, 디렉토리 10개

---

## 2. Fable 5의 재현 대상 행동 특성 역공학

### 2-1. fablize의 핵심 전제 (README + CHANGELOG에서 추출)

fablize 저자는 Fable 5 vs Opus 4.8을 A/B 19런 + 실작업 26세션(~1,500 tool calls)으로 통제 비교한 뒤, 차이를 두 범주로 분리했다:

| 범주 | 설명 | fablize 대응 |
|---|---|---|
| **절차(Procedure)** — 옮겨지는 것 | 모델이 "못 해서가 아니라 안 해서" 빠뜨린 행동 | ✅ 하네스로 강제 |
| **능력(Capability)** — 안 옮겨지는 것 | 모델의 추론 깊이·자발적 발견 | ❌ 정직하게 에스컬레이션 |

### 2-2. 재현 대상 Fable 행동 특성 5가지

#### ① 검증 접지 (Verification Grounding)
- **Fable 행동**: 렌더/실행 산출물을 만들면 반드시 실제로 돌려보고, 관측 결과를 근거로 수정
- **Opus 기본 행동**: 파일을 쓰고 "열어보세요"로 종료, 실제 구동 생략
- **fablize 재현**: `verification-grounding-pack.txt` — RUN→OBSERVE→FIX→RE-RUN 루프

#### ② 멀티스토리 완주 + 증거 게이트 (Multi-story Completion)
- **Fable 행동**: 복잡한 과제를 스토리로 분해하고 하나씩 증거를 남기며 완주, 마지막에 반드시 검증
- **Opus 기본 행동**: 전체를 한 번에 시도하거나, 중간에 근거 없이 "완료"선언
- **fablize 재현**: `goals.py` — create/next/checkpoint/status 사이클, 최종 스토리는 `--verify-cmd` + `--verify-evidence` 필수

#### ③ 체계적 조사 (Systematic Investigation)
- **Fable 행동**: 디버깅 시 재현 → 3+ 경쟁 가설 → 가설별 증거 → 인과사슬 추적 → 전후 검증 → 기각 가설 보고
- **Opus 기본 행동**: 첫 번째 그럴듯한 원인에 바로 달려들어 수정
- **fablize 재현**: `investigation-protocol.txt` — 6단계 규율

#### ④ 조기 종료 방지 (Early-Stop Prevention)
- **Fable 행동**: 작업을 실제로 끝낸 뒤에만 종료
- **Opus 기본 행동**: "다음에 X를 하겠습니다"라고 의향만 밝히고 멈춤
- **fablize 재현**: `finish-the-work.sh` — 마지막 assistant 메시지에서 미이행 약속 패턴 탐지 → block

#### ⑤ 관측 기반 완료 판정 (Observation-based Completion)
- **Fable 행동**: 검증 도구를 실제로 실행한 결과에 근거해서만 "통과" 주장
- **Opus 기본 행동**: 도구를 안 돌리고 "테스트 통과" 주장
- **fablize 재현**: 관측 게이트(gate_prompt → gate_post_tool → gate_stop) — ledger에 관측된 검증 결과가 없으면 deep 턴 완료 차단

### 2-3. 명시적으로 재현 불가로 분류한 Fable 행동

| 특성 | 왜 불가한가 |
|---|---|
| 정답지 밖 결함 발견 (Out-of-spec defect discovery) | 주입 실험으로 반증 — Opus가 Fable이 찾은 결함을 재현 못함 |
| 정답 없는 창작 디테일 | 모델 능력 영역 |
| 자발적 전파 깊이 (Self-driven propagation depth) | 지시된 전파는 옮겨지나 자발적 시작은 불가 |

---

## 3. 재현 메커니즘 분류

### 3-1. 4대 메커니즘 분류표

| 메커니즘 | 작동 방식 | 해당 구성요소 | 개입 강도 |
|---|---|---|---|
| **A. 상시 규칙 (Always-on Rules)** | CLAUDE.md에 주입된 텍스트 블록, 모든 턴에 컨텍스트 상주 | `fablize-block.md` → CLAUDE.md `<!-- FABLIZE:BEGIN -->` 블록 | 약 (소프트 지시) |
| **B. 신호 기반 조건부 팩 로딩 (Signal-routed Pack Injection)** | 프롬프트 키워드 매칭 → 해당 팩만 additionalContext로 주입 | `router.sh` (debug→investigation, html→grounding) + `gate_prompt.py` (모드 컨텍스트) | 중 (조건부 소프트 지시) |
| **C. 상태 파일 기반 추적 (.fablize/)** | 디스크에 JSON 상태를 영속화, 세션 간 생존 | `goals.py` → `.fablize/goals.json` + `ledger.jsonl`, `ledger.py` → `~/.fablize/ledgers/*.json` | 중 (추적 + 판정 근거) |
| **D. 결정론적 검증 게이트 (Deterministic Gate)** | 훅이 모델 출력/완료를 차단하고 행동 강제 | `gate_stop.py` (미검증 차단), `finish-the-work.sh` (미이행 약속 차단) | **강** (하드 블록) |

### 3-2. 데이터 흐름도

```
[사용자 프롬프트 입력]
    │
    ├─→ UserPromptSubmit Hook #1: router.sh
    │     └─ 키워드 매칭 → 팩 텍스트 additionalContext 주입
    │
    ├─→ UserPromptSubmit Hook #2: gate_prompt.py
    │     └─ 프롬프트 분류(quick/normal/deep) → ledger 리셋 → 모드 컨텍스트 주입
    │
    ▼
[모델 작업 수행]
    │
    ├─→ PostToolUse Hook: gate_post_tool.py  (Bash/Edit/Write/NotebookEdit/MultiEdit)
    │     └─ 변경 파일 종류, 검증 커맨드 성공/실패, 실패 기록 → ledger 갱신
    │     └─ 반복 실패(≥2회 같은 class) → "보고해라" additionalContext 주입
    │
    ▼
[모델 턴 종료 시도]
    │
    ├─→ Stop Hook #1: gate_stop.py
    │     └─ deep + 파일변경 + 검증미관측 → {"decision":"block"} (MAX 2회)
    │     └─ holdout 20% 세션은 차단 스킵 (측정용)
    │
    └─→ Stop Hook #2: finish-the-work.sh
          └─ 마지막 assistant 텍스트에 미이행 약속 패턴 → {"decision":"block"}
          └─ stop_hook_active 루프 가드 (1회만)
```

### 3-3. 상시 규칙 블록 상세 분석 (CLAUDE.md 주입)

CLAUDE.md 끝에 `<!-- FABLIZE:BEGIN -->` ~ `<!-- FABLIZE:END -->` 마커로 주입되는 5개 규칙:

| 라벨 | 트리거 조건 | 내용 |
|---|---|---|
| `[always]` | 무조건 | 결과 중심 · 범위 내 유지 · 완료 주장=tool result 근거 · 파괴적 작업 확인 |
| `[2+ sequential stories]` | 복수 스토리 | goals.py 사이클 실행, 최종 검증 게이트 |
| `[debugging / test failure / unknown cause / review]` | 디버깅/리뷰 | investigation-protocol.txt 따르기 |
| `[render/executable artifact]` | HTML/SVG/게임/UI/차트 | verification-grounding-pack.txt 따르기 |
| `[hard or ambiguous task]` | 어렵거나 모호 | adaptive thinking + `/effort xhigh` 권고 + 에스컬레이션 |

---

## 4. Claude Opus 4.6으로서의 1인칭 솔직 평가

> 이하는 내가 Claude Opus 4.6 Thinking으로서 fablize 팩 본문을 직접 읽고, "이 지시들이 실제로 나를 Fable 방향으로 끌어올리는가"를 솔직하게 평가한 것이다.

### 4-1. 실제로 효과적인 부분 ✅

#### a) investigation-protocol.txt — **매우 효과적**
- 나는 실제로 디버깅 시 첫 번째 그럴듯한 원인에 달려드는 경향이 있다. "3+ 경쟁 가설을 먼저 세워라"라는 지시는 **내 실제 약점을 정확히 찌른다**.
- "증상에 pattern-match하는 것이 반드시 root cause는 아니다"라는 경고는 내가 자주 빠지는 함정이다.
- "기각된 가설을 보고하라"는 요구는 내 사고의 투명성을 높여주고, 사후 검증 가능성을 만든다.
- **효과 등급: ★★★★★** — 지시 자체가 구체적이고, 내 실제 결함 패턴에 정확히 매핑된다.

#### b) verification-grounding-pack.txt — **효과적**
- "static check이 well-formed을 확인하지 correct을 확인하지 않는다"는 구분은 **정확하고 나에게 필요하다**. 나는 종종 `python -c "import json; json.load(open('x.json'))"` 같은 걸 돌리고 "valid"라고 주장하는데, 그건 문법이지 의미가 아니다.
- "produced-but-unobserved screenshot is not observation"도 정확하다 — 스크린샷을 찍어놓고 안 보는 패턴이 실제로 존재한다.
- 다만 "over-verifying a defect-free artifact wastes tokens"이라는 가드레일까지 포함된 점이 인상적 — 과잉 검증 유도를 의식적으로 차단한다.
- **효과 등급: ★★★★☆** — 실행 환경(headless browser 등)이 갖춰져 있을 때 강력하지만, 환경 없으면 지시만으로는 한계.

#### c) finish-the-work.sh — **결정론적이라 확실하게 효과적**
- 이건 내 의지와 무관하게 작동하는 **하드 게이트**다. 내가 "I'll implement this next"라고 쓰면 물리적으로 차단된다.
- 정규식이 구체적이고, 오탐 방지(질문으로 끝나면 통과)도 있다.
- 루프 가드(`stop_hook_active`)로 무한 차단을 막는다.
- **효과 등급: ★★★★★** — 소프트 지시가 아니라 결정론적 차단이라 "나를 설득"할 필요가 없다.

#### d) gate_stop.py (관측 게이트) — **부분적으로 효과적**
- deep 모드 + 파일 변경 + 미검증 조합에서만 발화하므로 정밀도가 높다.
- MAX 2회 차단 후 통과시키는 설계가 현실적이다 — 무한 트랩을 만들지 않는다.
- **효과 등급: ★★★★☆** — deep-only 축소가 옳은 판단이었다고 본다. normal에서의 과잉 발화는 실제로 노이즈였을 것이다.

### 4-2. 빈약하거나 한계가 있는 부분 ⚠️

#### a) 상시 규칙 블록 (fablize-block.md) — **빈약**
- `[always]` 규칙 4개는 너무 일반적이다: "결과 중심", "범위 내 유지", "tool result에 근거", "파괴적 작업 확인". 이것들은 **거의 모든 시스템 프롬프트에 이미 있는 수준의 지시**다.
- 나(Opus)의 시스템 프롬프트에 이미 유사한 지시가 포함되어 있어 **추가적 행동 변화를 유도하기 어렵다**.
- `[hard or ambiguous task]` 규칙은 "adaptive thinking scales with difficulty automatically"라고 적었는데, 이건 Claude의 내부 동작을 기술한 것이지 새로운 지시가 아니다.
- **빈약 이유**: 상시 규칙이 구체적 행동(예: "파일을 3개 이상 수정했으면 반드시 diff를 리뷰하라")보다 추상적 원칙에 머문다.

#### b) router.sh — **매우 단순**
- bash case 문으로 키워드 매칭만 한다: `debug|bug|error|traceback|...` → investigation, `html|svg|game|...` → grounding.
- **놓치는 경우가 많다**: "왜 안 돼?" (debug 키워드 없음), "페이지 만들어줘" (html 키워드 없음), "이거 고쳐" (bug 없이 수정 요청).
- 한국어 키워드 미지원 (router.sh는 영어만, classify_task.py만 한국어 일부 포함).
- **빈약 이유**: LLM 기반 분류를 의도적으로 피한 것 같지만(결정론성·속도 위해), 재현율이 떨어진다.

#### c) goals.py — **구조는 좋으나 채택 마찰이 높다**
- CLI 인터페이스(`goals.py create --brief "..." --goal "title::objective"`)가 모델에게 직접 실행하도록 요구하는데, 이건 **모델이 자발적으로 해야 하는 행동**이다.
- 상시 규칙에 "2+ sequential stories이면 goals.py를 실행하라"고 적혀있지만, 이게 **소프트 지시**라 나는 쉽게 무시할 수 있다.
- 하드 게이트가 아니라 소프트 지시로만 트리거되므로, 모델이 "이건 1개 스토리다"라고 합리화하면 우회된다.
- **빈약 이유**: 핵심 기능인데 강제 메커니즘이 없다.

#### d) 측정 인프라 (shadow/) — **사용자에게 직접적 가치 없음**
- 정교하게 설계된 out-of-band 측정 시스템이지만, 이건 **플러그인 개발자의 연구 도구**이지 사용자 가치가 아니다.
- 코드 분량의 약 30%를 차지하면서 런타임 행동에는 holdout 분기(gate 비활성화) 외에 기여가 없다.
- fable-lite에서는 불필요.

#### e) 범위 감지 부재 — **근본적 한계**
- fablize는 모델이 "범위를 벗어나는" 리팩토링/추가 작업을 하는 것을 탐지하는 메커니즘이 없다.
- `[always]` 규칙에 "stay within the requested scope"라고만 적었는데, 이를 검증하거나 차단할 수단이 없다.
- Fable은 이걸 자연스럽게 하지만, Opus는 소프트 지시만으로는 부족하다.

### 4-3. 총평

**fablize는 "절차를 강제하는 하네스"라는 자기 정의에 충실하며, 그 범위 안에서는 잘 설계되어 있다.** 특히:

1. **검증된 것만 탑재한다는 원칙**이 철저하다 — 미검증 아이디어를 HOLD로 두고 측정 프로토콜까지 설계한 것은 학술적 엄밀함에 가깝다.
2. **하드 게이트**(finish-the-work, gate_stop)가 가장 효과적이다 — 모델의 "의지"에 의존하지 않으므로.
3. **소프트 지시**(상시 규칙, 팩 텍스트)는 정확도는 높지만 **강제력이 부족**하다 — 나(Opus)는 이 지시들을 읽고 따를 수도 있고, 합리화하고 무시할 수도 있다.

**가장 큰 구조적 약점**: 하드 게이트(D)와 소프트 지시(A, B) 사이의 중간 강도 메커니즘이 없다. 팩의 지시가 아무리 정확해도, 그걸 실제로 따랐는지 검증할 수단이 결여되어 있다(investigation-protocol의 "3+ 가설을 세웠는가?"를 누가 확인하는가?).

---

## 5. fable-lite가 fablize 대비 개선할 기회 목록

### 5-1. 구조적 개선 (High Impact)

| # | 기회 | fablize 한계 | fable-lite 개선 방향 |
|---|---|---|---|
| **H1** | **팩 준수 검증 게이트** | investigation-protocol에 "3+ 가설"을 요구하지만 실제로 세웠는지 미검증 | 모델 출력을 파싱해 가설 수·증거 인용·기각 보고 존재를 확인하는 PostAssistant 게이트 추가 |
| **H2** | **goals.py 자동 트리거** | 소프트 지시로만 제안, 모델이 쉽게 우회 | 프롬프트 복잡도 분류 → 2+ 스토리 예상 시 자동으로 goals 플랜 생성 또는 명시적 확인 요구 |
| **H3** | **범위 이탈 감지** | "stay within scope"만 적음, 탐지 수단 없음 | PostToolUse에서 수정 파일을 추적하고, 원래 요청과 무관한 파일 수정 시 경고/차단 |
| **H4** | **한국어 라우팅 강화** | router.sh는 영어 키워드만, classify_task.py만 일부 한국어 | 한국어 패턴을 라우터에도 추가, 또는 LLM-free 형태소 분석 활용 |
| **H5** | **플랫폼 독립성** | Claude Code 플러그인 전용, bash 의존 | Antigravity/Codex 등 다중 에이전트 환경에서 동작하는 범용 하네스로 설계 |

### 5-2. 행동 특성 확장 (Medium Impact)

| # | 기회 | 설명 |
|---|---|---|
| **M1** | **증분적 커밋 규율** | Fable은 작업 단위마다 커밋하는 경향 — fablize에는 없음. 스토리 완료 시 자동 커밋 제안 |
| **M2** | **자기 리뷰 강제** | 코드 변경 후 스스로 diff를 읽고 리뷰하는 단계를 gate로 강제 |
| **M3** | **실패 시 복구 경로 선택** | fablize의 silent-recovery는 카운터만 — 실패 2회 시 대안 전략 선택을 모델에 요구 |
| **M4** | **에스컬레이션 프로토콜 구체화** | fablize는 "에스컬레이션하라"만 — 어떻게(어떤 정보를 정리해, 누구에게) 에스컬레이션할지 정의 |
| **M5** | **진행률 보고 규율** | 멀티스토리 중간에 사용자에게 진행률을 보고하는 규칙이 없음 |

### 5-3. 기술적 개선 (Lower Impact but Valuable)

| # | 기회 | 설명 |
|---|---|---|
| **L1** | **측정 인프라 분리** | fablize는 shadow 코드가 플러그인 안에 동거 — fable-lite는 측정을 별도 패키지로 분리 |
| **L2** | **상태 파일 통합** | `.fablize/goals.json` + `~/.fablize/ledgers/` + `events.jsonl`이 분산 — 단일 디렉토리/포맷으로 통합 |
| **L3** | **팩 모듈화·확장** | packs/에 2개만 있음 — 리뷰 팩, 리팩토링 팩, 테스트 작성 팩 등으로 확장 가능 |
| **L4** | **오탐 방지 개선** | finish-the-work.sh의 정규식이 "I'll"을 너무 넓게 잡음 — 문맥을 더 보는 개선 |
| **L5** | **Windows 네이티브** | fablize는 bash 기반 — fable-lite는 Python-only로 Windows 네이티브 호환 |

### 5-4. fablize가 의도적으로 제외한 것 (fable-lite에서 재고 가치 있음)

| fablize에서 제외한 것 | 제외 이유 | fable-lite에서의 재고 |
|---|---|---|
| 말투/스타일 모방 | 효과 미미 | 동의 — 제외 유지 |
| 넓은 추론 전파 주입 | 효과 미검증 | 일부 재고 — 특정 도메인(보안 리뷰 등)에서는 체크리스트 형태로 효과 있을 수 있음 |
| 무성 복구 가드 | 효과 미검증 → 2.1.0에서 silent-recovery로 부분 도입 | 이미 도입됨, 추가 개선 여지 |
| 리뷰 리콜 스캔 | 효과 미검증 | 재고 — 이전 세션의 리뷰 결과를 다음 세션에 자동 로딩하는 것은 가치 있을 수 있음 |

---

## 6. 결론 — fable-lite 설계를 위한 핵심 시사점

1. **하드 게이트가 핵심이다** — 소프트 지시는 모델이 무시할 수 있다. fable-lite는 검증 가능한 행동에 대해 결정론적 게이트를 우선 설계해야 한다.
2. **팩 내용은 우수하다** — fablize의 investigation-protocol과 verification-grounding은 재사용 가치가 높다. 하지만 **팩 준수를 검증하는 메커니즘**이 추가되어야 진짜 효과가 있다.
3. **범위 통제가 빠져있다** — Fable의 핵심 특성 중 하나인 "범위 내 유지"를 지시문으로만 처리하는 것은 불충분하다.
4. **다중 에이전트 환경을 고려하라** — fablize는 Claude Code 단일 에이전트 전제. fable-lite는 wmux 4-pane 환경(Opus + Gemini + Codex)에서 작동해야 하므로 플랫폼 독립적 설계가 필수.
5. **측정은 분리하라** — fablize의 측정 인프라는 엄밀하지만 플러그인 크기를 불필요하게 팽창시킨다. 별도 패키지로.

---

> **분석 완료**. 이 문서는 fablize 2.1.0의 전체 소스 코드(30개 파일)를 직접 읽고 역공학한 결과입니다.

# show-me-the-work (쇼미더워크)

**show-me-the-work**(짧은 표기 **smtw**, 쇼미더워크)는 "검증했다"는 말 대신 실제 실행 증거를 요구하는 AI 작업 감독 도구입니다.

[![version](https://img.shields.io/badge/version-2.0.1-brightgreen.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## 🎯 이게 뭔가요? (비개발자·바이브코더를 위한 설명)

AI(Claude·Codex 등)한테 코딩을 시켜보면, 실력은 좋은데 가끔 **덜렁댑니다**:

- 코드를 고쳐놓고 **실제로 돌려보지도 않고** "다 됐어요"라고 합니다
- 화면을 만들어놓고 "브라우저에서 열어보세요"로 끝냅니다 (자기가 안 봄)
- 버그 원인을 **하나만 대충 찍고** 바로 고칩니다 (틀린 곳을 고칠 수도)
- "다음엔 이걸 하겠습니다"라고 **말만 하고** 멈춥니다

`show-me-the-work`는 AI 옆에 붙는 **자동 품질 검사관**입니다. AI가 확인도 안 하고 "완료"하려 하면 **막아서고 "증거를 보여줘"라고 요구**합니다. 당신은 아무것도 할 필요 없이, 한 번 설치하면 모든 작업에서 알아서 작동합니다. 한국어로 말해도("이거 왜 안 돼", "화면 만들어줘") 알아듣습니다.

> 비유하면, **실력은 좋은데 덜렁대는 직원에게 붙여준 융통성 있는 품질관리 반장**입니다. 평소엔 조용하다가, 검증 없이 "됐어요" 하는 순간에만 "증거 가져와" 하고 잡습니다.

**한 가지 정직하게**: show-me-the-work는 AI를 더 **똑똑하게** 만들지는 못합니다. 대충 넘어가는 걸 **못 하게** 만들 뿐입니다. (실제로 켜고 끄고 비교해보니 정답률 자체는 같았지만, "검증을 얼마나 꼼꼼히 하는가"는 확실히 달랐습니다 — 아래 실측 참고.)

## 📖 왜 "show-me-the-work"인가

이 프로젝트는 v2.0에서 **fable-lite에서 show-me-the-work로 개명**했습니다. 출발점은 "Fable 5의 일하는 규율을 하위 모델에 옮길 수 있을까"라는 실험이었습니다. 모델 자체의 능력은 이식할 수 없지만, 조사·검증·완료 규율은 절차와 훅으로 강제할 수 있다는 결론을 얻었습니다.

v2.0에서는 Claude Code뿐 아니라 Codex·Antigravity까지 같은 증거 계약으로 감독하면서 정체성이 **Fable 작업규율 이식 실험에서 증거 기반 AI 작업 감독 인프라**로 확장됐습니다. 예전 이름은 출발점을 설명했지만, 새 이름은 지금 하는 일을 직접 설명합니다.

이름은 스타크래프트 치트키와 `쇼미더머니` 밈을 안티치트로 뒤집었습니다. AI가 "다 했습니다"라고 말하면 한 줄로 답합니다. **Show me the work. 일한 거 보여줘.** 말이 아니라 실행·관측·증거를 보여달라는 뜻입니다.

## 🤝 LazyCodex(ulw)와 같이 쓰면 시너지

[LazyCodex/OmO](https://github.com/code-yeongyu/oh-my-openagent)의 `ulw`는 작업을 끝까지 완주시키는 엔진이고, show-me-the-work는 완료 시점마다 검증 증거를 검문하는 장치라 역할이 겹치지 않습니다. Codex CLI에 둘을 같이 걸면(`adapters/codex_cli/INSTALL.ko.md`) **끝까지 밀고 나가되, 검증 없는 "다 됐어요"는 못 나가는** 조합이 됩니다. 실제로 이 저장소도 그 조합(ulw로 구현 + smtw로 검문)으로 개발됐습니다.

## 💬 자주 나오는 반론 (FAQ)

### "훅을 걸어봤자 AI가 안 따르면 그만 아닌가요?"

**"말로 시키는 것"과 "기계로 막는 것"은 다릅니다.** 그 비판은 앞쪽 절반에만 맞습니다.

- 프롬프트에 "꼭 검증하고 완료해"라고 적는 건 **부탁**입니다. AI가 바쁘면(?) 무시합니다. 저희 실측에서도 지시문만으로는 준수율이 **0/3**이었습니다.
- show-me-the-work는 부탁이 아니라 **자물쇠**입니다. 조건(실행된 검증 증거)이 채워지기 전에는 "완료" 선언과 도구 실행이 **프로그램 차원에서 거부**됩니다. AI가 "안 따를래"라고 마음먹을 수 있는 층이 아예 아닙니다 — 같은 실측에서 하드 게이트는 **3/3** 차단 후 진짜 증거를 갖고 돌아왔습니다 ([실험 보고서](docs/reviews/p5b-n1-natural.md)).

재미있는 증거 하나: 이 저장소를 개발하는 동안 **최상위 모델(Fable 5)조차 이 게이트에 차단당해서 보고서를 다시 쓴 일**이 실제로 있었습니다. 훅은 모델의 "성의"에 기대지 않습니다.

### "검증은 어차피 사람이 해야죠. 이걸 왜 만들어요?"

맞습니다 — **최종 책임은 사람에게 있고, show-me-the-work는 그걸 대체하지 않습니다.** 이 도구가 막는 건 그 전 단계의 문제입니다: **AI가 "검증했습니다"라고 말만 하고 실제로는 아무것도 실행하지 않은 경우**.

- 코드를 못 읽는 사람(이 도구의 핵심 사용자입니다)은 그 말이 진짜인지 가짜인지 **구별할 방법 자체가 없습니다**.
- 코드를 읽는 사람도, AI가 하루에 쏟아내는 수천 줄을 전부 직접 검증하는 건 이미 현실적으로 불가능합니다.

show-me-the-work는 "실행된 증거가 없으면 완료라고 못 말하게" 하는 **1차 필터**입니다. 사람 검증을 없애는 게 아니라, 사람 앞에 도달하는 "다 됐어요"의 신뢰도를 올리는 것입니다.

### "AI가 형식만 채우고 빠져나가면 그만 아니에요?"

**이론상 가능합니다. 완전 방어는 못 하고, 그렇게 주장하지도 않습니다.** 다만:

1. 게이트는 AI의 **말을 믿지 않고 도구 실행 결과를 읽습니다** — "테스트 통과했어요"라는 문장이 아니라, 테스트 명령이 실제로 실행되고 성공했는지를 봅니다.
2. 행동이 실제로 바뀌는 게 **블라인드 비교로 측정**됐습니다 — 켠 쪽이 5과제 전승, 격차는 전부 "검증이 귀찮은 지점"에서 났습니다 ([A/B 보고서](docs/reviews/e1-ab-report.md)).
3. 무한히 막지는 않습니다 — 2회 차단 후엔 통과시킵니다(작업 먹통 방지). 이건 안전장치이자, 저희가 문서에 적어둔 정직한 한계입니다.

요약하면: **show-me-the-work는 "AI를 믿게 만드는 도구"가 아니라 "AI를 덜 믿어도 되게 만드는 도구"입니다.**

---

## 기술 요약 (개발자용)

`show-me-the-work`는 조사·검증·완료 규율을 Claude Code·Codex·Antigravity 훅으로 강제하는 한국어 우선 AI 작업 감독 도구입니다.

> 왜 프롬프트가 아니라 훅인가: 도구 무제한 자연 세션 3회 실측에서 팩 지시만으로는 준수 **0/3**, 하드 게이트는 **3/3을 "1차단→1회복(실증거 동반)"으로 수렴**시켰습니다 — [실험 보고서](docs/reviews/p5b-n1-natural.md).
>
> 효과는? ON vs OFF 통제 A/B(5과제, 다른 모델이 블라인드 채점)에서 **ON 5/5 전승(총점 137 vs 109)**. 정확성은 무차이 — 값이 갈리는 지점은 "검증이 귀찮은 순간"입니다: 렌더 과제에서 OFF는 "열어보세요"로 끝, ON은 브라우저로 실제 관측·검증. 멀티스토리에서 OFF는 검증 시도 0회 — [A/B 보고서](docs/reviews/e1-ab-report.md).
>
> 이 정성적 차이는 **반복 3회에서 견고하게 재현**됐습니다(OFF 검증 0/3, ON 3/3). 단 "몇 배 비싸다"는 배율은 표본마다 크게 흔들려 단일 숫자로 볼 수 없습니다([반복 측정](docs/reviews/e1b-repeat.md)) — 이 반복 실험은 smtw 자체의 검증 인정 버그도 찾아내 수정으로 이어졌습니다. **재현되는 것만 보고하고, 안 되는 것은 안 된다고 적습니다.**

## v2 현재 기능

- 한국어 프롬프트 분류: `버그 고쳐줘`, `안돼`, `에러`, `페이지 만들어줘` 같은 요청을 조사·검증 팩으로 라우팅합니다.
- 파일시스템 provenance: bounded snapshot, 턴별 baseline, Stop full reconcile로 실제 파일 변경을 대조합니다.
- Stop gate: 비문서 파일 변경 후 fresh 성공 검증이 없으면 `quick`·`normal`·`deep` 모드 모두 최대 2회 완료를 차단합니다. 변경 없음과 문서 전용 변경은 기존처럼 허용합니다.
- N1 준수 게이트: 조사 출력에서 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:` 마커를 파싱합니다.
- N3 scope guard: 요청 범위 밖 파일 변경을 PostToolUse 단계에서 경고합니다.
- R1 high-risk contract: 인증, 결제, DB 마이그레이션 등 high-risk 편집은 구체적 `evidence`가 포함된 `.fable-lite/contract.json`이 있어야 진행됩니다.
- Session Scorecard: 세션별 차단 시도·회복·cap 통과를 append-only gate journal에서 집계합니다.

> 내부 상태 경로 `.fable-lite/`는 기존 설치를 깨지 않기 위해 v2.0에서 유지합니다. 공개 별칭은 v2.1에서 추가할 예정입니다.

### 호스트 지원 상태

| 호스트 | 현재 상태 |
|---|---|
| Claude Code | 라이브 훅 체인 확인 |
| Codex CLI | 라이브 훅 체인 확인 |
| Antigravity | payload injection 정합성 확인, 호스트 1.1.1 라이브 발동은 미확인 |

### 상태 파일과 정직한 한계

사용자 상태는 모두 `.fable-lite/` 아래에 둡니다. 주요 경로는 `ledger.json`, `goals.json`, intent·contract 파일, `agents/*.jsonl`, `snapshots/workspace-current.json`, `snapshots/turns/**`, `scorecard/gates.jsonl`, `provenance-config.json`이며, bounded Scorecard cache·락·백업·마이그레이션 복구 파일도 같은 디렉터리에 있습니다.

- Stop은 최대 2회 차단 후 fail-open으로 통과시킵니다. 교착 방지 장치이며 적대 모델을 막는 보안 경계가 아닙니다.
- 프로젝트 루트 밖 파일과 DB·네트워크 부작용은 직접 관측하지 않습니다.
- provenance 지원 상한은 관측 대상 10,000개·총 256 MiB입니다. 상한 근처의 Stop full reconcile은 수 초가 걸릴 수 있습니다. 더 크거나 느린 범위는 partial snapshot을 저장하지 않고 `scope too large`를 1회 안내한 뒤 advisory-only로 처리합니다. 시간 상한은 파일시스템 호출과 hash chunk 사이의 협력적 제한이라 단일 OS 호출 정지는 선점할 수 없습니다.
- 직접 `ssh`와 로컬→원격 `scp`는 remote mutation epoch로 기록하고, 나중에 별도로 시작한 성공 검증으로만 해소합니다. 이는 원격 상태를 clean으로 관측했다는 뜻이 아닙니다. 로컬 redirect·pipeline·download·chain·substitution·위험 SSH 옵션은 보수적인 local-mutation 경로를 유지합니다.
- 텍스트로만 "하겠습니다"라고 끝내는 경우는 manual probe `PRB-01`이며 전용 차단 규칙이 없습니다. gate별 독립 toggle은 manual probe `PRB-11`이고 아직 구현되지 않았습니다.
- 이 도구는 작업 규율과 증거 품질을 높이지만 모델 능력을 높이거나 의도적 우회를 완전히 방어하지는 않습니다.

## 설치 형태

Claude Code 플러그인 manifest는 `.claude-plugin/plugin.json`에 있으며, 훅 정의는 `adapters/claude_code/hooks.json`에 있습니다. 훅은 모두 Windows native Python 명령만 사용합니다.

> **전제: 대상 환경에 Python 3.12+가 PATH에 있어야 합니다.** 훅이 stdlib Python 스크립트이므로, `python`이 없는 호스트(예: 새 워커 노트북)는 먼저 Python을 설치해야 합니다. 외부 패키지 의존은 없습니다.

권장 설치는 로컬 클론을 먼저 Claude Code marketplace에 등록하는 방식입니다.

```powershell
git clone https://github.com/pinetreeB/show-me-the-work
claude plugin marketplace add <show-me-the-work 경로>
/plugin install show-me-the-work@show-me-the-work
```

원격 marketplace 등록이 완료된 뒤에는 `/plugin marketplace add pinetreeB/show-me-the-work`로 로컬 경로 등록 단계를 대체할 수 있습니다.

## 목표 체크포인트 CLI

```powershell
python goals/goals.py plan --root . --goal "페이지 만들기" --story "관리자 페이지 렌더" --verify-cmd "python -m pytest"
python goals/goals.py verify --root . --story "관리자 페이지 렌더" --evidence "pytest green"
python goals/goals.py status --root .
```

## 평가 프로브 러너

결정론적 프로브는 훅 스크립트를 fixture payload로 실행해 자동 판정하고, 모델 실행이나 루브릭 채점이 필요한 프로브는 `manual`로 남깁니다.

```powershell
python eval/run_probes.py
python eval/run_probes.py --strict
python eval/run_probes.py --output eval/results/probes-latest.json
```

기본 결과 파일은 `eval/results/probes-latest.json`입니다. 콘솔 요약은 Windows CP949에서도 깨지지 않도록 ASCII만 출력합니다.

```text
probes pass=15 fail=0 manual=3 total=18 result=PASS
```

기본 러너는 실패 프로브가 있어도 JSON 리포트를 끝까지 쓰고 종료코드 0으로 끝납니다. 릴리스 게이트에서는 `--strict`를 사용해 실패가 있으면 non-zero로 종료합니다. 결과는 `summary.fail`과 `result` 필드에도 기록됩니다.

## 오케스트레이터 CLI

wmux 좌상 오케스트레이터는 위임 전 `brief`로 작업 규율 블록을 만들고, 워커 완료 후 `check`로 원장과 git diff를 대조합니다.

```powershell
python -m fable_lite brief --paths "core/**,tests/**" --verify-cmd "python -m pytest tests/" --sentinel tmp/.done-x --target codex
python -m fable_lite check --root . --agent codex --since-file tmp/.delegation-start
python -m fable_lite brief --card C:\Users\rotat\.claude\tmp\cards\work.json && python -m fable_lite check --card C:\Users\rotat\.claude\tmp\cards\work.json
```

`check`는 변경 파일, 미검증 변경, scope 이탈, R1 계약 필요, sentinel 약속 미이행을 한국어로 요약합니다. exit code `0`만 green이며, sentinel 존재만으로 완료로 보지 않습니다.

## 출처

검증 접지, 분해/증거 게이트, 조사 루프, 조기종료 방지의 절차 구조는 MIT 라이선스의 `fivetaku/fablize`에서 검증된 아이디어를 참고했습니다. 의도 게이트의 인터뷰 방법론(요구사항 모호성 채점 → 임계 게이팅 → 1문1답 확정)은 MIT 라이선스의 [`Yeachan-Heo/gajae-code`](https://github.com/Yeachan-Heo/gajae-code)에서 차용했습니다. 문장과 코드는 전부 새로 작성했습니다.

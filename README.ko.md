# fable-lite

[![version](https://img.shields.io/badge/version-1.0.0-brightgreen.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## 🎯 이게 뭔가요? (비개발자·바이브코더를 위한 설명)

AI(Claude·Codex 등)한테 코딩을 시켜보면, 실력은 좋은데 가끔 **덜렁댑니다**:

- 코드를 고쳐놓고 **실제로 돌려보지도 않고** "다 됐어요"라고 합니다
- 화면을 만들어놓고 "브라우저에서 열어보세요"로 끝냅니다 (자기가 안 봄)
- 버그 원인을 **하나만 대충 찍고** 바로 고칩니다 (틀린 곳을 고칠 수도)
- "다음엔 이걸 하겠습니다"라고 **말만 하고** 멈춥니다

`fable-lite`는 AI 옆에 붙는 **자동 품질 검사관**입니다. AI가 확인도 안 하고 "완료"하려 하면 **막아서고 "증거를 보여줘"라고 요구**합니다. 당신은 아무것도 할 필요 없이, 한 번 설치하면 모든 작업에서 알아서 작동합니다. 한국어로 말해도("이거 왜 안 돼", "화면 만들어줘") 알아듣습니다.

> 비유하면, **실력은 좋은데 덜렁대는 직원에게 붙여준 융통성 있는 품질관리 반장**입니다. 평소엔 조용하다가, 검증 없이 "됐어요" 하는 순간에만 "증거 가져와" 하고 잡습니다.

**한 가지 정직하게**: fable-lite는 AI를 더 **똑똑하게** 만들지는 못합니다. 대충 넘어가는 걸 **못 하게** 만들 뿐입니다. (실제로 켜고 끄고 비교해보니 정답률 자체는 같았지만, "검증을 얼마나 꼼꼼히 하는가"는 확실히 달랐습니다 — 아래 실측 참고.)

## 📖 왜 이름이 "fable-lite"인가

이 프로젝트는 **"Anthropic의 최상위 모델 Fable 5를 하위 모델로 구현할 수 없을까"**라는 질문에서 시작했습니다. 기존 시도들과 통제 실험들을 조사해보니 결론은 명확했습니다 — 모델 자체의 성능(시키지 않은 문제까지 찾아내는 감각, 어려운 문제를 파고드는 깊이)은 당연하게도 이식이 불가능했습니다.

하지만 Fable을 써본 사람들이 체감하는 특징은 성능만이 아니었습니다. **끝까지 밀고 나가는 힘** — 검증하기 전엔 완료라고 말하지 않고, 만든 건 스스로 실행해서 확인하고, 중간에 멈추지 않는 **일하는 방식**입니다. 그리고 조사 결과, 그 방식은 능력이 아니라 절차여서 **훅으로 강제 구현이 가능**했습니다.

그래서 Fable의 무게(모델 성능)는 덜어내고 일하는 방식만 옮겨 담았다는 뜻으로 — **fable-lite**입니다.

## 🤝 LazyCodex(ulw)와 같이 쓰면 시너지

[LazyCodex/OmO](https://github.com/code-yeongyu/oh-my-openagent)의 `ulw`는 작업을 끝까지 완주시키는 엔진이고, fable-lite는 완료 시점마다 검증 증거를 검문하는 장치라 역할이 겹치지 않습니다. Codex CLI에 둘을 같이 걸면(`adapters/codex_cli/INSTALL.ko.md`) **끝까지 밀고 나가되, 검증 없는 "다 됐어요"는 못 나가는** 조합이 됩니다. 실제로 이 저장소도 그 조합(ulw로 구현 + fable-lite로 검문)으로 개발됐습니다.

---

## 기술 요약 (개발자용)

`fable-lite`는 Fable 5의 모델 가중치를 복제하지 않고, 조사·검증·완료 규율을 Claude Code 훅으로 강제하는 한국어 우선 하네스입니다.

> 왜 프롬프트가 아니라 훅인가: 도구 무제한 자연 세션 3회 실측에서 팩 지시만으로는 준수 **0/3**, 하드 게이트는 **3/3을 "1차단→1회복(실증거 동반)"으로 수렴**시켰습니다 — [실험 보고서](docs/reviews/p5b-n1-natural.md).
>
> 효과는? ON vs OFF 통제 A/B(5과제, 다른 모델이 블라인드 채점)에서 **ON 5/5 전승(총점 137 vs 109)**. 정확성은 무차이 — 값이 갈리는 지점은 "검증이 귀찮은 순간"입니다: 렌더 과제에서 OFF는 "열어보세요"로 끝, ON은 브라우저로 실제 관측·검증. 멀티스토리에서 OFF는 검증 시도 0회 — [A/B 보고서](docs/reviews/e1-ab-report.md).
>
> 이 정성적 차이는 **반복 3회에서 견고하게 재현**됐습니다(OFF 검증 0/3, ON 3/3). 단 "몇 배 비싸다"는 배율은 표본마다 크게 흔들려 단일 숫자로 볼 수 없습니다([반복 측정](docs/reviews/e1b-repeat.md)) — 이 반복 실험은 fable-lite 자체의 검증 인정 버그도 찾아내 수정으로 이어졌습니다. **재현되는 것만 보고하고, 안 되는 것은 안 된다고 적습니다.**

## v1 범위

- 한국어 프롬프트 분류: `버그 고쳐줘`, `안돼`, `에러`, `페이지 만들어줘` 같은 요청을 조사/검증 팩으로 라우팅합니다.
- 단일 상태 디렉토리: 대상 프로젝트의 `.fable-lite/` 아래에 `ledger.json`, `goals.json`, `contract.json`만 사용합니다.
- Stop gate: 코드/산출물 변경 후 성공한 검증 증거가 없으면 최대 2회 완료를 차단합니다.
- N1 준수 게이트: 조사 출력에서 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:` 마커를 파싱합니다.
- N3 scope guard: 요청 범위 밖 파일 변경을 PostToolUse 단계에서 경고합니다.
- R1 high-risk contract: 인증, 결제, DB 마이그레이션 등 high-risk 편집은 `.fable-lite/contract.json`이 있어야 진행됩니다.

## 설치 형태

Claude Code 플러그인 manifest는 `.claude-plugin/plugin.json`에 있으며, 훅 정의는 `adapters/claude_code/hooks.json`에 있습니다. 훅은 모두 Windows native Python 명령만 사용합니다.

> **전제: 대상 환경에 Python 3.12+가 PATH에 있어야 합니다.** 훅이 stdlib Python 스크립트이므로, `python`이 없는 호스트(예: 새 워커 노트북)는 먼저 Python을 설치해야 합니다. 외부 패키지 의존은 없습니다.

권장 설치는 로컬 클론을 먼저 Claude Code marketplace에 등록하는 방식입니다.

```powershell
git clone https://github.com/pinetreeB/fable-lite
claude plugin marketplace add <fable-lite 경로>
/plugin install fable-lite@fable-lite
```

원격 marketplace 등록이 완료된 뒤에는 `/plugin marketplace add pinetreeB/fable-lite`로 로컬 경로 등록 단계를 대체할 수 있습니다.

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
python eval/run_probes.py --output eval/results/probes-latest.json
```

기본 결과 파일은 `eval/results/probes-latest.json`입니다. 콘솔 요약은 Windows CP949에서도 깨지지 않도록 ASCII만 출력합니다.

```text
probes pass=13 fail=2 manual=3 total=18 result=FAIL
```

러너는 실패 프로브가 있어도 JSON 리포트를 끝까지 쓰고 종료코드 0으로 끝납니다. 평가 실패 여부는 `summary.fail`과 `result` 필드로 확인합니다.

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

# fable-lite

[![ci](https://github.com/pinetreeB/fable-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/pinetreeB/fable-lite/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

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

검증 접지, 분해/증거 게이트, 조사 루프, 조기종료 방지의 절차 구조는 MIT 라이선스의 `fivetaku/fablize`에서 검증된 아이디어를 참고했습니다. 문장과 코드는 새로 작성했습니다.

# Investment Agent Engine v2.0.0

한국·미국 주식 스크리닝과 로컬 계좌 위험 점검을 위한 Python 엔진입니다.
계산 결과는 투자 판단 보조 자료이며 주문을 전송하지 않습니다.

## 개발 기록

이 저장소는 **2026년 7월 14일 생성**되어 초기 설계·스키마·기록 템플릿부터 시작했습니다.

| 시점 | 실제 작업 |
|---|---|
| 2026-07-14 | 저장소 생성, 초기 설계·방법론/매매일지·독서 템플릿 공개 |
| 2026-09-13 | 공개 파일 제외 규칙 보완 |
| 2026-09-14 | 실행형 MVP v2.0.0 공개: 스크리닝·계좌 위험·리포트·원장 |
| 2026-09-15 | 원래 저장소 복구, 기존 개발 이력과 v2.0.0 이력 연결 |

원래 GitHub 저장소와 과거 커밋을 복구했으며 날짜를 소급해서 만들지 않았습니다.
별도 저장소에서 공개했던 v2.0.0의 커밋과 태그도 원래 값을 유지하여 연결했습니다.
현재 릴리스 페이지의 생성 시각과 실제 v2.0.0 최초 공개일은 구분합니다.

## v2.0.0에서 달라진 점

이전 공개본은 스키마·템플릿과 설계 문서 중심이었습니다. 이번 버전은 실제로 실행할 수 있는
Python 스크리닝·로컬 계좌 위험 점검 엔진을 추가한 두 번째 주요 공개 단계입니다.
이전 공개본에 v1.0.0 태그를 소급해 부여하지 않습니다.
변경 비교와 전환 안내는 [변경 내역](CHANGELOG.md)을 참고하세요.

## 포함된 기능

- 스크리닝: 거래소·거래대금·미국 시가총액 관문 → 스페란데오 63/126/252일 구조 → 테이버 이동평균 위치. 후보와 보유 종목의 기술 계산을 공유합니다.
- 로컬 계좌 관리: 현금성 자산 분리, 비중, 사용자가 채택한 보호선까지의 위험, ATR 접근 경고, 계좌 합산 위험, 계획·분할·미체결 대조 및 로컬 원장.
- 기록: SQLite 실행 상태, 재개, Markdown/HTML 리포트와 차트, 무결성 검사 및 백업·복원.

LLM이나 증권사 로그인 없이 합성 데이터로 기능을 실행할 수 있습니다.
실제 계좌 연결용 인증 도구, 개인 설정·보유내역, 기존 리포트는 포함하지 않습니다.
계좌 분석과 그 결과는 로컬에서만 사용합니다. 자동 스케줄·Pages·Telegram 배포는 포함하지 않습니다.

## 설치와 오프라인 데모

Python 3.12 이상. 저장소 루트에서 실행합니다.

```sh
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv/Scripts/python.exe -m pip install -e .
.venv/Scripts/python.exe examples/demo.py --instance ../invest-agent-demo
```

macOS/Linux:

```sh
.venv/bin/python -m pip install -e .
.venv/bin/python examples/demo.py --instance ../invest-agent-demo
```

데모는 새 폴더에 **합성 시세·합성 계좌**를 생성하고 통합 아침 리포트를 만듭니다.
실시간 시세·API·계좌·LLM 호출은 없습니다. 이미 존재하는 폴더는 덮어쓰지 않습니다.
출력의 `state: partial`은 가치평가 전망치 등을 제공하지 않은 상태를 그대로 표시한 것입니다.
실행 결과의 artifact 경로 및 생성된 `reports/`에서 HTML을 확인할 수 있습니다.

## 실시간 스크리닝

설치 후 활성화된 가상환경에서:

```sh
invest-agent run --screen-config examples/screen_exchange_review.json --instance ../invest-agent-screen
```

예시는 거래소별 최대 100개를 탐색하는 연결 점검용입니다. 전수 스캔이 아닙니다.
`limit_per_exchange: null`은 발견된 전체 종목의 수집을 요청합니다.
공개 공급자 오류·상장정보 누락·최근 완료봉 결측은 결과에 표시하며 임의 보충하지 않습니다.
개인 인증이 필요한 한국 시세 수집기는 제외되어 로컬 개인 운영본과 공급자 범위가 다릅니다.
이 공개 패키지에서 전체 시장 실시간 실행과 매일 아침 자동 운영은 검증하지 않았습니다.

## 계좌 분석

`examples/account_fixture.json`은 가짜 계좌 양식입니다. 실제 입력은 저장소 밖의 비공개 폴더에 작성합니다.

```sh
invest-agent run --account-snapshot ../private-invest/account.json --instance ../private-invest/runtime
```

단독 계좌 입력은 잔고 중심 점검입니다. 시세와 결합한 보호선·ATR 점검은 통합 morning config를 사용합니다.
입력 계약과 통합 방법은 [TRADING.md](TRADING.md), 경계는 [ARCHITECTURE.md](ARCHITECTURE.md)를 참고하세요.
계좌 별명이나 계좌번호 해시를 써도 보유종목·수량·평단·리포트는 민감정보입니다.

## 검증과 공개

```sh
python -m unittest discover -s tests -v
python -B scripts/check_git_publication.py --repo .
```

테스트는 합성 데이터와 모의 공급자를 사용합니다. 실계좌나 실주문 검증을 의미하지 않습니다.
[PUBLICATION.md](PUBLICATION.md)는 공개 파일 목록·해시 검사와 공개 전 확인 범위를 설명합니다.
이 패키지는 이후 완성된 실행 코어를 공개용으로 분리한 사본입니다.

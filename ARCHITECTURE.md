# 실행 구조와 개인정보 경계

```text
공개 시장 데이터 → universe gate → Sperandeo → Taver → 후보·차트
                                                      ↓
로컬 계좌 JSON → 현금/포지션 분리 → 보호선·위험 계산 → 로컬 리포트
                                                      ↓
                              로컬 SQLite / 계획 원장 / 백업
```

`src/invest_agent/trading/`가 계산·상태 저장의 중심입니다.

| 기능 | 주요 모듈 |
| --- | --- |
| 공개 시세·상장정보 | market_provider, universe |
| 종목군·후보·기술 구조 | universe_gate, market, sperandeo, taver, technical_context |
| 계좌·현금·보호선 | portfolio, cash_assets, protection, holding_review |
| 위험·분할·계획·대조 | risk_budget, risk_ledger, risk_monitoring, plans, scenarios, capacity |
| 통합 실행·저장·복구 | morning, runner, store, backup |
| 차트·보고 | charts, reporting, holding_report, report_html |

미국은 NYSE/NASDAQ, 시총 20억 USD 이상, 최근 20완료봉 평균 거래대금 2천만 USD 이상입니다.
한국은 KOSPI, 같은 평균 거래대금 20억 KRW 이상이며 시총 하한은 없습니다.
거래대금은 공급자가 제공하면 해당 값을 쓰고, 아니면 종가×거래량 근사치를 명시합니다.
후보는 스페란데오 독립 63/126/252일 창의 S1~S4 중 하나 이상으로 정합니다(primary 126).
테이버 SMA는 미국 50/100/200, 한국 60/120/240이며 후보 이후의 위치 설명입니다.
기존 설정의 MA/RS/activity 필드는 호환용으로 남아 있고 후보 편입 필터로 사용하지 않습니다.

보유 종목은 후보 조건 탈락 여부와 관계없이 기술 점검합니다. 위험 계산은 명시적으로 채택된
보호선과 승인된 정책만 사용합니다. 결측·오래된 입력·미채택 보호선은 안전한 0으로 간주하지 않습니다.
구조 탐지의 ATR과 보유 경고의 Wilder ATR은 별도 계산이며 용도를 섞지 않습니다.

계좌 JSON, SQLite, 차트, 리포트, 백업은 **모두 비공개 로컬 출력**입니다.
아침 통합 수집은 보유 종목 식별자를 시세 공급자에게 조회할 수 있습니다.
인증정보는 호출자의 비공개 어댑터가 관리하며 이 저장소에는 포함하지 않습니다.
`toss_snapshot`은 주입된 읽기 전용 호출기의 응답 정규화 도구입니다. 계좌 별명은 익명성 보장이 아닙니다.

CLI는 신뢰한 로컬 파일을 읽는 도구입니다. 인터넷에 노출된 API 서버가 아니며 타인이 제공한
config·DB·백업을 임의 실행/복원하는 서비스로 사용하지 않습니다. 백업 무결성 검사는 서명 인증이 아닙니다.
과거 계산 CLI와 데이터 공급자 모듈도 호환을 위해 보존하지만 자동 아침 경로는 LLM을 호출하지 않습니다.

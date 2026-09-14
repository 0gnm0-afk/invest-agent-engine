# 로컬 실행과 계좌 입력

계좌 분석은 저장소 밖의 비공개 작업 폴더에서 실행합니다. 결과를 GitHub나 Pages에 올리지 않습니다.

## 계좌 입력 계약

`examples/account_fixture.json`과 `examples/demo.py`에 가짜 데이터 예시가 있습니다.

| 필드 | 의미 |
| --- | --- |
| schema_version / source / as_of / max_age_hours | 버전, 출처, 실제 관측 시각, 허용 연령 |
| base_currency / fx_to_base | 기준 통화와 출처가 확인된 환율 |
| accounts[].alias | 로컬 별명; 실명·계좌번호 사용 금지 |
| accounts[].cash | 총 현금; 주문가능액과 구분 |
| positions[] | 종목, 시장, 통화, 수량, 현재가, 평단 |
| quote_symbol / benchmark | 시세 수집용 식별자; 결측이면 추측하지 않음 |
| adopted_stop | 사용자 채택 출처와 가격 또는 보호 규칙 |

`source: synthetic`은 합성 예시에만 사용합니다. 실제 export는 `broker_export`를 사용합니다.
API 원문을 그대로 넣지 말고 계약에 맞춰 로컬에서 정규화해야 합니다.
수량·평단·별명 역시 비공개이며 계좌번호 제거만으로 공개 가능한 자료가 되지는 않습니다.

## 시세와 계좌를 함께 점검

저장소 밖 `private-invest/morning.json` 예:

```json
{
  "schema_version": 1,
  "market_snapshot": "market.json",
  "account_snapshot": "account.json",
  "valuation_inputs": [],
  "holding_review_policy": {}
}
```

`market_snapshot`은 출처와 완료봉이 포함된 저장 시세 입력입니다. 형식은 데모가 생성한
`inputs/market.json`을 참고합니다. 실시간 수집은 `market_snapshot` 대신 `screen_config`를 사용합니다.
두 필드는 동시에 설정하지 않습니다. 상대경로는 config 파일 기준입니다.

```sh
invest-agent run --morning-config ../private-invest/morning.json --instance ../private-invest/runtime
invest-agent status --instance ../private-invest/runtime --json
invest-agent resume RUN_ID --instance ../private-invest/runtime
```

`holding_review_policy`의 atr_period, warning_atr_multiple, position_stop_risk_limit_pct,
account_total_stop_risk_limit_pct는 각각 value, approved_by_user, source_ref를 받습니다.
값이 없거나 미승인 상태이면 관련 판단은 unavailable입니다. 예시의 수치는 권고나 기본 채택이 아닙니다.

최신 입력으로 새 리포트를 만들려면 `--refresh`를 사용합니다. `resume`은 동결된 입력을 재사용하며
실시간 갱신하지 않습니다. 입력·코드·아티팩트가 변조되면 무결성 검사에서 거부할 수 있습니다.
이전 보고서는 보존됩니다. 부분 수집/미설정 평가 항목은 partial 및 종료코드 2로 표시될 수 있습니다.

## 계획과 백업

```sh
invest-agent plan record --input examples/plan_fixture.json --instance ../invest-agent-demo-plans
invest-agent plan status --instance ../invest-agent-demo-plans
invest-agent backup --instance ../private-invest/runtime
invest-agent restore --archive ../private-invest/backup.zip --destination ../private-invest/restored
```

계획 예시는 합성 입력입니다. 계획 기록·수량 시나리오·원장 사건은 증권사 주문이 아닙니다.
손절·위험한도·전략·매도계획 계약은 tests/test_risk_* 및 risk_ledger 모듈에서 확인할 수 있습니다.
복원 대상은 존재하지 않는 새 폴더여야 합니다. 백업에도 계좌 입력과 리포트가 들어가므로 비공개로 보관합니다.

## 개인 계좌 별명 키

Toss 어댑터를 직접 연결하는 비공개 호출자는 `collect(api_get, alias_key=key)`에
`secrets.token_bytes(32)`로 최초 한 번 생성한 키를 전달합니다. 키는 저장소 밖의
개인 비밀 저장소에 보관하고 매 실행 동일한 값을 불러옵니다. 실행마다 새로 생성하지 않습니다.
키가 없거나 32바이트보다 짧으면 계좌 API 조회 전에 실패합니다.
키, 원본 응답, 실제 보유내역과 리포트는 공개하지 않습니다. HMAC 별명도 공개 동의나 익명화를 뜻하지 않습니다.

기존 무키 SHA-256 별명과 새 별명은 다릅니다. 기존 사용자라면 비공개 환경에서
계좌와 별명의 대응을 검증하고 전략 계좌 목록·원장·계획 참조를 함께 전환해야 합니다.
키를 분실/교체해도 별명이 달라집니다. 이 패키지는 기존 원장을 자동 수정하거나
기존 별명으로 되돌리는 우회를 제공하지 않습니다. 합성 데모에는 키가 필요 없습니다.

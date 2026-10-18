# 매매 사이클 계약 v1

2026-10-15. 구현 정본: `scripts/trade_cycle.py`, `trade_cycle_api.py`, `trade_cycle_agent.py`, `trade_cycle_alerts.py`. 화면은 `dashboard/trade-cycle.js`다. 기존 `collaboration.v1` 계획·작도 감시와 구분하여 저장하며 주문을 실행하지 않는다.

## 사용자 흐름

1. 종목 상세의 **매매 사이클** 또는 사이드바에서 종목·계좌를 선택한다. 같은 통화의 저장 현금이 기본 예산이며 사용자가 수정할 수 있다. 예수금은 주문가능금액을 보장하지 않고 기준시점·결측·오래된 자료를 표시한다.
2. **Agent 초안 생성** 버튼으로 저장 차트·기술/정성 근거·이전 논의·선택 가능한 사용자 작도를 읽어 초안을 만든다. 작도가 없어도 생성할 수 있다. 생성 상태는 같은 화면에 표시하며 실패는 원래 초안과 구분한다.
3. 가격·조건·경로를 수정하고 6개 매수 순열 × 6개 매도 순열 × 상승/횡보/하락의 108개 결과를 비교한다. 초안 저장 뒤 정확한 버전과 조합을 화면에서 확정한다. 대표 조합은 선택을 돕는 계산 결과이며 자동 채택하지 않는다.
4. 실제 거래는 사용자가 수량·가격·시각을 별도 입력한다. 저장된 실제 거래로 현재 단계·차트 표시·현금·잔여 수량·평단·손익·앞으로의 경로를 다시 계산한다. 경보 발생이나 계좌 보유 관측만으로 체결을 만들지 않는다.
5. 첫 실제 매도 후 아직 실행하지 않은 매수 단계를 취소한다. 매도 분할 기준수량은 첫 매도 직전 수량으로 고정한다. 3차 매도 및 최종 손절은 잔여 전량이다. 종료 후 재진입은 별도 사이클로 시작한다.

목록 경로는 `#cycles`, 상세는 `#cycle?id=cy_…`다. 상세는 `#cycle/cy_…`도 읽는다.

## 계획과 계산값

`plan` 필수 필드는 다음과 같다. 가격·예산은 소수 문자열 또는 `null`, 주식 수량은 정수 또는 미정 `null`이다. 누락을 0으로 바꾸지 않는다.

| 필드 | 의미 |
|---|---|
| `currency` | `KRW` / `USD` / 미정 |
| `budget` | 사이클 시작 현금. 매수 비율의 기준 |
| `initial_holding` | `{quantity, average_price}`. 시작 보유 입력이며 체결 기록을 대신하지 않음 |
| `buy_steps`, `sell_steps` | 각 최대 3개 단계. 단계 번호는 1부터 시작 |
| `stop` | 최종 무효화/손절 단계. 미정이면 `null` |
| `scenarios` | `bullish`, `neutral`, `bearish` 각각 `{events:[…]}` |

선택 필드: `title`, `purpose`, `account_alias`, `notes`, `rationale`, `unresolved:[문자열]`, `sources:[{kind,reference,text?}]`. 근거는 확보한 snapshot/record/drawing ID로 연결한다. 실제 사용자 발언이나 합의를 만들지 않는다.

각 단계는 다음 형식이다.

```json
{
  "confirmation_price": "100",
  "assumed_fill_price": "101",
  "condition": "저장 근거와 기준일을 포함한 확인 조건",
  "trigger": {
    "kind": "price",
    "evaluation": "daily_close",
    "comparator": "gte"
  }
}
```

`confirmation_price`는 관찰 조건, `assumed_fill_price`는 계산에 쓰는 가정 체결가다. 서로 다를 수 있다. `trigger`가 없거나 `kind=manual`이면 기계 가격판정을 만들지 않는다. 정성 조건은 `manual`과 구체적인 설명으로 유지한다. `manual`의 comparator는 생략하거나 `null`이다. 다른 종류는 `price`, `trailing`, `ma5`, `ma10`이며 comparator는 `gt`, `gte`, `lt`, `lte`, `cross_above`, `cross_below`다. 호환 입력 `operator`도 받지만 `comparator`와 동시에 쓰지 않는다. 기울어진 작도의 미래 값을 고정가격으로 바꾸지 않는다.

시나리오 `events`는 가정의 시간순 배열이다. `buy`/`sell`은 `step`을 요구하고, `stop`/`mark`에는 `step`을 넣지 않는다. 선택 `price`가 없으면 해당 단계의 가정 체결가를 사용한다. `mark`는 양수 `price`가 필수다. 선택 `at`은 날짜 또는 시각이며 순서를 거꾸로 만들 수 없다. 경로는 마지막 평가를 확인할 수 있게 끝낸다.

```json
{
  "label": "상승 가정",
  "events": [
    {"kind": "buy", "step": 1},
    {"kind": "sell", "step": 1},
    {"kind": "mark", "price": "120"}
  ]
}
```

비율은 20/30/50의 여섯 순열이다. 조합 ID 예: `b20-30-50_s20-30-50`. 정수주 수량은 내림하여 계산하되 통화 표시 반올림과 혼동하지 않는다. 원 계산은 Decimal이며 통화 표시만 KRW 0자리·USD 2자리다. 수수료·세금·환전은 현재 계산에 포함하지 않는다. 1.5%/6% 제한이나 경로 발생확률을 추가하지 않는다.

`calculate`는 원본 36조합을 보존한다. 같은 경로 결과는 `duplicate_of`로 묶고, 채택 가능한 조합의 파레토 후보에서 `upside`, `defensive`, `balanced`를 고른다. 균형 대표는 경로별 최선 대비 정규화한 손익 차이의 최댓값을 최소화한다. 임의 확률 가중치는 없다. `needs_input`이 남으면 계산/확정을 완료했다고 표시하지 않는다.

실제 완료된 단계의 비율은 고정하고 남은 슬롯은 재비교할 수 있다. 기존 사이클의 통화·시작 예산·시작 보유·계좌를 다른 값으로 재채택하지 않는다. 실제 거래 이후 계산은 저장된 체결을 먼저 반영하고 이미 실행한 단계는 다시 가정 체결하지 않는다.

## HTTP와 MCP

기존 `POST /api/analysis-rpc`의 `{op,arguments}` 형식을 사용한다. loopback Host/Origin·세션 토큰·포트별 브라우저 확인 쿠키를 검증한다. 브라우저 승인/실제 거래 입력을 MCP에 노출하지 않는다.

| RPC | 입력 핵심 | 권한/결과 |
|---|---|---|
| `cycle_context` | `stock_key`, 선택 `account_alias` | 저장 예산·보유·차트 기준 반환 |
| `cycle_list` | 선택 `stock_key` | `{cycles:[…]}` |
| `cycle_read` | `cycle_id` | `{cycle,job,links}` |
| `cycle_agent_input` | `cycle_id` | 고정 snapshot·차트·작도·보고서·논의·실제 거래. 잘라낸 배열 수 표시 |
| `cycle_calculate` | `plan`, 선택 `cycle_id` 또는 `fills`/`adopted_combination_id` | 저장 없음. `cycle_id` 사용 시 저장 실제 거래와 채택 조합을 사용 |
| `cycle_request_draft` | `stock_key`, 선택 계좌/예산/기존 `cycle_id`+`expected_revision`/`reentry_from` | 브라우저 전용. 초안 생성 작업 시작 |
| `cycle_save_draft` | `cycle_id`, `expected_revision`, `plan` | 브라우저 또는 collaboration 역할. 새 불변 초안 버전 |
| `cycle_approve` | `cycle_id`, `draft_version`, `combination_id`, `expected_revision` | 브라우저 전용. 저장 근거 검증 후 정확한 버전 채택 |
| `cycle_fill` | `cycle_id`, `expected_revision`, `fill` | 브라우저 전용. 실제 거래 append |

MCP 이름은 `analysis_cycles`, `analysis_cycle_read`, `analysis_cycle_input`, `analysis_cycle_calculate`, `analysis_cycle_save_draft`다. `analysis_cycle_calculate(plan,fills?,cycle_id?,adopted_combination_id?)`는 순수 비교다. cycle_id가 있으면 저장 체결/채택 조합을 사용하며, 제공 fills만 있고 채택 조합이 없으면 완료 비율의 채택 가능성을 확정하지 않는다. Agent는 `analysis_cycle_input`을 읽은 뒤 예상 revision과 함께 **초안만** 저장한다. budget·initial_holding·account_alias·currency를 Agent가 바꿀 수 없다. risk 역할은 사이클 list/read만 가능하다.

실제 `fill`은 `{kind:'buy'|'sell'|'stop', quantity:양의정수, price:양수문자열, at:날짜또는시각, step?:정수, note?:문자열}`다. buy/sell에는 순서에 맞는 step이 필수다. 과잉 수량·현금 부족·중복/순서 위반·종료 사이클의 재매수는 거절한다. 기록된 실제 수량은 계획에서 차이가 있어도 원문을 유지한다.

## 자동 초안 실행과 보존

버튼은 로컬 `DraftJobs` 작업을 시작한다. 기존 Codex 로그인과 모델 설정을 사용하여 격리 임시 폴더에서 구조화 JSON을 생성한다. 사용자가 버튼을 누른 실행이며 도구·셸·앱·검색·프로젝트 지시 상속은 꺼져 있다. 모델 호출 외에 시장/계좌를 새로 조회하거나 주문·알림을 실행하지 않는다. 반환값은 원래 계좌 입력에 모델의 계획 필드만 합친 뒤 동일 core 검증을 거친다.

작업 상태는 `idle`, `queued`, `running`, `completed`, `failed`, `interrupted`다. 사용량/로그인/생성 실패는 상태로 남기며 성공인 것처럼 가격을 만들어 채우지 않는다. 실행 중 사용자가 초안을 고치면 예상 revision 충돌로 모델 결과 저장을 거절한다. 자동 재시도·자동 승인·자동 체결은 없다.

저장은 기존 분석 DB의 `trade_cycles`, `trade_cycle_drafts`, `trade_cycle_events`, `trade_cycle_jobs`를 사용한다. draft와 adoption/fill 이벤트는 append와 해시 검증, head는 revision 비교를 사용한다. `source`에는 `analysis_key`, `snapshot_id`, `snapshot_sha256`, `identity`, `as_of`, `test_record`가 보존된다. 조회에는 최신 draft, 정확한 adopted 버전, 최초 `original_plan`, 실제 `fills`, `state`, `next_steps`, 원래/현재 경로 결과가 구분되어 반환된다. 과거 분석·작도·시나리오 감시 기록을 덮어쓰지 않는다.

## 완료 종가 알림

기존 일일 시장·계좌 갱신 뒤 저장 자료만 읽어 판정한다. 승인한 다음 단계 중 명시된 고정 `price` 조건만 새 사이클 사건을 만든다. 고정 가격의 최종 손절 조건이 유지되는 동안 다른 분할 사건은 보류한다. `trailing`/`ma5`/`ma10`은 기존 해당 경고에 계획 비율·예정 수량·비교조건을 첨부하며 별도의 같은 조건 사건을 만들지 않는다. 기존 0.5ATR 접근 경고와 계획 조건 충족은 구분한다. 이 문구는 예정 계획이며 실제 체결이 아니다.

계좌/가격 기준일·완료세션·종목/가격 기준 불일치와 과거 처리 종가 변경은 보류한다. 기존 전송원장의 중복억제·전송 불명 상태·재시도 제한을 재사용한다. 미채택 draft 저장은 알림을 재무장하지 않는다. 테스트 기록은 운영 발송에서 제외하고 계좌 별칭·평단은 본문에 노출하지 않는다. cycle 저장자료 오류로 기존 가격 경고까지 막지 않는다. 새로운 알림 테이블은 `trade_cycle_alert_states`, `trade_cycle_alert_events`이며 실제 fill 테이블에는 쓰지 않는다.

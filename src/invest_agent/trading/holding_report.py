"""Human-readable account, cash and holding facts for the existing report."""
import html
from .report_format import money
from .report_names import company_label


def render(result):
    if 'cash_assets' not in result:return []
    def text(value):return '미확정' if value is None else html.escape(str(value)).replace('|','&#124;').replace('\n',' ')
    base_currency = result.get('base_currency')
    def amount(value, currency=None):return money(value, currency or base_currency)
    def pct(value):return '미확정' if value is None else f'{float(value)*100:.2f}%'
    def table(headers,rows):
        return ['| '+' | '.join(headers)+' |','| '+' | '.join('---' for _ in headers)+' |',
                *['| '+' | '.join(text(v) for v in row)+' |' for row in rows],'']
    coverage=result.get('stop_risk_coverage',{})
    out=['## 계좌 요약','',f"계좌 관측 시각: {text(result.get('as_of'))} · 기준통화: {text(result.get('base_currency'))}",
         '평가액과 보호선까지의 참조 위험액을 구분합니다. 기본비중 20%는 비교값이며 절대 상한이 아닙니다.','']
    out+=table(['항목','결과'],[
        ['위험자산 평가액',amount(result.get('risk_asset_value_base'))],
        ['현금성 자산',amount(result.get('cash_equivalent_value'))],
        ['관리 기준자산',amount(result.get('managed_equity_base'))],
        ['검증된 매수가능현금',amount(result.get('verified_investable_cash'))],
        ['현금 비중',pct(result['cash_assets'].get('cash_weight'))],
        ['보유 위험자산 수',result.get('risk_asset_count')],
        ['stop 위험 계산 coverage',f"{coverage.get('calculated',0)} / {coverage.get('total',result.get('risk_asset_count',0))} 종목"],
        ['계산 가능한 stop 위험 합계',amount(result.get('calculated_stop_risk_amount'))],
        ['계좌 전체 stop 위험률',pct(result.get('account_stop_risk_pct'))],
        ['전체 위험 계산 완전성',result.get('aggregate_complete',False)],
        ['계좌 위험 한도 판정',result.get('limit_evaluation_state','unavailable')],
        ['한도 미확정 사유',result.get('limit_reason')]])
    cash=result['cash_assets']
    out+=['## 현금성 자산','', 'CMA·원화·외화 현금은 이곳에 합산하며 종목별 추세 판단을 붙이지 않습니다. 평가액은 매수가능현금과 다릅니다.','']
    out+=table(['구성','통화','원금액','환율','환산액','자료시각','결측'],[
        [r['kind'],r['currency'],amount(r['original_amount'],r['currency']),r['fx_rate'],amount(r['value_base']),r['as_of'],r.get('reason') or '없음'] for r in cash['components']])
    out += [f"합계: {amount(cash['cash_equivalent_value'])} · 계산 가능한 구성요소 소계: {amount(cash['calculated_cash_component_subtotal'])} · 평가 완전성: {cash['valuation_complete']}",
            '중복 여부가 미확인인 소계는 완전한 현금 또는 계좌 자산으로 사용하지 않습니다.']
    for r in cash['reconciliation']:
        out += [f"- 정합성: {text(r['state'])} · CMA/현금 중복: {text(r.get('cash_and_CMA_overlap'))} · 근거: {text(r.get('basis'))} · 사유: {text(r.get('reason'))}"]
    out+=['','## 보유 주식 상세 점검','']
    states={'normal':'현재 계산 범위의 뚜렷한 약화 경고 없음','weakness_warning':'약화 관측 — 사용자 검토',
            'adopted_protection_breached':'채택 보호 기준 이탈','unavailable':'판독 불가/미확정',
            'maintained':'채택 기준 유지','approaching':'보호선 접근','breached':'참조가격이 보호선 이하'}
    for p in result['positions']:
        name=company_label(p)
        out += [f'### {text(name)}','']
        currency = p.get('currency')
        adopted=p.get('adopted_stop')
        rule=adopted.get('rule') if adopted else None
        rule_label=f"{rule['line_type']}{rule['period']} {rule['timeframe']} / {rule['breach_basis']}" if rule else f"고정 참조가격 {amount(adopted.get('price'),currency)}" if adopted else '사용자 미채택'
        near=p.get('proximity',{})
        out+=table(['항목','결과'],[
            ['보유 수량',p.get('quantity')],['평균단가',amount(p.get('average_cost'),currency)],
            ['현재 평가액',amount(p.get('position_market_value'))],['포트폴리오 비중',pct(p.get('portfolio_weight'))],
            ['기본20%와 차이(정보)',pct(p.get('difference_from_reference_weight'))],
            ['사용자 채택 보호 규칙',rule_label],['현재 보호선 참조가격',amount(p.get('adopted_stop_reference_price'),currency)],
            ['위험 계산 기준가격',amount(p.get('current_reference_price'),currency)],['가격 기준/시각',f"{p.get('price_basis')} / {p.get('price_as_of')}"],
            ['보호선까지 가격 거리',amount(p.get('distance_to_stop'),currency)],['보호 기준 확인',states.get(p.get('protection_state'),p.get('protection_state'))],
            ['현재→보호선 참조 위험액',amount(p.get('risk_to_adopted_stop_amount'))],
            ['관리 기준자산 대비 stop 위험률',pct(p.get('risk_to_adopted_stop_pct_of_equity'))],
            ['보호선 기준 평단 대비 총손익',amount(p.get('total_trade_pnl_at_stop'))],
            ['위험 미확정 사유',p.get('risk_reason') or '없음'],
            ['약화 점검',states.get(p.get('weakness_state'),p.get('weakness_state'))],
            ['약화 근거',' / '.join(p.get('weakness_reasons',[])) or '추가 근거 없음'],
            ['ATR 기간 / 배수',f"{text(near.get('atr_period'))} / {text(near.get('warning_atr_multiple'))}"],
            ['보호선까지 ATR 거리',near.get('distance_to_stop_atr')],
            ['ATR 접근 경고',states.get(near.get('proximity_state'),near.get('proximity_state'))],
            ['ATR 결측/미승인 사유',near.get('proximity_reason')],
            ['종목 위험 한도 판정',p.get('limit_evaluation_state')]])
        out += [text(p.get('execution_note')),'','**LLM 보호 규칙 후보:** 별도 LLM 해석의 동일 종목 아래 표시합니다. 권고가 있어도 사용자 채택 전 위험 계산은 미확정입니다.','']
        c=p.get('technical_context',{})
        out += [f"기술 자료: {text(c.get('as_of'))} · 가격 기준 {text(c.get('price_basis'))} · 일봉 {len(c.get('daily_bars',[]))} / 완료 주봉 {len(c.get('weekly_bars',[]))}개 · 결측 {text(c.get('missing'))}",'']
        out+=table(['스페란데오 기간','단계','구조 상태'],[
            [w, v.get('stage'),v.get('data_state') or v.get('status') or v.get('reason')] for w,v in (c.get('sperandeo') or {}).get('windows',{}).items()])
        out+=table(['이평선','시간축','현재값','1봉 기울기'],[
            [f"{r['line_type']}{r['period']}",r['timeframe'],amount(r['value'],currency),amount(r['slope_one_bar'],currency)] for r in c.get('moving_averages',[])])
        out += [f"테이버 위치: {text((c.get('taver') or {}).get('primary_ma_state'))} · RS: {text((c.get('relative_strength') or {}).get('value'))} · 52주 위치: {text(c.get('range_52w'))}",'']
    out+=['## 미입력·미승인 항목','',
          f"- 보호 규칙 미채택: {result.get('positions_without_protection','미확정')}종목",
          '- 위험 한도·ATR 경고 기간/배수: 사용자 승인 출처가 있는 설정만 활성화합니다.',
          '- 가격·환율·기술 결측은 해당 종목과 현금 표에 표시합니다.',
          '- 검증된 매수가능현금이 없으면 수량 계산에 현금성 자산 평가액을 대신 넣지 않습니다.','']
    return out

"""Offline synthetic plans and portfolio arithmetic; never reads a real account."""
import argparse
from copy import deepcopy
from html import escape
import json
from pathlib import Path

from invest_agent.scenarios.portfolio_scenarios import compare
from invest_agent.scenarios.risk_adapter import assemble, project_plan, shared_hash
from invest_agent.trading.report_format import money


def inputs():
    root = Path(__file__).resolve().parent
    data = json.loads((root/'synthetic_risk_portfolio.json').read_text(encoding='utf-8'))
    plan = json.loads((root/'synthetic_collaboration-v1.json').read_text(encoding='utf-8'))['plan']
    frozen = {'id': 'sn_synthetic', 'stock_key': 'US:ALPHA', 'chart': {'price_basis': 'raw'}}
    record = {'id': 'it_synthetic', 'case_id': 'an_synthetic', 'kind': 'plan_version',
              'plan_id': 'pl_synthetic', 'plan_version': 1, 'plan': plan,
              'plan_sha256': shared_hash(plan), 'snapshot_id': frozen['id'],
              'snapshot_sha256': shared_hash(frozen), 'identity': {'market': 'US',
              'symbol': 'ALPHA', 'stable_id': 'SYNTHETIC-US', 'currency': 'USD', 'price_basis': 'raw'}}
    envelope = {'contract_version': 'collaboration.v1', 'analysis_key': 'an_synthetic',
                'mode': 'hypothetical', 'plan_record': record, 'plan_id': record['plan_id'],
                'plan_version': 1, 'plan_sha256': record['plan_sha256'], 'snapshot': frozen,
                'snapshot_id': frozen['id'], 'snapshot_sha256': record['snapshot_sha256'],
                'adoption': None, 'blockers': ['scenario.s1.fx_to_base', 'scenario.s1.cost',
                                             'condition.c2.manual_assessment_required']}
    snapshot = data['snapshot']
    snapshot['positions'][1].update(account='synthetic-us', stable_id='SYNTHETIC-US')
    snapshot['cash'][1]['account'] = 'synthetic-us'
    assumptions = {'id': 'synthetic-calculation', 'version': 1,
                   'horizon': '2026-10-30T16:00:00+09:00', 'fx_to_base': {'KRW': '1', 'USD': '1100'},
                   'cost_policy': 'excluded', 'allocation_basis': 'initial', 'path_observations': {},
                   'fills': {'c1': {'path_date': '2026-10-06', 'at': '2026-10-06T22:30:00+09:00',
                                    'assumption': 'synthetic fill at separately assumed 89 USD'}}}
    for day in ('2026-10-06', '2026-10-30'):
        assumptions['path_observations'][day] = {'snapshot_id': 'synthetic-'+day,
            'as_of': day+'T06:00:00+09:00', 'currency': 'USD', 'frame': '1d', 'price_basis': 'raw',
            'complete': True, 'session': day, 'expected_session': day, 'max_age_seconds': 86400}
    return envelope, snapshot, assumptions


def run(output):
    output = Path(output).resolve()
    root = Path(__file__).resolve().parents[1]
    if output == root or root in output.parents:
        raise ValueError('output_must_be_outside_repository')
    if output.exists():
        raise ValueError('output_must_be_new')
    envelope, snapshot, assumptions = inputs()
    original = deepcopy((envelope, snapshot, assumptions))
    projection = project_plan(envelope, snapshot, scenario_id='s1', assumptions=assumptions)
    scenario = assemble([projection], assumptions)
    scenario['prices']['demo/KR'] = {'price': '800', 'horizon': assumptions['horizon'],
                                    'source_ref': 'explicit-synthetic-KR-assumption'}
    result = compare(snapshot, scenario)
    missing = deepcopy(snapshot)
    missing['fx'].pop('USD')
    incomplete = compare(missing, scenario)
    assert (envelope, snapshot, assumptions) == original
    output.mkdir(parents=True, exist_ok=False)
    payloads = {'plan-input.json': envelope, 'account.json': snapshot, 'assumptions.json': assumptions,
                'projection.json': projection, 'scenario.json': scenario,
                'result.json': result, 'missing-fx-result.json': incomplete}
    for name, payload in payloads.items():
        (output/name).write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    rows = ''.join('<tr><td>'+escape(label)+'</td><td>'+money(value, 'KRW')+'</td></tr>'
                   for label, value in [('기준 합성 자산', result['baseline']['total']),
                     ('대응 없음', result['no_response']['total']), ('가정 대응 있음', result['response']['total']),
                     ('기준 USD 환율 결측 시 전체 자산', incomplete['no_response']['total'])])
    links = ''.join(f'<li><a href="{name}">{name}</a></li>' for name in payloads)
    html = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>합성 계획 · 계좌 시나리오</title><style>body{{font:16px/1.7 system-ui;max-width:900px;margin:32px auto;padding:0 20px}}td,th{{padding:8px 18px;text-align:left;border-bottom:1px solid #ccc}}table{{border-collapse:collapse}}code{{overflow-wrap:anywhere}}</style>
<h1>합성 계획 · 계좌 시나리오</h1><p>가상 종목·계좌·가격·환율. 실제 사용자 판단, 실계좌, 현재 시세가 아닙니다.</p>
<p>고정 초안 → 명시 가정 → Python 계산 → 근거 확인. 계획 모드는 hypothetical이며 채택·주문을 실행하지 않습니다.</p>
<p>기준 입력: 2026-10-05 · 가정 평가일: 2026-10-30. 날짜는 합성 경로이며 실제 거래소 달력이나 미래 예측이 아닙니다.</p>
<table><thead><tr><th>구분</th><th>KRW</th></tr></thead><tbody>{rows}</tbody></table>
<p>ALPHA 10주 중 최초 배정의 절반을 가정 대응합니다. 종가 조건은 90.00 USD 미만,
가정 관측가격과 별도로 지정한 체결가격은 각각 89.00 USD, 최종 평가가격은 80.00 USD입니다.
기준 환율 1,000 KRW/USD, 가정 환율 1,100 KRW/USD, 비용 제외를 명시했습니다.</p>
<p>정성적 성장 논리 훼손은 수치 판정을 하지 않습니다. 원 계획의 미정은 보존하고 별도 계산 가정을 적용합니다.
환율이 빠지면 총액을 비우며 부분 합계를 전체 자산으로 표시하지 않습니다.</p>
<h2>계산 근거</h2><ul>{links}</ul></html>'''
    (output/'report.html').write_text(html, encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run(args.output)
    print('Synthetic portfolio report created; no account, network or LLM calls.')


if __name__ == '__main__':
    main()

"""Offline, synthetic review of entry stages and base false positives."""
import argparse
from copy import deepcopy
from html import escape
import json
from math import pi, sin
from pathlib import Path

from invest_agent.research.synthetic_watch import bars, vcp
from invest_agent.trading import base_structure, momentum_entry, momentum_watch


def fixture():
    """Seven invented cases; dates are daily indices, not exchange sessions."""
    slide = bars()
    selloff = bars()
    for i, bar in enumerate(slide[-40:]):
        price = 110 - .35 * i + .7 * (i % 5)
        bar.update(open=price, close=price, high=price + .8, low=price - .8)
    for i, bar in enumerate(selloff[-40:]):
        price = 100 + 16 * sin(pi * i / 39)
        bar.update(open=price, close=price, high=price + 1, low=price - 1)
    return {
        'source': 'synthetic',
        'calendar': 'invented_daily_indices_not_exchange_sessions',
        'cases': [
            {'id': 'forming', 'label': '돌파 전 관찰', 'bars': vcp()[:-1], 'market': False},
            {'id': 'entry', 'label': '거래량과 돌파 조건 충족', 'bars': vcp(), 'market': True},
            {'id': 'market_missing', 'label': '같은 모양 · 시장 근거 결측', 'bars': vcp(), 'market': None},
            {'id': 'bottom', 'label': '하락 후 바닥 횡보', 'bars': bars(), 'market': False},
            {'id': 'pullback', 'label': '상승 후 눌림 횡보', 'bars': bars('pullback'), 'market': False},
            {'id': 'slide', 'label': '반례 · 완만한 지속 하락', 'bars': slide, 'market': False},
            {'id': 'selloff', 'label': '반례 · 급락 중 이전 저점 복귀', 'bars': selloff, 'market': False},
        ],
    }


def analyze(snapshot):
    if snapshot.get('source') != 'synthetic':
        raise ValueError('synthetic_source_required')
    cases = snapshot['cases']
    if len({case['id'] for case in cases}) != len(cases):
        raise ValueError('duplicate_case_identity')
    results = []
    for case in cases:
        market = {'state': 'unavailable' if case['market'] is None else 'available',
                  'supportive': bool(case['market'])}
        strict = momentum_entry.analyze(case['bars'], 95, market)
        watch = momentum_watch.analyze(case['bars'], strict)
        base = base_structure.analyze(case['bars'], market=market)
        phase = watch.get('phase', 'excluded')
        if base['signals'] and phase == 'excluded':
            phase = 'watch'
        results.append({'id': case['id'], 'label': case['label'],
                        'as_of': case['bars'][-1]['date'], 'phase': phase,
                        'entry': strict, 'watch': watch, 'base': base})
    return {'source': 'synthetic', 'version': '2.2.0',
            'authority': 'review_only_no_adoption_or_orders',
            'rs_basis': 'fixed_synthetic_95_not_market_ranking', 'cases': results}


def render(result):
    rows = []
    for case in result['cases']:
        signals = case['watch']['signals'] + case['base']['signals']
        reasons = case['entry'].get('rejections', [])
        if case['id'] in ('slide', 'selloff'):
            reasons = sorted({r for w in case['base']['windows'] for r in w['rejections']})
        pending = sorted({item for s in signals for item in s.get('pending', []) + s.get('warnings', [])})
        rows.append('<tr>' + ''.join('<td>' + escape(str(v)) + '</td>' for v in (
            case['label'], case['as_of'], case['phase'],
            bool(case['entry'].get('candidate')), ' / '.join(pending or reasons))) + '</tr>')
    return '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>합성 관찰·진입 검토</title><style>
body{font:16px/1.6 system-ui,sans-serif;max-width:1180px;margin:40px auto;padding:0 24px;color:#17212b}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #ced6de;padding:12px;text-align:left;vertical-align:top}
th{background:#eef3f7}td:last-child{max-width:520px;overflow-wrap:anywhere}a{color:#075b9a}
</style><h1>관찰은 진입 조건 충족과 다릅니다</h1>
<p>v2.2.0 · 가상 자료 7개 사례 · API·계좌·LLM 호출 없음</p>
<p>기준일은 가상 날짜입니다. RS 95와 시장 상태는 합성 입력이며 실제 시장의 순위나 현재성이 아닙니다.
보호선 후보는 자동 채택되지 않으며 주문을 만들지 않습니다.</p>
<table><thead><tr><th>합성 사례</th><th>자료 기준일</th><th>검토 단계</th><th>진입 조건 충족</th><th>남은 조건·거부 이유</th></tr></thead><tbody>''' + ''.join(rows) + '''</tbody></table>
<p>entry는 코드에 정의된 조건을 충족했다는 뜻입니다. 매수 권고·수익성 검증이 아닙니다.
watch/ready는 관찰 또는 돌파 대기이며, excluded는 이 합성 사례가 해당 조건을 충족하지 않았다는 뜻입니다.</p>
<p><a href="review.json">전체 계산 근거</a> · <a href="inputs.json">합성 입력</a></p></html>'''


def run(output):
    output = Path(output).resolve()
    # Keep generated artifacts out of the source tree. Never overwrite a folder.
    source_root = Path(__file__).resolve().parents[3]
    if output == source_root or source_root in output.parents:
        raise ValueError('output_must_be_outside_source_tree')
    snapshot = fixture()
    result = analyze(deepcopy(snapshot))
    output.mkdir(parents=True, exist_ok=False)
    for name, data in [('inputs.json', snapshot), ('review.json', result)]:
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'report.html').write_text(render(result), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({'source': result['source'], 'cases': len(result['cases']),
                      'entry_cases': sum(bool(c['entry'].get('candidate')) for c in result['cases'])}))
    return 0

"""Offline synthetic screening and holding-risk report; no network or credentials."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from invest_agent.trading.runner import PipelineRunner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance', type=Path, required=True)
    args = parser.parse_args()
    root = args.instance.resolve()
    package = Path(__file__).resolve().parents[1]
    if root.is_relative_to(package) or root.exists():
        parser.error('Choose a new private directory outside the source package')
    now = datetime.now(timezone.utc)
    days = []
    cursor = now.date() - timedelta(days=1)
    while len(days) < 380:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days.reverse()  # Synthetic weekdays; not an exchange-calendar data claim.
    bars = [dict(date=d, open=100+i/10, high=102+i/10, low=99+i/10,
                 close=101+i/10, volume=1_000_000) for i, d in enumerate(days)]
    series = dict(symbol='SYNTH', market='US', currency='USD', benchmark='SYNTH_INDEX',
                  bars=bars, price_basis='synthetic', raw_exchange_code='NMS',
                  market_cap=3_000_000_000, market_cap_currency='USD', market_cap_as_of=days[-1])
    market = dict(schema_version=1, source='synthetic', fetched_at=now.isoformat(),
                  series=[series], sessions={'US': days},
                  benchmarks={'SYNTH_INDEX': {'bars': bars}},
                  profile={'US': {'rs_period': 63, 'ma_period': 200}}, errors=[],
                  coverage={'scope': 'synthetic_demo', 'requested': 1, 'received': 1,
                            'scope_errors': [], 'market_wide': False})
    account = dict(schema_version=1, source='synthetic', as_of=now.isoformat(), max_age_hours=24,
                   base_currency='USD', fx_to_base={'USD': '1'}, accounts=[
                       dict(alias='synthetic-account', cash={'USD': '5000'}, positions=[
                           dict(symbol='SYNTH', quote_symbol='SYNTH', market='US', benchmark='SYNTH_INDEX',
                                currency='USD', quantity='10', price=str(bars[-1]['close']), average_cost='100',
                                adopted_stop={'price': '130', 'adoption_ref': 'synthetic-demo-only',
                                              'price_basis': 'executable_raw'})])])
    config = dict(schema_version=1, market_snapshot='market.json', account_snapshot='account.json',
                  valuation_inputs=[], holding_review_policy={
                      key: {'value': value, 'approved_by_user': True, 'source_ref': 'synthetic-demo-only'}
                      for key, value in [('atr_period', 14), ('warning_atr_multiple', '1'),
                                         ('position_stop_risk_limit_pct', '0.02'),
                                         ('account_total_stop_risk_limit_pct', '0.08')]})
    inputs = root / 'inputs'
    inputs.mkdir(parents=True)
    for name, value in [('market', market), ('account', account), ('morning', config)]:
        (inputs / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    result = PipelineRunner(root).run_morning(inputs / 'morning.json', now.date().isoformat())
    print(json.dumps({'synthetic': True, 'state': result['state'], 'run_id': result['run_id'],
                      'report_directory': str(root / 'reports')}, ensure_ascii=False, indent=2))
    if result['state'] not in ('succeeded', 'partial'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()

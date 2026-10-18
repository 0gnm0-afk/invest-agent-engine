"""Regression coverage for common DART annual EPS/dividend supplements."""
import copy
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from financial_dart_supplement import (
    annual_supplement_candidates, annual_supplement_years, collect_annual_supplements)
from financial_sources import SourceError

TODAY = date(2026, 10, 4)


def raw(year, values=('100', '90', '80'), corp='001'):
    base = dict(corp_code=corp, rcept_no=f'{year + 1}0315000001', stlm_dt=f'{year}-12-31')
    return dict(status='000', list=[
        dict(base, se='(연결)주당순이익(원)', stock_knd='-', **dict(zip(('thstrm', 'frmtrm', 'lwfr'), values))),
        dict(base, se='주당 현금배당금(원)', stock_knd='보통주', thstrm='30', frmtrm='25', lwfr='20'),
    ])


def envelope(year, payload=None):
    return dict(year=year, quarter=4, end=f'{year}-12-31', endpoint='alotMatter',
                prefix=f'{year}-Q4', source_id=f'{year}-Q4-dividends.json', raw=payload or raw(year))


def source(*reports):
    return dict(provider='DART', identity=dict(corp_code='001', acc_mt='12'), reports=[],
                annual_supplements=list(reports))


class FakeCollector:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def dart(self, endpoint, name, **params):
        self.calls.append((endpoint, name, params))
        response = self.responses.get(int(params['bsns_year']), raw(int(params['bsns_year'])))
        if isinstance(response, Exception):
            raise response
        return response


class SupplementCandidates(unittest.TestCase):
    def test_unclassified_dividend_needs_independent_single_class(self):
        payload=raw(2025);payload['list'][1]['stock_knd']='-'
        s=source(envelope(2025,payload))
        self.assertFalse(any(c['key']=='dividend' for c in annual_supplement_candidates(s,as_of=TODAY)))
        s['reports']=[dict(year=2025,quarter=4,prefix='2025-Q4',shares=dict(status='000',list=[dict(corp_code='001',se=k,istc_totqy='100') for k in ['보통주','합계']]))]
        d=[c for c in annual_supplement_candidates(s,as_of=TODAY) if c['key']=='dividend']
        self.assertEqual([(c['fy'],c['value']) for c in d],[(2025,30)])
        self.assertIn('2025-Q4-shares.json',d[0]['sources'])

    def test_reported_dash_zero_needs_total_and_single_class(self):
        payload=raw(2025);row=payload['list'][1];row['thstrm']='-'
        payload['list'].append(dict(row,se='현금배당금총액(백만원)'))
        s=source(envelope(2025,payload));s['reports']=[dict(year=2025,quarter=4,prefix='2025-Q4',shares=dict(status='000',list=[dict(corp_code='001',se=k,istc_totqy='100') for k in ['보통주','합계']]))]
        def current():return [c for c in annual_supplement_candidates(s,as_of=TODAY) if c['key']=='dividend' and c['fy']==2025]
        self.assertEqual(current()[0]['value'],0);self.assertTrue(current()[0]['confirmed_zero'])
        for bad in ['',None,'100']:
            payload['list'][-1]['thstrm']=bad
            self.assertEqual(current(),[])

    def test_three_columns_have_own_period_and_original_values(self):
        rows = annual_supplement_candidates(source(envelope(2022)), as_of=TODAY)
        eps = {r['fy']: r for r in rows if r['key'] == 'reported_eps'}
        self.assertEqual(set(eps), {2020, 2021, 2022})
        self.assertEqual(eps[2020]['value'], 80)
        self.assertEqual(eps[2020]['reported_value'], '80')
        self.assertEqual(eps[2020]['report_column'], 'lwfr')
        self.assertEqual(eps[2020]['start'], '2020-01-01')
        self.assertEqual(eps[2020]['end'], '2020-12-31')
        self.assertEqual(eps[2020]['sources'], ['2022-Q4-dividends.json'])
        self.assertTrue(eps[2020]['comparative'])
        self.assertEqual(len(rows), 6)

    def test_eps_basis_is_never_invented(self):
        eps = annual_supplement_candidates(source(envelope(2025)), as_of=TODAY)[0]
        self.assertEqual(eps['key'], 'reported_eps')
        self.assertEqual(eps['eps_basis'], 'reported')
        self.assertEqual(eps['scope'], 'consolidated')
        self.assertFalse(eps['accounting_basis_verified'])

    def test_zero_loss_and_thousands(self):
        rows = annual_supplement_candidates(source(envelope(2025, raw(2025, ('0', '(1,234)', '-0.5')))), as_of=TODAY)
        self.assertEqual([r['value'] for r in rows[:3]], [0, -1234, -.5])

    def test_missing_is_not_zero(self):
        rows = annual_supplement_candidates(source(envelope(2025, raw(2025, ('-', 'NaN', '미공시')))), as_of=TODAY)
        self.assertFalse(any(r['key'] == 'reported_eps' for r in rows))

    def test_only_consolidated_eps_and_common_cash_dividend(self):
        payload = raw(2025)
        payload['list'][0]['se'] = '(별도)주당순이익(원)'
        payload['list'][1]['stock_knd'] = '우선주'
        self.assertEqual(annual_supplement_candidates(source(envelope(2025, payload)), as_of=TODAY), [])
        payload['list'][0]['se'] = '주당순이익(원)'
        payload['list'][1].update(stock_knd='보통주', se='주당 주식배당(주)')
        self.assertEqual(annual_supplement_candidates(source(envelope(2025, payload)), as_of=TODAY), [])

    def test_wrong_company_rejected(self):
        with self.assertRaisesRegex(ValueError, 'IDENTITY'):
            annual_supplement_candidates(source(envelope(2025, raw(2025, corp='other'))), as_of=TODAY)

    def test_wrong_end_and_bad_or_future_receipt_rejected(self):
        for changes in ({'stlm_dt': '2025-06-30'}, {'rcept_no': '20269999000001'},
                        {'rcept_no': '20270301000001'}, {'rcept_no': '20250301000001'}):
            payload = raw(2025)
            for row in payload['list']:
                row.update(changes)
            self.assertEqual(annual_supplement_candidates(source(envelope(2025, payload)), as_of=TODAY), [])

    def test_conflicting_and_old_filings_preserved_for_model_selection(self):
        newer = raw(2022, ('105', '90', '80'))
        for row in newer['list']:
            row['rcept_no'] = '20240401000001'
        payload = raw(2022)
        payload['list'].append(dict(payload['list'][0], thstrm='101'))
        candidates = annual_supplement_candidates(source(envelope(2022, payload), envelope(2022, newer)), as_of=TODAY)
        self.assertEqual({r['value'] for r in candidates if r['fy'] == 2022 and r['key'] == 'reported_eps'}, {100, 101, 105})

    def test_raw_input_is_immutable(self):
        data = source(envelope(2025))
        before = copy.deepcopy(data)
        annual_supplement_candidates(data, as_of=TODAY)
        self.assertEqual(data, before)


class SupplementCollection(unittest.TestCase):
    def test_six_years_need_only_two_three_column_requests(self):
        collector = FakeCollector()
        result = collect_annual_supplements(collector, source(), as_of=TODAY)
        self.assertEqual([c[2]['bsns_year'] for c in collector.calls], ['2025', '2022'])
        self.assertTrue(all(c[2]['corp_code'] == '001' and c[2]['reprt_code'] == '11011' for c in collector.calls))
        candidates = annual_supplement_candidates(result, as_of=TODAY)
        self.assertEqual({r['fy'] for r in candidates if r['key'] == 'reported_eps'}, set(range(2020, 2026)))
        self.assertEqual({r['fy'] for r in candidates if r['key'] == 'dividend'}, set(range(2020, 2026)))
        self.assertEqual(result['annual_supplement_missing'], [])

    def test_existing_raw_reused_and_no_input_mutation(self):
        data = source(envelope(2025))
        before = copy.deepcopy(data)
        collector = FakeCollector()
        result = collect_annual_supplements(collector, data, as_of=TODAY)
        self.assertEqual([c[2]['bsns_year'] for c in collector.calls], ['2022'])
        self.assertEqual(data, before)
        self.assertEqual(result['annual_supplements'][0], data['annual_supplements'][0])

    def test_explicit_target_does_not_fetch_other_years(self):
        collector = FakeCollector()
        collect_annual_supplements(collector, source(), years=[2021], as_of=TODAY)
        self.assertEqual([c[2]['bsns_year'] for c in collector.calls], ['2021'])

    def test_missing_column_gets_targeted_fallback(self):
        payload = raw(2025, ('100', '90', '-'))
        collector = FakeCollector({2025: payload})
        collect_annual_supplements(collector, source(), years=[2023, 2024, 2025], as_of=TODAY)
        self.assertEqual([c[2]['bsns_year'] for c in collector.calls], ['2025', '2023'])

    def test_no_data_and_optional_network_errors_preserve_core(self):
        data = source()
        data['reports'] = [{'sentinel': 'original'}]
        collector = FakeCollector({2025: {'status': '013', 'message': 'no data'}, 2024: SourceError('HTTP_503:2024-Q4-dividends.json')})
        result = collect_annual_supplements(collector, data, years=[2024, 2025], as_of=TODAY)
        self.assertEqual(result['reports'], data['reports'])
        self.assertEqual({r['error'] for r in result['annual_supplement_errors']}, {'DART_013', 'HTTP_503:2024-Q4-dividends.json'})
        self.assertEqual(len(result['annual_supplement_missing']), 2)
        self.assertEqual(result['annual_supplements'][0]['raw']['status'], '013')

    def test_bad_response_company_aborts_before_next_request(self):
        collector = FakeCollector({2025: raw(2025, corp='other')})
        with self.assertRaisesRegex(ValueError, 'IDENTITY'):
            collect_annual_supplements(collector, source(), as_of=TODAY)
        self.assertEqual(len(collector.calls), 1)

    def test_latest_available_fiscal_year_defines_six_years(self):
        data = source()
        data['reports'] = [dict(year=2024, quarter=4, end='2024-12-31', fs={'status': '000'})]
        self.assertEqual(annual_supplement_years(data, as_of=date(2026, 1, 5)), list(range(2019, 2025)))

    def test_invalid_period_and_non_december_not_guessed(self):
        for years in ([2026], [2014], [True]):
            with self.assertRaisesRegex(ValueError, 'INVALID_YEAR'):
                collect_annual_supplements(FakeCollector(), source(), years=years, as_of=TODAY)
        data = source()
        data['identity']['acc_mt'] = '03'
        with self.assertRaisesRegex(ValueError, 'PERIOD_MAPPING'):
            collect_annual_supplements(FakeCollector(), data, as_of=TODAY)

    def test_complete_raw_inputs_never_refetch(self):
        collector = FakeCollector()
        collect_annual_supplements(collector, source(envelope(2025), envelope(2022)), as_of=TODAY)
        self.assertEqual(collector.calls, [])


if __name__ == '__main__':
    unittest.main()

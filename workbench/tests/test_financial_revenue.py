"""Revenue accounting-basis, ambiguous-row and exact-period selection contracts."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from financial_revenue import revenue_metadata, select_dart_revenue, select_sec_revenue


def dart_row(name, value, tag='-표준계정코드 미사용-', ytd=None, **extra):
    return dict(account_id=tag, account_nm=name, thstrm_amount=str(value),
                thstrm_add_amount=str(ytd) if ytd is not None else '', currency='KRW', sj_div='IS',
                corp_code='001', rcept_no='20260801000001', bsns_year='2026', reprt_code='11012', **extra)


def sec_row(value, start='2025-01-01', end='2025-12-31', filed='2026-02-01', accn='a'):
    return dict(start=start, end=end, filed=filed, accn=accn, val=value, form='10-K')


def taxonomy(**tags):
    return {tag: {'units': {'USD': values}} for tag, values in tags.items()}


def sec_select(data):
    return select_sec_revenue(data, cutoff='2020-01-01', as_of='2026-10-04')


class DartRevenue(unittest.TestCase):
    def test_preserves_standard_revenue_over_other_alias(self):
        selected = select_dart_revenue([dart_row('매출액', 100, 'ifrs-full_Revenue', 190), dart_row('영업수익', 101)])
        self.assertEqual(selected['row']['thstrm_amount'], '100')
        self.assertEqual(selected['comparison_key'], 'reported_revenue')

    def test_exact_operating_revenue_alias(self):
        selected = select_dart_revenue([dart_row('영업 수익', 100)])
        self.assertEqual(selected['row']['thstrm_amount'], '100')

    def test_substring_components_are_not_total_revenue(self):
        for name in ('기타영업수익', '이자수익', '수수료수익', '투자수익', '영업수익의 증가'):
            with self.subTest(name=name):
                self.assertIsNone(select_dart_revenue([dart_row(name, 100)])['row'])

    def test_ibk_total_precedes_post_impairment_and_insurance(self):
        rows = [dart_row('순이자손익', 2084441000000, 'ifrs-full_InterestRevenueExpense', 4076295000000),
                dart_row('보험수익', 38126000000, 'ifrs-full_InsuranceRevenue', 70928000000),
                dart_row('총영업이익', 2541543000000, ytd=4893967000000),
                dart_row('순영업이익', 2071854000000, ytd=4041454000000)]
        selected = select_dart_revenue(rows)
        self.assertEqual(selected['row']['thstrm_amount'], '2541543000000')
        self.assertEqual(selected['row']['thstrm_add_amount'], '4893967000000')
        self.assertEqual(selected['basis'], 'bank_net_revenue')
        self.assertEqual(selected['account_label'], '매출(순영업수익)')

    def test_bank_components_alone_do_not_become_total(self):
        rows = [dart_row('순이자손익', 100, 'ifrs-full_InterestRevenueExpense'),
                dart_row('보험수익', 20, 'ifrs-full_InsuranceRevenue'), dart_row('순수수료손익', 5),
                dart_row('순영업이익', 90)]
        self.assertIsNone(select_dart_revenue(rows)['row'])

    def test_total_profit_name_requires_banking_evidence(self):
        self.assertIsNone(select_dart_revenue([dart_row('총영업이익', 100)])['row'])

    def test_complete_interest_and_noninterest_are_summed_with_distinct_basis(self):
        selected = select_dart_revenue([dart_row('순이자손익', 100, ytd=180), dart_row('비이자손익', 20, ytd=35)])
        self.assertEqual(selected['row']['thstrm_amount'], 120)
        self.assertEqual(selected['row']['thstrm_add_amount'], 215)
        self.assertEqual(selected['comparison_key'], 'bank_net_interest_plus_noninterest')
        self.assertEqual(len(selected['revenue_components']), 2)

    def test_component_period_mismatch_is_missing(self):
        a, b = dart_row('순이자손익', 100), dart_row('비이자손익', 20)
        b['reprt_code'] = '11014'
        self.assertIsNone(select_dart_revenue([a, b])['row'])

    def test_insurance_revenue_is_explicitly_labeled(self):
        selected = select_dart_revenue([dart_row('보험수익', 50, 'ifrs-full_InsuranceRevenue')])
        self.assertEqual(selected['basis'], 'insurance_revenue')
        self.assertEqual(selected['account_label'], '매출(보험수익)')

    def test_conflicting_duplicate_quarter_or_cumulative_is_missing(self):
        a = dart_row('매출액', 100, 'ifrs-full_Revenue', 200)
        for field, value in (('thstrm_amount', '101'), ('thstrm_add_amount', '201'), ('reprt_code', '11014')):
            with self.subTest(field=field):
                b = dict(a, **{field: value})
                self.assertIsNone(select_dart_revenue([a, b])['row'])

    def test_identical_is_cis_duplicates_are_not_summed(self):
        a = dart_row('매출액', 100, 'ifrs-full_Revenue', 200)
        selected = select_dart_revenue([a, dict(a, sj_div='CIS')])
        self.assertEqual(selected['row']['thstrm_amount'], '100')

    def test_wrong_currency_and_balance_sheet_are_excluded(self):
        a = dart_row('매출액', 100, 'ifrs-full_Revenue')
        self.assertIsNone(select_dart_revenue([dict(a, currency='USD'), dict(a, sj_div='BS')])['row'])


class SecRevenue(unittest.TestCase):
    def test_total_is_not_replaced_by_smaller_contract_component(self):
        data = taxonomy(RevenueFromContractWithCustomerExcludingAssessedTax=[sec_row(100)],
                        Revenues=[sec_row(110, filed='2026-03-01')])
        selected = sec_select(data)
        self.assertEqual(selected[0]['val'], 110)
        data['Revenues']['units']['USD'][0]['filed'] = '2026-02-01'
        self.assertEqual(sec_select(data)[0]['val'], 110)

    def test_net_financial_total_supported(self):
        selected = sec_select(taxonomy(RevenuesNetOfInterestExpense=[sec_row(100)]))
        self.assertEqual(selected[0]['val'], 100)
        self.assertEqual(selected[0]['comparison_key'], 'bank_net_revenue')

    def test_bank_contract_fees_do_not_override_total(self):
        data = taxonomy(RevenuesNetOfInterestExpense=[sec_row(100)],
                        RevenueFromContractWithCustomerExcludingAssessedTax=[sec_row(20, filed='2026-03-01')])
        self.assertEqual(sec_select(data)[0]['val'], 100)

    def test_gross_total_and_net_total_never_add_or_switch_on_later_filing(self):
        data = taxonomy(Revenues=[sec_row(150)],
                        RevenuesNetOfInterestExpense=[sec_row(100, filed='2026-03-01')])
        selected = sec_select(data)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['val'], 150)
        self.assertEqual(selected[0]['comparison_key'], 'reported_revenue')
        self.assertEqual(selected[0]['tag'], 'Revenues')

    def test_gross_to_net_different_periods_keep_different_comparison_keys(self):
        data = taxonomy(Revenues=[sec_row(150)],
                        RevenuesNetOfInterestExpense=[sec_row(100, start='2024-01-01', end='2024-12-31')])
        selected = sec_select(data)
        self.assertEqual(len(selected), 2)
        self.assertEqual({r['comparison_key'] for r in selected}, {'reported_revenue', 'bank_net_revenue'})

    def test_both_complete_components_required_in_same_filing(self):
        a = sec_row(80)
        self.assertEqual(sec_select(taxonomy(InterestIncomeExpenseNet=[a])), [])
        self.assertEqual(sec_select(taxonomy(InterestIncomeExpenseNet=[a], NoninterestIncome=[sec_row(20, accn='b')])), [])
        selected = sec_select(taxonomy(InterestIncomeExpenseNet=[a], NoninterestIncome=[sec_row(20)]))
        self.assertEqual(selected[0]['val'], 100)
        self.assertEqual(selected[0]['comparison_key'], 'bank_net_interest_plus_noninterest')

    def test_quarter_and_ytd_with_same_end_do_not_merge(self):
        a = sec_row(25, start='2025-10-01')
        b = sec_row(100)
        selected = sec_select(taxonomy(RevenuesNetOfInterestExpense=[a, b]))
        self.assertEqual(len(selected), 2)
        self.assertEqual({r['val'] for r in selected}, {25, 100})

    def test_conflicting_latest_fact_is_not_arbitrarily_selected(self):
        data = taxonomy(Revenues=[sec_row(100), sec_row(101)])
        conflict=sec_select(data)
        self.assertEqual(len(conflict),1)
        self.assertIsNone(conflict[0]['val'])
        self.assertIn('충돌',conflict[0]['reason'])
        data['Revenues']['units']['USD'].append(sec_row(102, filed='2026-03-01'))
        self.assertEqual(sec_select(data)[0]['val'], 102)

    def test_duplicate_equal_facts_are_one_fact(self):
        selected = sec_select(taxonomy(Revenues=[sec_row(100), sec_row(100)]))
        self.assertEqual(len(selected), 1)

    def test_future_filed_and_wrong_unit_are_excluded(self):
        data = taxonomy(Revenues=[sec_row(100, filed='2027-01-01')])
        data['Revenues']['units']['CAD'] = [sec_row(99)]
        self.assertEqual(sec_select(data), [])

    def test_metadata_extraction_never_copies_raw_value(self):
        row = sec_select(taxonomy(RevenuesNetOfInterestExpense=[sec_row(100)]))[0]
        metadata = revenue_metadata(row)
        self.assertNotIn('val', metadata)
        self.assertEqual(metadata['basis'], 'bank_net_revenue')


if __name__ == '__main__':
    unittest.main()

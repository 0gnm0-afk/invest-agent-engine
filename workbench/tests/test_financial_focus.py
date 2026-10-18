import copy
import json
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from financial_focus import summarize, ratio, movement
from financial_model import fact, dart_normalize, sec_normalize


def normal(provider='SEC', basic=True, diluted=True):
    currency='USD' if provider=='SEC' else 'KRW'
    result=dict(provider=provider,currency=currency,standard='fixture consolidated',identity={},annual=[],quarterly=[])
    for year in range(2019,2026):
        for q in range(0,5):
            end=f'{year}-12-31' if not q else f'{year}-{q*3:02d}-'+('31' if q in (1,4) else '30')
            start=f'{year}-01-01' if not q else f'{year}-{(q-1)*3+1:02d}-01'
            def cell(value,instant=False,unit=None,**extra):return fact(value,unit=unit or currency,start=None if instant else start,end=end,filed=f'{year+1}-02-01',sources=['official.json'],**extra)
            v={k:cell(x, k in ('liabilities','total_equity','assets')) for k,x in dict(sales=100+year-2019,operating=20+year-2019,income=10+year-2019,liabilities=60,total_equity=40,assets=100,interest_expense=2).items()}
            if basic:v['basic_eps']=cell(2+(year-2019)/10,unit=currency+'/share',eps_basis='basic')
            if diluted:v['eps']=cell(1+(year-2019)/10,unit=currency+'/share',eps_basis='diluted')
            result['annual' if not q else 'quarterly'].append(dict(date=end,label=end,values=v))
    return result

class FocusTests(unittest.TestCase):
    def test_korea_six_metrics_and_five_twelve(self):
        r=summarize(normal('DART'),{})
        self.assertEqual(len(r['summary']),7)
        self.assertEqual((len(r['annual']),len(r['quarterly'])),(5,12))
        self.assertEqual([m['id'] for m in r['performance_metrics']],['sales','operating','income'])
        self.assertEqual(r['quarterly'][-1]['metrics']['debt_ratio']['value'],1.5)
        self.assertNotIn('icr',r['quarterly'][-1]['metrics'])



    def test_added_toss_ratios(self):
        n=normal();v=n['quarterly'][-1]['values']
        v['current_assets']=fact(80,unit='USD',end=v['assets']['end'],filed=v['assets']['filed'])
        v['current_liabilities']=fact(40,unit='USD',end=v['assets']['end'],filed=v['assets']['filed'])
        row=summarize(n,{})['quarterly'][-1]
        self.assertEqual(row['metrics']['current_ratio']['value'],2)
        self.assertAlmostEqual(row['metrics']['net_margin']['value'],16/106)
        v['current_liabilities']['end']='2024-12-31'
        self.assertIsNone(summarize(n,{})['quarterly'][-1]['metrics']['current_ratio']['value'])

    def test_us_both_or_single_eps_never_synthetic(self):
        for basic,diluted,expected in [(True,True,['basic_eps','eps']),(True,False,['basic_eps']),(False,True,['eps']),(False,False,[])]:
            r=summarize(normal(basic=basic,diluted=diluted),{})
            self.assertEqual([m['id'] for m in r['performance_metrics']][3:],expected)
            for key in expected:self.assertEqual(r['quarterly'][-1]['growth'][key]['eps_basis'],'basic' if key=='basic_eps' else 'diluted')
    def test_yoy_not_shifted_after_missing_quarter(self):
        n=normal();n['quarterly']=[r for r in n['quarterly'] if r['date']!='2024-12-31']
        r=summarize(n,{})['quarterly'][-1]
        self.assertIsNone(r['growth']['sales']['value']);self.assertIsNotNone(r['qoq']['sales']['value'])
    def test_negative_transitions_no_forced_rate(self):
        for before,after,label,trend in [(-5,2,'흑자전환','improved'),(5,-2,'적자전환','worsened'),(-5,-2,'적자축소','improved'),(-2,-5,'적자확대','worsened')]:
            n=normal()
            for row in n['annual']:row['values']['income']['value']=after if row['date'].startswith('2025') else before
            r=summarize(n,{})['annual'][-1]['growth']['income']
            self.assertEqual((r['value'],r['display_text'],r['trend']),(None,label,trend))
    def test_equity_warning_only_nonpositive(self):
        for equity in (0,-1,None,1):
            n=normal();n['quarterly'][-1]['values']['total_equity']['value']=equity
            row=summarize(n,{})['quarterly'][-1]
            self.assertEqual(bool(row['warnings']),equity is not None and equity<=0)
            if equity is None or equity<=0:self.assertIsNone(row['metrics']['debt_ratio']['value'])
    def test_icr_does_not_substitute_finance_cost_or_cash_interest(self):
        n=normal();v=n['quarterly'][-1]['values'];v.pop('interest_expense');v['finance_cost']=fact(9);v['cash_interest_paid']=fact(7)
        self.assertNotIn('icr',summarize(n,{})['quarterly'][-1]['metrics'])
    def test_period_currency_and_scope_mismatch(self):
        for field,value in [('start','2025-01-01'),('unit','KRW'),('accounting_basis','separate'),('period_coverage','unknown')]:
            n=normal();n['quarterly'][-1]['values']['interest_expense'][field]=value
            self.assertNotIn('icr',summarize(n,{})['quarterly'][-1]['metrics'])
    def test_units_and_reversed_debt_color(self):
        n=normal();n['annual'][-1]['values']['liabilities']['value']=50
        row=summarize(n,{})['annual'][-1]
        self.assertEqual(row['metrics']['debt_ratio']['trend'],'improved')
        self.assertEqual(row['metrics']['debt_ratio']['change']['unit'],'percentage_point')
    def test_missing_neutral_and_growth_acceleration(self):
        n=normal();n['annual'][-1]['values']['sales']['value']=200
        r=summarize(n,{})['annual'][-1]
        self.assertEqual(r['growth']['sales']['trend'],'improved')
        n['annual'][-1]['values']['sales']['value']=None
        self.assertEqual(summarize(n,{})['annual'][-1]['growth']['sales']['trend'],'neutral')
    def test_partial_coverage_immutable_inputs(self):
        n=normal();n['annual']=n['annual'][-1:];n['quarterly']=[];old=copy.deepcopy(n)
        r=summarize(n,{})
        self.assertEqual(n,old);self.assertEqual(r['coverage_metadata']['annual']['available'],1)
        self.assertEqual(r['summary_frame'],'annual')
    def test_parent_equity_is_not_total(self):
        n=normal();v=n['quarterly'][-1]['values'];v['total_equity'].update(tag='StockholdersEquity',value=20)
        r=summarize(n,{})['quarterly'][-1]
        self.assertEqual(r['values']['total_equity']['value'],40)
        v['assets']['value']=None
        self.assertIsNone(summarize(n,{})['quarterly'][-1]['metrics']['debt_ratio']['value'])
    def test_direct_ratio_requires_definition_unit_and_period(self):
        v=normal()['annual'][-1]['values']
        v['operating_margin']=fact(.42,unit='ratio',start=v['operating']['start'],end=v['operating']['end'],source_type='company_reported',definition='영업이익 ÷ 매출액',definition_verified=True)
        self.assertEqual(ratio(v,'operating_margin')['value'],.42)
        v['operating_margin']['definition_verified']=False
        self.assertAlmostEqual(ratio(v,'operating_margin')['value'],26/106)
    def test_revision_and_eps_basis_comparability(self):
        n=normal();n['annual'][-1]['values']['basic_eps']['revision_basis']='new'
        self.assertIsNone(summarize(n,{})['annual'][-1]['growth']['basic_eps']['value'])

    def test_display_value_colors_in_both_period_modes(self):
        for frame in ('annual','quarterly'):
            for delta in (-10,0,10):
                n=normal()
                for row in n[frame]:
                    for key in ('net_income','dividend','current_assets','current_liabilities'):
                        row['values'][key]=dict(row['values']['sales'],value=100)
                row=n[frame][-1]
                for key in ('liabilities','net_income','dividend','current_assets','current_liabilities'):
                    row['values'][key]['value']+=delta
                out=summarize(n,{})[frame][-1]
                for group in ('value_movements','value_movements_qoq') if frame=='quarterly' else ('value_movements',):
                    for key in ('liabilities','net_income','dividend','current_assets','current_liabilities'):
                        sign=delta*(-1 if key in ('liabilities','current_liabilities') else 1)
                        expected='improved' if sign>0 else 'worsened' if sign<0 else 'neutral'
                        self.assertEqual(out[group][key]['trend'],expected,(frame,group,key,delta))
                        self.assertEqual(out[group][key]['change']['trend'],expected)

    def test_display_color_missing_or_incompatible_stays_neutral(self):
        for update in ({'value':None},{'accounting_basis':'separate'}):
            n=normal();n['annual'][-1]['values']['liabilities'].update(update)
            out=summarize(n,{})['annual'][-1]['value_movements']['liabilities']
            self.assertEqual(out['trend'],'neutral');self.assertIsNone(out['change']['value'])

    def test_sec_shares_are_instant_not_weighted_and_decrease_is_green(self):
        n=normal()
        for row in n['annual']:
            row['values']['shares']=fact(100,unit='shares',end=row['date'])
            row['values']['weighted_shares']=fact(999,unit='shares',end=row['date'])
        n['annual'][-1]['values']['shares']['value']=90
        out=summarize(n,{})['annual'][-1]
        self.assertEqual(out['value_movements']['shares']['value'],90)
        self.assertEqual(out['value_movements']['shares']['trend'],'improved')
        n['annual'][-1]['values'].pop('shares')
        out=summarize(n,{})['annual'][-1]
        self.assertIsNone(out['values']['shares']['value'])
        self.assertEqual(out['value_movements']['shares']['trend'],'neutral')

    def test_http_serves_new_assets_and_identical_result(self):
        import http.client, tempfile, threading
        from types import SimpleNamespace
        from dashboard import handler_for, ThreadingHTTPServer
        from test_analysis import AnalysisTests
        fixture=AnalysisTests();fixture.setUp()
        result=summarize(normal(),{});fixture.fin.read=lambda k:dict(state='complete',result=result);fixture.hist.saved.markets={}
        handler=handler_for({'accounts':fixture.accounts,'market':{}},{'US:NVDA':fixture.stock},ROOT/'dashboard','fixture-token',fixture.root/'http.sqlite3',analysis_dependencies={'financials':fixture.fin,'histories':fixture.hist})
        server=ThreadingHTTPServer(('127.0.0.1',0),handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            for path in ('/','/financial-focus.js','/financial-focus.css','/api/financials/US%3ANVDA'):
                c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('GET',path);r=c.getresponse();body=r.read();c.close()
                self.assertEqual(r.status,200,path)
                if path.startswith('/api'):self.assertEqual(json.loads(body)['result'],result)
                elif path=='/':
                    self.assertIn(b'/financial-focus.js',body);self.assertNotIn(b'src="/financials.js"',body)
        finally:server.shutdown();server.server_close();fixture.tearDown()

    def test_mcp_pages_match_shared_results(self):
        from test_analysis import AnalysisTests
        fixture=AnalysisTests();fixture.setUp()
        try:
            result=summarize(normal(),{});result['snapshot_id']='test-focus'
            fixture.fin.read=lambda key:dict(state='complete',result=result)
            fixed=fixture.s.dispatch('read',dict(analysis_key=fixture.cid,section='summary'),'http://127.0.0.1:1234')
            for section,field in [('financials','summary'),('financial_annual','annual'),('financial_quarterly','quarterly')]:
                items=[];offset=0
                while True:
                    packet=fixture.s.dispatch('read',dict(analysis_key=fixture.cid,snapshot_id=fixed['snapshot_id'],section=section,offset=offset,limit=5),'http://127.0.0.1:1234')
                    items.extend(packet['financials']['result'][field]);offset=packet['next_offset']
                    if offset is None:break
                self.assertEqual(items,result[field])
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()

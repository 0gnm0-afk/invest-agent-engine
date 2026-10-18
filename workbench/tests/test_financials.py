"""Calculation and cache/security contracts for requested-company financial summaries."""
import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from financial_model import fact,calc,number,trailing,dart_normalize,sec_normalize,summarize
from financial_store import FinancialStore,save,lock_file,unlock
from financial_sources import SourceError
from financial_supplements import inline_shares


def dart_report(year,q,sales,eps,cfo):
    end=f'{year}-{q*3:02d}-'+('31' if q in (1,4) else '30')
    def r(id,amount,sj='IS',cumulative=None):return dict(corp_code='001',sj_div=sj,account_id=id,account_nm=id,currency='KRW',rcept_no='20260101000001',thstrm_amount=str(amount),thstrm_add_amount=str(cumulative) if cumulative is not None else '')
    rows=[r('ifrs-full_Revenue',sales,cumulative=sales*q),r('ifrs-full_DilutedEarningsLossPerShare',eps,cumulative=eps*q),r('ifrs-full_CashFlowsFromUsedInOperatingActivities',cfo,'CF')]
    sh=dict(corp_code='001',se='보통주',stlm_dt=end,distb_stock_co='90',istc_totqy='100',tesstk_co='10',rcept_no='20260101000001')
    return dict(year=year,quarter=q,end=end,prefix=f'{year}-Q{q}',fs=dict(status='000',list=rows),shares=dict(status='000',list=[sh]))

class Calculations(unittest.TestCase):
    def test_missing_and_zero(self):
        self.assertIsNone(number('-'));self.assertIsNone(number(float('nan')));self.assertEqual(number('0'),0)
        self.assertEqual(calc('sum',[fact(0),fact(2)],lambda a,b:a+b)['value'],2)
        self.assertIsNone(calc('sum',[fact(),fact(2)],lambda a,b:a+b)['value'])
    def test_negative_denominator_not_valuation(self):
        self.assertIsNone(calc('PE',[fact(100),fact(-1)],lambda a,b:a/b,positive=(1,))['value'])
    def test_dart_quarter_income_is_not_ytd(self):
        source=dict(identity=dict(corp_code='001',stock_code='000001',corp_name='test'),reports=[dart_report(2025,1,10,1,8),dart_report(2025,2,20,2,18)])
        n=dart_normalize(source);self.assertEqual(n['quarterly'][-1]['values']['sales']['value'],20)
        self.assertEqual(n['quarterly'][-1]['values']['cfo']['value'],10)
    def test_q4_eps_not_subtracted(self):
        source=dict(identity=dict(corp_code='001',stock_code='000001',corp_name='test'),reports=[dart_report(2025,3,30,3,20),dart_report(2025,4,150,15,40)])
        n=dart_normalize(source);self.assertEqual(n['quarterly'][-1]['values']['sales']['value'],60)
        self.assertIsNone(n['quarterly'][-1]['values']['eps']['value']);self.assertEqual(n['annual'][0]['values']['eps']['value'],15)
    def test_shares_are_not_weighted_or_issued(self):
        n=dart_normalize(dict(identity=dict(corp_code='001',stock_code='000001'),reports=[dart_report(2025,4,100,5,20)]))
        self.assertEqual(n['annual'][0]['values']['shares']['value'],90)
    def test_dart_identity(self):
        with self.assertRaisesRegex(ValueError,'IDENTITY'):
            dart_normalize(dict(identity=dict(corp_code='wrong',stock_code='000001'),reports=[dart_report(2025,4,100,5,20)]))
    def test_ttm_requires_contiguous_quarters(self):
        rows=[dict(date=d,values={'x':fact(i,unit='USD',end=d)}) for d,i in [('2025-03-31',1),('2025-06-30',2),('2025-09-30',3),('2025-12-31',4)]]
        self.assertEqual(trailing(rows,'x')['value'],10);rows[1]['date']='2024-06-30';self.assertIsNone(trailing(rows,'x')['value'])
    def test_sec_split_applies_only_after_filed(self):
        r=dict(start='2020-01-01',end='2020-12-31',val=10,filed='2021-02-01',form='10-K',accn='a')
        facts={'us-gaap':{'EarningsPerShareDiluted':{'units':{'USD/shares':[r]}},'Revenues':{'units':{'USD':[dict(r,val=100)]}},'CommonStockSharesOutstanding':{'units':{'shares':[dict(end=r['end'],val=100,filed=r['filed'],form='10-K',accn='a')]}}}}
        source=dict(facts={'facts':facts},identity=dict(name='X',cik='1'),splits=[dict(date='2021-07-20',ratio=4)])
        n=sec_normalize(source);self.assertEqual(n['annual'][0]['values']['eps']['value'],2.5);self.assertEqual(n['annual'][0]['values']['shares']['value'],400)
        facts['us-gaap']['EarningsPerShareDiluted']['units']['USD/shares'][0].update(val=2.5,filed='2022-02-01')
        self.assertEqual(sec_normalize(source)['annual'][0]['values']['eps']['value'],2.5)
    def test_inline_shares_excludes_other_dimension(self):
        ctx='<xbrli:context id="c"><xbrli:entity><xbrli:identifier>1</xbrli:identifier><xbrli:segment><xbrldi:explicitmember dimension="us-gaap:StatementEquityComponentsAxis">us-gaap:CommonStockMember</xbrldi:explicitmember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>'
        doc=ctx+'<xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit><ix:nonfraction name="us-gaap:CommonStockSharesOutstanding" contextref="c" unitref="shares" scale="6">24,147</ix:nonfraction>'
        filing=dict(accessionNumber='a',filingDate='2026-08-01',form='10-Q')
        self.assertEqual(inline_shares(doc,'1',filing,'x')[0]['val'],24147000000)
        self.assertEqual(inline_shares(doc.replace('us-gaap:CommonStockMember','nvda:SubsidiaryMember'),'1',filing,'x'),[])

class StoredRequest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=FinancialStore(Path(self.temp.name))
    def tearDown(self):self.temp.cleanup()
    def test_get_does_not_collect(self):
        with patch('financial_store.Collector') as collector:
            self.assertEqual(self.store.read('US:NVDA')['state'],'not_requested');collector.assert_not_called()
    def test_reuse_does_not_collect(self):
        with patch.object(self.store,'read',return_value={'result':{'x':1},'state':'complete','result_stale':False}),patch('financial_store.Collector') as collector:
            self.assertEqual(self.store.start('US:NVDA',{},False)['result'],{'x':1});collector.assert_not_called()
    def test_cross_process_lock(self):
        f=lock_file(Path(self.temp.name)/'lock');self.assertIsNotNone(f);self.assertIsNone(lock_file(Path(self.temp.name)/'lock'));unlock(f)
    def test_path_traversal(self):
        for key in ['US:../../secrets','KR:..','US:a','KR:005930/other']:
            with self.assertRaises(ValueError):self.store.read(key)
    def test_failed_refresh_preserves_pointer(self):
        folder=self.store.folder('US:NVDA');save(folder/'latest.json',{'snapshot':'a'*32,'manifest_sha256':'old'})
        lease=lock_file(folder/'request.lock')
        with patch('financial_store.Collector') as c:
            c.return_value.usa.side_effect=SourceError('HTTP_403:companyfacts.json')
            self.store._work('US:NVDA',{'market':'US','symbol':'NVDA'},lease)
        self.assertEqual(json.loads((folder/'latest.json').read_bytes())['snapshot'],'a'*32)
        self.assertEqual(json.loads((folder/'attempt.json').read_bytes())['state'],'failed')
    def test_corrupt_snapshot_hidden(self):
        folder=self.store.folder('US:NVDA');save(folder/'latest.json',{'snapshot':'a'*32,'manifest_sha256':'bad'})
        save(folder/'snapshots'/('a'*32)/'manifest.json',{'files':{}})
        r=self.store.read('US:NVDA');self.assertIsNone(r['result']);self.assertEqual(r['attempt']['error'],'cached_snapshot_integrity_failed')

class FinancialHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dashboard import handler_for,ThreadingHTTPServer
        cls.temp=tempfile.TemporaryDirectory()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for({}, {'US:ABC':{'symbol':'ABC','market':'US','bars':[]}},Path('.'),'test-token',workspace_path=Path(cls.temp.name)/'workspace.sqlite3'))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def request(self,method,path='/api/financials/US%3AABC',headers=None,body=None):
        import http.client
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        c.request(method,path,body=body,headers=headers or {});r=c.getresponse();b=r.read();status=r.status;c.close();return status,b
    def headers(self):return {'Origin':'http://127.0.0.1:'+str(self.server.server_port),'Content-Type':'application/json','X-Workbench-Token':'test-token'}
    def test_saved_get_and_no_raw(self):
        with patch('financial_store.Collector') as collector:
            self.assertEqual(self.request('GET')[0],200);collector.assert_not_called()
        self.assertEqual(self.request('GET','/secrets/financial.json')[0],404)
    def test_request_requires_origin_and_token(self):
        self.assertEqual(self.request('POST',body='{"refresh":true}')[0],403)
        h=self.headers();h['Origin']='https://evil.example';self.assertEqual(self.request('POST',headers=h,body='{"refresh":true}')[0],403)
    def test_unknown_or_invalid_body_cannot_collect(self):
        h=self.headers()
        with patch('financial_store.Collector') as collector:
            self.assertEqual(self.request('POST','/api/financials/US%3AUNKNOWN',h,'{"refresh":true}')[0],404)
            self.assertEqual(self.request('POST',headers=h,body='{"refresh":"yes"}')[0],400)
            collector.assert_not_called()
    def test_valid_explicit_request_only(self):
        with patch('dashboard.FinancialStore.start',return_value={'state':'running'}) as start:
            self.assertEqual(self.request('POST',headers=self.headers(),body='{"refresh":true}')[0],202)
            self.assertTrue(start.call_args.kwargs['refresh'])

if __name__=='__main__':unittest.main()
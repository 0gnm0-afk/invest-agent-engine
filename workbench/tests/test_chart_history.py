from contextlib import closing
import copy
import hashlib
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from chart_history import HistoryStore, aggregate, validate_bars
from chart_workspace import defaults, moving_averages
from dashboard import handler_for, ThreadingHTTPServer

def bar(day, price=10, volume=100):
    return dict(date=day,open=price,high=price+3,low=price-2,close=price+1,volume=volume)

class History(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'history.sqlite3'
        self.stock=dict(market='US',symbol='AAA',currency='USD',bars=[bar('2026-09-24')])
        self.calls=[]
    def tearDown(self):self.tmp.cleanup()
    def client(self,path,params):
        self.calls.append((path,params))
        rows=[bar('2026-09-24'),bar('2016-01-04')]
        return {'result':{'nextBefore':None,'candles':[dict(timestamp=b['date']+'T00:00:00-04:00',currency='USD',openPrice=b['open'],highPrice=b['high'],lowPrice=b['low'],closePrice=b['close'],volume=b['volume']) for b in rows]}}
    def test_long_history_more_than_ten_pages(self):
        from datetime import date, timedelta
        from toss_candles import daily_series
        days=[];day=date(2016,1,1)
        while day<=date(2026,9,24):
            if day.weekday()<5:days.append(day.isoformat())
            day+=timedelta(days=1)
        chunks=[list(reversed(days))[i:i+200] for i in range(0,len(days),200)]
        calls=[]
        def get(path,params):
            idx=len(calls);calls.append(params)
            rows=[dict(timestamp=d+'T00:00:00-04:00',currency='USD',openPrice=10,highPrice=12,lowPrice=9,closePrice=11,volume=100) for d in chunks[idx]]
            return {'result':{'candles':rows,'nextBefore':rows[-1]['timestamp'] if idx+1<len(chunks) else None}}
        result=daily_series(get,'AAA','US',['2016-01-01','2026-09-24'],max_pages=40)
        self.assertEqual(len(result['bars']),len(days));self.assertGreater(len(calls),10)
        self.assertEqual(result['bars'][0]['date'],'2016-01-01')

    def test_week_year_boundary_and_ohlcv(self):
        rows=[bar('2020-12-31'),bar('2021-01-01',12),bar('2021-01-04',14)]
        got=aggregate(rows,'1w')
        self.assertEqual([b['date'] for b in got],['2020-12-28','2021-01-04'])
        self.assertEqual([got[0][k] for k in ['open','high','low','close','volume']],[10,15,8,13,200])
        self.assertEqual(got[0]['last_session'],'2021-01-01')
    def test_month_and_missing_volume(self):
        rows=[bar('2024-02-28'),bar('2024-02-29',12,None),bar('2024-03-01')]
        got=aggregate(rows,'1mo');self.assertEqual(got[0]['date'],'2024-02-01');self.assertIsNone(got[0]['volume']);self.assertEqual(len(got),2)
    def test_daily_not_mutated_and_invalid_frame(self):
        rows=[bar('2026-09-24')];copy_rows=copy.deepcopy(rows);aggregate(rows,'1w');self.assertEqual(rows,copy_rows)
        with self.assertRaises(ValueError):aggregate(rows,'1h')
    def test_history_cached_reopen_source_preserved_and_cutoff(self):
        original=copy.deepcopy(self.stock);s=HistoryStore(self.path,lambda:self.client)
        a=s.ensure(self.stock);self.assertEqual(a['history']['state'],'complete');self.assertEqual(a['history']['first'],'2016-01-04')
        self.assertEqual(self.calls[0][1]['before'],'2026-09-25T00:00:00-04:00')
        HistoryStore(self.path,lambda:self.fail).ensure(self.stock);self.assertEqual(len(self.calls),1);self.assertEqual(self.stock,original);self.assertFalse(a['history']['screening_price_difference'])
        changed=copy.deepcopy(self.stock);changed['bars'][0]['close']=10.5
        self.assertTrue(s.ensure(changed)['history']['screening_price_difference'])
    def fail(self,*args):raise ValueError('SECRET must never appear')
    def test_failure_no_repeat_and_explicit_retry(self):
        s=HistoryStore(self.path,lambda:self.fail);r=s.ensure(self.stock);self.assertEqual(r['history']['state'],'failed');self.assertEqual(r['bars'],self.stock['bars']);self.assertNotIn('SECRET',str(r))
        s.client=self.client;r=s.ensure(self.stock);self.assertEqual(len(self.calls),0)
        s.client_factory=lambda:self.client;r=s.ensure(self.stock,retry=True);self.assertEqual(r['history']['state'],'complete')
    def test_partial_is_not_full_history(self):
        def short(path,params):
            packet=self.client(path,params);packet['result']['candles']=packet['result']['candles'][:1];return packet
        s=HistoryStore(self.path,lambda:short);self.assertEqual(s.ensure(self.stock)['history']['state'],'partial')
    def test_cutoff_not_silently_advanced(self):
        def wrong(path,params):
            packet=self.client(path,params);packet['result']['candles']=packet['result']['candles'][1:];return packet
        s=HistoryStore(self.path,lambda:wrong);r=s.ensure(self.stock);self.assertEqual(r['history']['state'],'failed');self.assertEqual(r['history']['last'],'2026-09-24')
    def test_cache_tampering_and_input_validation(self):
        s=HistoryStore(self.path,lambda:self.client);s.ensure(self.stock)
        with closing(sqlite3.connect(self.path)) as db, db:db.execute("UPDATE history SET payload='{}'")
        self.assertEqual(s.view(self.stock)['history']['state'],'failed')
        with self.assertRaises(ValueError):validate_bars([bar('2026-10-01')],'2026-09-24')
        with self.assertRaises(ValueError):validate_bars([dict(bar('2026-09-24'),low=100)],'2026-09-24')
    def test_pinned_failure_or_empty_never_reads_selected_cache_or_fetches(self):
        packets = [
            {'history': {'state': 'failed', 'reason': 'saved_us_history_verification_failed'}},
            {'history': {'state': 'failed', 'reason': 'saved_kr_history_verification_failed'}},
            {'series': dict(self.stock, bars=[]), 'history': {
                'state': 'unavailable', 'reason': 'saved_history_empty_before_cutoff'}},
            {'history': {'state': 'failed', 'reason': 'saved_us_symbol_unavailable'}},
            {'history': {'state': 'unavailable', 'reason': 'saved_history_unknown'}},
        ]
        for packet in packets:
            with self.subTest(reason=packet['history']['reason'], state=packet['history']['state']):
                factory = Mock(side_effect=AssertionError('unexpected provider access'))
                store = HistoryStore(self.path, factory)
                store.saved.packet = Mock(return_value=packet)
                store.cached = Mock(side_effect=AssertionError('unexpected selected cache access'))
                result = store.ensure(self.stock, retry=True)
                self.assertEqual(result['history']['state'], packet['history']['state'])
                self.assertEqual(result['history']['reason'], packet['history']['reason'])
                self.assertEqual(result['bars'], packet.get('series', self.stock)['bars'])
                self.assertEqual(store.view(self.stock)['bars'], result['bars'])
                factory.assert_not_called()
                store.cached.assert_not_called()

    def test_absent_symbol_still_requires_saved_cutoff_and_explicit_retry(self):
        absent = {'history': {'state': 'unavailable', 'reason': 'saved_us_symbol_unavailable'}}
        for rows in ([], [bar('2015-12-31')]):
            with self.subTest(rows=rows):
                factory = Mock(side_effect=AssertionError('unexpected provider access'))
                store = HistoryStore(self.path, factory)
                store.saved.packet = Mock(return_value=absent)
                stock = dict(self.stock, bars=rows)
                result = store.ensure(stock)
                self.assertEqual(result['history']['reason'], 'saved_cutoff_missing')
                factory.assert_not_called()
        store = HistoryStore(self.path, lambda: self.fail)
        store.saved.packet = Mock(return_value=absent)
        self.assertEqual(store.ensure(self.stock)['history']['state'], 'failed')
        store.client_factory = lambda: self.client
        self.assertEqual(store.ensure(self.stock)['history']['state'], 'failed')
        self.assertEqual(self.calls, [])
        self.assertEqual(store.ensure(self.stock, retry=True)['history']['state'], 'complete')
        self.assertEqual(len(self.calls), 1)

    def test_absent_symbol_exclusion_blocks_cache_and_provider(self):
        factory = Mock(side_effect=AssertionError('unexpected provider access'))
        store = HistoryStore(self.path, factory)
        store.saved.packet = Mock(return_value={'history': {
            'state': 'unavailable', 'reason': 'saved_us_symbol_unavailable'}})
        store.cached = Mock(side_effect=AssertionError('unexpected selected cache access'))
        with patch('market_scope.match', return_value={'reason': 'user_approved', 'approved_on': '2026-10-04'}):
            self.assertEqual(store.ensure(self.stock, retry=True)['history']['state'], 'excluded')
            self.assertEqual(store.view(self.stock)['bars'], [])
        store.saved.packet.assert_not_called()
        store.cached.assert_not_called()
        factory.assert_not_called()

    def test_sma_uses_weekly_closes(self):
        bars=aggregate([bar('2026-09-04',10),bar('2026-09-07',20),bar('2026-09-08',30)],'1w')
        values=moving_averages({'bars':bars},[2])['2'];self.assertNotIn('value',values[0]);self.assertEqual(values[1]['value'],21)

class PeriodAPI(unittest.TestCase):
    def test_isolation_security_and_invalid_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_for({}, {'US:AAA':{'bars':[bar('2026-09-24')]}},Path(tmp),'test-token',Path(tmp)/'workspace.sqlite3'))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(method,path,body=None,authorized=True):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}'}
                if authorized:headers['X-Workbench-Token']='test-token'
                conn.request(method,path,json.dumps(body) if body is not None else None,headers);r=conn.getresponse();result=(r.status,json.loads(r.read()));conn.close();return result
            try:
                self.assertEqual(request('POST','/api/chart-history/US:AAA',{'retry':False},False)[0],403)
                self.assertEqual(request('GET','/api/chart/US:AAA?timeframe=1h')[0],400)
                self.assertEqual(request('GET','/api/chart/US:unknown')[0],404)
                self.assertEqual(request('POST','/api/chart-state/US:AAA?timeframe=1w',{'revision':0,'state':defaults()})[0],200)
                self.assertEqual(request('GET','/api/chart-state/US:AAA')[1]['revision'],1)
                self.assertEqual(request('GET','/api/chart-state/US:AAA?timeframe=1w')[1]['revision'],1)
                self.assertEqual(request('GET','/api/chart-state/US:AAA?timeframe=1mo')[1]['revision'],1)
            finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()

import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import recent_trendlines as r
import recent_sperandeo as s
import recent_screen_projection as projection
from chart_history import aggregate
from test_downtrend_approach import example
from test_momentum_entry import fixture,series
import momentum_review


class RecentTrendlineTests(unittest.TestCase):
    def test_upstream_source_and_non_collinear_regression(self):
        import hashlib
        notice=Path(r.trendln.__file__).with_name('NOTICE.md').read_text(encoding='utf-8')
        self.assertIn(hashlib.sha256(Path(r.trendln.__file__).read_bytes()).hexdigest(),notice)
        bars=example();bars[290]['high']+=.01
        with patch.object(r.trendln,'calc_support_resistance',wraps=r.trendln.calc_support_resistance) as calculate:
            line=r.select(bars)
        calculate.assert_called_once()
        self.assertEqual(line['library_version'],'0.1.18')
        self.assertEqual(line['touch_count'],3)
        # Independent OLS oracle over three non-collinear highs. Endpoints must
        # use the fitted values, never snap the regression back to two candles.
        xs=[280,290,300];ys=[bars[i]['high'] for i in xs]
        xmean=sum(xs)/3;ymean=sum(ys)/3
        slope=sum((x-xmean)*(y-ymean) for x,y in zip(xs,ys))/sum((x-xmean)**2 for x in xs)
        expected=ymean+slope*(320-xmean)
        self.assertAlmostEqual(line['value_latest'],expected)
        self.assertNotEqual(line['anchor_a_price'],line['anchor_a_source_price'])
        self.assertGreater(line['fit_ssr'],0)
        import downtrend_approach
        boundary=copy.deepcopy(bars);level=line['value_latest']
        boundary[-1].update(open=level,close=level,high=level,low=level-1)
        self.assertEqual(downtrend_approach.analyze(boundary)['signals'][0]['trendline']['gap_atr'],0)
        packet=r.overlay(bars,bars,screening_line=line)
        actual=next(l for l in packet['lines'] if l['side']=='resistance')
        self.assertEqual(actual['extension'][-1]['value'],line['value_latest'])

    def test_no_custom_two_point_fallback(self):
        bars=example();bars[290].update(open=96,close=96,high=96.4,low=95.6)
        self.assertIsNone(r.select(bars))

    def test_recent_limits_and_candle_envelope(self):
        bars=example();line=r.select(bars)
        self.assertEqual(line['anchor_a_date'],bars[280]['date'])
        self.assertEqual(line['anchor_b_date'],bars[300]['date'])
        self.assertLess(line['trend_bars'],60)
        self.assertLessEqual(line['last_touch_age'],20)
        # Much larger but obsolete high must never become an anchor.
        bars[250].update(open=999,high=1000,low=998,close=999)
        self.assertEqual(r.select(bars)['anchor_a_date'],line['anchor_a_date'])
        # A large wick cuts through the proposed envelope even without a close break.
        bars[290]['high']=120
        self.assertFalse(any(l['anchor_a_date']==line['anchor_a_date'] and l['anchor_b_date']==line['anchor_b_date'] for l in r.candidates(bars)))

    def test_no_future_confirmation_or_resurrection(self):
        bars=example();line=r.select(bars)
        self.assertIsNone(r.select(bars[:302]))
        future=dict(bars[-1],date='2099-01-01',high=999,complete=False)
        self.assertEqual(r.select(bars+[future]),line)
        self.assertEqual(r.select(bars+[dict(future,complete=True)],as_of=bars[-1]['date']),line)
        bars[310].update(open=104,low=103,high=106,close=105)
        self.assertFalse(any(l['first_close_break_date'] is None and l['anchor_b_date']==line['anchor_b_date'] for l in r.candidates(bars)))

    def test_support_symmetry_and_weekly_projection(self):
        bars=example();mirror=[{**b,'open':300-b['open'],'close':300-b['close'],'high':300-b['low'],'low':300-b['high']} for b in bars]
        support=r.select(mirror,'low');resistance=r.select(bars)
        self.assertAlmostEqual(support['value_latest'],300-resistance['value_latest'])
        week=aggregate(bars,'1w');packet=r.overlay(bars,week)
        line=next(l for l in packet['lines'] if l['side']=='resistance')
        self.assertAlmostEqual(line['extension'][-1]['value'],resistance['value_latest'])
        self.assertEqual(line['extension'][-1]['time'],week[-1]['date'])
        self.assertEqual(packet['timeframe'],'1d')
        bad=copy.deepcopy(bars);bad[-1]['close']=None
        self.assertEqual(r.overlay(bad,bad)['state'],'invalid_history')

    def test_sperandeo_uses_same_recent_line_and_ordered_stages(self):
        bars=example()[:311]
        # Break after both pivot confirmation and the intervening low.
        bars[-1].update(open=104,close=105,high=106,low=104)
        state=s.analyze(bars)['windows']['60']
        self.assertEqual(state['stage'],'S2')
        self.assertEqual(state['anchor_high_a_date'],bars[280]['date'])
        more=series([106,105,104,105,106,108])
        from datetime import date,timedelta
        for i,b in enumerate(more):b['date']=(date.fromisoformat(bars[-1]['date'])+timedelta(days=i+1)).isoformat()
        ready=s.analyze(bars+more[:-1])['windows']['60']
        self.assertEqual(ready['stage'],'S3')
        complete=s.analyze(bars+more)['windows']['60']
        self.assertEqual(complete['stage'],'S4')
        self.assertLess(complete['trendline']['trend_bars'],60)
        # A lower low after the breakout invalidates the sequence.
        invalid=copy.deepcopy(more);invalid[-1].update(low=80,close=81,open=82)
        self.assertFalse(any(w['stage'] in ('S3','S4') for w in s.analyze(bars+invalid)['windows'].values()))

    def test_main_chart_api_keeps_drawings_and_matches_screening(self):
        import http.client,threading
        from dashboard import handler_for,ThreadingHTTPServer
        from chart_history import HistoryStore
        from chart_workspace import defaults
        bars=example();line=r.select(bars)
        stock=dict(market='US',symbol='TEST',bars=bars,currency='USD')
        data={'market':{'entries':[dict(market='US',symbol='TEST',route_signals={'downtrend_approach':{'trendline':line}})]}}
        def view(_self,stock,frame='1d'):
            return {**stock,'bars':aggregate(bars,frame),'timeframe':frame,'history':{}}
        with tempfile.TemporaryDirectory() as folder,patch.object(HistoryStore,'view',view):
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(data,{'US:TEST':stock},Path(folder),'test',Path(folder)/'workspace.sqlite3',offline=True))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(path,method='GET',body=None):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                conn.request(method,path,json.dumps(body) if body else None,{'X-Workbench-Token':'test','Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}'})
                response=conn.getresponse();self.assertEqual(response.status,200);out=json.loads(response.read());conn.close();return out
            try:
                state=defaults();state['drawings']=[dict(id='user-line',kind='trend',points=[dict(time=bars[-5]['date'],price=100),dict(time=bars[-2]['date'],price=101)],color='#218775',width=2,extend=False)]
                request('/api/chart-state/US:TEST','POST',dict(revision=0,state=state))
                before=request('/api/chart-state/US:TEST')
                for frame in ('1d','1w','1mo'):
                    packet=request('/api/chart/US:TEST?timeframe='+frame)['auto_trendlines']
                    actual=next(l for l in packet['lines'] if l['side']=='resistance')
                    self.assertTrue(actual['screening'])
                    self.assertEqual(actual['value_latest'],line['value_latest'])
                    self.assertEqual(actual['extension'][-1]['value'],line['value_latest'])
                after=request('/api/chart-state/US:TEST')
                self.assertEqual(before['revision'],after['revision']);self.assertEqual(before['state'],after['state'])
            finally:server.shutdown();server.server_close();thread.join()

    def test_projection_cache_and_daily_policy(self):
        screen,snapshot=fixture()
        cfg=dict(strategy='pdf_momentum',watchlist=dict(enabled=True,downtrend_approach={}))
        old=momentum_review.build(screen,snapshot,cfg);before=copy.deepcopy(old)
        with tempfile.TemporaryDirectory() as folder:
            result=projection.cached(folder,screen,snapshot,old,'hash',{})
            self.assertEqual(result['config']['trendline_policy'],r.VERSION)
            for obs in result['observations']:
                if obs.get('features',{}).get('state')=='available':self.assertEqual(obs['recent_structure']['version'],r.VERSION)
            with patch.object(momentum_review,'build',side_effect=AssertionError('cache_not_used')):
                self.assertEqual(projection.cached(folder,screen,snapshot,old,'hash',{}),result)
            path=next(Path(folder).rglob('review.json'));record=json.loads(path.read_text(encoding='utf-8'));record['sha256']='bad';path.write_text(json.dumps(record),encoding='utf-8')
            with self.assertRaises(ValueError):projection.cached(folder,screen,snapshot,old,'hash',{})
        self.assertEqual(old,before)

if __name__=='__main__':unittest.main()

import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_momentum_entry import vcp,cup,spr,fixture
from invest_agent.trading import momentum_entry as entry,momentum_watch as watch
import momentum_review,watch_review,daily_market,priority_review

def observe(b,rs=95,market=False,structure=None):
    strict=entry.analyze(b,rs,{'supportive':market},structure)
    return watch.analyze(b,strict,structure),strict

class WatchTests(unittest.TestCase):
    def test_prebreakout_and_market_caution(self):
        result,strict=observe(vcp()[:-1])
        self.assertFalse(strict['main_candidate']);self.assertTrue(result['signals'])
        self.assertTrue(all(s['phase'] in ('watch','ready') for s in result['signals']))
        self.assertTrue(all(s['breakout_date'] is None and s['entry_zone'] is None for s in result['signals']))
        self.assertTrue(all(s['warnings'] for s in result['signals']))
        self.assertTrue(all(s['known_at']<vcp()[-2]['date'] for s in result['signals']))

    def test_entry_is_preserved_and_separate(self):
        result,strict=observe(vcp(),market=True,structure=spr(vcp()))
        self.assertEqual(result['phase'],'entry')
        self.assertEqual(len([s for s in result['signals'] if s['phase']=='entry']),len(strict['active_events']))
        result,strict=observe(vcp(),market=False)
        self.assertNotIn('entry',[s['phase'] for s in result['signals']])

    def test_not_every_strong_stock_is_watch(self):
        b=vcp()[:290];result,_=observe(b)
        self.assertFalse(result['signals'])
        for rs in (None,20):self.assertFalse(observe(vcp()[:-1],rs)[0]['signals'])

    def test_broken_low_and_chased_price_not_promoted(self):
        b=vcp()[:-1];b[-1]['low']=180
        self.assertFalse(observe(b)[0]['signals'])
        b=vcp();b[-1].update(close=250,high=251)
        self.assertFalse(observe(b)[0]['signals'])

    def test_watch_with_rs_between_70_and_80(self):
        result,strict=observe(vcp()[:-1],rs=75)
        self.assertTrue(result['signals']);self.assertEqual(result['phase'],'watch')
        self.assertFalse(strict['main_candidate'])

    def test_s3_confirmed_and_intact_only(self):
        b=vcp()[:-1];structure=spr(b);w=structure['windows']['126'];w.update(stage='S3',retest_low=205,completion_level=219)
        result,_=observe(b,rs=65,structure=structure)
        self.assertTrue(any(s['kind']=='sperandeo_s3' for s in result['signals']))
        for change in ({'stage':'S2'},{'retest_confirmed_at':'2099-01-01'},{'retest_low':215},{'retest_confirmed_at':b[-40]['date']}):
            altered=copy.deepcopy(structure);altered['windows']['126'].update(change)
            self.assertFalse(any(s['kind']=='sperandeo_s3' for s in observe(b,rs=65,structure=altered)[0]['signals']))

    def test_config_and_insufficient(self):
        for config in ({'rs_min':101},{'max_pivot_gap':.01},{'s3_max_age':True},{'other':1}):
            with self.assertRaises(ValueError):watch.settings(config)
        self.assertFalse(observe(vcp()[:200])[0]['signals'])

    def test_future_and_incomplete_not_used(self):
        b=vcp()[:-1];strict=entry.analyze(b,95,{'supportive':False});expected=watch.analyze(b,strict)
        f=copy.deepcopy(b[-1]);f.update(date='2099-01-01',open=900,close=1000,high=1100,low=800)
        self.assertEqual(expected,watch.analyze(b+[f],strict,as_of=b[-1]['date']))
        f['complete']=False
        self.assertEqual(expected,watch.analyze(b+[f],strict))

class IntegrationTests(unittest.TestCase):
    def make(self):
        screen,snap=fixture()
        for item in snap['series']:item['bars']=item['bars'][:-1]
        for item in snap['benchmarks'].values():item['bars']=item['bars'][:-1]
        for market in snap['sessions']:snap['sessions'][market].pop();screen['session_dates'][market]=snap['sessions'][market][-1]
        for row in screen['rows']:row['as_of']=screen['session_dates'][row['market']]
        return screen,snap

    def test_period_ties_survive_sorted_json_without_input_mutation(self):
        for stage in ('S3','S4'):
            with self.subTest(stage=stage):
                screen,snap=self.make() if stage=='S3' else fixture()
                for row in screen['rows']:
                    bars=next(x['bars'] for x in snap['series'] if x['market']==row['market'] and x['symbol']==row['symbol'])
                    w=spr(bars)['windows']['126']
                    if stage=='S3':w.update(stage='S3',retest_low=205,completion_level=219)
                    row['sperandeo']={'windows':{str(n):copy.deepcopy(w) for n in (63,126,252)}}
                before=copy.deepcopy(screen)
                cfg={'strategy':'pdf_momentum','watchlist':{}}
                live=priority_review.build(screen,snap,cfg)
                saved=json.loads(json.dumps(screen,sort_keys=True))
                replay=priority_review.build(saved,snap,cfg)
                self.assertEqual(live,replay)
                self.assertEqual(screen,before)
                values=[e for o in live['observations'] for e in o.get('signals' if stage=='S3' else 'events',[]) if e['route']=='sperandeo']
                self.assertTrue(values)
                self.assertEqual([v['metrics']['window'] for v in values[:3]],['63','126','252'])

    def test_phases_counts_caps_and_legacy_invariance(self):
        screen,snap=self.make();before=copy.deepcopy(screen)
        legacy=priority_review.build(screen,snap,{'strategy':'pdf_momentum'})
        r=priority_review.build(screen,snap,{'strategy':'pdf_momentum','watchlist':{'enabled':True}})
        self.assertEqual(r['schema'],4);self.assertEqual(screen,before);self.assertEqual(legacy['candidate_count'],0)
        self.assertEqual(r['candidate_count'],2);self.assertEqual(r['entry_candidate_count'],0)
        self.assertTrue(all(sum(g['phase_counts'].values())==g['candidate_count'] for g in r['markets'].values()))
        zero=priority_review.build(screen,snap,{'strategy':'pdf_momentum','watchlist':{},'limit_per_market':0})
        self.assertEqual(sum(g['selected_count'] for g in zero['markets'].values()),0)

    def test_report_csv_chart_and_phase_labels(self):
        screen,snap=self.make();r=priority_review.build(screen,snap,{'strategy':'pdf_momentum','watchlist':{}})
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);momentum_review.write(p/'priority-review.json',r);momentum_review.write_outputs(p,r)
            def draw(b,path,e):path.write_bytes(b'fake')
            charts=priority_review.charts(r,screen,snap,p,draw);momentum_review.write(p/'priority-charts.json',charts)
            daily_market.render({'state':'failed','quality':{'received':4,'failed':2}},p,screen)
            page=(p/'report.html').read_text(encoding='utf-8')
            self.assertIn('<h1>스크리너',page);self.assertNotIn('섹터 변화 먼저 보기',page)
            self.assertIn('진입후보',page);self.assertIn('돌파 대기',page);self.assertIn('다음 확인',page)
            self.assertIn('phase,entry_candidate',(p/'candidates.csv').read_text(encoding='utf-8-sig'))
            self.assertIn('data-phase=',(p/'entry-scan.html').read_text(encoding='utf-8'))
            self.assertEqual(charts['available'],2)

    def test_complete_watch_output_ignores_future(self):
        screen,snap=self.make();cfg={'strategy':'pdf_momentum','watchlist':{}};r=priority_review.build(screen,snap,cfg)
        for item in snap['series']:
            b=copy.deepcopy(item['bars'][-1]);b['date']='2099-01-01';item['bars'].append(b)
        self.assertEqual(r,priority_review.build(screen,snap,cfg))

if __name__=='__main__':unittest.main()

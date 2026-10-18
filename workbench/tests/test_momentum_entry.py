import copy
from datetime import date,timedelta
from pathlib import Path
import math
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from invest_agent.trading import momentum_entry as m
import momentum_review as review
import priority_review


def series(values,volumes=None):
    return [{'date':(date(2024,1,1)+timedelta(days=i)).isoformat(),'open':c-.15,'high':c+.4,'low':c-.4,'close':c,
             'volume':volumes[i] if volumes else 10000} for i,c in enumerate(values)]


def vcp():
    values=[60+i*.5 for i in range(300)]
    for amplitude in (8,4,1.5):values += [210+amplitude*math.cos(i*2*math.pi/9) for i in range(10)]
    volume=[10000]*320+[2000]*10
    values += [220.];volume += [40000]
    b=series(values,volume);b[-1].update(open=215,low=214.8,high=220.5)
    return b


def cup():
    values=[25+i*.6 for i in range(250)]
    values += [175-35*math.sin(math.pi*i/59) for i in range(60)]
    values += [174,173,171,170,169,169,170,171,172,173]
    volumes=[10000]*310+[2000]*10
    values += [176.];volumes += [40000]
    b=series(values,volumes);b[-1].update(open=174.5,low=174,high=176.3)
    return b


def spr(b):
    return {'windows':{'126':{'stage':'S4','completion_date':b[-1]['date'],'completion_level':218.5,'retest_low':210.,
        'retest_confirmed_at':b[-3]['date'],'retest_pivot_date':b[-5]['date'],'anchor_high_a_date':b[-50]['date'],
        'completion_source_date':b[-10]['date']}}}


class EngineTests(unittest.TestCase):
    def test_three_patterns_have_prebreakout_pivots(self):
        b=vcp();patterns=m.setups(b,len(b)-1,m.settings())
        self.assertIn('vcp',[p['kind'] for p in patterns]);self.assertIn('range_breakout',[p['kind'] for p in patterns])
        result=m.analyze(b,95,{'supportive':True})
        self.assertTrue(result['main_candidate'],result)
        self.assertTrue(all(e['known_at']<e['breakout_date'] for e in result['active_events']))
        self.assertIn('cup_handle',[p['kind'] for p in m.setups(cup(),len(cup())-1,m.settings())])
        self.assertTrue(m.analyze(cup(),95,{'supportive':True})['main_candidate'])
        b=vcp();b[-1].update(open=212.,low=211.95,close=214.,high=214.3)
        self.assertIn('vcp',[e['kind'] for e in m.analyze(b,95,{'supportive':True})['active_events']])

    def test_s4_only_recent_confirmed_entry(self):
        b=vcp();r=m.analyze(b,95,{'supportive':True},spr(b))
        self.assertTrue(r['sperandeo_candidate'],r)
        for stage in ('S0','S1','S2','S3'):
            structure=spr(b);structure['windows']['126']['stage']=stage
            self.assertFalse(m.analyze(b,95,{'supportive':True},structure)['sperandeo_candidate'])
        structure=spr(b);structure['windows']['126']['retest_confirmed_at']='2099-01-01'
        self.assertFalse(m.analyze(b,95,{'supportive':True},structure)['sperandeo_candidate'])

    def test_s4_can_qualify_without_main_pattern(self):
        b=vcp()
        for bar in b[:-1]:bar['volume']=10000
        r=m.analyze(b,95,{'supportive':True},spr(b))
        self.assertFalse(r['main_candidate'])
        self.assertTrue(r['sperandeo_candidate'])
        self.assertEqual({e['route'] for e in r['active_events']},{'sperandeo'})

    def test_future_and_incomplete_do_not_change_results(self):
        b=vcp();asof=b[-1]['date'];expected=m.analyze(b,95,{'supportive':True})
        future=copy.deepcopy(b[-1]);future.update(date='2099-01-01',open=900,close=1000,high=1100,low=800,volume=1e12)
        self.assertEqual(expected,m.analyze(b+[future],95,{'supportive':True},as_of=asof))
        future['complete']=False
        self.assertEqual(expected,m.analyze(b+[future],95,{'supportive':True}))

    def test_weak_volume_and_upper_wick_not_entries(self):
        for field,value in [('volume',1000),('high',240)]:
            b=vcp();b[-1][field]=value
            r=m.analyze(b,95,{'supportive':True},spr(b));self.assertFalse(r['candidate'])

    def test_setup_alone_not_entry(self):
        b=vcp()[:-1];r=m.analyze(b,95,{'supportive':True})
        self.assertFalse(r['main_candidate']);self.assertTrue(r['forming_setups'])

    def test_old_breakout_failed_pivot_and_structure_excluded(self):
        base=vcp();setup=m.setups(base,len(base)-1,m.settings())[0]
        for closes in ([220,220.2,220.3],[setup['pivot']-.1],[220]):
            b=copy.deepcopy(base)
            for c in closes:
                new=copy.deepcopy(b[-1]);new.update(date=(date.fromisoformat(b[-1]['date'])+timedelta(days=1)).isoformat(),open=c-.1,high=c+.4,low=c-.4,close=c,volume=1000);b.append(new)
            if closes==[220]:b[-1]['low']=setup['structure_stop']-.1
            event=m.entry_event(b,len(base)-1,setup,m.settings());self.assertFalse(event['entry_pass'],event)

    def test_no_chasing_or_wide_stop(self):
        b=vcp();setup=m.setups(b,len(b)-1,m.settings())[0]
        b[-1].update(close=250,high=251)
        self.assertFalse(m.entry_event(b,len(b)-1,setup,m.settings())['checks']['not_extended'])
        b=vcp();setup['structure_stop']=150
        self.assertFalse(m.entry_event(b,len(b)-1,setup,m.settings())['checks']['bounded_structure_risk'])

    def test_leadership_market_and_trend_are_required(self):
        self.assertFalse(m.analyze(vcp(),70,{'supportive':True})['main_candidate'])
        self.assertFalse(m.analyze(vcp(),95,{'supportive':False})['main_candidate'])
        self.assertFalse(m.analyze(vcp(),None,{'supportive':True})['main_candidate'])
        b=series([300-i*.3 for i in range(320)])
        self.assertFalse(m.features(b)['template_pass'])
        self.assertFalse(m.market_context(b)['supportive'])

    def test_flat_v_shape_and_short_history_are_not_cup(self):
        b=cup();
        for i in range(250,310):b[i].update(open=174,close=174,high=174.4,low=173.6)
        b[280].update(open=140,close=140,high=140.4,low=139.6)
        self.assertNotIn('cup_handle',[p['kind'] for p in m.setups(b,len(b)-1,m.settings())])
        self.assertEqual(m.analyze(b[:200],95,{'supportive':True})['state'],'insufficient_history')

    def test_settings_and_bar_validation(self):
        for cfg in ({'unknown':1},{'market_filter':1},{'max_entry_age':True},{'rs_min':101},{'volume_multiple':.5},{'dry_volume_ratio':1}):
            with self.assertRaises(ValueError):m.settings(cfg)
        b=vcp();b[-1]['close']=float('nan')
        with self.assertRaises(ValueError):m.analyze(b,95,{'supportive':True})
        b=vcp();b[-1]['date']=b[-2]['date']
        with self.assertRaises(ValueError):m.completed(b)


def fixture():
    b=vcp();last=b[-1]['date'];weak=series([100+i*.02 for i in range(len(b))]);rows=[];items=[]
    for market in ('KR','US'):
        for symbol,data in [('BEST',b),('WEAK',weak)]:
            rows.append({'market':market,'symbol':symbol,'name':'진입후보' if market=='KR' else symbol,'state':'not_selected',
                         'as_of':last,'price_basis':'synthetic','universe':{'universe_pass':True},'sperandeo':spr(data) if symbol=='BEST' else {}})
            items.append({'market':market,'symbol':symbol,'bars':data,'provider':'synthetic'})
    return {'rows':rows,'candidate_count':0,'session_dates':{'KR':last,'US':last}}, {'series':items,
       'sessions':{'KR':[v['date'] for v in b],'US':[v['date'] for v in b]},
       'benchmarks':{'^KS11':{'bars':weak},'^GSPC':{'bars':weak}}}


class IntegrationTests(unittest.TestCase):
    def test_independent_main_overlap_caps_and_rs(self):
        screen,snapshot=fixture();before=copy.deepcopy(screen)
        result=priority_review.build(screen,snapshot,{'strategy':'pdf_momentum','limit_per_market':1,'sperandeo_limit_per_market':1})
        self.assertEqual(screen,before);self.assertEqual(result['schema'],3)
        for g in result['markets'].values():
            self.assertEqual(g['selected_count'],1);self.assertEqual(g['overlap_symbols'],['BEST'])
            self.assertEqual(g['entries'][0]['selected_by'],['pdf_momentum','sperandeo'])
            self.assertEqual(g['entries'][0]['analysis']['rs_percentile'],100)
        zero=review.build(screen,snapshot,{'strategy':'pdf_momentum','limit_per_market':0,'sperandeo_limit_per_market':0})
        self.assertEqual(zero['markets']['KR']['selected_count'],0)

    def test_tied_rs_is_not_artificial_leader_and_stale_benchmark_blocks(self):
        screen,snap=fixture();snap['series'][1]['bars']=copy.deepcopy(snap['series'][0]['bars'])
        r=review.build(screen,snap,{'strategy':'pdf_momentum'})
        self.assertEqual(next(o for o in r['observations'] if o['market']=='KR' and o['symbol']=='BEST')['rs_percentile'],50)
        screen,snap=fixture();snap['benchmarks']['^GSPC']['bars'].pop()
        r=review.build(screen,snap,{'strategy':'pdf_momentum'})
        self.assertEqual(r['markets']['US']['candidate_count'],0)

    def test_csv_scan_chart_errors_and_input_isolation(self):
        screen,snap=fixture();r=review.build(screen,snap,{'strategy':'pdf_momentum'})
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);review.write_outputs(out,r)
            text=(out/'entry-scan.html').read_text(encoding='utf-8');self.assertIn('진입후보',text)
            self.assertTrue((out/'candidates.csv').is_file());self.assertFalse((out/'taver-scan.html').exists())
            def draw(b,p,e):
                if e['market']=='KR':raise RuntimeError('test failure')
                p.write_bytes(b'fixture')
            charts=review.charts(r,screen,snap,out,draw)
            self.assertEqual((charts['requested'],charts['available'],charts['failed']),(2,1,1))
            self.assertIn('차트 생성 실패','\n'.join(review.report_lines(r,charts)))

    def test_daily_report_uses_only_active_entries(self):
        import daily_market as daily
        screen,snap=fixture();screen.update(unavailable_count=0,research_order={'KR':[{'symbol':'LEGACY_ONLY'}]})
        run={'state':'partial','processing_state':'completed_with_exceptions','mode':'replay','started_at':'now',
             'stages':{},'quality':{'received':4,'requested':4,'failed':0,'quality_issue_series':0,
             'state':'partial','processing_state':'completed_with_exceptions','data_complete':False},
             'data_times':{'fetched_at':'now','collected_until':'now'}}
        for rs_min in (80,100):
            r=review.build(screen,snap,{'strategy':'pdf_momentum','momentum':{'rs_min':rs_min}})
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);review.write(out/'priority-review.json',r)
                review.write(out/'priority-charts.json',{'requested':2,'available':0,'failed':2,'rows':[]})
                daily.render(run,out,screen)
                page=(out/'report.html').read_text(encoding='utf-8')
                self.assertNotIn('LEGACY_ONLY',page)
                self.assertIn('추세, 조정, 돌파',page);self.assertIn('진입후보',page)
                self.assertIn('href="entry-scan.html"',page)
                self.assertNotIn('## 조사 순서',page)
        for item in snap['series']:item['bars'][-1]['volume']=1
        r=review.build(screen,snap,{'strategy':'pdf_momentum'})
        self.assertEqual(r['candidate_count'],0)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);review.write(out/'priority-review.json',r);daily.render(run,out,screen)
            page=(out/'report.html').read_text(encoding='utf-8')
            self.assertIn('현재 적극적 진입 조건을 모두 충족한 종목이 없습니다.',page)
            self.assertNotIn('LEGACY_ONLY',page)

    def test_future_append_does_not_change_pool_or_candidates(self):
        screen,snap=fixture();before=review.build(screen,snap,{'strategy':'pdf_momentum'})
        for item in snap['series']:
            future=copy.deepcopy(item['bars'][-1]);future['date']='2099-01-01';future.update(open=900,low=800,close=1000,high=1100)
            item['bars'].append(future)
        self.assertEqual(before,review.build(screen,snap,{'strategy':'pdf_momentum'}))

if __name__=='__main__':unittest.main()

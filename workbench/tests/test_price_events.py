import copy
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from price_events import build, sessions, validate_research
import test_analysis_skill_link as base_tests


def weekdays(start='2024-01-01',count=180):
    days=[];day=date.fromisoformat(start)
    while len(days)<count:
        if day.weekday()<5: days.append(day.isoformat())
        day+=timedelta(days=1)
    return days


class PriceEventsTests(unittest.TestCase):
    def setUp(self):
        self.days=weekdays(count=300)
        self.bars=[dict(date=d,open=100,high=102,low=98,close=100,volume=1000) for d in self.days[:90]]
        self.mock=patch('price_events.sessions',return_value=tuple(self.days));self.mock.start();self.addCleanup(self.mock.stop)
    def run_events(self,bars=None,financial=None):
        return build(dict(market='US',symbol='TEST',provider='fixture',price_basis='adjusted',bars=bars if bars is not None else self.bars),financial)

    def test_exact_gap_threshold_and_full_gap(self):
        self.bars[30].update(open=103,high=105,low=103,close=104,volume=2000)
        up=next(e for e in self.run_events()['events'] if e['event_date']==self.days[30] and e['kind']=='gap')
        self.assertAlmostEqual(up['gap_pct'],3);self.assertTrue(up['full_gap']);self.assertEqual(up['status'],'confirmed')
        self.assertEqual(up['metrics']['volume_change_pct'],100);self.assertEqual(up['metrics']['volume_ratio_20'],2)
        self.assertEqual(up['news_window']['start'],self.days[27]);self.assertEqual(up['news_window']['end'],self.days[32])
    def test_down_gap_and_partial_overlap(self):
        self.bars[30].update(open=97,high=100,low=96,close=98)
        e=next(e for e in self.run_events()['events'] if e['event_date']==self.days[30] and e['kind']=='gap')
        self.assertAlmostEqual(e['gap_pct'],-3);self.assertFalse(e['full_gap'])
    def test_bundles_do_not_chain(self):
        for i in [30,32,34]:self.bars[i].update(open=104,high=106,low=98,close=100)
        p=self.run_events();es=[e for e in p['events'] if e['kind']=='gap']
        self.assertEqual(es[0]['group_id'],es[1]['group_id']);self.assertNotEqual(es[1]['group_id'],es[2]['group_id'])
        for g in p['groups']:self.assertLess(self.days.index(g['end'])-self.days.index(g['start']),3)
    def test_null_volume_is_not_zero(self):
        self.bars[30].update(open=104,high=106,volume=None)
        e=next(e for e in self.run_events()['events'] if e['kind']=='gap')
        self.assertIsNone(e['metrics']['volume_change_pct']);self.assertIsNone(e['metrics']['volume_ratio_20'])
    def test_followup_is_labeled_ex_post_and_strong(self):
        self.bars[30].update(open=104,high=105,low=99,close=100)
        for i in range(31,51):self.bars[i].update(open=120,high=121,low=119,close=120)
        p=self.run_events();e=next(e for e in p['events'] if e['kind']=='gap' and e['event_date']==self.days[30])
        self.assertAlmostEqual(e['metrics']['followup_return_20_pct'],20);self.assertEqual(e['metrics']['metrics_as_of'],self.days[89])
    def test_input_immutable_and_deterministic(self):
        original=copy.deepcopy(self.bars);self.assertEqual(self.run_events(),self.run_events());self.assertEqual(original,self.bars)
    def test_calendar_failure_is_explicit(self):
        self.bars[30].update(open=104,high=106)
        with patch('price_events.sessions',side_effect=ValueError('calendar')):
            p=self.run_events();self.assertEqual(p['calendar_state'],'unavailable_observed_sessions_only');self.assertTrue(all(e['status']=='provisional' for e in p['events']))
    def test_research_validation_and_provisional_guard(self):
        self.bars[30].update(open=104,high=106);p=self.run_events();gid=p['groups'][0]['id']
        with self.assertRaises(ValueError):validate_research([{'group_id':gid,'status':'cause_unconfirmed'}],p)
        self.assertEqual(validate_research([{'group_id':gid,'status':'failed','failure_reason':'network unavailable'}],p)[0]['status'],'failed')
        p['groups'][0]['confirmed_event_ids']=[]
        with self.assertRaises(ValueError):validate_research([{'group_id':gid,'status':'failed','failure_reason':'network'}],p)
    def test_five_year_cutoff(self):
        days=weekdays('2019-01-01',2200);bars=[dict(date=d,open=100,high=102,low=98,close=100,volume=1000) for d in days[:1800]]
        bars[20].update(open=104,high=106);bars[1750].update(open=104,high=106)
        with patch('price_events.sessions',return_value=tuple(days)):
            p=self.run_events(bars);self.assertTrue(all(e['event_date']>=p['start'] for e in p['events']));self.assertFalse(any(e['event_date']==days[20] for e in p['events']))


class WeeklySwingTests(unittest.TestCase):
    def setUp(self):
        self.days=weekdays(count=400)
        self.bars=[dict(date=d,open=100,high=101,low=99,close=100,volume=1000) for d in self.days[:250]]
        for week,kind,value in [(6,'high',130),(14,'low',70),(22,'high',140),(30,'low',60)]:
            self.bars[week*5+1][kind]=value
        self.mock=patch('price_events.sessions',return_value=tuple(self.days));self.mock.start();self.addCleanup(self.mock.stop)

    def packet(self,bars=None):
        return build(dict(market='US',symbol='SYNTH',bars=self.bars if bars is None else bars))

    def pivots(self,bars=None):
        return [e for e in self.packet(bars)['events'] if e['kind']!='gap']

    def test_alternation_real_dates_and_separate_confirmation(self):
        rows=self.pivots()
        self.assertEqual([e['kind'] for e in rows],['high','low','high','low'])
        self.assertEqual(rows[0]['event_date'],self.days[31])
        self.assertEqual(rows[0]['local_confirmed_at'],self.days[49])
        self.assertEqual(rows[0]['confirmed_at'],self.days[89])
        self.assertEqual(rows[-1]['status'],'provisional')
        self.assertTrue(all(e['timeframe']=='1w' for e in rows))
        self.assertTrue(all(e['confirmation_rule']=='weekly-swing-2' for e in rows))

    def test_in_progress_week_is_preview_without_confirming_previous_leg(self):
        bars=self.bars[:112]  # Wednesday's data is not required to preview Tuesday.
        rows=self.pivots(bars)
        tail=next(e for e in rows if e['event_date']==self.days[111])
        self.assertEqual(tail['status'],'provisional')
        self.assertFalse(tail['period_complete'])
        self.assertIsNone(tail['local_confirmed_at'])
        self.assertEqual(tail['right_bars_available'],0)
        previous=next(e for e in rows if e['event_date']==self.days[71])
        self.assertEqual(previous['status'],'provisional')
        self.assertEqual(previous['selection_state'],'awaiting_opposite_confirmation')
        self.assertEqual(rows[0]['status'],'confirmed')

    def test_missing_session_in_current_week_is_not_a_preview(self):
        rows=self.pivots(self.bars[:110]+self.bars[111:112])
        self.assertFalse(any(e['event_date']==self.days[111] for e in rows))

    def test_preview_retracts_when_later_same_week_price_invalidates_it(self):
        bars=copy.deepcopy(self.bars[:112])
        self.assertTrue(any(e['event_date']==self.days[111] for e in self.pivots(bars)))
        bars.append({**self.bars[112], 'high':150})
        rows=self.pivots(bars)
        self.assertFalse(any(e['event_date']==self.days[111] for e in rows))
        self.assertTrue(any(e['event_date']==self.days[112] and e['status']=='provisional' for e in rows))

    def test_daily_prefixes_preserve_closed_extrema(self):
        final={e['id']:e for e in self.pivots() if e['status']=='confirmed'}
        for end in range(45,251):
            for e in self.pivots(self.bars[:end]):
                if e['status']=='confirmed':
                    self.assertIn(e['id'],final)
                    self.assertEqual(e['confirmed_at'],final[e['id']]['confirmed_at'])

    def test_small_fluctuations_removed_even_with_volume(self):
        for b in self.bars:b.update(high=101,low=99)
        self.bars[31].update(high=108,volume=100000)
        self.assertEqual(self.pivots(),[])

    def test_exact_ten_percent_threshold(self):
        for b in self.bars:b.update(high=90,low=89,open=90,close=90)
        self.bars[31]['high']=100
        self.bars[41]['low']=90  # baseline 89 already supplies >10%; set flanks exactly90.
        for i in range(15,50):
            if i!=31:self.bars[i].update(high=90,low=90)
        rows=self.pivots()
        self.assertEqual(rows[0]['event_date'],self.days[31])
        self.assertAlmostEqual(rows[0]['prominence_left_pct'],10)
        self.bars[31]['high']=99.99
        self.assertFalse(any(e['event_date']==self.days[31] for e in self.pivots()))

    def test_weaker_repeated_high_removed_and_plateau_earliest(self):
        self.bars[51]['high']=125
        self.bars[32]['high']=130
        rows=self.pivots()
        self.assertEqual(rows[0]['event_date'],self.days[31])
        self.assertFalse(any(e['event_date']==self.days[51] for e in rows))

    def test_stronger_repeated_high_replaces_only_open_leg(self):
        self.bars[51]['high']=135
        rows=self.pivots()
        self.assertEqual(rows[0]['event_date'],self.days[51])
        self.assertEqual(rows[0]['status'],'confirmed')

    def test_missing_session_never_confirms_affected_local_window(self):
        rows=self.pivots([b for i,b in enumerate(self.bars) if i!=46])
        self.assertFalse(any(e['event_date']==self.days[31] and e['status']=='confirmed' for e in rows))

    def test_missing_week_between_swings_never_closes_leg(self):
        rows=self.pivots([b for i,b in enumerate(self.bars) if not 50<=i<55])
        high=next(e for e in rows if e['event_date']==self.days[31])
        self.assertEqual(high['status'],'provisional');self.assertEqual(high['selection_state'],'data_gap')

    def test_prefix_replay_never_changes_closed_extrema(self):
        final={e['id']:e for e in self.pivots() if e['status']=='confirmed'}
        for end in range(45,251,5):
            for e in self.pivots(self.bars[:end]):
                if e['status']=='confirmed':
                    self.assertIn(e['id'],final)
                    self.assertEqual(e['confirmed_at'],final[e['id']]['confirmed_at'])
                    self.assertLessEqual(e['confirmed_at'],self.bars[end-1]['date'])

    def test_same_day_gap_preserved_and_swing_research_priority(self):
        self.bars[31].update(open=104,close=104,low=103)
        packet=self.packet();rows=[e for e in packet['events'] if e['event_date']==self.days[31]]
        self.assertEqual({e['kind'] for e in rows},{'gap','high'})
        self.assertEqual(len({e['group_id'] for e in rows}),1)
        group=next(g for g in packet['groups'] if g['id']==rows[0]['group_id'])
        self.assertEqual(group['priority'],'high')

    def test_calendar_failure_does_not_fabricate_swings(self):
        with patch('price_events.sessions',side_effect=ValueError('missing_calendar')):
            self.assertEqual(self.pivots(),[])

    def test_holiday_local_confirmation_uses_actual_last_session(self):
        self.mock.stop()
        days=sessions('US','2024-02-01','2024-05-31')
        bars=[dict(date=d,open=100,high=101,low=99,close=100,volume=1000) for d in days]
        next(b for b in bars if b['date']=='2024-03-05')['high']=130
        next(b for b in bars if b['date']=='2024-04-16')['low']=70
        e=next(e for e in self.pivots(bars) if e['kind']=='high')
        self.assertEqual(e['local_confirmed_at'],'2024-03-28')
        self.assertEqual(e['confirmed_at'],'2024-05-10')



class ResearchSnapshotTests(base_tests.AnalysisSkillLinkTests):
    def test_frame_independent_events_and_financial_hash(self):
        before=self.service.snapshot(self.cid,self.sid)
        self.service.dispatch('context',{'analysis_key':self.cid,'context':{'timeframe':'1mo','range':'3'}},ORIGIN:='http://127.0.0.1:1234')
        after=self.service.dispatch('read',{'analysis_key':self.cid},ORIGIN)
        self.assertEqual(before['component_hashes']['price_events'],after['component_hashes']['price_events'])
        self.assertEqual(before['component_hashes']['financials'],after['component_hashes']['financials'])
    def test_legacy_snapshot_not_backfilled(self):
        from analysis_service import digest,packed
        snap=self.service.snapshot(self.cid,self.sid);snap.pop('price_events')
        with closing(self.store.connect()) as db,db:db.execute('UPDATE analysis_snapshots SET payload=?,sha256=? WHERE id=?',(packed(snap),digest(snap),self.sid))
        p=self.read(section='events');self.assertEqual(p['price_events']['state'],'not_in_legacy_snapshot');self.assertEqual(p['events'],[])
    def test_event_research_save_and_replay(self):
        self.stock['bars'][3].update(open=120,high=122,low=117,close=120)
        detail=self.create();sid=detail['snapshots'][0]['id'];cid=detail['case']['id']
        p=self.read(section='research_groups',analysis_key=cid,snapshot_id=sid,limit=1)
        gid=p['groups'][0]['id'];record=dict(analysis_key=cid,snapshot_id=sid,kind='report',title='trace',body='fixture',author='llm',event_research=[{'group_id':gid,'status':'failed','failure_reason':'fixture denied'}])
        r=self.service.dispatch('record',record,'http://127.0.0.1:1234')
        self.assertEqual(self.service.item(cid,r['id'])['event_research'],record['event_research'])
        self.assertEqual(self.read(section='research_groups',analysis_key=cid,snapshot_id=sid)['groups'][0]['research_status'],'unresearched')


if __name__=='__main__': unittest.main()

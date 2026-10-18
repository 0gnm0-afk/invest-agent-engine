"""All records in this suite are synthetic and isolated from runtime delivery."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from chart_workspace import WorkspaceStore, defaults, Conflict
from scenario_monitor import ScenarioStore, levels, line_state
from telegram_warnings import dispatch, scenario_message

class Scenarios(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.path=self.root/'runtime/chart-workspaces.sqlite3';self.store=ScenarioStore(self.path);self.ws=WorkspaceStore(self.path)
  self.now=datetime(2026,10,8,tzinfo=timezone.utc)
  self.bars=[dict(date=(datetime(2026,9,1)+timedelta(days=i)).date().isoformat(),open=100,high=102,low=98,close=100,volume=10) for i in range(15)]
  self.identity=dict(stock_key='US:S',market='US',symbol='S',name='합성종목',currency='USD',stable_id='SYNTHETIC_ISIN',price_basis='adjusted',exchange='NASDAQ')
  self.stock=dict(currency='USD',isin_code='SYNTHETIC_ISIN',price_basis='adjusted',exchange='NASDAQ',bars=self.bars)
  self.bundle=dict(as_of=self.now.isoformat(),query_state='received',accounts=[dict(alias='PRIVATE_ACCOUNT',holdings_complete=True,positions=[dict(market='US',symbol='S',quantity=2,currency='USD')])])
  self.state=defaults();self.state['drawings']=[dict(id='box',kind='box',points=[dict(time='2026-09-01',price=90),dict(time='2026-09-15',price=110)],color='#2271b2',width=2,extend=False)]
  self.ws.shared('US:S',revision=0,state=self.state)
 def register(self,**kw):
  return self.store.register(stock_key='US:S',identity=self.identity,case_id='an_synthetic',plan_record_id='it_synthetic',plan_id='pl_synthetic',plan_version=1,drawing_id='box',workspace_revision=1,chart=dict(bars=self.bars),test_record=True,**kw)
 def update(self):
  return self.store.update(self.bundle,{'US:S':self.stock},{'US':self.bars[-1]['date']},self.now,{'US':[b['date'] for b in self.bars]})
 def add(self,close):
  self.bars.append(dict(date=(datetime.fromisoformat(self.bars[-1]['date'])+timedelta(days=1)).date().isoformat(),open=close,close=close,high=close+2,low=close-2,volume=10))
 def test_atr_and_approach_touch_breakdown_recovery(self):
  self.register();self.update();self.add(92);r=self.update();self.assertEqual(r['events'][0]['state'],'approach')
  self.assertEqual(len(self.update()['events']),1)
  self.add(90);r=self.update();self.assertEqual(r['events'][0]['state'],'reached')
  self.add(89);r=self.update();self.assertEqual(r['events'][0]['state'],'breakdown')
  self.add(88);self.assertEqual(len(self.update()['events']),3)
  self.add(100);self.update();self.add(89);self.assertEqual(len(self.update()['events']),4)
 def test_upper_and_lower_independent_and_gap(self):
  self.register();self.update();self.add(120);r=self.update();self.assertEqual(r['events'][0]['boundary'],'upper');self.assertEqual(r['events'][0]['state'],'breakout')
  self.add(80);r=self.update();self.assertEqual(r['events'][0]['boundary'],'lower')
 def test_saved_edits_history_and_snapshot_preserved(self):
  m=self.register();self.update();original=deepcopy(m['drawing'])
  self.state['drawings'][0]['points'][0]['price']=101
  self.ws.shared('US:S',revision=1,state=self.state);self.store.sync_workspace('US:S')
  current=self.store.read()['monitors'][0];self.assertTrue(current['latest']['superseded']);self.assertEqual(current['revision'],2)
  self.assertEqual(self.store.history(m['id'])[0]['monitor']['drawing'],original)
  self.assertEqual(self.update()['monitors'][0]['latest']['lines'][0]['value'],101)
 def test_unsaved_and_unadopted_drawing_not_applied(self):
  self.register();self.state['drawings'][0]['points'][0]['price']=1000
  self.assertEqual(self.update()['monitors'][0]['latest']['lines'][0]['value'],90)
 def test_deleted_and_restored_drawing(self):
  self.register();original=deepcopy(self.state);self.state['drawings']=[]
  self.ws.shared('US:S',revision=1,state=self.state);r=self.update();self.assertEqual(r['monitors'][0]['status'],'deferred');self.assertFalse(r['events'])
  self.ws.shared('US:S',revision=2,state=original);self.assertIsNone(self.update()['monitors'][0]['issue'])
 def test_stale_account_holding_deferred_buy_still_evaluates(self):
  self.register();self.register(purpose='buy');self.bundle['query_state']='failed';self.add(89)
  r=self.update();self.assertEqual(r['monitors'][0]['issue'],'account_stale_or_incomplete');self.assertIsNone(r['monitors'][1]['issue'])
  self.assertEqual(len(r['events']),1)
 def test_buy_confirmed_holding_once_and_decline_persists(self):
  self.register(purpose='buy');m=self.update()['monitors'][0];self.assertEqual(m['conversion']['status'],'pending')
  m=self.store.resolve_conversion(m['id'],m['revision'],False)
  self.assertEqual(self.update()['monitors'][0]['conversion']['status'],'declined');self.assertEqual(m['purpose'],'buy')
  with self.assertRaises(Conflict):self.store.resolve_conversion(m['id'],m['revision'],True)
 def test_buy_conversion_accepted(self):
  self.register(purpose='buy');m=self.update()['monitors'][0];m=self.store.resolve_conversion(m['id'],m['revision'],True)
  self.assertEqual(m['purpose'],'holding');self.assertEqual(m['conversion']['status'],'accepted')
  self.assertEqual(m['status'],'pending');self.assertTrue(m['latest']['superseded'])
  self.assertNotEqual(m['latest']['monitor_revision'],m['revision'])
  self.assertFalse(self.update()['monitors'][0]['latest']['superseded'])
 def test_conversion_decline_preserves_evaluation_and_delivery_revision(self):
  self.register(purpose='buy');m=self.update()['monitors'][0];revision=m['revision'];events=self.store.read()['events']
  declined=self.store.resolve_conversion(m['id'],revision,False)
  self.assertEqual(declined['revision'],revision);self.assertFalse(declined['latest']['superseded'])
  self.assertEqual(declined['latest']['monitor_revision'],revision)
  self.assertTrue(all(e['monitor_revision']==revision for e in events))
  result=self.update();self.assertEqual(result['events'],events)
  self.assertTrue(any(e['revision']==revision for e in result['monitors'][0]['evaluations']))
 def test_no_holding_no_conversion(self):
  self.register(purpose='buy');self.bundle['accounts'][0]['positions']=[];self.assertIsNone(self.update()['monitors'][0]['conversion'])
 def test_incomplete_account_no_conversion(self):
  self.register(purpose='buy');self.bundle['accounts'][0]['holdings_complete']=False;self.assertIsNone(self.update()['monitors'][0]['conversion'])
 def test_stale_missing_gap_and_identity_defer(self):
  self.register();self.bars.pop(3);r=self.store.update(self.bundle,{'US:S':self.stock},{'US':self.bars[-1]['date']},self.now,{'US':[(datetime(2026,9,1)+timedelta(days=i)).date().isoformat() for i in range(15)]})
  self.assertEqual(r['monitors'][0]['issue'],'price_sessions_incomplete');self.assertFalse(r['events'])
  self.stock['isin_code']='DIFFERENT';self.assertEqual(self.update()['monitors'][0]['issue'],'market_identity_or_basis_changed')
 def test_price_correction_and_date_regression(self):
  self.register();self.update();self.bars[-1]['high']=103;self.add(80)
  self.assertEqual(self.update()['monitors'][0]['issue'],'processed_price_history_changed');self.assertFalse(self.store.read()['events'])
 def test_revision_conflicts_and_duplicate(self):
  m=self.register()
  with self.assertRaises(Conflict):self.register()
  with self.assertRaises(Conflict):self.store.configure(m['id'],0,tolerance_atr=1)
  m=self.store.configure(m['id'],1,tolerance_atr=1,active=False);self.assertEqual(m['tolerance_atr'],1)
  self.assertEqual(self.update()['monitors'][0]['status'],'paused')
 def test_tolerance_validation(self):
  for v in (-1,11,float('nan'),True):
   with self.assertRaises(ValueError):self.register(tolerance_atr=v)
 def test_unchanged_settings_and_style_do_not_reemit(self):
  m=self.register();self.add(89);self.update()
  m=self.store.configure(m['id'],1,tolerance_atr=.5,active=True);self.assertEqual(m['revision'],1)
  self.state['drawings'][0]['color']='#ffffff';self.ws.shared('US:S',revision=1,state=self.state)
  result=self.update();self.assertEqual(len(result['events']),1);self.assertEqual(result['monitors'][0]['revision'],1)
 def test_daily_evaluations_remain_immutable_and_not_duplicated(self):
  self.register();self.update();self.update();self.add(89);result=self.update()
  self.assertEqual(len(result['monitors'][0]['evaluations']),2)
  self.assertEqual(result['monitors'][0]['evaluations'][-1]['close'],100)
 def test_recovery_between_missed_runs_rearms_latest_event(self):
  self.register();self.add(89);self.update();self.add(100);self.add(89);self.add(88)
  result=self.update();self.assertEqual(len(result['events']),2)
  self.assertEqual(result['events'][0]['date'],self.bars[-1]['date'])
 def test_linear_channel_actual_daily_axis(self):
  drawing=dict(id='c',kind='channel',points=[dict(time='2026-09-01',price=80),dict(time='2026-09-11',price=90),dict(time='2026-09-01',price=100)],color='#2271b2',width=2,extend=True)
  v=levels(drawing,dict(timeframe='1d',bars=self.bars),'2026-09-15','both','holding')
  self.assertEqual(v,[dict(name='lower',value=94),dict(name='upper',value=114)])
 def test_weekly_geometry_keeps_slope(self):
  drawing=dict(id='c',kind='channel',points=[dict(time='2026-08-31',price=80),dict(time='2026-09-07',price=90),dict(time='2026-08-31',price=100)],color='#2271b2',width=2,extend=True)
  chart=dict(timeframe='1w',bars=[dict(date='2026-08-31',last_session='2026-09-04'),dict(date='2026-09-07',last_session='2026-09-11'),dict(date='2026-09-14',last_session='2026-09-18')])
  early=levels(drawing,chart,'2026-09-14','both','holding');later=levels(drawing,chart,'2026-09-18','both','holding')
  self.assertGreater(later[0]['value'],early[0]['value']);self.assertAlmostEqual(later[1]['value']-later[0]['value'],20)
 def test_riskreward_boundary(self):
  d=dict(id='rr',kind='riskreward',points=[dict(time='2026-09-01',price=100),dict(time='2026-09-15',price=90),dict(time='2026-09-15',price=120)],color='#2271b2',width=2,extend=False)
  self.assertEqual([x['name'] for x in levels(d,dict(timeframe='1d',bars=self.bars),'2026-09-15','both','buy')],['entry'])
  self.assertEqual([x['name'] for x in levels(d,dict(timeframe='1d',bars=self.bars),'2026-09-15','both','holding')],['stop','target'])
 def test_synthetic_events_never_dispatched(self):
  self.register();self.add(89);self.update();sent=[]
  dispatch(self.root,{'US':self.bars[-1]['date']},config=dict(enabled_at='2020-01-01',chat='fake'),sender=lambda message:sent.append(message) or {'message_id':1})
  self.assertFalse(sent)
 def test_buy_zone_center_is_reached_and_exit_is_distinct(self):
  self.register(purpose='buy');self.bundle['accounts'][0]['positions']=[]
  result=self.update();self.assertEqual(result['monitors'][0]['latest']['states'],{'zone':'reached'})
  self.assertEqual(result['events'][0]['boundary'],'zone');self.assertEqual(result['events'][0]['zone_lower'],90)
  message=scenario_message(result['events'][0]);self.assertIn('대응 기준 미정',message);self.assertIn('도달 사건을',message)
  self.assertIn('종가 100.00 USD\n매수 구간: 90.00 USD ~ 110.00 USD',message)
  self.assertEqual(len(self.update()['events']),1)
  self.add(120);self.assertEqual(self.update()['events'][0]['state'],'breakout')
  self.add(125);self.assertEqual(len(self.update()['events']),2)
 def test_read_staleness_is_display_only(self):
  self.register();self.update()
  stale=self.store.read(now=self.now+timedelta(days=2),targets={'US':'2026-09-17'})['monitors'][0]
  self.assertEqual(stale['status'],'deferred');self.assertEqual(stale['issue'],'account_stale_or_incomplete')
  self.assertIsNone(self.store.read()['monitors'][0]['issue'])
 def test_account_alias_does_not_use_other_account(self):
  self.register(account_alias='empty');self.bundle['accounts'].append(dict(alias='empty',holdings_complete=True,positions=[]))
  self.assertEqual(self.update()['monitors'][0]['issue'],'holding_not_confirmed')
 def test_each_boundary_uses_its_confirmed_response(self):
  self.register(confirmed_response='전체 시나리오',confirmed_responses={'lower':'하단 재검토','upper':'상단 검토'})
  self.add(89);self.assertEqual(self.update()['events'][0]['confirmed_response'],'하단 재검토')
  self.add(120);self.assertEqual(self.update()['events'][0]['confirmed_response'],'상단 검토')
 def test_saved_versions_recover_every_missed_hook(self):
  m=self.register();self.state['drawings'][0]['points'][0]['price']=92;self.ws.shared('US:S',revision=1,state=self.state)
  self.state['drawings'][0]['points'][0]['price']=95;self.ws.shared('US:S',revision=2,state=self.state)
  self.store.sync_workspace('US:S');h=self.store.history(m['id'])
  self.assertEqual([r['monitor']['drawing']['points'][0]['price'] for r in h],[90,92,95])
 def test_failed_evaluation_delivery_gate(self):
  self.register();self.add(89);self.update();event=self.store.read()['events'][0];event['test_record']=False
  with closing(self.store.connect()) as db,db:db.execute('UPDATE scenario_monitor_events SET payload=? WHERE id=?',(__import__('json').dumps(event),event['id']))
  sent=[];dispatch(self.root,{'US':self.bars[-1]['date']},config=dict(enabled_at='2020-01-01',chat='fake'),sender=lambda msg:sent.append(msg) or {'message_id':1},include_scenarios=False,include_trailing=False)
  self.assertFalse(sent)
 def test_message_and_delivery_duplicate_unknown(self):
  self.register();self.add(89);self.update();event=self.store.read()['events'][0]
  event['test_record']=False
  with closing(self.store.connect()) as db,db:db.execute('UPDATE scenario_monitor_events SET payload=? WHERE id=?',(__import__('json').dumps(event),event['id']))
  sent=[];send=lambda msg:sent.append(msg) or {'message_id':1}
  config=dict(enabled_at='2020-01-01',chat='fake')
  dispatch(self.root,{'US':self.bars[-1]['date']},config=config,sender=send);dispatch(self.root,{'US':self.bars[-1]['date']},config=config,sender=send)
  self.assertEqual(len(sent),1);self.assertIn('다음 행동',sent[0]);self.assertIn('분석 요청',sent[0]);self.assertNotIn('PRIVATE_ACCOUNT',sent[0]);self.assertIn('90.00 USD',sent[0])

if __name__=='__main__':unittest.main()

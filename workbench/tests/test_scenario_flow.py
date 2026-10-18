"""Isolated saved-record → drawing plan → confirmed watch integration."""
from contextlib import closing
from copy import deepcopy
import unittest

import test_collaboration as fixtures
from analysis_collaboration import Forbidden
from chart_workspace import defaults, Conflict


class ScenarioFlowTests(unittest.TestCase):
    setUp = fixtures.CollaborationTests.setUp
    tearDown = fixtures.CollaborationTests.tearDown
    call = fixtures.CollaborationTests.call
    read = fixtures.CollaborationTests.read
    adopt = fixtures.CollaborationTests.adopt
    envelope = fixtures.CollaborationTests.envelope

    def drawing(self, kind='box'):
        prices = {'box':[95,110], 'channel':[95,100,110], 'riskreward':[100,95,110]}[kind]
        points = [dict(time=d,price=p) for d,p in zip(('2026-09-18','2026-09-23','2026-09-22'),prices)]
        state=defaults();state['drawings']=[dict(id='scenario',kind=kind,points=points,color='#218775',width=2,extend=True)]
        current=self.store.shared('US:SYNTH')
        return self.store.shared('US:SYNTH',revision=current['revision'],state=state)

    def draft(self, **extra):
        current=self.store.shared('US:SYNTH')
        return self.call('scenario_draft', dict(expected_revision=self.read()['revision'],drawing_id='scenario',
            workspace_revision=current['revision'], title='명확히 합성 시나리오',purpose='통합 검증',response=None, **extra),
            role='browser',session='fixture')['record']

    def register(self, plan, purpose='buy', **extra):
        return self.call('monitor_register',dict(plan_record_id=plan['id'],drawing_id='scenario',
            workspace_revision=self.store.shared('US:SYNTH')['revision'],purpose=purpose,boundary='both',tolerance_atr=.5,**extra),
            role='browser',session='fixture')

    def test_box_channel_riskreward_frozen_plan_and_two_purposes(self):
        for kind in ('box','channel','riskreward'):
            with self.subTest(kind=kind):
                self.drawing(kind);plan=self.draft()
                with self.assertRaisesRegex(ValueError,'not_adopted'): self.register(plan)
                self.adopt(plan)
                buy=self.register(plan);held=self.register(plan,'holding')
                self.assertNotEqual(buy['id'],held['id'])
                self.assertEqual(buy['plan_record_id'],plan['id'])
                self.assertTrue(buy['test_record'])
                self.assertEqual(buy['confirmed_response'],'대응 기준 미정')
                self.assertTrue(all(c['price'] is None for c in plan['plan']['conditions']))
                self.assertIsNone(plan['plan']['allocation']['quantity'])
                self.assertEqual(plan['references'],[])
                before=deepcopy(self.s.snapshot(self.cid,plan['snapshot_id']))
                state=self.store.shared('US:SYNTH');state['state']['drawings'][0]['points'][0]['price']+=1
                self.store.shared('US:SYNTH',revision=state['revision'],state=state['state'])
                from scenario_monitor import ScenarioStore
                monitor=ScenarioStore(self.store.path);monitor.sync_workspace('US:SYNTH')
                changed=next(m for m in monitor.read()['monitors'] if m['id']==buy['id'])
                self.assertGreater(changed['revision'],buy['revision'])
                self.assertEqual(self.s.snapshot(self.cid,plan['snapshot_id']),before)
                self.assertEqual(self.s.item(self.cid,plan['id']),plan)
                self.assertGreaterEqual(len(monitor.history(buy['id'])),2)

    def test_stale_saved_drawing_requires_new_draft(self):
        self.drawing();plan=self.draft();self.adopt(plan);self.drawing()
        with self.assertRaises(Conflict):self.register(plan)

    def test_simple_editor_preserves_full_plan_by_rejecting_replacement(self):
        self.drawing()
        original=fixtures.CollaborationTests.plan(self)
        with self.assertRaisesRegex(ValueError,'full_plan_requires_full_editor'):
            self.draft(plan_id=original['plan_id'],expected_plan_version=original['plan_version'])
        self.assertEqual(self.s.item(self.cid,original['id']),original)

    def test_conversion_accept_rechecks_current_saved_account(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from scenario_monitor import ScenarioStore
        self.drawing();plan=self.draft();self.adopt(plan);watch=self.register(plan)
        args=dict(id=watch['id'],revision=watch['revision'],accept=True)
        # The downstream atomic one-time decision is tested in test_scenario_monitor.
        with patch.object(ScenarioStore,'resolve_conversion',return_value={'accepted':True}) as decide:
            with self.assertRaisesRegex(ValueError,'holding_confirmation_stale_or_missing'):
                self.s.dispatch('monitor_conversion',args,self.origin,principal='browser',session='fixture')
            decide.assert_not_called()
            self.accounts.update(as_of=datetime.now(timezone.utc).isoformat(),query_state='received')
            self.accounts['accounts'][0]['holdings_complete']=True
            result=self.s.dispatch('monitor_conversion',args,self.origin,principal='browser',session='fixture')
            self.assertTrue(result['accepted']);decide.assert_called_once()

    def test_monitor_and_scenario_writes_require_browser(self):
        self.drawing();plan=self.draft();self.adopt(plan)
        for role in ('legacy','collaboration','risk'):
            for op,args in [('scenario_draft',{}),('monitor_register',{}),('monitor_update',{}),('monitor_conversion',{})]:
                with self.subTest(role=role,op=op),self.assertRaises(Forbidden):self.call(op,args,role=role)

    def test_workspace_versions_and_resume_preserve_original_records(self):
        self.drawing();plan=self.draft();self.adopt(plan);watch=self.register(plan)
        self.drawing('channel')
        with closing(self.store.connect()) as db:
            rows=db.execute('SELECT revision,payload FROM chart_workspace_versions ORDER BY revision').fetchall()
        self.assertEqual([r[0] for r in rows],[0,1,2])
        from analysis_service import AnalysisService
        reopened=AnalysisService(self.store,self.hist,self.fin,self.view,self.root)
        listing=reopened.dispatch('list',{'stock_key':'US:SYNTH'},self.origin)
        self.assertEqual(listing['cases'][0]['latest_record_id'],self.read()['active_plans'][0]['adoption']['id'])
        value=reopened.dispatch('monitor_list',{'stock_key':'US:SYNTH'},self.origin)
        self.assertEqual(value['monitors'][0]['id'],watch['id'])
        self.assertIn('history',value['monitors'][0])
        self.assertIn('analysis='+self.cid,value['monitors'][0]['detail_url'])

    def test_provisional_weekly_research_does_not_change_pivot_status(self):
        from price_events import validate_research
        packet={'groups':[{'id':'g','event_ids':['p'],'confirmed_event_ids':[]}],
                'events':[{'id':'p','kind':'high','timeframe':'1w','status':'provisional'}]}
        before=deepcopy(packet)
        result=validate_research([dict(group_id='g',status='evidence_found',finding='합성 출처를 조사했으나 점은 잠정',
            searched_windows=[dict(start='2026-09-18',end='2026-09-23',query='synthetic')],
            sources=[dict(url='https://example.com/synthetic')])],packet)
        self.assertEqual(result[0]['status'],'evidence_found');self.assertEqual(packet,before)


if __name__=='__main__':unittest.main()

"""All inputs/providers/HTTP endpoints are synthetic and rooted in TemporaryDirectory."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from analysis_service import AnalysisService, digest
from analysis_collaboration import VERSION, Forbidden
from chart_workspace import WorkspaceStore, Conflict, defaults

FIXTURE = json.loads((ROOT / 'tests/fixtures/collaboration-v1.json').read_text(encoding='utf-8'))


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.store = WorkspaceStore(self.root / 'chart.sqlite3')
        self.bars = [dict(date=d, open=100, high=112, low=90, close=105, volume=1000)
                     for d in ('2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23')]
        self.stock = dict(symbol='SYNTH', market='US', name='Synthetic only', currency='USD', bars=self.bars,
                          isin_code='US0000000001', exchange='NASDAQ', price_basis='adjusted')
        self.accounts = {'as_of':'2026-10-01', 'accounts':[{'alias':'synthetic-us','positions':[
            dict(symbol='SYNTH', market='US', quantity='10', currency='USD')]}], 'risk':{}}
        self.data = {'accounts':self.accounts, 'market':{'state':'partial'}}
        self.hist = SimpleNamespace(saved=SimpleNamespace(stocks={}, markets={}),
                                    view=lambda s,f:{**s, 'timeframe':f},
                                    ensure=lambda *a,**k: self.fail('provider access forbidden'))
        self.fin = SimpleNamespace(read=lambda k:{'state':'unavailable', 'result':None})
        self.view = lambda:(self.data, {'US:SYNTH':self.stock})
        self.s = AnalysisService(self.store, self.hist, self.fin, self.view, self.root)
        detail = self.s.dispatch('create', {'stock_key':'US:SYNTH','test_record':True}, 'http://127.0.0.1:1234')
        self.cid = detail['case']['id']; self.sid = detail['snapshots'][0]['id']; self.origin='http://127.0.0.1:1234'
    def tearDown(self): self.tmp.cleanup()
    def call(self, op, args=None, role='collaboration', session=None):
        return self.s.dispatch(op, {'analysis_key':self.cid, **(args or {})}, self.origin, principal=role, session=session)
    def read(self): return self.call('collaboration_read')
    def envelope(self):
        return dict(contract_version=VERSION, snapshot_id=self.sid, expected_revision=self.read()['revision'])
    def discussion(self, **changes):
        return self.call('discussion_save', {**self.envelope(), **deepcopy(FIXTURE['discussion']), **changes})['record']
    def plan(self, **changes):
        return self.call('plan_save', {**self.envelope(), 'plan_id':None, 'expected_plan_version':0,
                    'plan':deepcopy(FIXTURE['plan']), 'references':[], **changes})['record']
    def adopt(self, plan, action='adopt'):
        rev=self.read()['revision']
        review=self.call('plan_review',dict(plan_record_id=plan['id'],expected_revision=rev),role='browser',session='browser-fixture')
        return self.call('plan_decide',dict(contract_version=VERSION,expected_revision=rev,confirmation=review['confirmation'],action=action,reason='synthetic confirmation'),role='browser',session='browser-fixture')

    def test_resume_source_nulls_and_read_has_no_capture(self):
        record=self.discussion(); before=self.s.snapshot(self.cid,self.sid)
        snapshot_count=len(self.s.rows('analysis_snapshots',self.cid))
        reopened=AnalysisService(self.store, self.hist, self.fin, lambda:self.fail('read called provider view'), self.root)
        result=reopened.dispatch('collaboration_read', {'analysis_key':self.cid}, self.origin)
        self.assertEqual(result['current']['id'],record['id'])
        self.assertIsNone(result['current']['sections']['scenario_assumptions'])
        self.assertEqual(result['current']['references'][0]['text'],FIXTURE['discussion']['references'][0]['text'])
        self.assertEqual(len(self.s.rows('analysis_snapshots',self.cid)),snapshot_count)
        self.assertEqual(self.s.snapshot(self.cid,self.sid),before)
        self.assertEqual(result['active_plans'],[])
        self.assertIn('analysis_collaboration_read',result['links']['resume_prompt'])
        self.assertNotIn('risk_calculate',result['links']['resume_prompt'])
        self.assertIn('트레일링·MA5',result['links']['resume_prompt'])

    def test_additive_migration_keeps_legacy_records_snapshots_and_drawings(self):
        self.call('record',dict(snapshot_id=self.sid,kind='report',title='legacy',body='original',author='llm'))
        with closing(self.store.connect()) as db,db:
            before={table:db.execute('SELECT * FROM '+table).fetchall() for table in ('analysis_cases','analysis_snapshots','analysis_items','shared_chart_workspaces')}
            db.execute('DROP TABLE analysis_collaboration_heads')
        reopened=AnalysisService(self.store,self.hist,self.fin,self.view,self.root)
        with closing(self.store.connect()) as db:
            for table,rows in before.items():self.assertEqual(rows,db.execute('SELECT * FROM '+table).fetchall())
        value=reopened.collaboration.read(reopened.case(self.cid),self.origin)
        self.assertIsNone(value['current']);self.assertEqual(value['active_plans'],[]);self.assertEqual(len(value['legacy_records']),1)

    def test_draft_adoption_new_draft_replacement_and_revoke(self):
        p1=self.plan()
        with self.assertRaises(ValueError):self.call('plan_input',dict(plan_record_id=p1['id'],mode='adopted'))
        preview=self.call('plan_input',dict(plan_record_id=p1['id'],mode='hypothetical'))
        self.assertIsNone(preview['adoption']); self.assertIn('scenario.s1.fx_to_base',preview['blockers'])
        d1=self.adopt(p1)['record']
        p2=self.plan(plan_id=p1['plan_id'],expected_plan_version=1)
        self.assertEqual(self.read()['active_plans'][0]['record']['id'],p1['id'])
        self.assertEqual(p2['previous_record_id'],p1['id'])
        self.assertEqual(self.call('plan_input',dict(plan_record_id=p1['id'],mode='adopted'))['adoption']['id'],d1['id'])
        self.adopt(p2)
        self.assertEqual(len(self.read()['active_plans']),1)
        with self.assertRaises(Conflict):self.adopt(p1,'revoke')
        with self.assertRaises(ValueError):self.call('plan_input',dict(plan_record_id=p1['id'],mode='adopted'))
        self.adopt(p2,'revoke');self.assertEqual(self.read()['active_plans'],[])
        self.assertEqual(self.s.item(self.cid,p1['id']),p1)

    def test_roles_forgery_and_legacy_are_not_adoption(self):
        args={**self.envelope(),**deepcopy(FIXTURE['discussion'])}
        for role in ('risk','legacy','browser'):
            with self.assertRaises(Forbidden):self.call('discussion_save',args,role)
        for key,value in [('author','user'),('area','collaboration'),('status','adopted'),('principal','browser')]:
            with self.assertRaises(ValueError):self.call('discussion_save',{**args,key:value})
        report=self.call('record',dict(snapshot_id=self.sid,kind='review',title='legacy',body='채택',author='user',user_response={'adoption':'채택'}))
        self.assertEqual(self.read()['active_plans'],[])
        self.assertIn('not_verified',report['authority'])
        with self.assertRaises(ValueError):self.call('record',dict(snapshot_id=self.sid,kind='plan_version',title='fake',body='x',author='user'))
        with self.assertRaises(Forbidden):self.call('record',dict(snapshot_id=self.sid,kind='review',title='fake',body='x',author='user'),role='risk')
        p=self.plan()
        for role in ('collaboration','risk','legacy'):
            with self.assertRaises(Forbidden):self.call('plan_review',dict(plan_record_id=p['id'],expected_revision=1),role,session='forged')
            with self.assertRaises(Forbidden):self.call('plan_decide',dict(author='user',confirmed_by_user=True),role,session='forged')

    def test_confirmation_single_use_session_and_revision_binding(self):
        p=self.plan();a=dict(plan_record_id=p['id'],expected_revision=1)
        review=self.call('plan_review',a,'browser','a')
        decision=dict(contract_version=VERSION,expected_revision=1,confirmation=review['confirmation'],action='adopt',reason='')
        with self.assertRaises(Forbidden):self.call('plan_decide',decision,'browser','b')
        with self.assertRaises(Forbidden):self.call('plan_decide',decision,'browser','a')
        review=self.call('plan_review',a,'browser','a');decision['confirmation']=review['confirmation']
        self.discussion()
        with self.assertRaises(Conflict):self.call('plan_decide',decision,'browser','a')
        self.assertEqual(self.read()['active_plans'],[])

    def test_concurrent_revision_one_winner(self):
        args={**self.envelope(),**deepcopy(FIXTURE['discussion'])}
        def run(_):
            try:return self.call('discussion_save',args)['verified']
            except Conflict:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
        self.assertEqual(sorted(results),[False,True]);self.assertEqual(self.read()['revision'],1)

    def test_retired_risk_routes_preserve_records(self):
        current=self.discussion();p=self.plan();self.adopt(p)
        for op in ('risk_review_save','risk_overview','risk_calculate','risk_save','calculate'):
            with self.assertRaisesRegex(ValueError,'retired_risk_operation'):
                self.call(op,{},'risk')
        self.assertEqual(self.read()['current'],current)
        self.assertEqual(self.read()['active_plans'][0]['record']['id'],p['id'])

    def test_db_failure_rolls_back_markdown_and_revision(self):
        with closing(self.store.connect()) as db,db:
            db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON analysis_items BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.discussion()
        self.assertEqual(self.read()['revision'],0)
        self.assertEqual(list((self.root/'judgments').rglob('*.md')),[])

    def test_markdown_failure_and_later_corruption_not_success(self):
        original=Path.open
        def deny(path,*a,**k):
            if path.suffix=='.md':raise OSError('synthetic disk failure')
            return original(path,*a,**k)
        with patch.object(Path,'open',deny),self.assertRaises(OSError):self.discussion()
        self.assertEqual(self.read()['revision'],0)
        r=self.discussion();(self.root/r['markdown_path']).write_text('corrupt',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'integrity'):self.read()

    def test_database_and_markdown_cannot_diverge(self):
        r=self.discussion()
        with closing(self.store.connect()) as db,db:
            r['body']='changed database only'
            db.execute('UPDATE analysis_items SET payload=? WHERE id=?',(json.dumps(r),r['id']))
        with self.assertRaisesRegex(ValueError,'database_markdown_mismatch'):self.read()

    def test_legacy_bridge_blocks_new_writes_without_restarting(self):
        import analysis_bridge
        with patch.object(analysis_bridge,'ensure',return_value=dict(url=self.origin,token='legacy',analysis_capabilities={})),patch.dict('os.environ',{'INVEST_ANALYSIS_ROLE':'collaboration'}):
            with self.assertRaisesRegex(ValueError,'dedicated_role_capability_required'):analysis_bridge.call('plan_save',{})
        with patch.object(analysis_bridge,'ensure',return_value=dict(url=self.origin,token='legacy',analysis_capabilities={})),patch.dict('os.environ',{'INVEST_ANALYSIS_ROLE':'risk'}):
            with self.assertRaisesRegex(ValueError,'dedicated_role_capability_required'):analysis_bridge.call('record',{})

    def test_cross_case_refs_and_snapshot_rejected(self):
        other=self.s.dispatch('create',{'stock_key':'US:SYNTH','test_record':True},self.origin)
        sid=other['snapshots'][0]['id']
        with self.assertRaises(ValueError):self.discussion(snapshot_id=sid)
        with self.assertRaises(ValueError):self.discussion(references=[dict(kind='utterance',reference='chat-url',text=None,speaker='user')])
        r=self.discussion()
        with self.assertRaises(ValueError):self.s.dispatch('discussion_save',dict(analysis_key=other['case']['id'],snapshot_id=sid,expected_revision=0,contract_version=VERSION,**{**FIXTURE['discussion'],'references':[dict(kind='record',reference=r['id'],text=None,speaker='llm')]}),self.origin,principal='collaboration')

    def test_semantic_validation_and_missing_not_zero(self):
        for mutate in [lambda p:p['conditions'][0].update(currency='KRW'),
                       lambda p:p['conditions'][0]['quantity'].update(value='1.1'),
                       lambda p:p['conditions'][0].update(price='NaN'),
                       lambda p:p['conditions'][0].update(priority=True),
                       lambda p:p['conditions'][1].update(price='100'),
                       lambda p:p['allocation'].update(quantity='-1')]:
            p=deepcopy(FIXTURE['plan']);mutate(p)
            with self.assertRaises(ValueError):self.plan(plan=p)
        p=deepcopy(FIXTURE['plan']);p['conditions'][0].update(evaluation=None,operator=None,execution_price=None)
        r=self.plan(plan=p)
        self.assertIn('condition.c1.evaluation',r['blockers']);self.assertIsNone(r['plan']['conditions'][0]['execution_price'])

    def test_frozen_sloped_drawing_not_current_chart_constant(self):
        state=defaults();state['drawings']=[dict(id='channel',kind='channel',points=[dict(time='2026-09-18',price=90),dict(time='2026-09-23',price=95),dict(time='2026-09-22',price=110)],color='#218775',width=2,extend=True)]
        self.store.shared('US:SYNTH',revision=0,state=state)
        self.sid=self.s.capture(self.s.case(self.cid))['id']
        p=deepcopy(FIXTURE['plan']);p['conditions'][0].update(price=None,drawing_ref=dict(drawing_id='channel',workspace_revision=1,timeframe='1d',price_basis='adjusted',geometry_version='drawing-tools-linear-v1',line='line'))
        r=self.plan(plan=p);self.adopt(r)
        a=self.call('plan_input',dict(plan_record_id=r['id'],mode='adopted',evaluation_at='2026-09-24'))
        state['drawings'][0]['points'][0]['price']=10;self.store.shared('US:SYNTH',revision=1,state=state)
        b=self.call('plan_input',dict(plan_record_id=r['id'],mode='adopted',evaluation_at='2026-09-25'))
        self.assertEqual(a['snapshot_sha256'],b['snapshot_sha256'])
        self.assertNotEqual(a['drawing_evaluations'][0]['geometry']['calculations']['line_price'],b['drawing_evaluations'][0]['geometry']['calculations']['line_price'])
        self.assertEqual(b['snapshot']['workspace']['state']['drawings'][0]['points'][0]['price'],90)
        self.assertIn('evaluation_after_last_actual_session_projected_calendar_not_exchange_session',b['drawing_evaluations'][0]['basis']['limitations'])

    def test_browser_notes_and_decisions_keep_previous_links(self):
        args={**self.envelope(),'body':'첫 발언'}
        first=self.call('discussion_note_save',args,'browser','fixture')['record']
        second=self.call('discussion_note_save',{**self.envelope(),'body':'두번째 발언'},'browser','fixture')['record']
        self.assertEqual(second['previous_record_id'],first['id']);self.assertIsNone(self.read()['current'])
        p=self.plan();a=self.adopt(p)['record'];b=self.adopt(p,'revoke')['record']
        self.assertEqual(b['previous_record_id'],a['id'])

    def test_real_stdio_mcp_fresh_session_read_write_consumer(self):
        import asyncio
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        from dashboard import handler_for,ThreadingHTTPServer
        capabilities={'collaboration':'stdio-collab','risk':'stdio-risk'}
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.data,{'US:SYNTH':self.stock},ROOT/'dashboard','browser',self.store.path,
            analysis_capabilities=capabilities,analysis_dependencies={'histories':self.hist,'financials':self.fin}))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        endpoint=self.root/'endpoint.json';endpoint.write_text(json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),analysis_capabilities=capabilities)),encoding='utf-8')
        wrapper=self.root/'mcp_fixture.py'
        wrapper.write_text('import sys,runpy\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(ROOT/'scripts'))+')\nimport analysis_bridge\nanalysis_bridge.ENDPOINT=Path('+repr(str(endpoint))+')\nrunpy.run_module("analysis_mcp",run_name="__main__")\n',encoding='utf-8')
        async def session(role, action):
            params=StdioServerParameters(command=sys.executable,args=[str(wrapper)],env={'INVEST_ANALYSIS_ROLE':role,'PYTHONUTF8':'1'})
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    return await action(client)
        async def save(client):
            listed=await client.list_tools();self.assertIn('analysis_save_discussion',{t.name for t in listed.tools})
            response=await client.call_tool('analysis_save_discussion',dict(analysis_key=self.cid,**self.envelope(),**deepcopy(FIXTURE['discussion'])))
            self.assertFalse(response.isError)
            return json.loads(response.content[0].text)['record']['id']
        async def consume(client):
            response=await client.call_tool('analysis_collaboration_read',dict(analysis_key=self.cid))
            self.assertFalse(response.isError);packet=json.loads(response.content[0].text)
            self.assertEqual(packet['current']['id'],saved_id)
            listed=await client.list_tools()
            self.assertFalse({'analysis_save_risk_review','risk_calculate','risk_overview','risk_save','analysis_calculate'} & {t.name for t in listed.tools})
        try:
            saved_id=asyncio.run(session('collaboration',save));plan=self.plan();self.adopt(plan)
            asyncio.run(session('risk',consume))
            self.assertEqual(self.read()['active_plans'][0]['record']['id'],plan['id'])
        finally:server.shutdown();server.server_close();thread.join()

    def test_http_capability_cookie_origin_and_mcp_transport(self):
        from dashboard import handler_for,ThreadingHTTPServer
        import analysis_bridge
        capabilities={'collaboration':'collab-secret','risk':'risk-secret'}
        deps={'histories':self.hist,'financials':self.fin}
        h=handler_for(self.data,{'US:SYNTH':self.stock},ROOT/'dashboard','legacy-token',self.store.path,
                      analysis_capabilities=capabilities,analysis_dependencies=deps)
        server=ThreadingHTTPServer(('127.0.0.1',0),h);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        def request(op,args,token='collab-secret',extras=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            conn.request('POST','/api/analysis-rpc',json.dumps(dict(op=op,arguments={'analysis_key':self.cid,**args})),
                {'Content-Type':'application/json','Origin':origin,'X-Workbench-Token':token,**(extras or {})})
            response=conn.getresponse();status=response.status;body=json.load(response);conn.close();return status,body
        try:
            self.assertEqual(request('collaboration_read',{},'bad')[0],403)
            self.assertEqual(request('collaboration_read',{},extras={'Origin':'http://attacker'})[0],403)
            args={**self.envelope(),**deepcopy(FIXTURE['discussion'])}
            self.assertEqual(request('discussion_save',args,'risk-secret')[0],403)
            self.assertEqual(request('discussion_save',args,'legacy-token')[0],403)
            self.assertEqual(request('discussion_save',args)[0],200)
            p=self.plan()
            review_args={'plan_record_id':p['id'],'expected_revision':2}
            self.assertEqual(request('plan_review',review_args)[0],403)
            self.assertEqual(request('holding',{'confirmed_by_user':True},'legacy-token')[0],403)
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.request('GET','/health',headers={'Sec-Fetch-Site':'same-origin'})
            resp=conn.getresponse();cookie=resp.getheader('Set-Cookie').split(';')[0];health=json.load(resp);conn.close()
            self.assertNotIn('analysis_capabilities',health)
            browser={'Sec-Fetch-Site':'same-origin','Cookie':cookie}
            status,review=request('plan_review',review_args,'legacy-token',browser);self.assertEqual(status,200)
            status,saved=request('plan_decide',dict(contract_version=VERSION,expected_revision=2,confirmation=review['confirmation'],action='adopt',reason='fixture'),'legacy-token',browser)
            self.assertEqual(status,200);self.assertTrue(saved['verified'])
            with patch.object(analysis_bridge,'ensure',return_value=dict(url=origin,token='legacy-token',analysis_capabilities=capabilities)),patch.dict('os.environ',{'INVEST_ANALYSIS_ROLE':'risk'}):
                packet=analysis_bridge.call('plan_input',dict(analysis_key=self.cid,plan_record_id=p['id'],mode='adopted'))
                self.assertEqual(packet['plan_record']['id'],p['id'])
                with self.assertRaises(ValueError):analysis_bridge.call('discussion_save',{'analysis_key':self.cid,**args})
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()

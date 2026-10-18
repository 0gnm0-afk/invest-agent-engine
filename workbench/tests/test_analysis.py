import copy
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from analysis_service import AnalysisService, digest
from analysis_geometry import geometry, timeline, logical
from chart_workspace import WorkspaceStore, defaults, Conflict
from chart_history import aggregate

def drawing(kind='trend'):
    ps=[{'time':'2026-09-18','price':100.0},{'time':'2026-09-22','price':110.0},{'time':'2026-09-23','price':105.0}]
    if kind=='riskreward': ps[1]['price']=90;ps[2]['price']=120
    return dict(id='test',kind=kind,points=ps[:{'horizontal':1,'channel':3,'riskreward':3}.get(kind,2)],color='#218775',width=2,extend=False)

class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.store=WorkspaceStore(self.root/'chart-workspaces.sqlite3')
        self.bars=[dict(date=d,open=100,high=112,low=90,close=105,volume=1000) for d in ['2026-09-18','2026-09-21','2026-09-22','2026-09-23']]
        self.stock=dict(symbol='NVDA',market='US',currency='USD',bars=self.bars,price_basis='adjusted')
        self.accounts={'as_of':'2026-10-01T00:00:00+00:00','accounts':[{'alias':'test','positions':[dict(symbol='NVDA',market='US',quantity='2',currency='USD')]}],'risk':{'known_stop_exposure_base':'123'},'limitations':['orderable_cash_missing']}
        self.hist=SimpleNamespace(saved=SimpleNamespace(stocks={}),view=lambda s,f:{**s,'timeframe':f,'bars':aggregate(s['bars'],f),'history':{'state':'complete'}})
        self.fin=SimpleNamespace(read=lambda k:{'state':'failed','result':{'snapshot_id':'saved-success','value':12},'attempt':{'error':'representative_failure'},'reused':True})
        self.s=AnalysisService(self.store,self.hist,self.fin,lambda:({'accounts':self.accounts,'market':{'state':'partial'}},{'US:NVDA':self.stock}),self.root)
        self.detail=self.s.dispatch('create',{'stock_key':'US:NVDA','test_record':True},'http://127.0.0.1:1234');self.case=self.detail['case'];self.cid=self.case['id'];self.sid=self.detail['snapshots'][0]['id']
    def tearDown(self): self.tmp.cleanup()
    def test_empty_drawings_reconnect(self):
        v=self.s.snapshot(self.cid,self.sid);self.assertEqual(v['workspace']['state']['drawings'],[])
        reopened=AnalysisService(self.store,self.hist,self.fin,self.s.view,self.root)
        d=reopened.detail(self.cid,'http://127.0.0.1:9999');self.assertEqual(d['case']['id'],self.cid);self.assertIn(':9999',d['links']['dashboard_url']);self.assertNotIn('token',d['links']['copy_text'])
    def test_immutable_snapshot_and_previous_financial_success(self):
        before=self.s.snapshot(self.cid,self.sid);self.bars[-1]['close']=106
        after=self.s.capture(self.case);self.assertNotEqual(after['component_hashes']['chart'],before['component_hashes']['chart'])
        self.assertEqual(self.s.snapshot(self.cid,self.sid),before);self.assertEqual(before['financials']['state'],'failed');self.assertTrue(before['financials']['reused'])
    def test_proposal_is_separate_adoption_preserves_original_and_audit(self):
        state=defaults();state['drawings']=[drawing()];self.store.shared('US:NVDA',revision=0,state=state)
        snap=self.s.capture(self.case);p=self.s.proposal(self.case,snap['id'],[drawing('horizontal')],'fixture')
        self.assertEqual(len(self.store.shared('US:NVDA')['state']['drawings']),1)
        changed=[drawing('horizontal')];changed[0]['points'][0]['price']=99
        d=self.s.decide(self.case,p['id'],'adopt','test user edit',changed)
        current=self.store.shared('US:NVDA');self.assertEqual(current['state']['drawings'][0],state['drawings'][0]);self.assertEqual(current['state']['drawings'][1]['points'][0]['price'],99)
        self.assertEqual(d['original_workspace']['drawings'],state['drawings']);self.assertEqual(d['authority'],'drawing_only_not_trade_or_risk_adoption')
        with self.assertRaises(Conflict):self.s.decide(self.case,p['id'],'adopt','twice')
    def test_stale_proposal_rejected_without_any_overwrite(self):
        p=self.s.proposal(self.case,self.sid,[drawing()],'fixture');self.store.shared('US:NVDA',revision=0,state=defaults());before=self.store.shared('US:NVDA')
        with self.assertRaises(Conflict):self.s.decide(self.case,p['id'],'adopt','stale')
        self.assertEqual(before,self.store.shared('US:NVDA'))
    def test_concurrent_adoptions_only_one_wins(self):
        p=self.s.proposal(self.case,self.sid,[drawing()],'fixture');results=[]
        def run():
            try:self.s.decide(self.case,p['id'],'adopt','race');results.append('ok')
            except Conflict:results.append('conflict')
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(results,['ok','conflict'])
    def test_discard_does_not_change_workspace(self):
        p=self.s.proposal(self.case,self.sid,[drawing()],'fixture');before=self.store.shared('US:NVDA');self.s.decide(self.case,p['id'],'discard','no')
        self.assertEqual(before,self.store.shared('US:NVDA'))
    def test_reports_append_md_hash_and_user_fields(self):
        args=dict(snapshot_id=self.sid,kind='report',title='검증용',body='실제 분석 아님',author='llm',user_response={'counterexample':'검증 예시'},sources=[{'label':'fixture','url':'local:fixture','as_of':'2026-10-01'}])
        a=self.s.record(self.case,args);b=self.s.record(self.case,args);self.assertNotEqual(a['id'],b['id'])
        import hashlib
        self.assertEqual(hashlib.sha256((self.root/a['markdown_path']).read_bytes()).hexdigest(),a['markdown_sha256']);self.assertTrue(a['test_record']);self.assertEqual(a['snapshot_id'],self.sid)
    def test_cross_case_snapshot_and_path_rejected(self):
        other=self.s.dispatch('create',{'stock_key':'US:NVDA'},'http://127.0.0.1:1')['case']
        with self.assertRaises(ValueError):self.s.snapshot(other['id'],self.sid)
        with self.assertRaises(ValueError):self.s.case('../../secret')
    def test_holding_requires_observed_positive_position_and_user_confirmation(self):
        a=dict(snapshot_id=self.sid,adopted_snapshot_id=self.sid,account_alias='test',thesis='fixture',invalidation='fixture',confirmation='test observation')
        with self.assertRaises(ValueError):self.s.holding(self.case,a)
        link=self.s.holding(self.case,{**a,'confirmed_by_user':True});self.assertEqual(link['adopted_snapshot_id'],self.sid)
        with self.assertRaises(ValueError):self.s.holding(self.case,{**a,'confirmed_by_user':True,'account_alias':'unknown'})
    def test_retired_sizing_cannot_be_called(self):
        with self.assertRaisesRegex(ValueError,'retired_risk_operation'):
            self.s.dispatch('calculate',{},'http://127.0.0.1:1234')
    def test_geometry_all_kinds_frame_and_js_projection_parity(self):
        js="const fs=require('fs'),vm=require('vm');global.window={};vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));const a=JSON.parse(fs.readFileSync(0,'utf8'));const t=window.DrawingTools.timeline(a.bars,a.frame);console.log(JSON.stringify(a.days.map(d=>window.DrawingTools.index(d,t,a.frame))));"
        for frame in ('1d','1w','1mo'):
            bars=aggregate(self.bars,frame);slots=timeline(bars,frame);days=['2026-09-18','2026-09-19','2026-09-21','2026-09-22','2026-09-23','2027-01-05']
            expected=json.loads(subprocess.check_output(['node','-e',js,str(ROOT/'dashboard/drawing-tools.js')],input=json.dumps(dict(bars=bars,frame=frame,days=days)).encode()))
            for day,value in zip(days,expected):
                actual=logical(day,slots,frame)
                if value is None:self.assertIsNone(actual)
                else:self.assertAlmostEqual(value,actual,places=10)
            drawings=[dict(drawing(k),id=k) for k in ('trend','channel','box','horizontal','measure','riskreward')]
            v=geometry({'state':{'drawings':drawings}},dict(bars=bars,timeframe=frame),{})
            self.assertEqual(v['drawings'][-1]['calculations']['ratio'],2)
    def test_unknown_operation_and_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):self.s.dispatch('order',{'analysis_key':self.cid},'http://127.0.0.1:1')
        with self.assertRaises(ValueError):self.s.dispatch('context',{'analysis_key':self.cid,'context':{'price_scale':'log'}},'http://127.0.0.1:1')

    def test_http_write_auth_size_origin_and_read_only_boundaries(self):
        from dashboard import handler_for, ThreadingHTTPServer
        h=handler_for({'accounts':self.accounts,'market':{}},{'US:NVDA':self.stock},ROOT/'dashboard','secret',self.root/'http.sqlite3')
        server=ThreadingHTTPServer(('127.0.0.1',0),h);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        def req(path,headers):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('POST',path,json.dumps({'op':'list','arguments':{}}),headers);r=c.getresponse();r.read();c.close();return r.status
        headers={'Content-Type':'application/json','Origin':origin,'X-Workbench-Token':'secret'}
        try:
            self.assertEqual(req('/api/analysis-rpc',headers),200)
            self.assertEqual(req('/api/analysis-rpc',{**headers,'X-Workbench-Token':'bad'}),403)
            self.assertEqual(req('/api/analysis-rpc',{**headers,'Origin':'https://example.org'}),403)
            self.assertEqual(req('/api/analysis-rpc',{'Content-Type':'application/json'}),403)
            self.assertEqual(req('/api/orders',headers),405)
        finally:server.shutdown();server.server_close();thread.join()

    def test_financial_pagination_matches_stored_metrics(self):
        self.fin.read=lambda k:{'state':'saved','result':{'summary':[{'value':i} for i in range(45)],'sources':[{'url':str(i)} for i in range(12)]}}
        snap=self.s.capture(self.case);values=[];offset=0
        while True:
            r=self.s.dispatch('read',dict(analysis_key=self.cid,snapshot_id=snap['id'],section='financials',offset=offset),'http://127.0.0.1:1');values.extend(r['financials']['result']['summary']);offset=r['next_offset']
            if offset is None:break
        self.assertEqual(values,snap['financials']['result']['summary'])

if __name__=='__main__':unittest.main()

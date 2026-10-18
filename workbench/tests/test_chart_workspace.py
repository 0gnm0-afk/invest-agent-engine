import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from chart_workspace import WorkspaceStore, defaults, validate, Conflict, moving_averages
from dashboard import handler_for, ThreadingHTTPServer


def drawing(kind='trend', ident='test'):
    points=[{'time':'2026-09-21','price':1000.125},{'time':'2026-09-22','price':1100.25},{'time':'2026-09-23','price':1050.5}]
    n={'trend':2,'channel':3,'horizontal':1,'box':2,'measure':2}[kind]
    return {'id':ident,'kind':kind,'points':points[:n],'color':'#218775','width':2,'extend':False}


class Workspaces(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'workspace.sqlite3';self.store=WorkspaceStore(self.path)
    def tearDown(self):self.temp.cleanup()
    def test_bounded_extensions_shared_reopened_and_legacy_preserved(self):
        state=defaults();state['drawings']=[drawing(ident='legacy'),drawing('channel','bounded')]
        state['drawings'][1].update(extend=True,extension={'left':'2026-09-01','right':'2026-10-15'})
        original=copy.deepcopy(state);self.store.shared('US:EXT',revision=0,state=state)
        for frame in ('1d','1w','1mo'):
            packet=WorkspaceStore(self.path).shared('US:EXT',frame)
            self.assertEqual(packet['drawing_extensions'],'bounded-v1')
            self.assertEqual(packet['state']['drawings'],original['drawings'])
        state['drawings'][1]['extension']['right']='2026-11-20'
        self.store.shared('US:EXT',revision=1,state=state)
        with self.assertRaises(Conflict):self.store.shared('US:EXT',revision=1,state=original)
        self.assertEqual(self.store.shared('US:EXT')['state']['drawings'],state['drawings'])
    def test_invalid_extension_payloads_rejected(self):
        for extension in (None,True,{}, {'left':'2026-09-01'}, {'left':'2026-09-01','right':'2026-10-10','extra':0},
                {'left':0,'right':'2026-10-10'}, {'left':'2026-02-30','right':'2026-10-10'},
                {'left':'2026-09-22','right':'2026-10-10'}, {'left':'2026-09-01','right':'2026-09-21'}):
            with self.subTest(extension=extension):
                state=defaults();state['drawings']=[dict(drawing(),extension=extension)]
                with self.assertRaises(ValueError):validate(state)
        state=defaults();state['drawings']=[dict(drawing('measure'),extension={'left':'2026-09-01','right':'2026-10-10'})]
        with self.assertRaises(ValueError):validate(state)
    def test_seven_defaults_sma_and_limit(self):
        self.assertEqual([a['period'] for a in defaults()['averages']], [5,10,20,50,100,150,200])
        self.assertTrue(all(a['visible'] for a in defaults()['averages']))
        bars=[{'date':str(i),'close':float(i+1)} for i in range(200)]
        result=moving_averages({'bars':bars},[5,10,20,50,100,150,200])
        self.assertAlmostEqual(result['200'][-1]['value'],100.5)
        self.assertNotIn('value',result['200'][-2])
        with self.assertRaises(ValueError):moving_averages({'bars':bars},[5,10,20,50,100,150,200,250])
        with self.assertRaises(ValueError):validate({**defaults(),'averages':defaults()['averages']+[dict(defaults()['averages'][0],period=250)]})

    def test_six_preset_upgrade_is_once_preserves_custom_styles_and_history(self):
        import sqlite3
        state=defaults();state['averages'].pop();state['averages'][0].update(color='#123456',width=4,visible=False)
        state['drawings']=[drawing('box')]
        self.store.shared('KR:SIX',revision=0,state=state)
        original=copy.deepcopy(state)
        packet=self.store.shared('KR:SIX')
        self.assertEqual(packet['revision'],2)
        self.assertEqual(packet['state']['averages'][:-1],original['averages'])
        self.assertEqual(packet['state']['averages'][-1]['period'],200)
        self.assertEqual(packet['state']['drawings'],original['drawings'])
        self.assertEqual(self.store.shared('KR:SIX')['revision'],2)
        with self.assertRaises(Conflict):self.store.shared('KR:SIX',revision=1,state=original)
        from contextlib import closing
        with closing(sqlite3.connect(self.path)) as db:
            payload=json.loads(db.execute('SELECT payload FROM chart_workspace_versions WHERE identity=? AND revision=1',('KR:SIX',)).fetchone()[0])
        self.assertEqual(payload['averages']['1d'],original['averages'])
        # Once upgraded, an explicit later deletion of SMA200 stays deleted.
        packet['state']['averages'].pop()
        self.store.shared('KR:SIX',revision=2,state=packet['state'])
        self.assertEqual(self.store.shared('KR:SIX')['state']['averages'],original['averages'])
        custom=copy.deepcopy(original);custom['averages'][0]['period']=7
        self.store.shared('KR:CUSTOM',revision=0,state=custom)
        self.assertEqual(self.store.shared('KR:CUSTOM')['state'],custom)

    def test_box_and_rr_extensions_shared_preserve_all_anchors(self):
        box=dict(drawing('box'),extend=True,extension={'left':'2026-09-01','right':'2026-10-10'})
        rr=dict(drawing('channel','rr'),kind='riskreward',extend=True,points=[{'time':'2026-09-21','price':100},{'time':'2026-09-22','price':90},{'time':'2026-10-01','price':120}],extension={'left':'2026-09-01','right':'2026-10-10'})
        state={**defaults(),'drawings':[box,rr]}
        self.store.shared('US:FLAT',revision=0,state=state)
        for frame in ('1d','1w','1mo'):
            self.assertEqual(WorkspaceStore(self.path).shared('US:FLAT',frame)['state']['drawings'],state['drawings'])
        rr['extension']['right']='2026-09-25'
        with self.assertRaises(ValueError):validate(state)

    def test_every_tool_and_average_persists_across_store_reopen(self):
        state=defaults();state['drawings']=[drawing(k,k) for k in ['trend','channel','horizontal','box','measure']]
        original=copy.deepcopy(state);self.store.save('KR:AAA',0,state)
        loaded=WorkspaceStore(self.path).read('KR:AAA')
        self.assertEqual(loaded['state'],original);self.assertEqual(loaded['revision'],1);self.assertEqual(state,original)
    def test_riskreward_saved_shared_reopened_and_conflict(self):
        state=defaults();state['drawings']=[drawing(ident='existing'),dict(drawing('channel','rr'),kind='riskreward',points=[{'time':'2026-09-21','price':100},{'time':'2026-09-22','price':90},{'time':'2026-09-23','price':120}])]
        self.store.shared('US:R',revision=0,state=state)
        for frame in ('1d','1w','1mo'):
            packet=WorkspaceStore(self.path).shared('US:R',frame)
            self.assertEqual(packet['state']['drawings'],state['drawings']);self.assertIn('riskreward',packet['drawing_kinds'])
        with self.assertRaises(Conflict):self.store.shared('US:R',revision=0,state=state)
        state['drawings'].pop();self.store.shared('US:R',revision=1,state=state)
        self.assertEqual(self.store.shared('US:R')['state']['drawings'],[drawing(ident='existing')])
    def test_riskreward_invalid_geometry_rejected(self):
        for prices in [(100,100,120),(100,90,80),(100,110,120),(100,90,100)]:
            d=dict(drawing('channel'),kind='riskreward')
            for p,v in zip(d['points'],prices):p['price']=v
            with self.assertRaises(ValueError):validate({**defaults(),'drawings':[d]})
        d=dict(drawing('channel'),kind='riskreward')
        for p,v in zip(d['points'],[100,90,120]):p.update(time='2026-09-21',price=v)
        with self.assertRaises(ValueError):validate({**defaults(),'drawings':[d]})
    def test_riskreward_short_full_precision(self):
        d=dict(drawing('channel'),kind='riskreward')
        for p,v in zip(d['points'],[100.1234,110.2345,80.0123]):p['price']=v
        state={**defaults(),'drawings':[d]};self.store.save('US:R',0,state)
        self.assertEqual(self.store.read('US:R')['state'],state)

    def test_future_dates_shared_and_preserve_existing_drawings(self):
        state=defaults();state['drawings']=[drawing(ident='existing'),drawing(ident='future')]
        state['drawings'][1]['points'][1]['time']='2027-02-01'
        original=copy.deepcopy(state)
        self.store.shared('US:FUTURE',revision=0,state=state)
        for frame in ('1d','1w','1mo'):
            loaded=WorkspaceStore(self.path).shared('US:FUTURE',frame)
            self.assertEqual(loaded['state']['drawings'],original['drawings'])
        self.assertEqual(state,original)

    def test_symbol_isolation(self):
        state=defaults();state['drawings']=[drawing()];self.store.save('KR:AAA',0,state)
        self.assertEqual(self.store.read('US:AAA')['revision'],0)
    def test_conflict_does_not_overwrite(self):
        a,b=WorkspaceStore(self.path),WorkspaceStore(self.path)
        a.save('KR:AAA',0,defaults())
        with self.assertRaises(Conflict):b.save('KR:AAA',0,defaults())
        self.assertEqual(b.read('KR:AAA')['revision'],1)
    def test_delete_and_restore_revision(self):
        state=defaults();state['drawings']=[drawing()];self.store.save('KR:A',0,state)
        self.store.save('KR:A',1,defaults());self.assertEqual(self.store.read('KR:A')['state']['drawings'],[])
        self.store.save('KR:A',2,state);self.assertEqual(self.store.read('KR:A')['state'],state)
    def test_invalid_shapes_rejected(self):
        for change in [{'color':'url(secret)'},{'points':[]},{'width':10},{'kind':'order'},{'id':'../private'}]:
            state=defaults();state['drawings']=[{**drawing(),**change}]
            with self.assertRaises(ValueError):validate(state)
    def test_nan_bad_dates_and_same_date_rejected(self):
        for point in [{'time':'2026-09-31','price':10},{'time':'2026-09-22','price':float('nan')},{'time':'2026-09-22','price':-1},{'time':'2026-09-22','price':True}]:
            state=defaults();state['drawings']=[drawing()];state['drawings'][0]['points'][0]=point
            with self.assertRaises(ValueError):validate(state)
        state=defaults();state['drawings']=[drawing()];state['drawings'][0]['points'][1]['time']='2026-09-21'
        with self.assertRaises(ValueError):validate(state)
    def test_limits_and_unknown_fields(self):
        for patch in [{'averages':[{'period':0,'color':'#ffffff','width':2,'visible':True}]},{'drawings':[drawing(ident=str(i)) for i in range(101)]},{'order':'buy'}]:
            with self.assertRaises(ValueError):validate({**defaults(),**patch})
    def test_sma_full_precision_and_warmup(self):
        bars=[{'date':f'2026-09-{i+1:02}','close':v} for i,v in enumerate([1000.1,1000.2,1000.3,1000.4])]
        original=copy.deepcopy(bars);values=moving_averages({'bars':bars},[3])['3']
        self.assertNotIn('value',values[1]);self.assertAlmostEqual(values[2]['value'],1000.2);self.assertAlmostEqual(values[3]['value'],1000.3);self.assertEqual(bars,original)
    def test_sma_missing_and_insufficient_not_filled(self):
        bars=[{'date':f'2026-09-{i+1:02}','close':v} for i,v in enumerate([1,None,3,4])]
        values=moving_averages({'bars':bars},[2,500]);self.assertNotIn('value',values['2'][2]);self.assertEqual(values['2'][3]['value'],3.5);self.assertTrue(all('value' not in p for p in values['500']))


class SharedWorkspaces(unittest.TestCase):
    setUp = Workspaces.setUp
    tearDown = Workspaces.tearDown
    def test_legacy_merge_collision_averages_and_original_preserved(self):
        original={}
        for frame in ('1d','1w','1mo'):
            key='KR:A'+('' if frame=='1d' else '|'+frame)
            value=defaults();value['drawings']=[drawing(ident='same')]
            if frame=='1mo':value['drawings'][0]['points'][0]['price']=1234
            value['averages'][0]['period']={'1d':7,'1w':11,'1mo':12}[frame]
            self.store.save(key,0,value);original[key]=self.store.read(key)
        daily=self.store.shared('KR:A')
        self.assertEqual(len(daily['state']['drawings']),2)
        for frame,n in [('1d',7),('1w',11),('1mo',12)]:
            got=WorkspaceStore(self.path).shared('KR:A',frame)
            self.assertEqual(got['state']['drawings'],daily['state']['drawings'])
            self.assertEqual(got['state']['averages'][0]['period'],n)
        for key,value in original.items():self.assertEqual(self.store.read(key),value)

    def test_shared_edit_delete_conflict_and_reopen(self):
        a=self.store.shared('KR:A');b=self.store.shared('KR:A','1w')
        state=a['state'];state['drawings']=[drawing(k,k) for k in ('trend','channel','horizontal','box','measure')]
        state['averages'][0]['period']=7
        self.store.shared('KR:A',revision=a['revision'],state=state)
        with self.assertRaises(Conflict):self.store.shared('KR:A','1w',revision=b['revision'],state=b['state'])
        month=self.store.shared('KR:A','1mo')
        self.assertEqual(month['state']['drawings'],state['drawings'])
        self.assertEqual(month['state']['averages'][0]['period'],5)
        month['state']['drawings'].pop()
        self.store.shared('KR:A','1mo',revision=month['revision'],state=month['state'])
        self.assertEqual(len(WorkspaceStore(self.path).shared('KR:A')['state']['drawings']),4)
        self.assertEqual(self.store.shared('US:A')['state']['drawings'],[])

    def test_all_three_legacy_limits_preserved(self):
        for frame in ('1d','1w','1mo'):
            state=defaults();state['drawings']=[drawing(ident=frame+str(i)) for i in range(100)]
            self.store.save('KR:A'+('' if frame=='1d' else '|'+frame),0,state)
        got=self.store.shared('KR:A');self.assertEqual(len(got['state']['drawings']),300)
        self.store.shared('KR:A',revision=got['revision'],state=got['state'])
        got['state']['drawings'].append(drawing(ident='extra'))
        with self.assertRaises(ValueError):self.store.shared('KR:A',revision=2,state=got['state'])

    def test_corrupt_shared_payload_fails_without_overwrite(self):
        import sqlite3
        from contextlib import closing
        self.store.shared('KR:A')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE shared_chart_workspaces SET payload='{}' WHERE identity='KR:A'")
        with self.assertRaises(ValueError):self.store.shared('KR:A')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT payload FROM shared_chart_workspaces').fetchone()[0],'{}')

    def test_invalid_migration_does_not_create_shared_row(self):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.path)) as db, db:
            self.store.connect().close()
            db.execute('INSERT INTO chart_workspaces VALUES (?,?,?,?)',('KR:A|1w',1,'{}','2026-10-01'))
        with self.assertRaises(ValueError):self.store.shared('KR:A')
        self.assertEqual(self.store.read('KR:A')['revision'],0)


class WorkspaceHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        stocks={'KR:A':{'market':'KR','symbol':'A','bars':[{'date':'2026-09-21','close':100.1},{'date':'2026-09-22','close':100.2}]}}
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for({},stocks,cls.root,'test-token',cls.root/'state.sqlite3'))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def call(self,path,method='GET',body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        c.request(method,path,body=body,headers=headers or {});r=c.getresponse();status=r.status;data=r.read();c.close();return status,json.loads(data)
    def headers(self):return {'Origin':'http://127.0.0.1:'+str(self.server.server_port),'X-Workbench-Token':'test-token','Content-Type':'application/json'}
    def test_save_requires_origin_and_token(self):
        body=json.dumps({'revision':0,'state':defaults()})
        for headers in [{},{**self.headers(),'Origin':'https://evil.test'},{**self.headers(),'X-Workbench-Token':'wrong'},{**self.headers(),'Host':'evil.test'}]:
            self.assertEqual(self.call('/api/chart-state/KR%3AA','POST',body,headers)[0],403)
    def test_workspace_roundtrip_and_conflict(self):
        code,current=self.call('/api/chart-state/KR%3AA');self.assertEqual(code,200)
        body=json.dumps({'revision':current['revision'],'state':defaults()})
        self.assertEqual(self.call('/api/chart-state/KR%3AA','POST',body,self.headers())[0],200)
        self.assertEqual(self.call('/api/chart-state/KR%3AA','POST',body,self.headers())[0],409)
    def test_path_and_order_boundaries(self):
        self.assertEqual(self.call('/api/chart-state/..%2Fsecret')[0],404)
        self.assertEqual(self.call('/api/home','POST','{}',self.headers())[0],405)
        self.assertEqual(self.call('/api/chart-state/US%3Ano','POST','{}',self.headers())[0],404)
    def test_invalid_payload_and_size(self):
        self.assertEqual(self.call('/api/chart-state/KR%3AA','POST','{}',self.headers())[0],400)
        self.assertEqual(self.call('/api/chart-state/KR%3AA','POST',' '*131073,self.headers())[0],413)
    def test_ohlc_anchor_dates_survive_every_timeframe_save(self):
        points = [('open', '2025-04-07', 141.55), ('high', '2025-04-09', 159.55),
                  ('low', '2025-04-07', 140.53), ('close', '2025-04-11', 157.14)]
        for source in ('1d', '1w', '1mo'):
            route = '/api/chart-state/KR%3AA?timeframe=' + source
            _, current = self.call(route)
            state = defaults()
            state['drawings'] = [dict(id=field, kind='horizontal', points=[dict(time=day, price=price)],
                                     color='#218775', width=2, extend=False) for field, day, price in points]
            body = json.dumps(dict(revision=current['revision'], state=state))
            self.assertEqual(self.call(route, 'POST', body, self.headers())[0], 200)
            for target in ('1d', '1w', '1mo'):
                code, restored = self.call('/api/chart-state/KR%3AA?timeframe=' + target)
                self.assertEqual(code, 200)
                self.assertEqual(restored['state']['drawings'], state['drawings'])

    def test_flat_extensions_and_seven_averages_http_roundtrip(self):
        _, packet=self.call("/api/chart-state/KR%3AA")
        state=defaults()
        state["drawings"]=[dict(drawing("box"),extend=True,extension={"left":"2026-09-01","right":"2026-10-10"}),dict(drawing("channel","rr"),kind="riskreward",extend=True,points=[dict(time="2026-09-21",price=100),dict(time="2026-09-22",price=90),dict(time="2026-10-01",price=120)],extension={"left":"2026-09-01","right":"2026-10-10"})]
        self.assertEqual(self.call("/api/chart-state/KR%3AA","POST",json.dumps(dict(revision=packet["revision"],state=state)),self.headers())[0],200)
        for frame in ("1d","1w","1mo"):
            code,restored=self.call("/api/chart-state/KR%3AA?timeframe="+frame)
            self.assertEqual(code,200);self.assertEqual(restored["state"]["drawings"],state["drawings"])
            self.assertEqual(restored["average_limit"],7)
        code,values=self.call("/api/averages/KR%3AA?periods=5,10,20,50,100,150,200")
        self.assertEqual(code,200);self.assertEqual(len(values),7)

    def test_sma_endpoint(self):
        status,data=self.call('/api/averages/KR%3AA?periods=2');self.assertEqual(status,200);self.assertAlmostEqual(data['2'][1]['value'],100.15)
        self.assertEqual(self.call('/api/averages/KR%3AA?periods=0')[0],400)


if __name__=='__main__':unittest.main()

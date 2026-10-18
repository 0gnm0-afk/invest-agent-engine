"""Public default must be runnable with invented inputs and no outbound connections."""
from copy import deepcopy
from pathlib import Path
import socket,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT.parent/'src')]
from public_demo import build,seed
from analysis_service import AnalysisService
from chart_workspace import WorkspaceStore
from trade_cycle import TradeCycleStore,calculate

class PublicDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        with patch.object(socket.socket,'connect',side_effect=AssertionError('outbound_forbidden')):
            cls.data,cls.stocks,cls.deps=build(cls.root)
            seed(cls.root,cls.data,cls.stocks,cls.deps)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_generated_inputs_and_real_screen(self):
        self.assertTrue(self.data['synthetic']);self.assertEqual(len(self.stocks),10)
        self.assertGreater(self.data['market']['candidate_count'],0)
        self.assertTrue(all(s['provider']=='synthetic' for s in self.stocks.values()))
        r=self.deps['financials'].read('US:DEMO1')['result']
        self.assertEqual((len(r['annual']),len(r['quarterly'])),(5,12))
        self.assertTrue(r['synthetic'])
        self.assertAlmostEqual(r['annual'][-1]['metrics']['net_margin']['value'],.14)
        for frame in ('1d','1w','1mo'):
            chart=self.deps['histories'].view(self.stocks['US:DEMO1'],frame)
            self.assertEqual(chart['history']['daily_count'],380)
            self.assertEqual(chart['history']['state'],'complete')
            self.assertGreater(len(chart['bars']),0)
    def test_restart_preserves_records_and_108_arithmetic_paths(self):
        store=WorkspaceStore(self.root/'chart-workspaces.sqlite3')
        s=AnalysisService(store,self.deps['histories'],self.deps['financials'],lambda:(self.data,self.stocks),self.root)
        before=deepcopy(s.rows('analysis_items'))
        seed(self.root,self.data,self.stocks,self.deps)
        self.assertEqual(s.rows('analysis_items'),before)
        self.assertEqual(len(s.rows('analysis_cases')),1)
        cycles=TradeCycleStore(store.path).list()['cycles'];self.assertEqual(len(cycles),1)
        self.assertEqual(calculate(cycles[0]['draft']['plan'])['evaluated_result_count'],108)
        self.assertIsNone(cycles[0]['adopted'])
        self.assertEqual(store.shared('US:DEMO1','1d')['state']['drawings'],store.shared('US:DEMO1','1w')['state']['drawings'])
    def test_live_agent_is_explicit_opt_in(self):
        import trade_cycle_agent
        with patch.dict('os.environ',{'INVEST_ENABLE_CODEX_DRAFT':'0'}),patch.object(trade_cycle_agent.subprocess,'run') as call:
            with self.assertRaisesRegex(ValueError,'agent_not_enabled'):trade_cycle_agent.generate({},self.root)
            call.assert_not_called()

if __name__=='__main__':unittest.main()

"""Synthetic saved cycles and stub Telegram delivery only; no real accounts or sends."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from trade_cycle_alerts import TradeCycleAlertStore, delivery_events, basis, context_text
import telegram_warnings as telegram


class CycleAlerts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.path = self.root/'runtime/chart-workspaces.sqlite3'
        self.store = TradeCycleAlertStore(self.path); self.now = datetime.now(timezone.utc)
        self.bars = [dict(date='2026-10-12', close=99), dict(date='2026-10-13', close=101)]
        self.stock = dict(currency='USD', price_basis='adjusted', isin_code='SYNTHETIC', name='합성종목', bars=self.bars)
        self.bundle = dict(as_of=self.now.isoformat(), query_state='received', accounts=[
            dict(alias='PRIVATE_ACCOUNT', holdings_complete=True, positions=[])])
        self.action = dict(kind='buy', step=1, ratio=20, confirmation_price='100', assumed_fill_price='100',
            condition='사용자 확인 조건', planned_quantity=2, quantity_basis='initial_budget',
            trigger=dict(kind='price', comparator='gte', evaluation='daily_close'))
        self.cycle = dict(cycle_id='tc_synthetic', stock_key='US:S', revision=2, fills=[], state=dict(phase='buying'),
            adopted=dict(version=1, combination_id='b235-s235', plan=dict(currency='USD', account_alias='PRIVATE_ACCOUNT'),
                source=dict(identity=dict(market='US', symbol='S', stable_id='SYNTHETIC', currency='USD', price_basis='adjusted'))),
            next_steps=[self.action])

    def update(self):
        return self.store.update([self.cycle], self.bundle, {'US:S':self.stock}, {'US':self.bars[-1]['date']},
            self.now, {'US':[b['date'] for b in self.bars]})

    def add(self, close):
        date = (datetime.fromisoformat(self.bars[-1]['date'])+timedelta(days=1)).date().isoformat()
        self.bars.append(dict(date=date, close=close))

    def test_condition_only_creates_alert_and_latches_until_recovery(self):
        original = deepcopy(self.cycle)
        self.assertEqual(len(self.update()['events']), 1)
        self.assertEqual(self.cycle, original)
        self.add(102); self.assertEqual(len(self.update()['events']), 1)
        self.add(98); self.update(); self.add(102)
        self.assertEqual(len(self.update()['events']), 2)
        self.assertEqual(len(TradeCycleAlertStore(self.path).read()['events']), 2)
        self.assertFalse(self.cycle['fills'])

    def test_free_text_and_missing_trigger_never_become_numeric_condition(self):
        self.action.pop('trigger'); self.action['condition'] = '100 이상이면 매수'
        self.assertFalse(self.update()['events'])
        self.action['trigger'] = dict(kind='manual', evaluation='daily_close', comparator='gte')
        self.assertFalse(self.update()['events'])

    def test_cross_comparator_requires_previous_side(self):
        self.action['trigger']['comparator'] = 'cross_above'; self.bars[0]['close'] = 101
        self.assertFalse(self.update()['events'])
        self.add(99); self.update(); self.add(102)
        self.assertEqual(len(self.update()['events']), 1)

    def test_stale_account_and_identity_change_defer(self):
        self.bundle['as_of'] = (self.now-timedelta(days=2)).isoformat()
        result = self.update(); self.assertFalse(result['events'])
        self.assertEqual(result['states'][0]['issue'], 'account_stale_or_incomplete')
        self.bundle['as_of'] = self.now.isoformat(); self.stock['isin_code'] = 'OTHER'
        result = self.update(); self.assertFalse(result['events'])
        self.assertEqual(result['states'][0]['issue'], 'market_identity_or_basis_changed')

    def test_changed_history_and_session_gap_defer(self):
        self.update(); self.bars[-1]['close'] = 98; result = self.update()
        self.assertEqual(result['states'][0]['issue'], 'processed_price_history_changed')
        self.assertEqual(self.update()['states'][0]['issue'], 'processed_price_history_changed')
        result = self.store.update([self.cycle], self.bundle, {'US:S':self.stock}, {'US':'2026-10-13'}, self.now,
            {'US':['2026-10-12','2026-10-12-gap','2026-10-13']})
        self.assertEqual(result['states'][0]['issue'], 'price_sessions_incomplete')

    def test_unadopted_draft_does_not_rearm_and_new_adoption_does(self):
        self.update(); old = basis(self.cycle)
        self.cycle['revision'] += 1; self.cycle['draft_version'] = 2
        self.assertEqual(basis(self.cycle), old); self.assertEqual(len(self.update()['events']), 1)
        self.cycle['adopted']['version'] = 2
        self.assertEqual(len(self.update()['events']), 2)

    def test_final_stop_overrides_other_installment_alerts(self):
        self.cycle['next_steps'].append({**deepcopy(self.action), 'kind':'stop', 'step':None, 'ratio':None,
            'confirmation_price':'102', 'trigger':dict(kind='price', comparator='lte', evaluation='daily_close')})
        events = self.update()['events']
        self.assertEqual(len(events), 1); self.assertEqual(events[0]['action']['kind'], 'stop')
        self.add(100); self.assertEqual(len(self.update()['events']),1)
        self.add(101); self.assertEqual(len(self.update()['events']),1)
        self.add(103); events=self.update()['events']
        self.assertEqual(len(events),2); self.assertEqual(events[0]['action']['kind'],'buy')

    def test_drawing_reference_is_not_frozen_as_confirmation_price(self):
        self.action['drawing_ref'] = {'drawing_id':'sloped_channel'}
        self.assertFalse(self.update()['events'])

    def test_context_scope_privacy_and_no_fabricated_fill(self):
        self.update()
        event = dict(id='old', kind='trailing', market='US', symbol='S', currency='USD', account='PRIVATE_ACCOUNT', date='2026-10-13')
        with patch('trade_cycle_alerts.cycle_rows', return_value=[self.cycle]):
            output = delivery_events(self.root, [event, {**event,'account':'OTHER_ACCOUNT'}], {'US':'2026-10-13'}, now=self.now)
        text = context_text(output[0]); self.assertIn('20%', text); self.assertIn('예정 2주', text)
        self.assertNotIn('PRIVATE_ACCOUNT', text); self.assertNotIn('cycle_context', output[1])
        self.assertEqual(output[2]['kind'], 'trade_cycle'); self.assertFalse(self.cycle['fills'])
        with patch('trade_cycle_alerts.cycle_rows', side_effect=AssertionError('must_not_read_failed_cycle_inputs')):
            self.assertEqual(delivery_events(self.root,[event],{'US':'2026-10-13'},include_cycles=False),[event])

    def test_closed_changed_or_test_cycle_does_not_deliver(self):
        self.update()
        for changes in ('closed', 'fill', 'test'):
            current = deepcopy(self.cycle)
            if changes == 'closed': current['state']['phase'] = 'closed'
            elif changes == 'fill': current['fills'].append(dict(id='actual_record'))
            else:
                current['adopted']['source']['test_record'] = True
                self.store.update([current], self.bundle, {'US:S':self.stock}, {'US':'2026-10-13'}, self.now,
                    {'US':[b['date'] for b in self.bars]})
            with patch('trade_cycle_alerts.cycle_rows', return_value=[current]):
                self.assertFalse(delivery_events(self.root, [], {'US':'2026-10-13'}, now=self.now))

    def test_existing_delivery_ledger_deduplicates_new_cycle_events(self):
        self.update(); sent = []
        config = dict(enabled_at=(self.now-timedelta(hours=1)).isoformat(), token='fake', chat='fake')
        endpoint = self.root/'runtime/dashboard/analysis-endpoint.json'; endpoint.parent.mkdir(parents=True)
        endpoint.write_text(json.dumps({'url':'http://127.0.0.1:43210'}))
        def sender(body): sent.append(body); return dict(message_id=len(sent))
        with patch('trade_cycle_alerts.cycle_rows', return_value=[self.cycle]):
            for _ in range(2):
                result = telegram.dispatch(self.root, {'US':'2026-10-13'}, config=config, sender=sender,
                    include_trailing=False, include_scenarios=False)
        self.assertEqual(result['counts'], {'sent':1}); self.assertEqual(len(sent), 1)
        self.assertIn('100.00 USD', sent[0]); self.assertIn('예정 2주', sent[0])
        self.assertIn('/#cycle?id=tc_synthetic', sent[0]); self.assertNotIn('PRIVATE_ACCOUNT', sent[0])

    def test_existing_ma_warning_enriched_without_second_cycle_notification(self):
        from trailing_warnings import TrailingStore
        self.action['kind'] = 'sell'; self.action['trigger'] = dict(kind='ma5', comparator='lte', evaluation='daily_close')
        self.assertFalse(self.update()['events'])
        warning = dict(id='existing-ma5', kind='ma5', policy='high95-approach-v2', account='PRIVATE_ACCOUNT',
            market='US', symbol='S', name='합성종목', currency='USD', date='2026-10-13',
            created_at=self.now.isoformat(), threshold=100, close=101, alert_threshold=102,
            tolerance=2, atr=4, high=110, reason='합성 접근', average_cost=777)
        store = TrailingStore(self.root/'runtime/trailing-warnings.sqlite3')
        with closing(store.connect()) as db, db:
            db.execute('INSERT INTO warnings VALUES(?,?)', (warning['id'], json.dumps(warning)))
        config = dict(enabled_at=(self.now-timedelta(hours=1)).isoformat(), token='fake', chat='fake'); sent=[]
        with patch('trade_cycle_alerts.cycle_rows', return_value=[self.cycle]):
            for _ in range(2):
                telegram.dispatch(self.root, {'US':'2026-10-13'}, config=config,
                    sender=lambda body:(sent.append(body) or {'message_id':len(sent)}), include_scenarios=False)
        self.assertEqual(len(sent), 1); self.assertIn('매도 1차', sent[0]); self.assertIn('예정 2주', sent[0])
        self.assertIn('5일선 이하',sent[0]); self.assertIn('접근 경고와 계획 조건 충족은 다를 수 있습니다',sent[0])
        self.assertNotIn('PRIVATE_ACCOUNT', sent[0]); self.assertNotIn('777', sent[0])
        # Corrupt new-feature storage must not disable the existing warning path.
        warning['id']='existing-after-cycle-error'
        with closing(store.connect()) as db, db:
            db.execute('INSERT INTO warnings VALUES(?,?)', (warning['id'], json.dumps(warning)))
        with patch('trade_cycle_alerts.cycle_rows',side_effect=ValueError('cycle_saved_record_integrity_failed')):
            result=telegram.dispatch(self.root,{'US':'2026-10-13'},config=config,
                sender=lambda body:(sent.append(body) or {'message_id':len(sent)}),include_scenarios=False)
        self.assertEqual(len(sent),2); self.assertEqual(result['trade_cycle_state'],'unavailable')

    def test_actual_cycle_store_contract_and_fills_preserved(self):
        from trade_cycle import TradeCycleStore, combination_id
        def step(price, comparator):
            return dict(confirmation_price=str(price), assumed_fill_price=str(price), condition='합성 조건',
                        trigger=dict(kind='price', comparator=comparator, evaluation='daily_close'))
        plan = dict(title='합성 알림 통합', currency='USD', budget='1000', account_alias='PRIVATE_ACCOUNT',
            initial_holding=dict(quantity=0, average_price='0'),
            buy_steps=[step(100,'gte'),step(90,'lte'),step(80,'lte')],
            sell_steps=[step(110,'gte'),step(120,'gte'),step(130,'gte')], stop=step(70,'lte'),
            scenarios={name:dict(events=[dict(kind='buy',step=1),dict(kind='mark',price='105')])
                       for name in ('bullish','neutral','bearish')})
        store=TradeCycleStore(self.path)
        draft=store.save_draft('US:S',plan,source=self.cycle['adopted']['source'])
        cycle=store.adopt(draft['cycle_id'],1,combination_id((20,30,50),(20,30,50)),draft['revision'],principal='browser')
        self.cycle=cycle; original=deepcopy(cycle); self.update()
        events=delivery_events(self.root,[],{'US':'2026-10-13'},now=self.now)
        self.assertEqual(len(events),1); self.assertEqual(events[0]['action']['planned_quantity'],2)
        self.assertEqual(store.read(cycle['cycle_id']),original)


if __name__ == '__main__': unittest.main()

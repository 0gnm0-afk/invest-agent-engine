import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from invest_agent.trading.morning import portfolio_component
from invest_agent.trading.portfolio import review
from invest_agent.trading.toss_snapshot import collect


class TossTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.accounts=[{'accountNo':'synthetic-private-id','accountSeq':7,'accountType':'BROKERAGE'}]
        self.items=[{'symbol':'SYNTH','marketCountry':'US','currency':'USD','quantity':'0.125',
                    'lastPrice':'100','averagePurchasePrice':'80'}]
        self.fail=set();self.calls=[];self.expired=False;self.stock_market='KOSDAQ'

    def api(self,path,params=None,seq=None):
        self.calls.append(path)
        if path in self.fail:raise SystemExit('private API error body must never leak')
        if path=='/api/v1/accounts':value=self.accounts
        elif path=='/api/v1/holdings':value={'items':self.items}
        elif path=='/api/v1/buying-power':value={'currency':params['currency'],'cashBuyingPower':'1000'}
        elif path=='/api/v1/exchange-rate':
            value={'baseCurrency':'USD','quoteCurrency':'KRW','midRate':'1400',
                   'validFrom':(self.now-timedelta(minutes=1)).isoformat(),
                   'validUntil':(self.now+timedelta(minutes=-1 if self.expired else 1)).isoformat()}
        elif path=='/api/v1/stocks':value=[{'symbol':self.items[0]['symbol'],'market':self.stock_market}]
        else:raise AssertionError('Unapproved endpoint')
        return {'result':copy.deepcopy(value)}

    def collect(self):return collect(self.api,clock=lambda:self.now,alias_key=b"synthetic-test-key-only-32-bytes!!")

    def test_fractional_holdings_and_buying_power_do_not_invent_gross_cash(self):
        bundle=self.collect();account=bundle['accounts'][0]
        self.assertEqual(account['positions'][0]['quantity'],'0.125')
        self.assertEqual(account['orderable_cash']['USD'],'1000')
        self.assertEqual(account['cash'],{})
        result=review(bundle,self.now)
        self.assertIsNone(result['equity_base'])
        self.assertIsNone(result['positions'][0]['weight'])
        self.assertNotIn('adopted_stop',account['positions'][0])

    def test_private_identifiers_and_raw_errors_do_not_reach_export(self):
        self.fail={'/api/v1/buying-power'}
        bundle=self.collect();encoded=json.dumps(bundle)
        self.assertNotIn('synthetic-private-id',encoded)
        self.assertNotIn('accountSeq',encoded)
        self.assertNotIn('private API error',encoded)
        self.assertEqual(bundle['accounts'][0]['alias'],self.collect()['accounts'][0]['alias'])

    def test_empty_account_list_differs_from_failed_account_list(self):
        self.accounts=[]
        bundle=self.collect();self.assertTrue(bundle['accounts_complete'])
        self.assertEqual(review(bundle,self.now)['equity_base'],'0')
        self.fail={'/api/v1/accounts'}
        bundle=self.collect();self.assertFalse(bundle['accounts_complete'])
        self.assertIsNone(review(bundle,self.now)['equity_base'])

    def test_holdings_failure_keeps_account_and_incompleteness(self):
        self.fail={'/api/v1/holdings'}
        bundle=self.collect()
        self.assertEqual(len(bundle['accounts']),1)
        self.assertFalse(bundle['accounts'][0]['holdings_complete'])
        self.assertFalse(review(bundle,self.now)['stop_exposure_complete'])

    def test_bad_holding_does_not_remove_other_positions(self):
        self.items.append({'symbol':'BAD','marketCountry':'US','currency':'USD','quantity':'1'})
        result=review(self.collect(),self.now)
        self.assertEqual(len(result['positions']),2)
        self.assertEqual(result['positions'][0]['state'],'needs_stop')
        self.assertEqual(result['positions'][1]['state'],'unavailable')
        component=portfolio_component({'account_input':{'state':'loaded','payload':self.collect()},'evaluated_at':self.now.isoformat()})
        self.assertEqual(component['state'],'partial')
        self.assertEqual(component['result']['positions'][0]['state'],'needs_stop')

    def test_expired_fx_is_not_silently_used(self):
        self.expired=True;bundle=self.collect()
        self.assertNotIn('USD',bundle['fx_to_base'])
        self.assertEqual(review(bundle,self.now)['positions'][0]['reason'],'missing_fx')

    def test_korean_market_mapping_is_observed_not_assumed(self):
        self.items=[{'symbol':'123450','marketCountry':'KR','currency':'KRW','quantity':'2','lastPrice':'1000','averagePurchasePrice':'900'}]
        bundle=self.collect()
        self.assertEqual(bundle['accounts'][0]['positions'][0]['quote_symbol'],'123450.KQ')
        self.stock_market='KR_ETC'
        self.assertNotIn('quote_symbol',self.collect()['accounts'][0]['positions'][0])
        self.assertNotIn('/api/v1/exchange-rate',self.calls)

    def test_private_key_separates_instances_and_is_not_exported(self):
        first = collect(self.api, clock=lambda:self.now, alias_key=b"a"*32)
        repeat = collect(self.api, clock=lambda:self.now, alias_key=b"a"*32)
        other = collect(self.api, clock=lambda:self.now, alias_key=b"b"*32)
        self.assertEqual(first['accounts'][0]['alias'], repeat['accounts'][0]['alias'])
        self.assertNotEqual(first['accounts'][0]['alias'], other['accounts'][0]['alias'])
        self.assertNotIn('a'*32, json.dumps(first))
        self.assertNotIn('synthetic-private-id', json.dumps(first))

    def test_missing_or_short_key_fails_before_account_request(self):
        with self.assertRaises(TypeError):
            collect(self.api)
        for key in (None, b'short', 'a'*32):
            with self.assertRaises(ValueError):
                collect(self.api, alias_key=key)
        self.assertEqual(self.calls, [])

"""Autocomplete uses only existing, in-scope detail identities and compact saved metadata."""
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dashboard_data import StockSearch, saved_market, write, sha
from dashboard import handler_for, ThreadingHTTPServer

EMPTY_POLICY = {'records': [], 'lifecycle_records': []}


def stock(market, symbol, name, **extra):
    return dict(market=market, symbol=symbol, name=name, bars=[{'date': '2026-10-02'}], **extra)


class SearchIndex(unittest.TestCase):
    def setUp(self):
        self.stocks = {
            'US:NVDA': stock('US', 'NVDA', '엔비디아', english_name='Nvidia', listing_exchange_family='NASDAQ'),
            'US:NVDL': stock('US', 'NVDL', 'Nvidia Daily ETF'),
            'US:ABCNV': stock('US', 'ABCNV', 'Other'),
            'KR:005930.KS': stock('KR', '005930.KS', '삼성전자', english_name='SamsungElec', listing_exchange_family='KOSPI'),
        }
        self.index = StockSearch(self.stocks, policy=EMPTY_POLICY)

    def keys(self, query):
        return [row['key'] for row in self.index.search(query)['items']]

    def test_korean_english_case_spacing_and_bare_domestic_code(self):
        for query in ('엔 비 디 아', 'n V d A', 'ｎｖｄａ', 'NVIDIA'):
            self.assertEqual(self.keys(query)[0], 'US:NVDA')
        for query in ('삼 성', '005930', 'Samsung Elec'):
            self.assertEqual(self.keys(query), ['KR:005930.KS'])

    def test_exact_prefix_contains_ranking_and_stable_order(self):
        self.assertEqual(self.keys('nv'), ['US:NVDA', 'US:NVDL', 'US:ABCNV'])
        reverse = StockSearch(dict(reversed(list(self.stocks.items()))), policy=EMPTY_POLICY)
        self.assertEqual(self.index.search('nv'), reverse.search('nv'))
        self.assertEqual(self.keys('nvidia'), ['US:NVDA', 'US:NVDL'])

    def test_empty_unknown_and_long_query(self):
        self.assertEqual(self.index.search('   ')['items'], [])
        self.assertEqual(self.index.search('unknown')['total'], 0)
        with self.assertRaisesRegex(ValueError, 'invalid_search_query'):
            self.index.search('x' * 101)

    def test_bounded_result_and_safe_projection_without_modification(self):
        stocks = {'US:X' + str(i): stock('US', 'X' + str(i), 'Common', source_path='private', token='secret') for i in range(20)}
        before = copy.deepcopy(stocks)
        result = StockSearch(stocks, policy=EMPTY_POLICY).search('common')
        self.assertEqual((len(result['items']), result['total'], result['limit']), (12, 20, 12))
        self.assertEqual(set(result['items'][0]), {'key', 'market', 'symbol', 'name', 'exchange'})
        self.assertEqual(stocks, before)

    def test_listing_metadata_aliases_and_identity_bound_exclusion(self):
        excluded = {'market': 'US', 'symbol': 'OLD', 'isin_code': 'former', 'identity_bound': True}
        stocks = {'US:OLD': stock('US', 'OLD', 'Current', isin_code='new'),
                  'US:NONE': stock('US', 'NONE', 'Existing'),
                  'US:BAD': stock('US', 'OTHER', 'Wrong key')}
        listings = {'US:OLD': {'name_en': 'Former', 'isinCode': 'former'},
                    'US:NONE': {'aliases': ['Known Alias'], 'listing_exchange': 'NYSE'}}
        index = StockSearch(stocks, listings, {'records': [excluded], 'lifecycle_records': []})
        self.assertEqual(index.search('former')['items'], [])
        self.assertEqual(index.search('current')['items'][0]['symbol'], 'OLD')
        self.assertEqual(index.search('KnownAlias')['items'][0]['exchange'], 'NYSE')
        self.assertEqual(index.search('Wrong key')['items'], [])
        stocks['US:OLD'].pop('isin_code')
        self.assertEqual(StockSearch(stocks, listings, {'records': [excluded], 'lifecycle_records': []}).search('current')['items'], [])

    def test_current_user_exclusions_apply_to_legacy_saved_rows(self):
        index = StockSearch({'US:BKYI': stock('US', 'BKYI', 'BIO-key International'),
                             'US:NVDA': self.stocks['US:NVDA']}, policy={'records':[{'market':'US','symbol':'BKYI','reason':'synthetic_exclusion'}], 'lifecycle_records':[]})
        self.assertEqual(index.search('BKYI')['items'], [])
        self.assertEqual(index.search('NVDA')['total'], 1)


class SearchHttp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.current = {'US:NVDA': stock('US', 'NVDA', '엔비디아', english_name='Nvidia')}
        self.views = {'view': ({'market': {'entries': []}}, self.current)}
        history_stock = stock('KR', '005930.KS', '삼성전자')
        saved = SimpleNamespace(stocks={'KR:005930.KS': history_stock}, markets={
            'KR': SimpleNamespace(market='KR', listings={'005930.KS': {'listing_exchange': 'KOSPI'}})})
        self.history = SimpleNamespace(saved=saved)
        with patch('dashboard.HistoryStore', return_value=self.history):
            handler = handler_for(*self.views['view'], self.root, 'secret-token', self.root/'workspace.sqlite3', views=self.views)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def get(self, path, headers=None, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_saved_history_and_non_candidate_names_reach_existing_detail(self):
        for query, expected in [('삼성', 'KR:005930.KS'), ('nvidia', 'US:NVDA')]:
            status, response = self.get('/api/stock-search?q=' + quote(query))
            self.assertEqual(status, 200)
            self.assertEqual(response['items'][0]['key'], expected)
            status, detail = self.get('/api/stock/' + quote(expected))
            self.assertEqual(status, 200)
            self.assertEqual(detail['name'], response['items'][0]['name'])

    def test_daily_replacement_rebuilds_index(self):
        self.assertEqual(self.get('/api/stock-search?q=NVDA')[1]['total'], 1)
        self.views['view'] = (self.views['view'][0], {'US:NEW': stock('US', 'NEW', 'Replacement')})
        self.assertEqual(self.get('/api/stock-search?q=NVDA')[1]['total'], 0)
        self.assertEqual(self.get('/api/stock-search?q=NEW')[1]['total'], 1)

    def test_invalid_query_rejected_and_no_body_or_external_origin_authorized(self):
        self.assertEqual(self.get('/api/stock-search')[1]['items'], [])
        for path in ('/api/stock-search?q=' + 'x'*101, '/api/stock-search?q=a&q=b', '/api/stock-search?q=a&limit=5000'):
            self.assertEqual(self.get(path)[0], 400)
        self.assertEqual(self.get('/api/stock-search?q=NVDA', {'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.get('/api/stock-search?q=NVDA', {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.get('/api/stock-search?q=NVDA', method='POST')[0], 405)


class SavedSearchMetadata(unittest.TestCase):
    def test_adapter_keeps_only_allowlisted_search_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root/'runtime/run'
            source = root/'runtime/source/snapshot.json'
            write(source, {'series': [stock('US', 'NVDA', '엔비디아', english_name='Nvidia',
                                          listing_exchange_family='NASDAQ', isin_code='US67066G1040',
                                          token='private')], 'universe': []})
            write(root/'runtime/latest.json', {'output': str(run)})
            write(run/'run.json', {'state': 'succeeded', 'processing_state': 'completed', 'market_source': str(source.parent)})
            write(run/'manifest.json', {'output_hashes': {'run.json': sha(run/'run.json')}, 'input_hashes': {str(source): sha(source)}})
            value = saved_market(root)['stocks']['US:NVDA']
            self.assertEqual(value['english_name'], 'Nvidia')
            self.assertEqual(value['listing_exchange_family'], 'NASDAQ')
            self.assertEqual(value['isin_code'], 'US67066G1040')
            self.assertNotIn('token', value)


if __name__ == '__main__':
    unittest.main()

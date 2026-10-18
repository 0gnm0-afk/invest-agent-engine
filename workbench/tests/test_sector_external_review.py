import copy
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import sector_observation as so
import sector_external_review as review
import finviz_public_classification as fp
import test_sector_observation as fixtures


def profile(symbol='ABC', exchange='NASDAQ', sector='Technology'):
    fields = {'Ticker Symbol':symbol, 'Exchange':exchange, 'Sector':sector, 'Industry':'Software'}
    return ('<link rel="canonical" href="https://stockanalysis.com/stocks/abc/company/"><table>'+
            ''.join('<tr><td>'+k+'</td><td>'+v+'</td></tr>' for k,v in fields.items())+'</table>').encode()


class ReviewTest(unittest.TestCase):
    def test_alternate_sector_is_named_and_not_adopted_as_finviz(self):
        value, issue = review.stockanalysis_profile(profile(), 'ABC', 'NASDAQ')
        self.assertIsNone(issue)
        self.assertEqual(value['sector'], 'Technology')
        self.assertFalse(value['adopted_as_Finviz'])

    def test_share_class_exchange_and_canonical_must_match(self):
        for data in (profile('ABC.B'), profile(exchange='NYSE'), profile().replace(b'/abc/', b'/xyz/')):
            self.assertIsNone(review.stockanalysis_profile(data,'ABC','NASDAQ')[0])

    def test_unknown_and_duplicate_sector_not_accepted(self):
        for data in (profile(sector='Other'),profile().replace(b'</table>',b'<tr><td>Sector</td><td>Energy</td></tr></table>')):
            self.assertIsNone(review.stockanalysis_profile(data,'ABC','NASDAQ')[0])

    def test_finviz_fund_classification_can_be_known_while_excluded(self):
        records = [{'path':'source','sector':'Financial','industry':'Closed-End Fund - Equity',
                    'observed_at':'2026-09-25T00:00:00+00:00'}]
        args = (records,None,'NASDAQ',True,'2026-09-25T01:00:00+00:00',so.age_issue)
        self.assertEqual(fp.select_classification(*args)[1], 'finviz_security_type_conflict')
        self.assertEqual(fp.select_classification(*args,classification_only=True)[:2],('Financial',None))
        records.append({**records[0], 'path':'other', 'sector':'Energy'})
        self.assertEqual(fp.select_classification(*args,classification_only=True)[1],'finviz_direct_sources_conflict')

    def test_alternate_sector_does_not_resolve_observation_group(self):
        fixture = fixtures.MappingTest(); self.addCleanup(fixture.doCleanups)
        root = fixture.fixture()
        fixture.original[0].update(market='US',exchange='NASDAQ',provider_symbol='ABC',symbol='ABC',state='conflict',reason='security_type_conflict')
        original = copy.deepcopy(fixture.original[0])
        records = {'ABC':[{'path':'source','sector':'Technology','industry':'Software',
                          'observed_at':'2026-09-24T01:00:00+00:00'}]}
        with patch.object(so,'load_public',return_value=records), patch.object(so,'load_reviews',return_value={'ABC':{'alternate_classification':{'sector':'Technology'}}}):
            row = fixture.grouped(root)
        self.assertEqual(row['group_state'],'unmatched')
        self.assertIsNone(row['group_name'])
        self.assertEqual(row['external_finviz_sector'],'Technology')
        self.assertEqual(row['original_classification'],original)
        self.assertEqual(review.review_category(row),'finviz_confirmed_but_observation_excluded')

    def test_saved_404_redirect_checked_without_claiming_delisting(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); sources=root/'sources'; sources.mkdir()
            path=sources/'quote.html'; path.write_bytes(b'Not found')
            meta={'url':'https://finviz.com/quote.ashx?t=ABC','final_url':'https://finviz.com/stock?t=ABC',
                  'status':404,'observed_at':'2026-09-25T00:00:00+00:00','sha256':so.sha(path)}
            so.write(path.with_suffix('.html.meta.json'),meta)
            so.write(root/'external-review.json',{'documents':[{'symbol':'ABC','exchange':'NASDAQ','finviz_quote':'quote.html'}]})
            def read():return review.load_reviews(root,so.checked,so.load,{},'2026-09-25T01:00:00+00:00',so.age_issue)['ABC']
            value=read()
            self.assertEqual(value['checks'][0]['issue'],'public_page_http_404')
            self.assertIsNone(value['alternate_classification'])
            meta['final_url']='https://finviz.com/stock?t=XYZ'; so.write(path.with_suffix('.html.meta.json'),meta)
            with self.assertRaisesRegex(ValueError,'source_identity'):read()
            meta['final_url']='https://finviz.com/stock?t=ABC'; so.write(path.with_suffix('.html.meta.json'),meta)
            path.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'hash_mismatch'):read()


if __name__ == '__main__': unittest.main()

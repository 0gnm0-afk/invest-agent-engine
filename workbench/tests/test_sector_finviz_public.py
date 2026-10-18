import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sector_observation as so
import finviz_public_classification as fp

MAP=b'prefix;123(e){e.exports={name:"Root",children:[{name:"Technology",children:[{name:"Software",children:[{name:"ABC",description:"ABC: name, children",value:12}]}]}]}};notExecuted()'


class FinvizPublicTest(unittest.TestCase):
    def record(self,**changes):
        return dict(symbol='ABC',sector='Technology',industry='Software',path='map.js',
                    observed_at='2026-09-24T01:00:00+00:00',**changes)

    def select(self,records,legacy=None,exchange='NASDAQ',unique=True):
        return fp.select_classification(records,legacy,exchange,unique,'2026-09-25T00:00:00+00:00',so.age_issue)

    def test_map_literal_does_not_execute_javascript_or_rewrite_string(self):
        self.assertEqual(fp.parse_map(MAP),[{'symbol':'ABC','name':'ABC: name, children','sector':'Technology','industry':'Software'}])

    def test_ambiguous_or_missing_root_rejected(self):
        for data in (MAP+MAP,b'e.exports={name:"Else",children:[]}'):
            with self.assertRaises(ValueError):fp.parse_map(data)

    def test_code_instead_of_literal_rejected(self):
        with self.assertRaises(ValueError):fp.parse_map(MAP.replace(b'value:12',b'value:danger()'))

    def test_exact_ticker_match_no_exchange_claim(self):
        rows=[self.record()];before=copy.deepcopy(rows)
        self.assertEqual(self.select(rows)[:2],('Technology',None));self.assertEqual(rows,before)

    def test_duplicate_ticker_in_one_document_rejected(self):
        self.assertEqual(self.select([self.record(),self.record()])[1],'finviz_duplicate_source_ticker')

    def test_duplicate_universe_ticker_rejected(self):
        self.assertEqual(self.select([self.record()],unique=False)[1],'finviz_universe_ticker_ambiguous')

    def test_independent_sources_agree(self):
        other=self.record();other['path']='screener.html'
        self.assertEqual(self.select([self.record(),other])[:2],('Technology',None))

    def test_industry_or_sector_conflict_not_majority_vote(self):
        for field,value in (('sector','Financial'),('industry','Other')):
            other=self.record();other.update(path='other',**{field:value})
            self.assertEqual(self.select([self.record(),other])[1],'finviz_direct_sources_conflict')

    def test_fund_is_new_type_conflict(self):
        row=self.record();row['industry']='Closed-End Fund - Equity'
        self.assertEqual(self.select([row])[1],'finviz_security_type_conflict')

    def test_old_direct_quote_disagreement_blocks(self):
        legacy=({'group_name':'Financial','exchange':'NASDAQ'},None,self.record())
        self.assertEqual(self.select([self.record()],legacy)[1],'finviz_direct_sources_conflict')

    def test_stale_and_future_proof_rejected(self):
        for time,reason in (('2026-08-01T00:00:00+00:00','stale_classification'),
                            ('2026-09-26T00:00:00+00:00','future_observation')):
            row=self.record();row['observed_at']=time
            self.assertEqual(self.select([row])[1],reason)

    def test_absent_is_unmatched_not_label_fallback(self):
        self.assertEqual(self.select([])[:2],(None,'finviz_public_evidence_absent'))

    def alias_page(self,identity='ABC.B:NYSE'):
        return ('<title>ABC-B - Company</title><a href="/screener?f=sec_technology">Technology</a>'
                '<a href="/screener?f=ind_software">Software</a><a href="/screener?f=exch_nyse">NYSE</a>'
                '<a href="https://www.google.com/finance/quote/'+identity+'">open</a>').encode()

    def test_alias_requires_published_exact_identity(self):
        row=fp.parse_alias_quote(self.alias_page(),'ABC-B','ABC.B','NYSE')
        self.assertEqual(row['provider_symbol'],'ABC.B')
        for identity in ('ABC.A:NYSE','ABC.B:NASDAQ','ABC-B:NYSE'):
            with self.assertRaisesRegex(ValueError,'identity_unconfirmed'):
                fp.parse_alias_quote(self.alias_page(identity),'ABC-B','ABC.B','NYSE')

    def test_alias_exchange_conflict_blocks_mapping(self):
        self.assertEqual(self.select([self.record(exchange='NYSE')])[1],'finviz_exchange_conflict')

    def test_screener_header_and_ticker_link(self):
        head='<table class="screener_table"><tr>'+''.join('<th>'+v+'</th>' for v in ['No.','Ticker','Company','Sector','Industry','Country'])+'</tr>'
        body='<tr><td>1</td><td data-boxover-ticker="ABC"><a href="/quote.ashx?t=ABC">ABC</a></td><td>Company</td><td>Technology</td><td>Software</td><td>USA</td></tr></table>'
        self.assertEqual(fp.parse_screener((head+body).encode())[0]['symbol'],'ABC')
        with self.assertRaisesRegex(ValueError,'ticker_conflict'):
            fp.parse_screener((head+body.replace('t=ABC','t=ABD')).encode())
        with self.assertRaisesRegex(ValueError,'header_changed'):
            fp.parse_screener((head.replace('Sector','Other')+body).encode())

    def public_fixture(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);sources=root/'sources';sources.mkdir()
        docs={'data.js':(MAP,'https://finviz.com/assets/data.js'),
              'page.html':(b'<link data-chunk-id="map_base_sec_all" href="/assets/data.js">','https://finviz.com/map.ashx?t=sec_all')}
        for name,(data,url) in docs.items():
            path=sources/name;path.write_bytes(data)
            so.write(path.with_suffix(path.suffix+'.meta.json'),{'url':url,'final_url':url,'status':200,
                     'sha256':so.sha(path),'observed_at':'2026-09-24T00:00:00+00:00'})
        so.write(root/'finviz-public.json',{'documents':[{'kind':'full_market_map','file':'data.js','page':'page.html'}]})
        return root

    def test_public_map_chain_and_hash_validation(self):
        root=self.public_fixture();inputs={}
        self.assertEqual(fp.load_public(root,so.checked,so.load,inputs,so.SECTORS)['ABC'][0]['sector'],'Technology')
        self.assertEqual(len(inputs),5)
        (root/'sources/data.js').write_bytes(MAP+b'tamper')
        with self.assertRaisesRegex(ValueError,'hash_mismatch'):
            fp.load_public(root,so.checked,so.load,{},so.SECTORS)

    def test_other_map_cannot_claim_full_market(self):
        root=self.public_fixture();path=root/'sources/page.html.meta.json'
        value=so.load(path);value['url']='https://finviz.com/map.ashx?t=sec';so.write(path,value)
        with self.assertRaisesRegex(ValueError,'full_market_page_required'):
            fp.load_public(root,so.checked,so.load,{},so.SECTORS)

    def test_no_manifest_is_legacy_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(fp.load_public(Path(folder),so.checked,so.load,{},so.SECTORS),{})


if __name__=='__main__':unittest.main()

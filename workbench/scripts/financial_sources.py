"""Official, selected-company financial collection. Never logs credential-bearing URLs."""
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_GATE = threading.Lock()

class SourceError(Exception):
    pass

def settings():
    return {k:os.environ.get(k) for k in ('DART_API_KEY','SEC_USER_AGENT')}


class Collector:
    def __init__(self, folder, progress=lambda x: None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.sources = []
        self.progress = progress
        self.config = settings()

    def fetch(self, url, name, params=None, sec=False, binary=False):
        headers = {'User-Agent': 'InvestWorkbench/1.0 official financial research', 'Accept': 'application/json'}
        if sec:
            ua = self.config.get('SEC_USER_AGENT') or ''
            if not re.search(r'[^\s@]+@[^\s@]+\.[^\s@]+', ua):
                raise SourceError('SEC_USER_AGENT_REQUIRED')
            headers['User-Agent'] = ua
        public_url = url
        if params:
            public_url += '?' + urllib.parse.urlencode({k:v for k,v in params.items() if k != 'crtfc_key'})
            url += '?' + urllib.parse.urlencode(params)
        self.progress(name)
        for attempt in range(3):
            try:
                with _GATE:
                    time.sleep(.15)
                    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                        raw = r.read(50_000_001)
                if len(raw) > 50_000_000:
                    raise SourceError('SOURCE_TOO_LARGE')
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1 + attempt * 2)
                    continue
                raise SourceError('HTTP_' + str(exc.code) + ':' + name) from None
            except (OSError, TimeoutError):
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise SourceError('NETWORK_ERROR:' + name) from None
        (self.folder / name).write_bytes(raw)
        self.sources.append(dict(id=name, url=public_url, fetched_at=datetime.now(timezone.utc).isoformat(),
                                 sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
        return raw if binary else json.loads(raw)

    def dart(self, endpoint, name, **params):
        key = self.config.get('DART_API_KEY')
        if not key:
            raise SourceError('DART_API_KEY_REQUIRED')
        v = self.fetch('https://opendart.fss.or.kr/api/' + endpoint + '.json', name, dict(crtfc_key=key, **params))
        if v.get('status') not in ('000', '013'):
            raise SourceError('DART_' + str(v.get('status')) + ':' + name)
        return v

    def korea(self, symbol):
        key = self.config.get('DART_API_KEY')
        if not key:
            raise SourceError('DART_API_KEY_REQUIRED')
        raw = self.fetch('https://opendart.fss.or.kr/api/corpCode.xml', 'corp-code.zip', {'crtfc_key':key}, binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            body = z.read('CORPCODE.xml')
        matches = [x for x in ET.fromstring(body).findall('list') if x.findtext('stock_code','').strip() == symbol]
        if len(matches) != 1:
            raise SourceError('DART_STOCK_ID_NOT_UNIQUE')
        corp = matches[0].findtext('corp_code')
        company = self.dart('company', 'company.json', corp_code=corp)
        if company.get('stock_code') != symbol or company.get('corp_code') != corp:
            raise SourceError('DART_IDENTITY_MISMATCH')
        from financial_business import eligibility
        scope=eligibility(company,'DART')
        if scope['status']=='unsupported':return dict(provider='DART',identity=company,eligibility=scope)
        if company.get('acc_mt') != '12':
            raise SourceError('DART_NON_DECEMBER_YEAR_REQUIRES_PERIOD_MAPPING')
        reports = []
        today = date.today()
        for year in range(today.year - 6, today.year + 1):
            for quarter, code in ((1,'11013'),(2,'11012'),(3,'11014'),(4,'11011')):
                end = date(year,quarter*3, (31 if quarter in (1,4) else 30))
                if end >= today:
                    continue
                prefix = f'{year}-Q{quarter}'
                args = dict(corp_code=corp,bsns_year=str(year),reprt_code=code)
                fs = self.dart('fnlttSinglAcntAll', prefix+'-statements.json',fs_div='CFS',**args)
                # Never silently mix separate and consolidated accounts.
                shares = self.dart('stockTotqySttus',prefix+'-shares.json',**args)
                reports.append(dict(year=year,quarter=quarter,end=end.isoformat(),fs=fs,shares=shares,prefix=prefix))
        statement_scope='CFS'
        if not any(r['fs'].get('status')=='000' for r in reports):
            statement_scope='OFS'
            for report in reports:
                code={1:'11013',2:'11012',3:'11014',4:'11011'}[report['quarter']]
                report['prefix']+='-OFS'
                report['fs']=self.dart('fnlttSinglAcntAll',report['prefix']+'-statements.json',
                    fs_div='OFS',corp_code=corp,bsns_year=str(report['year']),reprt_code=code)
                # The share report is independent of CFS/OFS; retain its real source id.
                report['shares_source_id']=report['prefix'].removesuffix('-OFS')+'-shares.json'
        from financial_dart_supplement import collect_annual_supplements
        return collect_annual_supplements(self, dict(provider='DART',identity=company,
            reports=reports,statement_scope=statement_scope,eligibility=scope))

    def usa(self, symbol):
        listing = self.fetch('https://www.sec.gov/files/company_tickers.json','tickers.json',sec=True)
        matches = [x for x in listing.values() if x['ticker'] == symbol]
        if len(matches) != 1:
            raise SourceError('SEC_STOCK_ID_NOT_UNIQUE')
        cik = str(matches[0]['cik_str']).zfill(10)
        submission = self.fetch(f'https://data.sec.gov/submissions/CIK{cik}.json','submissions.json',sec=True)
        if symbol not in submission.get('tickers',[]) or str(submission['cik']).zfill(10) != cik:
            raise SourceError('SEC_IDENTITY_MISMATCH')
        from financial_business import eligibility
        scope=eligibility(submission,'SEC')
        if scope['status']=='unsupported':return dict(provider='SEC',identity=submission,eligibility=scope)
        facts = self.fetch(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json','companyfacts.json',sec=True)
        if str(facts['cik']).zfill(10) != cik:
            raise SourceError('SEC_FACTS_IDENTITY_MISMATCH')
        from financial_supplements import sec_supplement, nvidia_supplement
        scope=eligibility(submission,'SEC',facts)
        source = dict(provider='SEC', identity=submission, facts=facts,eligibility=scope)
        if scope['status']=='unsupported':return source
        from financial_sec_filing import enrich_sec
        return enrich_sec(self,nvidia_supplement(self, sec_supplement(self, source)))

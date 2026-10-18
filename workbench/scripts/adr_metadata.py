"""Dated, identity-checked ADR market caps and sectors from public Stock Analysis pages."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests

ROOT = Path(__file__).resolve().parents[1]
SECTORS = {'Basic Materials':'Basic Materials', 'Materials':'Basic Materials',
    'Communication Services':'Communication Services', 'Consumer Cyclical':'Consumer Cyclical',
    'Consumer Discretionary':'Consumer Cyclical', 'Consumer Defensive':'Consumer Defensive',
    'Consumer Staples':'Consumer Defensive', 'Energy':'Energy', 'Financials':'Financial',
    'Financial':'Financial', 'Healthcare':'Healthcare', 'Health Care':'Healthcare',
    'Industrials':'Industrials', 'Real Estate':'Real Estate', 'Technology':'Technology',
    'Information Technology':'Technology', 'Utilities':'Utilities'}


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    from atomic_io import atomic_write
    atomic_write(Path(path), json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def identity(row):
    return row['market'], row['symbol'], row.get('isin_code'), row.get('listing_exchange_family', row.get('exchange'))


def page_fields(soup):
    fields = {}
    for tr in soup.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) == 2:
            key = cells[0].get_text(' ', strip=True)
            value = cells[1].get_text(' ', strip=True)
            if key in fields and fields[key] != value:
                raise ValueError('conflicting_page_field:' + key)
            fields[key] = value
    return fields


def parse_pages(statistics, company, row, session):
    """Read visible tables only; never evaluate embedded page code."""
    stats, profile = [BeautifulSoup(data, 'html.parser') for data in (statistics, company)]
    text = stats.get_text(' ', strip=True)
    profile_text = profile.get_text(' ', strip=True)
    fields = page_fields(profile)
    exchange = row.get('listing_exchange_family', row.get('exchange'))
    expected = re.escape(exchange) + r':\s*' + re.escape(row['symbol']) + r'\s*·[^·]+·\s*USD\b'
    if not re.search(expected, text, re.I) or not re.search(expected, profile_text, re.I):
        raise ValueError('adr_ticker_exchange_currency_mismatch')
    if fields.get('Ticker Symbol') != row['symbol']:
        raise ValueError('adr_profile_symbol_mismatch')
    if not row.get('isin_code') or fields.get('ISIN Number') != row['isin_code']:
        raise ValueError('adr_profile_isin_mismatch')
    close = re.search(r'At close:\s*([A-Za-z]+ \d{1,2}, \d{4}),', text)
    if not close:
        close = re.search(r'([A-Za-z]+ \d{1,2}, \d{4}), [\d:]+ [AP]M E[DS]T - Market closed', text)
    if not close or datetime.strptime(close[1], '%b %d, %Y').date().isoformat() != session:
        raise ValueError('adr_cap_session_mismatch')
    updated = re.search(r'Last updated:\s*([A-Za-z]+ \d{1,2}, \d{4})', text)
    updated_on = datetime.strptime(updated[1], '%b %d, %Y').date() if updated else None
    session_day = datetime.strptime(session, '%Y-%m-%d').date()
    # Global statistics may refresh after the US close (next UTC date/weekend).
    if not updated_on or not session_day <= updated_on <= session_day + timedelta(days=4):
        raise ValueError('adr_statistics_date_unverified')
    caps = []
    for tr in stats.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) == 2 and cells[0].get_text(' ', strip=True) == 'Market Cap':
            exact = cells[1].get('title', '')
            if not re.fullmatch(r'[\d,]+(?:\.\d+)?', exact):
                raise ValueError('adr_exact_market_cap_missing')
            caps.append(float(exact.replace(',', '')))
    if len(caps) != 1 or not 0 < caps[0] < 1e16:
        raise ValueError('adr_market_cap_invalid')
    sector = SECTORS.get(fields.get('Sector'))
    industry = fields.get('Industry')
    if not sector or not industry or industry in ('n/a', '-'):
        raise ValueError('adr_classification_missing')
    return {'market_cap':caps[0], 'market_cap_as_of':session, 'market_cap_currency':'USD',
        'market_cap_quality':'reported_market_cap', 'market_cap_error':None,
        'market_cap_basis':'stockanalysis_reported_company_market_cap_no_share_conversion',
        'market_cap_reason':None, 'sector':sector, 'industry':industry,
        'market_cap_statistics_updated_on':updated_on.isoformat(),
        'provider_sector':fields['Sector'], 'identity_method':'exact_ticker_exchange_isin',
        'source':'Stock Analysis (reported market capitalization)'}


def fetch_page(url, path):
    response = requests.get(url, timeout=25)
    if response.status_code != 200:
        raise ValueError('adr_http_' + str(response.status_code))
    if urlparse(response.url).hostname != 'stockanalysis.com' or urlparse(response.url).path != urlparse(url).path:
        raise ValueError('adr_redirect_identity_change')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    meta = {'path':str(path.resolve()), 'url':url, 'final_url':response.url,
            'status':200, 'sha256':sha(path), 'observed_at':datetime.now(timezone.utc).isoformat()}
    write(path.with_suffix('.meta.json'), meta)
    return meta


def cached_page(url, path, max_age):
    meta_path = path.with_suffix('.meta.json')
    if path.is_file() and meta_path.is_file():
        meta = read(meta_path)
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(meta['observed_at'])).total_seconds()
        if meta.get('url') == url and meta.get('status') == 200 and 0 <= age <= max_age and sha(path) == meta['sha256']:
            return meta
    return fetch_page(url, path)


def collect(snapshot, output, *, cache=None, workers=2):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    cache = Path(cache or ROOT/'runtime/adr-metadata-cache')
    session = snapshot['sessions']['US'][-1]
    rows = [r for r in snapshot['universe'] if r.get('security_type') == 'DEPOSITARY_RECEIPT']
    def one(row):
        result = {k:row.get(k) for k in ('market','symbol','isin_code','listing_exchange_family')}
        try:
            if row['market'] != 'US' or not re.fullmatch(r'[A-Za-z0-9.\-]+', row['symbol']) or not row.get('isin_code'):
                raise ValueError('adr_identity_invalid')
            key = hashlib.sha256(json.dumps(identity(row)).encode()).hexdigest()[:24]
            base = 'https://stockanalysis.com/stocks/' + row['symbol'].lower() + '/'
            stats = cached_page(base+'statistics/', cache/key/session/'statistics.html', 86400)
            time.sleep(.3)
            profile = cached_page(base+'company/', cache/key/'company.html', 7*86400)
            documents = []
            for label, doc in [('statistics',stats), ('company',profile)]:
                target = output/'documents'/(key+'-'+label+'.html')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(doc['path'],target)
                documents.append({**doc,'path':str(target.resolve())})
            result['documents'] = documents
            parsed = parse_pages(*(Path(d['path']).read_bytes() for d in documents), row, session)
            result.update(state='available', values=parsed)
        except (ValueError, OSError, requests.RequestException) as exc:
            result.update(state='unavailable', reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        return result
    results = []
    progress_write_failures = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one,row) for row in rows]
        for future in as_completed(futures):
            results.append(future.result())
            try:
                write(output/'progress.json', {'requested':len(rows), 'completed':len(results),
                    'available':sum(r['state']=='available' for r in results),
                    'failures':[{k:r.get(k) for k in ('symbol','reason')} for r in results if r['state']!='available']})
            except PermissionError:
                # Progress is advisory; only the final evidence bundle commits results.
                # A locked progress display must not discard successful collection.
                progress_write_failures += 1
            if len(results)%25 == 0 or len(results)==len(rows):
                print(json.dumps({'adr_completed':len(results),'requested':len(rows),
                                 'available':sum(r['state']=='available' for r in results)}),flush=True)
    manifest = output/'bundle.json'
    write(manifest, {'schema':1, 'session':session, 'progress_write_failures':progress_write_failures,
        'observed_at':datetime.now(timezone.utc).isoformat(),
        'records':sorted(results,key=lambda r:r['symbol'])})
    return {'path':str(manifest), 'sha256':sha(manifest)}


def verified(reference, inputs=None):
    if not reference:
        return {}
    inputs = inputs if inputs is not None else {}
    path = Path(reference['path'])
    if sha(path) != reference['sha256']:
        raise ValueError('adr_bundle_hash_mismatch')
    inputs[str(path)] = reference['sha256']
    bundle = read(path)
    if bundle.get('schema') != 1:
        raise ValueError('adr_bundle_schema')
    records = {}
    for row in bundle['records']:
        if identity(row) in records:
            raise ValueError('adr_duplicate_identity')
        if row['state'] != 'available':
            continue
        pages = []
        for doc in row['documents']:
            if (doc.get('status') != 200 or any(urlparse(doc[k]).scheme != 'https' or
                    urlparse(doc[k]).hostname != 'stockanalysis.com' for k in ('url','final_url'))
                    or sha(doc['path']) != doc['sha256']):
                raise ValueError('adr_document_invalid')
            inputs[doc['path']] = doc['sha256']
            pages.append(Path(doc['path']).read_bytes())
        if len(pages) != 2:
            raise ValueError('adr_documents_missing')
        parsed = parse_pages(*pages, row, bundle['session'])
        if parsed != row['values']:
            raise ValueError('adr_values_replay_mismatch')
        records[identity(row)] = row
    return records


def apply_market(snapshot, reference):
    rows = verified(reference)
    session = snapshot['sessions']['US'][-1]
    snapshot['adr_metadata'] = reference
    for row in snapshot['universe'] + snapshot['series']:
        found = rows.get(identity(row))
        if row.get('security_type') != 'DEPOSITARY_RECEIPT' or not found:
            continue
        if found['values']['market_cap_as_of'] != session:
            raise ValueError('adr_market_session_mismatch')
        row.update({k:v for k,v in found['values'].items() if k.startswith('market_cap')})
        row['market_cap_source'] = found['documents'][0]['url']
        row['market_cap_observed_at'] = found['documents'][0]['observed_at']
    from market_scope import refresh_coverage
    refresh_coverage(snapshot)
    return snapshot


def apply_groups(groups, snapshot, inputs, applied=None):
    records = verified(snapshot.get('adr_metadata'), inputs)
    result = []
    for row in groups:
        found = records.get(identity(row))
        if not found:
            result.append(row); continue
        value = found['values']
        proof = [{'source':'Stock Analysis','url':doc['url'],'path':doc['path'],
            'sha256':doc['sha256'],'observed_at':doc['observed_at'],'identity_method':value['identity_method']}
            for doc in found['documents']]
        from sector_observation import classification_freshness
        freshness = classification_freshness(proof, applied or max(p['observed_at'] for p in proof))
        result.append({**row, 'group_state':'matched', 'group_code':value['sector'], 'group_name':value['sector'],
            'group_taxonomy':'US_SECTOR_STOCKANALYSIS', 'reason':None, 'industry':value['industry'],
            'mapping_version':'stockanalysis_exact_identity_adr_v1',
            'evidence':proof, 'classification_freshness':freshness})
    return result


def derive(source, output, reference):
    from market_collect import audit, write_json
    from daily_market import source_check
    source, output = Path(source).resolve(), Path(output).resolve()
    source_check(source)
    snapshot = apply_market(read(source/'snapshot.json'), reference)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output/'snapshot.json', snapshot)
    write_json(output/'summary.json', audit(snapshot))
    write_json(output/'derivation.json', {'source':str(source),'source_snapshot_sha256':sha(source/'snapshot.json'),
               'adr_metadata':reference,'prices_changed':False})
    if (source/'basis').is_dir():
        shutil.copytree(source/'basis', output/'basis')
    write_json(output/'manifest.json',{str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()})
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    from daily_market import source_check
    source_check(args.source)
    reference = collect(read(args.source/'snapshot.json'), args.output)
    print(json.dumps(reference),flush=True)

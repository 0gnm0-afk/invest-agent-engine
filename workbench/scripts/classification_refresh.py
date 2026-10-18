"""Apply immutable, directly verified Finviz refreshes to exact saved identities."""
import json
from pathlib import Path
from urllib.parse import urlparse
from finviz_public_classification import parse_alias_quote


def apply(groups, snapshot, checked, inputs, applied, age_issue, allowed_sectors):
    reference = snapshot.get('classification_refresh')
    if not reference:
        return groups
    manifest_path = checked(Path(reference['path']), reference['sha256'], inputs)
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1:
        raise ValueError('invalid_classification_refresh_schema')
    source = checked(Path(manifest['identity_snapshot']['path']), manifest['identity_snapshot']['sha256'], inputs)
    identities = {(r['market'],r['symbol'],r.get('isin_code'),r['listing_exchange_family']) for r in json.loads(source.read_text(encoding='utf-8'))['universe']}
    updates = {}
    for record in manifest['records']:
        key = tuple(record[k] for k in ('market','symbol','isin_code','exchange'))
        if key in updates or key not in identities or record['market'] != 'US':
            raise ValueError('classification_refresh_identity_unverified')
        paths = [(root/record[k]).resolve() for k in ('document','meta')]
        if any(not p.is_relative_to(root.resolve()) for p in paths):
            raise ValueError('classification_refresh_path_outside_root')
        document, metadata = paths
        meta = json.loads(checked(metadata,record['meta_sha256'],inputs).read_text(encoding='utf-8'))
        if meta['status'] != 200 or any(urlparse(meta[k]).scheme!='https' or urlparse(meta[k]).hostname!='finviz.com' for k in ('url','final_url')):
            raise ValueError('classification_refresh_source_invalid')
        if age_issue(meta['observed_at'],applied):
            raise ValueError('classification_refresh_timestamp_invalid')
        parsed = parse_alias_quote(checked(document,meta['sha256'],inputs).read_bytes(), record['finviz_symbol'],record['symbol'],record['exchange'])
        if parsed['sector'] not in allowed_sectors or parsed['industry'].startswith('Closed-End Fund') or parsed['industry']=='Exchange Traded Fund':
            raise ValueError('classification_refresh_security_type_conflict')
        updates[key] = (parsed,{'path':str(document),'sha256':meta['sha256'],'url':meta['url'],
                              'observed_at':meta['observed_at'],'identity_method':'exact_saved_isin_and_finviz_ticker_exchange_link',
                              'allow_operating_unit':record.get('allow_operating_unit',False)})
    result=[]
    for row in groups:
        key = tuple(row.get(k) for k in ('market','symbol','isin_code','exchange'))
        found = updates.get(key)
        if not found or row['group_state']=='matched':
            result.append(row);continue
        parsed, proof = found
        if 'security_type_conflict' in (row.get('reason') or ''):
            original = row.get('original_classification') or {}
            if not (proof.get('allow_operating_unit') and original.get('identity_matched') is True
                    and original.get('source_security_type') == '유닛'
                    and tuple(original.get(k) for k in ('market','symbol','isin_code','exchange')) == key):
                result.append(row);continue
        result.append({**row,'group_code':parsed['sector'],'group_name':parsed['sector'],'group_state':'matched',
                       'reason':None,'evidence':[proof],'mapping_version':'direct_finviz_identity_refresh_v1',
                       'classification_refresh_previous_reason':row.get('reason')})
    return apply_issuer_links(result, manifest, identities, checked, inputs, applied, age_issue)


def apply_issuer_links(groups, manifest, identities, checked, inputs, applied, age_issue):
    """Transfer company sector only across SEC-confirmed common share classes."""
    reference = manifest.get('issuer_reference')
    if not reference:
        return groups
    meta_path = checked(Path(reference['meta_path']),reference['meta_sha256'],inputs)
    metadata = json.loads(meta_path.read_text(encoding='utf-8'))
    if (len(metadata)!=1 or metadata[0]['url']!='https://www.sec.gov/files/company_tickers_exchange.json'
            or age_issue(metadata[0]['fetched_at'],applied)):
        raise ValueError('issuer_reference_source_or_time_invalid')
    path = checked(Path(reference['path']),metadata[0]['sha256'],inputs)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['fields'] != ['cik','name','ticker','exchange']:
        raise ValueError('issuer_reference_schema_invalid')
    issuers = {}
    for cik,name,ticker,exchange in data['data']:
        key = ticker,{'Nasdaq':'NASDAQ','NYSE':'NYSE'}.get(exchange,exchange)
        if key in issuers and issuers[key]!=cik:
            raise ValueError('issuer_reference_ambiguous')
        issuers[key]=cik
    links = {}
    for link in manifest.get('issuer_links',[]):
        key = tuple(link[k] for k in ('market','symbol','isin_code','exchange'))
        source = tuple(link['source'][k] for k in ('market','symbol','isin_code','exchange'))
        if key not in identities or source not in identities or key in links or key[0]!='US' or source[0]!='US':
            raise ValueError('issuer_share_identity_unverified')
        # SEC uses '-' for the provider's '.' class separator, never suffix guessing.
        if (issuers.get((key[1].replace('.','-'),key[3])) != link['cik']
                or issuers.get((source[1].replace('.','-'),source[3])) != link['cik']):
            raise ValueError('issuer_share_cik_mismatch')
        links[key]=source,link['cik']
    by_key = {tuple(r.get(k) for k in ('market','symbol','isin_code','exchange')):r for r in groups}
    def common(row):
        original = row.get('original_classification') or {}
        return original.get('identity_matched') is True and original.get('source_security_type')=='보통주' and all(original.get(k)==row.get(k) for k in ('market','symbol','isin_code','exchange'))
    result=[]
    for row in groups:
        key=tuple(row.get(k) for k in ('market','symbol','isin_code','exchange'))
        link=links.get(key)
        if row['group_state']=='matched' or not link or not common(row):
            result.append(row);continue
        source,cik=link;parent=by_key.get(source)
        siblings=[r for r in groups if r['market']=='US' and r['group_state']=='matched' and common(r)
                  and issuers.get((r['symbol'].replace('.','-'),r['exchange']))==cik]
        if not parent or parent not in siblings or len({r['group_code'] for r in siblings})!=1:
            result.append(row);continue
        proof={'path':str(path),'sha256':metadata[0]['sha256'],'url':metadata[0]['url'],
               'observed_at':metadata[0]['fetched_at'],'identity_method':'SEC_same_CIK_common_share_classes',
               'cik':cik,'source_symbol':parent['symbol'],'source_isin_code':parent['isin_code']}
        result.append({**row,'group_state':'matched','reason':None,'group_code':parent['group_code'],
            'group_name':parent['group_name'],'evidence':parent['evidence']+[proof],
            'mapping_version':'finviz_company_sector_sec_share_classes_v1',
            'classification_refresh_previous_reason':row.get('reason')})
    return result

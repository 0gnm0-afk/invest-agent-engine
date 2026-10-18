"""Read saved public FnGuide WICS top-level components; never infer absent stocks."""
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup


def industry_hierarchy(data, sectors):
    """Roll up only exact published six-digit WICS labels, ignoring whitespace."""
    tables = BeautifulSoup(data, 'html.parser').select('table.sector')
    if len(tables) != 1:
        raise ValueError('wics_hierarchy_table_invalid')
    result, tops = {}, {}
    for tr in tables[0].select('tr'):
        cells = [c.get_text(' ', strip=True) for c in tr.find_all('td', recursive=False)]
        if not cells:
            continue
        if len(cells) not in (2, 4, 6) or not re.fullmatch(r'\d{6}', cells[-2]):
            raise ValueError('wics_hierarchy_row_invalid')
        if len(cells) == 6:
            tops['G'+cells[0]] = cells[1]
        code, label = cells[-2:]
        parent = 'G'+code[:2]
        key = ''.join(label.split())
        if parent not in sectors or key in result:
            raise ValueError('wics_hierarchy_ambiguous')
        result[key] = {'code': parent, 'name': sectors[parent], 'industry_code': code,
                       'industry_name': label}
    if tops != sectors:
        raise ValueError('wics_hierarchy_top_level_conflict')
    return result


def company_industry(data, symbol, hierarchy):
    soup = BeautifulSoup(data, 'html.parser')
    codes = [x.get('value') for x in soup.select('input#hd_cmp_cd, input#hidCMP_CD')]
    name_nodes = soup.select('span.name')
    identity = name_nodes[0].find_parent('dt') if len(name_nodes) == 1 else None
    numbers = [x.get_text(strip=True) for x in identity.select('b.num')] if identity else []
    names = [x.get_text(strip=True) for x in name_nodes]
    labels = [x.get_text(' ', strip=True) for x in soup.select('dt.line-left')]
    industries = [x.removeprefix('WICS :').strip() for x in labels if x.startswith('WICS :')]
    if (not re.fullmatch(r'[0-9A-Z]{6}', symbol) or codes != [symbol, symbol] or
            numbers != [symbol] or len(names) != 1 or not names[0] or
            len([x for x in labels if x.startswith('KOSPI :')]) != 1):
        raise ValueError('wics_company_identity_or_market_conflict')
    if len(industries) != 1 or ''.join(industries[0].split()) not in hierarchy:
        raise ValueError('wics_company_industry_unconfirmed')
    return {**hierarchy[''.join(industries[0].split())], 'published_industry_name': industries[0],
            'source_name': names[0], 'symbol': symbol}


def top_sectors(tree):
    found = []
    def walk(value):
        if isinstance(value, dict):
            if value.get('title') == 'WICS':
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(tree)
    if len(found) != 1:
        raise ValueError('wics_tree_ambiguous')
    rows = found[0]['children']
    if (len(rows) != 10 or any(not re.fullmatch(r'G\d{2}', r['key']) or
            not r.get('isIndex') for r in rows) or len({r['key'] for r in rows}) != 10):
        raise ValueError('wics_top_level_invalid')
    return {r['key']: r['title'] for r in rows}


def components(payload, code, name, as_of):
    info, rows = payload['info'], payload['list']
    stamp = re.fullmatch(r'/Date\((\d+)\)/', info['TRD_DT'])
    day = datetime.fromtimestamp(int(stamp[1])/1000, timezone(timedelta(hours=9))).date().isoformat() if stamp else None
    if day != as_of or info['CNT'] != len(rows):
        raise ValueError('wics_date_or_count_mismatch')
    for row in rows:
        if (row['IDX_CD'] != code or row['SEC_CD'] != code or row['SEC_NM_KOR'] != name or
                not re.fullmatch(r'[0-9A-Z]{6}', row['CMP_CD']) or not row['CMP_KOR']):
            raise ValueError('wics_component_identity_or_sector_conflict')
    return rows


def load_wics(root, checked, load, inputs):
    manifest_path = root/'wics-public.json'
    if not manifest_path.exists():
        return None
    manifest = load(checked(manifest_path, None, inputs))
    source_root = (root/'sources').resolve()
    def source(name, host='www.wiseindex.com'):
        path = (source_root/name).resolve()
        if not path.is_relative_to(source_root):
            raise ValueError('wics_path_outside_root')
        meta = load(checked(path.with_suffix(path.suffix+'.meta.json'), None, inputs))
        checked(path, meta['sha256'], inputs)
        for key in ('url', 'final_url'):
            url = urlparse(meta[key])
            if url.scheme != 'https' or url.hostname != host or meta['status'] != 200:
                raise ValueError('wics_source_domain_or_status')
        return path, meta
    page, page_meta = source(manifest['page'])
    script, script_meta = source(manifest['script'])
    tree, tree_meta = source(manifest['tree'])
    taxonomy, taxonomy_meta = source(manifest['taxonomy'])
    text = page.read_text(encoding='utf-8-sig')
    if (urlparse(script_meta['url']).path not in text or '/api/tree/4' not in text or
            urlparse(tree_meta['url']).path != '/api/tree/4' or
            urlparse(taxonomy_meta['url']).path != '/About/WICS' or
            '/Index/GetIndexComponets' not in script.read_text(encoding='utf-8-sig')):
        raise ValueError('wics_public_source_chain_invalid')
    sectors = top_sectors(load(tree))
    entries = manifest['documents']
    if len(entries) != 10 or {r['code'] for r in entries} != set(sectors):
        raise ValueError('wics_sector_documents_incomplete')
    records = defaultdict(list)
    for entry in entries:
        path, meta = source(entry['file'])
        url = urlparse(meta['url'])
        if (url.path != '/Index/GetIndexComponets' or parse_qs(url.query) !=
                {'dt': [manifest['as_of'].replace('-', '')], 'sec_cd': [entry['code']], 'ceil_yn': ['0']}):
            raise ValueError('wics_query_mismatch')
        for row in components(load(path), entry['code'], sectors[entry['code']], manifest['as_of']):
            records[row['CMP_CD']].append({'code': row['SEC_CD'], 'name': row['SEC_NM_KOR'],
                'source_name': row['CMP_KOR'], 'symbol': row['CMP_CD'], 'url': meta['url'],
                'path': str(path), 'sha256': meta['sha256'], 'observed_at': meta['observed_at'],
                'context_observed_at': [m['observed_at'] for m in (page_meta, script_meta, tree_meta, taxonomy_meta)],
                'classification_as_of': manifest['as_of'],
                'identity_method': 'exact_KR_short_code_published_WICS_component_no_isin'})
    hierarchy = industry_hierarchy(taxonomy.read_bytes(), sectors) if manifest.get('companies') else {}
    for entry in manifest.get('companies', []):
        path, meta = source(entry['file'], 'navercomp.wisereport.co.kr')
        for key in ('url', 'final_url'):
            url = urlparse(meta[key])
            if (url.path != '/v2/company/c1010001.aspx' or
                    parse_qs(url.query) != {'cmp_cd': [entry['symbol']]}):
                raise ValueError('wics_company_query_mismatch')
        row = company_industry(path.read_bytes(), entry['symbol'], hierarchy)
        records[entry['symbol']].append({**row, 'url': meta['url'], 'path': str(path),
            'sha256': meta['sha256'], 'observed_at': meta['observed_at'],
            'context_observed_at': [tree_meta['observed_at'], taxonomy_meta['observed_at']],
            'classification_as_of': None, 'source': 'FnGuide_company_WICS_official_hierarchy_rollup',
            'identity_method': 'exact_KOSPI_short_code_published_WICS_industry_no_isin'})
    return {'sectors': sectors, 'records': dict(records), 'as_of': manifest['as_of']}


def select_wics(records, applied, age_issue):
    if not records:
        return None, 'wics_direct_evidence_absent'
    if (len({r.get('path') for r in records}) != len(records) or
            len({(r['code'], r['name']) for r in records}) != 1):
        return None, 'wics_duplicate_or_conflicting_assignment'
    for row in records:
        dates = [row['observed_at'], *row.get('context_observed_at', [])]
        if row['classification_as_of'] is not None:
            dates.append(row['classification_as_of']+'T00:00:00+09:00')
        for observed in dates:
            issue = age_issue(observed, applied)
            if issue:
                return None, issue
    return records[0], None

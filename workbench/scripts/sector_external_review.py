"""Saved per-security external checks; alternate providers never fill Finviz groups."""
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

SA_SECTORS = {'Financials', 'Healthcare', 'Technology', 'Industrials', 'Consumer Discretionary',
              'Materials', 'Energy', 'Communication Services', 'Real Estate', 'Consumer Staples', 'Utilities'}


def stockanalysis_profile(data, symbol, exchange):
    soup = BeautifulSoup(data, 'html.parser')
    fields = {}
    for tr in soup.select('tr'):
        cells = tr.find_all('td', recursive=False)
        if len(cells) == 2:
            key, value = [c.get_text(' ', strip=True) for c in cells]
            if key in ('Ticker Symbol', 'Exchange', 'Sector', 'Industry'):
                if key in fields:
                    return None, 'duplicate_profile_field'
                fields[key] = value
    canonical = soup.select('link[rel="canonical"]')
    expected = 'https://stockanalysis.com/stocks/'+symbol.lower()+'/company/'
    if (len(canonical) != 1 or canonical[0].get('href') != expected or
            fields.get('Ticker Symbol') != symbol or fields.get('Exchange') != exchange):
        return None, 'profile_identity_or_exchange_unconfirmed'
    if fields.get('Sector') not in SA_SECTORS or not fields.get('Industry'):
        return None, 'profile_sector_unconfirmed'
    # Preserve explicit listing notices, without deriving delisting from missing pages.
    notices = [x.strip() for x in soup.stripped_strings if symbol+' was delisted' in x]
    return {'provider': 'Stock Analysis', 'taxonomy': 'StockAnalysis_published_sector',
            'sector': fields['Sector'], 'industry': fields['Industry'],
            'symbol': symbol, 'exchange': exchange, 'listing_notices': notices,
            'classification_effective_date': None, 'adopted_as_Finviz': False}, None


def load_reviews(root, checked, load, inputs, applied, age_issue):
    manifest_path = root/'external-review.json'
    if not manifest_path.exists():
        return {}
    manifest = load(checked(manifest_path, None, inputs))
    sources = (root/'sources').resolve()
    result = {}
    for entry in manifest['documents']:
        symbol, exchange = entry['symbol'], entry['exchange']
        if symbol in result:
            raise ValueError('external_review_duplicate_symbol')
        review = {'alternate_classification': None, 'checks': []}
        for kind in ('stockanalysis', 'finviz_quote'):
            if kind not in entry:
                continue
            path = (sources/entry[kind]).resolve()
            if not path.is_relative_to(sources):
                raise ValueError('external_review_path_outside_root')
            meta = load(checked(path.with_suffix(path.suffix+'.meta.json'), None, inputs))
            checked(path, meta['sha256'], inputs)
            for key in ('url', 'final_url'):
                url = urlparse(meta[key])
                valid = (url.scheme == 'https' and
                    ((kind == 'stockanalysis' and url.hostname == 'stockanalysis.com' and
                      url.path == '/stocks/'+symbol.lower()+'/company/' and not url.query) or
                     (kind == 'finviz_quote' and url.hostname == 'finviz.com' and
                      url.path in ('/quote.ashx', '/stock') and parse_qs(url.query) == {'t': [symbol]})))
                if not valid:
                    raise ValueError('external_review_source_identity_conflict')
            check = {**meta, 'path': str(path), 'provider': kind}
            issue = age_issue(meta['observed_at'], applied)
            if issue:
                check['issue'] = issue
            elif meta['status'] != 200:
                check['issue'] = 'public_page_http_'+str(meta['status'])
            elif kind == 'stockanalysis':
                value, issue = stockanalysis_profile(path.read_bytes(), symbol, exchange)
                check['issue'] = issue
                review['alternate_classification'] = value
            else:
                check['issue'] = 'quote_requires_Finviz_mapping_validation'
            review['checks'].append(check)
        result[symbol] = review
    return result


def review_category(row):
    if row.get('external_finviz_sector'):
        return 'finviz_confirmed_but_observation_excluded'
    if row.get('external_review', {}).get('alternate_classification'):
        return 'alternate_provider_confirmed_Finviz_unconfirmed'
    return 'checked_sources_have_no_verified_sector'

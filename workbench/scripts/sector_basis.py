"""Official provider semantics applied only to the local sector observation path."""
import hashlib
from datetime import datetime
from pathlib import Path

FAQ_URL = 'https://openapi.tossinvest.com/openapi-docs/faq.md'
CONTRACT = 'toss_daily_faq_observed_20260925'
# Full diff reviewed 2026-10-02: auth diagnostics/orderbook/order FAQ additions only.
# Daily session, adjusted OHLC and volume semantics are unchanged. Unknown revisions block.
REVIEWED_REVISIONS = frozenset({(
    CONTRACT, FAQ_URL,
    '0dd9ad07af2bdf4b333a8ba902ede5b727e5f87cf95d77d7b5786119159d216b',
    '65a904ebe773284d42639a1f8d1eaeaf7fb19526572372d68cf7bd8bc3c986dc',
), (
    # Full diff reviewed 2026-10-07. Adjustment trigger thresholds/history coverage
    # clarified; provider-adjusted values and non-total-return contract unchanged.
    CONTRACT, FAQ_URL,
    '0dd9ad07af2bdf4b333a8ba902ede5b727e5f87cf95d77d7b5786119159d216b',
    '89198a71f91c88d672fc2e55fc363015e5c99069378c93c35460203e19177b4e',
), (
    # Full FAQ diff reviewed 2026-10-13. KR/US adjustment rounding differs;
    # daily session and comprehensive volume still use provider values as supplied.
    CONTRACT, FAQ_URL,
    '0dd9ad07af2bdf4b333a8ba902ede5b727e5f87cf95d77d7b5786119159d216b',
    '24719bffec1c419af9dab1e16aba66e2d35103a1a417e28ce36bd84bd2b270ca',
)})


def load_basis(root, checked, load, inputs, applied, age_issue, *, rechecks=None):
    path = root/'calculation-basis.json'
    if not path.exists():
        return None
    config = load(checked(path, None, inputs))
    if config.get('contract') != CONTRACT:
        raise ValueError('unknown_calculation_basis_contract')
    if config.get('kr_reference') != 'saved_universe_equal_weight':
        raise ValueError('approved_KR_reference_required')
    receipt = None
    if rechecks is not None:
        rechecks = Path(rechecks).resolve()
        receipt = load(checked(rechecks, None, inputs))
        if (receipt.get('schema') != 1 or receipt.get('contract') != CONTRACT
                or receipt.get('basis_sha256') != hashlib.sha256(path.read_bytes()).hexdigest()):
            raise ValueError('basis_recheck_identity_mismatch')
    docs = []
    for entry in config['documents']:
        doc = (root/'sources'/entry['file']).resolve()
        if not doc.is_relative_to((root/'sources').resolve()):
            raise ValueError('basis_document_outside_root')
        meta = load(checked(doc.with_suffix(doc.suffix+'.meta.json'), None, inputs))
        checked(doc, entry['sha256'], inputs)
        checked(doc, meta['sha256'], inputs)
        if meta['url'] != entry['url'] or meta['final_url'] != entry['url'] or meta['status'] != 200:
            raise ValueError('basis_document_identity_mismatch')
        issue = age_issue(meta['observed_at'], applied)
        item = {**meta, 'path':str(doc)}
        if issue == 'stale_classification' and receipt is not None:
            matches = [r for r in receipt.get('documents', []) if r.get('url') == entry['url']]
            if len(matches) != 1:
                raise ValueError('basis_recheck_identity_mismatch')
            fresh = matches[0]
            raw = (rechecks.parent / fresh['file']).resolve()
            if not raw.is_relative_to(rechecks.parent):
                raise ValueError('basis_recheck_outside_root')
            checked(raw, fresh['sha256'], inputs)
            same_contract = (fresh['sha256'] == entry['sha256'] or
                             (CONTRACT, entry['url'], entry['sha256'], fresh['sha256']) in REVIEWED_REVISIONS)
            if (not same_contract or fresh.get('final_url') != entry['url']
                    or fresh.get('status') != 200):
                raise ValueError('basis_document_changed_review_required')
            issue = age_issue(fresh['observed_at'], applied)
            if datetime.fromisoformat(fresh['observed_at']) < datetime.fromisoformat(meta['observed_at']):
                raise ValueError('basis_recheck_identity_mismatch')
            item['recheck'] = {**fresh, 'path':str(raw)}
        if issue:
            raise ValueError('basis_document_not_current')
        docs.append(item)
    if not any(d['url'] == FAQ_URL for d in docs):
        raise ValueError('official_toss_faq_required')
    return {**config, 'evidence':docs}


def series_basis(series):
    """Validate saved request metadata. Returned bars are already adjusted by Toss."""
    if not series:
        return None, 'series_missing'
    market = series.get('market')
    if series.get('provider') == 'synthetic' and series.get('price_basis') == 'synthetic' and market in ('KR','US'):
        return {'contract':'synthetic-demo-v1','market':market,'price_session':'synthetic-weekdays',
                'price_adjustment':'synthetic','volume_adjustment':'synthetic',
                'return_semantics':'invented-price-path','volume_semantics':'invented-activity'}, None
    if (market == 'KR' and series.get('provider') == 'Toss OpenAPI candles' and
            series.get('source_url') == 'https://openapi.tossinvest.com/api/v1/market-indicators/KOSPI/candles' and
            series.get('currency') == 'KRW' and series.get('provider_symbol') == 'KOSPI' and
            series.get('instrument_type') == 'INDEX' and series.get('price_basis') == 'toss_index_points_observation_only'):
        return {'contract':CONTRACT, 'market':'KR', 'price_session':'KR_KRX_regular_index_close',
                'price_adjustment':'cash_index_points'}, None
    if (market not in ('KR','US') or series.get('provider') != 'Toss OpenAPI candles' or
            series.get('source_url') != 'https://openapi.tossinvest.com/api/v1/candles'):
        return None, 'unverified_price_provider_or_session'
    if series.get('currency') != ('KRW' if market == 'KR' else 'USD'):
        return None, 'basis_currency_mismatch'
    if (series.get('price_basis') != 'toss_adjusted_ohlc_no_order_authority' or
            series.get('collection',{}).get('adjusted') is not True):
        return None, 'adjusted_price_and_volume_required'
    return {'contract':CONTRACT, 'market':market,
            'price_session':'KR_integrated_provider_daily_close' if market == 'KR' else 'US_regular_official_close',
            'price_adjustment':('provider_price_times_adjustment_ratio_KRW_rounded_no_local_readjustment'
                                if market == 'KR' else
                                'provider_price_times_adjustment_ratio_USD_unrounded_no_local_readjustment'),
            'volume_adjustment':('provider_volume_divided_by_adjustment_ratio_floored'
                                 if market == 'KR' else
                                 'provider_volume_divided_by_adjustment_ratio_nearest_integer'),
            'volume_session':'provider_daily_comprehensive_including_off_session_trades',
            'return_semantics':'provider_adjusted_close_return_not_certified_reinvested_total_return',
            'volume_semantics':'relative_provider_activity_not_consolidated_market_flow'}, None


def comparison_issue(stock_basis, benchmark_basis):
    if not stock_basis or not benchmark_basis:
        return 'benchmark_basis_unverified'
    for field in ('market','price_session','price_adjustment'):
        if stock_basis.get(field) != benchmark_basis.get(field):
            return 'benchmark_'+field+'_mismatch'
    return None

"""Read-only view adapter. No market collection. Cached sector and downtrend projections on verified saved inputs."""
import hashlib
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, allow_nan=False)


def checked(path, expected):
    if not expected or sha(path) != expected:
        raise ValueError('saved_artifact_hash_mismatch:' + Path(path).name)
    return load(path)


def saved_market(root=ROOT, run_dir=None):
    """Default to latest attempt; explicit prior views require a separate daily failure banner."""
    if run_dir is None:
        run_dir = load(root / 'runtime/latest.json')['output']
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_relative_to((root / 'runtime').resolve()):
        raise ValueError('latest_outside_private_runtime')
    manifest = load(run_dir / 'manifest.json')
    def artifact(name):
        return checked(run_dir / name, manifest['output_hashes'].get(name) or manifest['output_hashes'].get(name.replace('/', '\\')))
    run = artifact('run.json')
    result = {'state': run['state'], 'run_id': run_dir.name, 'processing_state': run['processing_state'],
              'data_times': run.get('data_times', {}), 'quality': run.get('quality', {}),
              'approved_exclusions': run.get('approved_exclusions', []), 'analysis_pending': run.get('analysis_pending', []),
              'finished_at': run.get('finished_at'), 'coverage': run.get('sector_coverage'),
              'entries': [], 'sectors': [], 'stocks': {}, 'errors': [], 'session_dates': {},
              'candidate_count': None, 'entry_candidate_count': None}
    review = {'markets': {'KR': {'entries': []}, 'US': {'entries': []}}}
    observation = {}
    try:
        review = artifact('priority-review.json')
        if review.get('schema') != 4 or review.get('strategy') != 'pdf_momentum':
            raise ValueError('current_pdf_watch_schema_required')
        result.update(session_dates=review['session_dates'], candidate_count=review['candidate_count'],
                      entry_candidate_count=review['entry_candidate_count'], limitations=review['limitations'])
        result['entries'] = [e for m in review['markets'].values() for e in m['entries']]
    except (OSError, ValueError, KeyError) as exc:
        result['errors'].append('screening_unavailable:' + type(exc).__name__)
    try:
        signals = artifact('sectors/signals.json')
        observation = artifact('sectors/observation.json')
        observations = {(s['market'], s['group_code']): s for s in observation['observations']}
        for s in signals['sectors']:
            o = observations.get((s['market'], s['group_code']), {})
            result['sectors'].append({k: s.get(k) for k in
                ('market', 'group_code', 'group_name', 'current', 'history', 'leaders', 'weak_contributors',
                 'transitions', 'candidates', 'state_since')} | {'horizons': o.get('horizons', {})})
    except (OSError, ValueError, KeyError) as exc:
        result['errors'].append('sectors_unavailable:' + type(exc).__name__)
    source = Path(run['market_source']) / 'snapshot.json'
    snapshot = checked(source, manifest['input_hashes'].get(str(source)))
    # Keep all saved symbols accessible from sector leaders and actual holdings.
    for s in snapshot['series']:
        result['stocks'][s['market'] + ':' + s['symbol']] = {
            k: s.get(k) for k in ('market', 'symbol', 'name', 'currency', 'provider', 'price_basis',
                                  'collected_at', 'bars', *SEARCH_METADATA_FIELDS)}
    from sector_focus import cached, screen_groups
    from invest_agent.trading.store import BusyError
    try:
        groups = artifact('sectors/groups.json')
        config = load(root/'config.json') if (root/'config.json').exists() else {}
        if config.get('sector_manifest'):
            from sector_projection import cached as project
            projected = project(root, run_dir, config, source.parent, observation, review, snapshot=snapshot)
            groups = projected['groups']
            horizons = {(s['market'],s['group_code']):s['horizons'] for s in projected['observation']['observations']}
            result['sectors'] = [dict(s,horizons=horizons[(s['market'],s['group_code'])]) for s in projected['signals']['sectors']]
            result['coverage'] = projected['observation']['coverage']
            result['classification_policy'] = projected['policy']
        result['focus'] = cached(root, run_dir, snapshot, groups, result['sectors'], sha(source), config.get('sector_focus'))
    except (OSError, ValueError, KeyError, TypeError, BusyError) as exc:
        result['focus'] = {'items': [], 'markets': {}, 'error': '자료 또는 캐시 확인 필요 ('+type(exc).__name__+')'}
    # Describe incomplete inputs separately from successful calculation stages.
    result['quality_gaps'] = {
        'price_history': result['quality'].get('quality_issue_series'),
        'market_cap': (sum(str(row.get('market_cap_quality', '')).startswith('unavailable')
                           for row in snapshot['universe']) if 'universe' in snapshot else None),
        'classification': (sum(c['unmatched'] for c in result['coverage'].values())
                           if result.get('coverage') and all('unmatched' in c for c in result['coverage'].values()) else None),
    }
    downtrend_options = load(root/'config.json').get('priority_review',{}).get('watchlist',{}).get('downtrend_approach') if (root/'config.json').exists() else None
    if downtrend_options is not None and review.get('schema') == 4:
        from recent_screen_projection import cached as trendline_projection
        try:
            projected_review = trendline_projection(root,artifact('screen.json'),snapshot,review,sha(source),downtrend_options)
            result['entries'] = [e for g in projected_review['markets'].values() for e in g['entries']]
            result['candidate_count'] = projected_review['candidate_count']
            result['entry_candidate_count'] = projected_review['entry_candidate_count']
            result['trendline_policy'] = projected_review['config']['trendline_policy']
            result['downtrend_settings'] = projected_review['config']['watchlist']['downtrend_approach']
        except (OSError, ValueError, KeyError, TypeError, BusyError) as exc:
            result['errors'].append('recent_trendlines_unavailable:' + type(exc).__name__)
            result.update(entries=[],candidate_count=None,entry_candidate_count=None,trendline_policy='unavailable')
    result['screen_groups'] = screen_groups(result['entries'], snapshot.get('universe', []))
    return result



SEARCH_NAME_FIELDS = ('name', 'english_name', 'korean_name', 'name_en', 'name_ko')
SEARCH_METADATA_FIELDS = (*SEARCH_NAME_FIELDS[1:], 'aliases', 'provider_symbol',
                          'listing_exchange_family', 'listing_exchange', 'exchange', 'isin_code')


def search_text(value):
    """Match user spelling across case, spacing and Unicode composition."""
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())


class StockSearch:
    """Compact in-memory index of stocks already addressable by the detail view."""
    limit = 12

    def __init__(self, stocks, listings=None, policy=None):
        from market_scope import load as scope_policy, match
        policy = scope_policy() if policy is None else policy
        self.rows = []
        for key, stock in stocks.items():
            market, symbol = stock.get('market'), stock.get('symbol')
            if market not in ('KR', 'US') or not isinstance(symbol, str) or key != market + ':' + symbol:
                continue
            listing = (listings or {}).get(key, {})
            listed_isin = listing.get('isin_code') or listing.get('isinCode')
            if stock.get('isin_code') and listed_isin and stock['isin_code'] != listed_isin:
                listing = {}  # A reused ticker must not inherit a former company's names.
            row = {**listing, **stock}
            row['isin_code'] = stock.get('isin_code') or listing.get('isin_code') or listing.get('isinCode')
            if match(row, policy):
                continue
            names = [row.get(field) for field in SEARCH_NAME_FIELDS]
            names += row.get('aliases', []) if isinstance(row.get('aliases'), list) else []
            terms = [symbol, row.get('provider_symbol'), *names]
            if market == 'KR' and symbol.endswith(('.KS', '.KQ')):
                terms.append(symbol.rsplit('.', 1)[0])
            terms = tuple(dict.fromkeys(search_text(value) for value in terms if isinstance(value, str) and value.strip()))
            name = next((value for value in names if isinstance(value, str) and value.strip()), symbol)
            exchange = next((row.get(field) for field in ('listing_exchange_family', 'listing_exchange', 'exchange')
                             if isinstance(row.get(field), str) and row[field]), None)
            self.rows.append(({'key': key, 'market': market, 'symbol': symbol, 'name': name, 'exchange': exchange}, terms))

    def search(self, query):
        if not isinstance(query, str) or len(query) > 100:
            raise ValueError('invalid_search_query')
        needle = search_text(query)
        result = {'query': query, 'items': [], 'total': 0, 'limit': self.limit}
        if not needle:
            return result
        matches = []
        for item, terms in self.rows:
            rank = min((0 if term == needle else 1 if term.startswith(needle) else 2
                        for term in terms if needle in term), default=None)
            if rank is not None:
                matches.append((rank, search_text(item['symbol']), item['market'], item['key'], item))
        matches.sort(key=lambda row: row[:4])
        result.update(items=[row[-1] for row in matches[:self.limit]], total=len(matches))
        return result


def collect_balances(reader, pages, error_type):
    """Explicit three-endpoint allowlist; no order/reservation/market calls."""
    allowed = {'/n2/acctinfo', '/krstock/inquiry/v1/balance', '/gbstock/inquiry/v1/balance'}
    calls = []
    def restricted(path, inputs, continuation=None):
        if path not in allowed:
            raise ValueError('dashboard_balance_read_only')
        calls.append(path)
        return reader(path, inputs, continuation)
    started = now()
    body, _ = restricted('/n2/acctinfo', {})
    rows = body.get('Output_0')
    if not isinstance(rows, list) or any(word in str(body.get('rsp_msg', '')) for word in ('실패', '오류', '불가', '권한')):
        raise ValueError('account_list_not_confirmed')
    if not rows and not any(word in str(body.get('rsp_msg', '')) for word in ('완료', '조회되었습니다', '내역이 없습니다')):
        raise ValueError('empty_account_list_unconfirmed')
    accounts, seen = [], set()
    for row in rows:
        if row.get('acct_type') not in ('01', '02'):
            continue
        account = row.get('acct_no')
        if not isinstance(account, str) or not account.isdigit() or len(account) != 11 or account in seen:
            raise ValueError('account_identity_invalid')
        seen.add(account)
        item = {'alias': 'nh-' + hashlib.sha256(account.encode()).hexdigest()[:12],
                'account_type': row['acct_type'], 'markets': {}, 'details': {}}
        requests = {
            'KR': ('/krstock/inquiry/v1/balance', {'act_no': account, 'bnc_bse_cd': '1',
                    'ltg_aot_dit_cd': '9', 'aet_bse': '1', 'qut_dit_cd': 'UNT'}),
            'US': ('/gbstock/inquiry/v1/balance', {'act_no': account, 'qut_iqr_dit_cd': '1',
                    'fc_sec_trd_nat_cd': '200', 'cur_cd': 'USD', 'xns_dit_cd': '0'})}
        for market, (path, inputs) in requests.items():
            try:
                item['markets'][market] = {'state': 'received', 'pages': pages(path, inputs, restricted)}
            except error_type as exc:
                # Transport errors are controlled codes from the existing adapter.
                item['markets'][market] = {'state': 'unavailable', 'reason': str(exc)}
        accounts.append(item)
    return {'provider': 'NH PLUG', 'source': 'broker_export', 'as_of': started,
            'collected_until': now(), 'scope': 'KR_US_balances_only_no_orders',
            'accounts': accounts, 'endpoint_calls': calls}


def account_view(config, output, refresh=True, saved_dashboard_root=None):
    """Read only a caller-selected normalized account bundle; no broker discovery."""
    if refresh:
        raise ValueError('broker_adapter_not_in_public_package')
    source = config.get('account_snapshot')
    if source is None:
        return dict(query_state='not_requested',accounts=[],as_of=None,source_mode='missing',limitations=['no_account_input'])
    bundle = load(source)
    if not isinstance(bundle,dict) or not isinstance(bundle.get('accounts'),list):
        raise ValueError('invalid_account_bundle')
    return bundle



def build(root=ROOT, refresh=True, *, market_run=None, daily_update=None, persist_view=False):
    config = load(root / 'config.json')
    # Public engine is installed or supplied by the repository launcher.
    output = root / 'runtime/dashboard' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True)
    try:
        market = saved_market(root, market_run)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        market = {'state': 'unavailable', 'errors': ['saved_market_unavailable:' + type(exc).__name__],
                  'entries': [], 'sectors': [], 'stocks': {}, 'session_dates': {}, 'quality': {},
                  'candidate_count': None, 'entry_candidate_count': None}
    accounts = account_view(config, output, refresh)
    if (daily_update or {}).get('account_state') == 'failed':
        accounts.update(query_state='failed', reason='daily_account_refresh_failed')
    stocks = market.pop('stocks')
    from trailing_warnings import TrailingStore
    from daily_launch import plan_for
    warning_store = TrailingStore(root/'runtime/trailing-warnings.sqlite3')
    warning_time = datetime.now(timezone.utc)
    warning_targets = plan_for(warning_time)['targets']
    # Saved-only viewing must not rewrite monitor timestamps/state. The daily
    # collection path already evaluates warnings after its account refresh.
    warnings = (warning_store.update(accounts, stocks, warning_targets, warning_time) if refresh
                else warning_store.public(warning_time, warning_targets))
    data = {'generated_at': now(), 'market': market, 'accounts': accounts, 'trailing': warnings}
    if daily_update is not None:
        data['daily_update'] = daily_update
    write(output / 'account-view.json', accounts)
    # Explicit --build-only exports retain their prior artifact contract.
    if persist_view:
        write(output / 'dashboard.json', data)
    # Normal HTTP views are already in memory; keep only source/account receipts.
    # Prices remain in the immutable market source, projected in memory only.
    # Do not duplicate hundreds of MB of full-universe bars on every launch.
    write(output / 'manifest.json', {'files': {p.name: sha(p) for p in output.iterdir() if p.is_file()},
                                    'market_network_calls': 0, 'authority': 'read_only_no_orders',
                                    'view_storage': 'export' if persist_view else 'memory', 'market_run': str(market_run) if market_run else None})
    return output, data, stocks


def refresh_account_snapshot(root=ROOT):
    raise ValueError('broker_adapter_not_in_public_package')


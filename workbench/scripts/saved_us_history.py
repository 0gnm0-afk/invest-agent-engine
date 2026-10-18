"""Read a pinned, fully audited market history collection without network calls."""
import hashlib
import json
import math
from datetime import date
from pathlib import Path


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def with_daily(packet, stock, listing):
    """Compose verified stored inputs only; never mutate either immutable source."""
    current = stock.get('bars') or []
    info = packet['history']
    if not current or current[-1]['date'] <= info.get('requested_end', ''):
        return packet
    dates = [b['date'] for b in current]
    try:
        if dates != sorted(set(dates)):
            raise ValueError()
        for b in current:
            date.fromisoformat(b['date'])
            if (any(type(b[k]) not in (int, float) or not math.isfinite(b[k]) or b[k] < 0
                    for k in ('open', 'high', 'low', 'close', 'volume'))
                    or not 0 < b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']):
                raise ValueError()
    except (ValueError, KeyError, TypeError):
        return {**packet, 'history': {**info, 'state': 'partial', 'daily_update_reason': 'invalid_daily_bars'}}
    source = packet.get('series', {})
    historical = source.get('bars') or []
    prior = {b['date']: b for b in historical}
    overlap = [b for b in current if b['date'] in prior]
    reason = None
    for old_key, new_key in (('isinCode', 'isin_code'), ('listing_exchange', 'listing_exchange_family')):
        if listing.get(old_key) and listing[old_key] != stock.get(new_key):
            reason = 'listing_identity_changed'
    if not reason and any(source.get(k) != stock.get(k) for k in ('currency', 'provider', 'price_basis')):
        reason = 'price_basis_changed'
    if not reason and (not overlap or (historical and historical[-1]['date'] not in dates)):
        reason = 'history_overlap_unavailable'
    if not reason and any(not math.isclose(b[k], prior[b['date']][k], rel_tol=1e-8, abs_tol=1e-8)
                          for b in overlap for k in ('open', 'high', 'low', 'close', 'volume')):
        reason = 'history_overlap_changed'
    combined = current if reason else [b for b in historical if b['date'] < dates[0]] + current
    return {'series': {**stock, 'bars': combined}, 'history': {
        **info, 'state': 'partial' if reason or info['state'] == 'partial' else 'complete',
        'archive_requested_end': info.get('requested_end'), 'requested_end': dates[-1],
        'daily_as_of': dates[-1], 'daily_update_mode': 'daily_only' if reason else 'merged',
        'daily_update_reason': reason, 'overlap_count': len(overlap),
        'screening_newer_than_history': False,
        'screening_price_difference': reason in ('price_basis_changed', 'history_overlap_changed'),
    }}


class SavedHistory:
    def __init__(self, pointer, market):
        self.market = market
        self.currency = {"US": "USD", "KR": "KRW"}[market]
        self.pointer = Path(pointer)
        self.enabled = self.pointer.exists()
        self.stocks = {}
        self.error = False
        if not self.enabled:
            return
        try:
            active = json.loads(self.pointer.read_bytes())
            name = active['collection']
            if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
                raise ValueError('invalid_collection')
            self.root = self.pointer.parent / name
            raw = (self.root / 'manifest.json').read_bytes()
            if hashlib.sha256(raw).hexdigest() != active['manifest_sha256']:
                raise ValueError('manifest_mismatch')
            self.manifest = {k.replace('\\', '/'): v for k, v in json.loads(raw).items()}
            self.config = self.read('collection.json', wrapped=True)
            verification = self.read('verification.json')
            universe = self.read('universe.json', wrapped=True)['rows']
            rows = self.read('coverage.json')
            self.coverage = {x['symbol']: x for x in rows}
            self.listings = {x['symbol']: x for x in universe}
            if (any(x.get('market') != market for x in universe)
                    or self.config.get('markets', [market]) != [market]
                    or verification['integrity'] != 'PASS' or not verification['all_attempted']
                    or len(self.listings) != len(universe) or len(self.coverage) != len(rows)
                    or set(self.listings) != set(self.coverage)
                    or verification['verified'] != len(universe)):
                raise ValueError('incomplete_audit')
            self.stocks = {market + ':' + s: dict(market=market, symbol=s, name=x.get('name') or s,
                           currency=self.currency, bars=[], provider='Toss OpenAPI candles')
                           for s, x in self.listings.items()}
        except (OSError, ValueError, KeyError, TypeError):
            self.error = True

    def read(self, name, wrapped=False):
        raw = (self.root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.manifest.get(name):
            raise ValueError('artifact_hash_mismatch')
        value = json.loads(raw)
        if wrapped:
            if hashlib.sha256(packed(value['data'])).hexdigest() != value['sha256']:
                raise ValueError('content_hash_mismatch')
            value = value['data']
        return value

    def packet(self, stock):
        if stock.get('market') != self.market or not self.enabled:
            return None
        info = dict(kind='local_verified_' + self.market.lower(), can_retry=False)
        try:
            if self.error:
                raise ValueError('collection_unavailable')
            symbol = stock['symbol']
            info.update(requested_start='provider_earliest_available', requested_end=self.config['end'],
                        source_id=self.root.name)
            if symbol not in self.coverage:
                return {'history': dict(info, state='unavailable', reason='saved_' + self.market.lower() + '_symbol_unavailable')}
            record = self.coverage[symbol]
            d = self.read('series/' + hashlib.sha256(symbol.encode()).hexdigest() + '.json', wrapped=True)
            if (d['symbol'] != symbol or d['market'] != self.market or d['currency'] != self.currency
                    or d['listing'] != self.listings[symbol] or d['state'] not in ('received', 'empty')
                    or not d['provider_end_reached'] or d['requested_end'] != self.config['end']
                    or record['bars'] != len(d['bars']) or record['state'] != d['state']
                    or not record['provider_end_reached']):
                raise ValueError('series_identity_mismatch')
            bars = d['bars']
            source = {k: d.get(k) for k in ('market','symbol','currency','provider','price_basis','collected_at')}
            if d['state'] == 'empty':
                if bars or record['first'] is not None or record['last'] is not None:
                    raise ValueError('invalid_empty_series')
                return with_daily({'series': dict(source, bars=[]), 'history': dict(info,
                        state='unavailable', reason='saved_history_empty_before_cutoff',
                        stored_count=0, source_first=None, source_last=None)}, stock, self.listings[symbol])
            dates = [b['date'] for b in bars]
            if (not dates or dates != sorted(set(dates)) or dates[0] != record['first']
                    or dates[-1] != record['last'] or dates[-1] > self.config['end']):
                raise ValueError('invalid_series_dates')
            if any(type(b[k]) not in (int, float) or not math.isfinite(b[k]) or b[k] < 0
                   for b in bars for k in ('open', 'high', 'low', 'close', 'volume')):
                raise ValueError('invalid_series_value')
            invalid = [b['date'] for b in bars if not
                       0 < b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']]
            if invalid != record['invalid_ohlc']:
                raise ValueError('quality_mismatch')
            # Historic XKRX calendar disagreements are not proof that a price is invalid.
            excluded = set(invalid) | (set(record['non_sessions']) if self.market == 'US' else set())
            displayed = [b for b in bars if b['date'] not in excluded]
            prior = {b['date']: b for b in stock.get('bars', [])}
            different = any(b['date'] in prior and any(not math.isclose(b[k], prior[b['date']][k], rel_tol=1e-8, abs_tol=1e-8)
                            for k in ('open', 'high', 'low', 'close')) for b in displayed)
            screening_end = max(prior, default=None)
            newer_screen = bool(screening_end and screening_end > self.config['end'])
            quality = {k: record[k] for k in ('invalid_ohlc', 'non_sessions', 'missing_sessions', 'latest_session_missing')}
            quality['excluded_dates'] = sorted(excluded)
            quality['calendar_policy'] = 'retain_with_warning' if self.market == 'KR' else 'exclude_non_sessions'
            flagged = bool(excluded or record['non_sessions'] or record['missing_sessions'] or record['latest_session_missing'])
            info.update(state='partial' if flagged else 'complete', reason='saved_history_quality_flags' if flagged else 'saved_history_verified',
                        stored_count=len(bars), source_first=dates[0], source_last=dates[-1], quality=quality,
                        screening_as_of=screening_end, screening_price_difference=different,
                        screening_newer_than_history=newer_screen)
            source = {k: d.get(k) for k in ('market','symbol','currency','provider','price_basis','collected_at')}
            return with_daily({'series': dict(source, bars=displayed), 'history': info}, stock, self.listings[symbol])
        except (OSError, ValueError, KeyError, TypeError):
            return {'history': dict(info, state='failed', reason='saved_' + self.market.lower() + '_history_verification_failed')}


class SavedUSHistory(SavedHistory):
    def __init__(self, pointer):
        super().__init__(pointer, 'US')


class SavedMarketHistories:
    def __init__(self, root):
        self.markets = {m: SavedHistory(Path(root) / (m.lower() + '-history/active.json'), m)
                        for m in ('US', 'KR')}
        from market_scope import load, match
        policy = load()
        self.stocks = {k: v for history in self.markets.values() for k, v in history.stocks.items()
                       if not match(v, policy)}

    def packet(self, stock):
        history = self.markets.get(stock.get('market'))
        return history.packet(stock) if history else None

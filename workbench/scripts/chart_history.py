"""Selected-stock chart history. Never replaces market/screening snapshots."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from zoneinfo import ZoneInfo

from saved_us_history import SavedMarketHistories
from toss_candles import daily_series
from public_boundaries import HistoryProvider as TossMarketClient, MarketReadError

START = '2016-01-01'
FRAMES = {'1d', '1w', '1mo'}


def _allows_selected_history(packet):
    if packet is None:
        return True
    info = packet.get('history', {})
    # Only an audited absence may use the selected cache/provider path.
    # An invalid or empty pinned series must keep its original result.
    return info.get('state') == 'unavailable' and info.get('reason') in {
        'saved_us_symbol_unavailable', 'saved_kr_symbol_unavailable'}


def timeframe(value):
    if value not in FRAMES:
        raise ValueError('invalid_timeframe')
    return value


def aggregate(bars, frame):
    timeframe(frame)
    if frame == '1d':
        return [dict(b) for b in bars]
    groups = {}
    for b in bars:
        day = date.fromisoformat(b['date'])
        label = (day - timedelta(days=day.weekday())).isoformat() if frame == '1w' else day.replace(day=1).isoformat()
        groups.setdefault(label, []).append(b)
    return [dict(date=label, open=rows[0]['open'], high=max(b['high'] for b in rows),
                 low=min(b['low'] for b in rows), close=rows[-1]['close'],
                 volume=sum(b['volume'] for b in rows) if all(b.get('volume') is not None for b in rows) else None,
                 first_session=rows[0]['date'], last_session=rows[-1]['date'], sessions=len(rows))
            for label, rows in groups.items()]


def validate_bars(bars, end):
    if not bars or [b['date'] for b in bars] != sorted({b['date'] for b in bars}):
        raise ValueError('invalid_history_dates')
    for b in bars:
        date.fromisoformat(b['date'])
        if not START <= b['date'] <= end:
            raise ValueError('history_outside_bounds')
        if any(type(b[k]) not in (int, float) or not math.isfinite(b[k]) or b[k] < 0 for k in ('open','high','low','close','volume')):
            raise ValueError('invalid_history_value')
        if not 0 < b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
            raise ValueError('invalid_history_ohlc')


class HistoryStore:
    def __init__(self, path, client_factory=None):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.client_factory = client_factory
        self.client = None
        self.saved = SavedMarketHistories(self.path.parent)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('CREATE TABLE IF NOT EXISTS history (identity TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL)')
        return db

    def identity(self, stock):
        return hashlib.sha256(json.dumps(stock, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def cached(self, stock):
        with closing(self.connect()) as db, db:
            row = db.execute('SELECT payload,sha256 FROM history WHERE identity=?', (self.identity(stock),)).fetchone()
        if not row:
            return None
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError('history_cache_hash_mismatch')
        return json.loads(row[0])

    def view(self, stock, frame='1d'):
        timeframe(frame)
        from market_scope import match
        exclusion = match(stock)
        if exclusion:
            return {**stock, 'bars':[], 'timeframe':frame, 'history':{
                'state':'excluded', 'reason':exclusion['reason'], 'approved_on':exclusion['approved_on']}}
        try:
            value = self.saved.packet(stock)
            if _allows_selected_history(value):
                value = self.cached(stock) or value
        except (ValueError, sqlite3.Error, OSError):
            value = {'history': {'state':'failed','reason':'history_cache_unavailable'}}
        value = value or {'history': {'state':'not_requested','reason':'history_not_requested'}}
        source = value.get('series', stock)
        bars = source.get('bars') or []
        info = dict(requested_start=START, requested_end=(stock.get('bars') or [{}])[-1].get('date'),
                    first=bars[0]['date'] if bars else None, last=bars[-1]['date'] if bars else None,
                    daily_count=len(bars), collected_at=source.get('collected_at'))
        info.update(value['history'])
        return {**source, 'name':stock.get('name'), 'bars':aggregate(bars, frame), 'history':info, 'timeframe':frame}

    def ensure(self, stock, retry=False):
        from market_scope import match
        exclusion = match(stock)
        if exclusion:
            return {**self.view(stock), 'history': {'state':'excluded','reason':exclusion['reason'], 'approved_on':exclusion['approved_on']}}
        if not _allows_selected_history(self.saved.packet(stock)):
            return self.view(stock)
        # Cross-process file lock prevents two dashboard launchers fetching the same cache.
        from invest_agent.trading.store import instance_lock, BusyError
        try:
            with self.lock, instance_lock(self.path.parent / 'history-locks' / self.identity(stock)):
                cached = self.cached(stock)
                if cached and not (retry and cached['history']['state'] == 'failed'):
                    return self.view(stock)
                if retry:
                    self.client = None
                end = (stock.get('bars') or [{}])[-1].get('date')
                try:
                    if not end or end < START:
                        raise ValueError('saved_cutoff_missing')
                    if self.client is None:
                        self.client = (self.client_factory or TossMarketClient)()
                    # Start at the saved cutoff, never request a live/incomplete candle.
                    before = datetime.combine(date.fromisoformat(end) + timedelta(days=1), datetime.min.time(), ZoneInfo('Asia/Seoul' if stock['market']=='KR' else 'America/New_York')).isoformat()
                    def get(path, params):
                        return self.client(path, {'before':before, **params})
                    series = daily_series(get, stock['symbol'], stock['market'], [START, end], max_pages=40)
                    validate_bars(series['bars'], end)
                    if series['bars'][-1]['date'] != end:
                        raise ValueError('saved_cutoff_not_received')
                    prior = {b['date']: b for b in stock.get('bars', [])}
                    differs = (stock.get('price_basis') not in (None, series['price_basis']) or any(
                        b['date'] in prior and any(not math.isclose(b[k], prior[b['date']][k], rel_tol=1e-8, abs_tol=1e-8)
                                                  for k in ('open','high','low','close')) for b in series['bars']))
                    series['collected_at'] = datetime.now(timezone.utc).isoformat()
                    short = series['bars'][0]['date'] > '2016-01-08'
                    packet = {'series':series, 'history':{'state':'partial' if short else 'complete',
                              'reason':'provider_available_history_only' if short else 'requested_history_received',
                              'screening_price_difference':differs}}
                except (Exception, SystemExit) as exc:
                    # Never expose arbitrary provider errors or authentication content.
                    known = {'saved_cutoff_missing','saved_cutoff_not_received','invalid_history_ohlc','invalid_history_value','invalid_history_dates','history_outside_bounds'}
                    reason = str(exc) if str(exc) in known or isinstance(exc, MarketReadError) else 'history_collection_failed'
                    packet = {'history':{'state':'failed','reason':reason, 'attempted_at':datetime.now(timezone.utc).isoformat()}}
                body = json.dumps(packet, ensure_ascii=False, allow_nan=False)
                with closing(self.connect()) as db, db:
                    db.execute('INSERT OR REPLACE INTO history VALUES (?,?,?)', (self.identity(stock),body,hashlib.sha256(body.encode()).hexdigest()))
                return self.view(stock)
        except BusyError:
            value = self.view(stock)
            value['history'].update(state='pending',reason='another_dashboard_loading')
            return value

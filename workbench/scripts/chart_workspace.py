"""Local user chart workspaces; independent of immutable market/broker data."""
from contextlib import closing
import hashlib
import json
import math
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

COLORS = ['#cc6963', '#cda45d', '#b4cf63', '#59c78c', '#5a9fca', '#6668cd', '#c181ce']
DEFAULT_PERIODS = [5, 10, 20, 50, 100, 150, 200]
EXTENDABLE_KINDS = ('trend', 'channel', 'box', 'riskreward')
KINDS = {'trend': 2, 'channel': 3, 'horizontal': 1, 'box': 2, 'measure': 2, 'riskreward': 3}


class Conflict(Exception):
    pass


def defaults():
    return {'schema': 1, 'drawings': [], 'averages': [
        {'period': n, 'color': c, 'width': w, 'visible': True}
        for n, c, w in zip(DEFAULT_PERIODS, COLORS, [1, 1, 1, 2, 3, 3, 3])]}


def validate(state, drawing_limit=100):
    if not isinstance(state, dict) or set(state) != {'schema', 'drawings', 'averages'} or type(state['schema']) is not int or state['schema'] != 1:
        raise ValueError('invalid_schema')
    drawings, averages = state['drawings'], state['averages']
    if not isinstance(drawings, list) or len(drawings) > drawing_limit or not isinstance(averages, list) or len(averages) > 7:
        raise ValueError('item_limit')
    ids, periods = set(), set()
    for average in averages:
        if not isinstance(average, dict) or set(average) != {'period', 'color', 'width', 'visible'}:
            raise ValueError('invalid_average')
        n = average['period']
        if type(n) is not int or not 1 <= n <= 500 or n in periods or type(average['visible']) is not bool:
            raise ValueError('invalid_period')
        periods.add(n)
        style(average)
    for drawing in drawings:
        required = {'id', 'kind', 'points', 'color', 'width', 'extend'}
        if not isinstance(drawing, dict) or not required <= set(drawing) or set(drawing) - required - {'extension'}:
            raise ValueError('invalid_drawing')
        ident = drawing['id']
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident) or ident in ids:
            raise ValueError('invalid_id')
        ids.add(ident)
        kind = drawing['kind']
        if kind not in KINDS or type(drawing['extend']) is not bool:
            raise ValueError('invalid_kind')
        points = drawing['points']
        if not isinstance(points, list) or len(points) != KINDS[kind]:
            raise ValueError('invalid_points')
        for point in points:
            if not isinstance(point, dict) or set(point) != {'time', 'price'}:
                raise ValueError('invalid_point')
            if not isinstance(point['time'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', point['time']):
                raise ValueError('invalid_date')
            date.fromisoformat(point['time'])
            if type(point['price']) not in (int, float) or not math.isfinite(point['price']) or not 0 < point['price'] <= 1e15:
                raise ValueError('invalid_price')
        if kind in ('trend', 'channel') and points[0]['time'] == points[1]['time']:
            raise ValueError('distinct_dates_required')
        if 'extension' in drawing:
            extension = drawing['extension']
            if kind not in EXTENDABLE_KINDS or not isinstance(extension, dict) or set(extension) != {'left', 'right'}:
                raise ValueError('invalid_extension')
            for value in extension.values():
                if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError('invalid_extension_date')
                date.fromisoformat(value)
            times = [p['time'] for p in (points if kind == 'riskreward' else points[:2])]
            left, right = min(times), max(times)
            if extension['left'] > left or extension['right'] < right:
                raise ValueError('extension_must_contain_anchors')
        if kind == 'riskreward':
            entry, stop, target = (p['price'] for p in points)
            if not (stop < entry < target or target < entry < stop):
                raise ValueError('riskreward_levels_must_straddle_entry')
            if len({p['time'] for p in points}) < 2:
                raise ValueError('riskreward_date_span_required')
        style(drawing)
    return state


def style(item):
    if not isinstance(item['color'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', item['color']):
        raise ValueError('invalid_color')
    if type(item['width']) is not int or not 1 <= item['width'] <= 4:
        raise ValueError('invalid_width')


class WorkspaceStore:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('CREATE TABLE IF NOT EXISTS chart_workspaces (identity TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL)')
        return db

    def read(self, identity):
        with closing(self.connect()) as db, db:
            row = db.execute('SELECT revision,state,updated_at FROM chart_workspaces WHERE identity=?', (identity,)).fetchone()
        return {'revision': row[0], 'state': validate(json.loads(row[1])), 'updated_at': row[2]} if row else {
            'revision': 0, 'state': defaults(), 'updated_at': None}

    def save(self, identity, revision, state):
        if type(revision) is not int or revision < 0:
            raise ValueError('invalid_revision')
        validate(state)
        body = json.dumps(state, ensure_ascii=False, allow_nan=False)
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM chart_workspaces WHERE identity=?', (identity,)).fetchone()
            if (row[0] if row else 0) != revision:
                raise Conflict('workspace_changed_elsewhere')
            db.execute('INSERT INTO chart_workspaces VALUES (?,?,?,?) ON CONFLICT(identity) DO UPDATE SET revision=excluded.revision,state=excluded.state,updated_at=excluded.updated_at',
                       (identity, revision + 1, body, now))
        return {'revision': revision + 1, 'updated_at': now}


    def shared(self, identity, frame='1d', *, revision=None, state=None):
        """Atomically migrate legacy rows once; drawings and revision are symbol-wide."""
        if frame not in ('1d', '1w', '1mo'):
            raise ValueError('invalid_timeframe')
        if state is not None:
            if type(revision) is not int or revision < 0:
                raise ValueError('invalid_revision')
            validate(state, drawing_limit=300)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS shared_chart_workspaces (identity TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS chart_workspace_versions (identity TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT, PRIMARY KEY(identity, revision))')
            row = db.execute('SELECT revision,payload,updated_at FROM shared_chart_workspaces WHERE identity=?', (identity,)).fetchone()
            if row:
                current, payload, updated = row[0], json.loads(row[1]), row[2]
            else:
                payload = {'drawings': [], 'averages': {}}
                seen = {}; current = 0; updated = None
                for period in ('1d', '1w', '1mo'):
                    key = identity if period == '1d' else identity + '|' + period
                    old = db.execute('SELECT revision,state,updated_at FROM chart_workspaces WHERE identity=?', (key,)).fetchone()
                    legacy = validate(json.loads(old[1])) if old else defaults()
                    payload['averages'][period] = legacy['averages']
                    if old:
                        current = max(current, old[0]); updated = max(updated or '', old[2])
                    for drawing in legacy['drawings']:
                        ident = drawing['id']
                        if ident in seen:
                            if seen[ident] == drawing:
                                continue
                            # Different legacy drawings with the same ID must both survive.
                            salt = 0
                            while ident in seen:
                                ident = hashlib.sha256((identity + period + drawing['id'] + str(salt)).encode()).hexdigest()
                                salt += 1
                            drawing = dict(drawing, id=ident)
                        seen[ident] = drawing; payload['drawings'].append(drawing)
                # Legacy rows remain intact for recovery; future reads use this snapshot.
            if (not isinstance(payload, dict) or set(payload) != {'drawings', 'averages'}
                    or not isinstance(payload['averages'], dict)
                    or set(payload['averages']) != {'1d', '1w', '1mo'}):
                raise ValueError('invalid_shared_workspace')
            for period in ('1d', '1w', '1mo'):
                validate({'schema': 1, 'drawings': payload['drawings'], 'averages': payload['averages'][period]}, drawing_limit=300)
            db.execute('CREATE TABLE IF NOT EXISTS chart_workspace_migrations (identity TEXT PRIMARY KEY, version INTEGER NOT NULL)')
            migrated = db.execute('SELECT version FROM chart_workspace_migrations WHERE identity=?', (identity,)).fetchone()
            upgraded = False
            if state is None and not migrated:
                for period in ('1d', '1w', '1mo'):
                    averages = payload['averages'][period]
                    legacy_defaults = [{'period': n, 'color': c, 'width': 2, 'visible': True}
                                       for n, c in zip([20,50,150,200], ['#dd9500','#2271b2','#8254a8','#687780'])]
                    six = len(averages) == 6 and {a['period'] for a in averages} == set(DEFAULT_PERIODS[:-1])
                    if six or averages == legacy_defaults:
                        if not upgraded:
                            db.execute('INSERT OR IGNORE INTO chart_workspace_versions VALUES (?,?,?,?)',
                                       (identity, current, json.dumps(payload, ensure_ascii=False, allow_nan=False), updated))
                        if six:
                            averages.append(defaults()['averages'][-1])
                        else:
                            by_period = {a['period']: a for a in averages}
                            payload['averages'][period] = [by_period.get(a['period'], a) for a in defaults()['averages']]
                        upgraded = True
                db.execute('INSERT INTO chart_workspace_migrations VALUES (?,1)', (identity,))
                if upgraded:
                    current += 1; updated = datetime.now(timezone.utc).isoformat()
                    db.execute('INSERT INTO chart_workspace_versions VALUES (?,?,?,?)',
                               (identity, current, json.dumps(payload, ensure_ascii=False, allow_nan=False), updated))
            if state is not None:
                if current != revision:
                    raise Conflict('workspace_changed_elsewhere')
                db.execute('INSERT OR IGNORE INTO chart_workspace_versions VALUES (?,?,?,?)',
                           (identity, current, json.dumps(payload, ensure_ascii=False, allow_nan=False), updated))
                payload['drawings'] = state['drawings']
                payload['averages'][frame] = state['averages']
                current += 1; updated = datetime.now(timezone.utc).isoformat()
                db.execute('INSERT INTO chart_workspace_versions VALUES (?,?,?,?)',
                           (identity, current, json.dumps(payload, ensure_ascii=False, allow_nan=False), updated))
            if row is None or state is not None or upgraded:
                db.execute('INSERT INTO shared_chart_workspaces VALUES (?,?,?,?) ON CONFLICT(identity) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at',
                           (identity, current, json.dumps(payload, ensure_ascii=False, allow_nan=False), updated))
        return {'revision': current, 'updated_at': updated, 'drawing_limit': 300, 'drawing_kinds': list(KINDS), 'drawing_extensions': 'bounded-v1', 'extension_kinds': list(EXTENDABLE_KINDS), 'average_limit': 7,
                'state': {'schema': 1, 'drawings': payload['drawings'], 'averages': payload['averages'][frame]}}


def moving_averages(stock, periods):
    from invest_agent.trading.momentum_entry import sma
    if len(periods) > 7 or any(type(n) is not int or not 1 <= n <= 500 for n in periods):
        raise ValueError('invalid_period')
    bars = stock.get('bars', [])
    result = {}
    for n in dict.fromkeys(periods):
        data = []
        for i, bar in enumerate(bars):
            window = bars[max(0, i + 1 - n):i + 1]
            valid = len(window) == n and all(type(b.get('close')) in (int, float) and math.isfinite(b['close']) for b in window)
            data.append({'time': bar['date'], **({'value': sma(bars, n, i + 1)} if valid else {})})
        result[str(n)] = data
    return result

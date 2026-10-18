"""Completed-close alerts for adopted trade cycles; never records a trade."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def active(cycle):
    return bool(cycle.get('adopted')) and cycle.get('state', {}).get('phase') != 'closed'


def basis(cycle):
    # Saving an unadopted draft must not re-arm or invalidate the live plan.
    evidence = dict(version=cycle['adopted']['version'], combination_id=cycle['adopted'].get('combination_id'),
                    fills=cycle.get('fills', []))
    return hashlib.sha256(packed(evidence).encode()).hexdigest()


def cycle_rows(path):
    """No migration or creation when the feature has no saved cycles yet."""
    path = Path(path)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'trade_cycles' not in tables:
        return []
    from trade_cycle import TradeCycleStore
    return TradeCycleStore(path).list()['cycles']


def number(value):
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError('invalid_positive_price')
    return result


def account_ready(bundle, alias, now):
    try:
        stamp = datetime.fromisoformat(bundle['as_of'])
        if stamp.tzinfo is None or not 0 <= (now-stamp).total_seconds() <= 86400:
            return False
        rows = [a for a in bundle['accounts'] if not alias or a['alias'] == alias]
        return bundle.get('query_state') not in ('failed', 'missing') and bool(rows) and all(a.get('holdings_complete') for a in rows)
    except (KeyError, TypeError, ValueError):
        return False


def action_snapshot(step):
    """Only reviewable planned values enter the delivery body."""
    keys = ('kind', 'step', 'ratio', 'confirmation_price', 'assumed_fill_price', 'condition',
            'trigger', 'planned_quantity', 'quantity_basis')
    return {k: deepcopy(step.get(k)) for k in keys}


def matched(comparator, close, level, previous=None):
    if comparator == 'gt': return close > level
    if comparator == 'gte': return close >= level
    if comparator == 'lt': return close < level
    if comparator == 'lte': return close <= level
    if comparator == 'cross_above': return previous is not None and previous <= level < close
    if comparator == 'cross_below': return previous is not None and previous >= level > close
    raise ValueError('unsupported_cycle_comparator')


class TradeCycleAlertStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS trade_cycle_alert_states(cycle_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trade_cycle_alert_events(id TEXT PRIMARY KEY,payload TEXT NOT NULL);''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def read(self):
        with closing(self.connect()) as db:
            return dict(states=[json.loads(r[0]) for r in db.execute('SELECT payload FROM trade_cycle_alert_states')],
                        events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM trade_cycle_alert_events ORDER BY rowid DESC')])

    def update(self, cycles, bundle, stocks, targets, now=None, sessions=None):
        now = now or datetime.now(timezone.utc)
        if sessions is None:
            from invest_agent.trading.market_provider import completed_sessions
            sessions = {market: completed_sessions(market, now) for market in targets}
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            prior = {r[0]: json.loads(r[1]) for r in db.execute('SELECT cycle_id,payload FROM trade_cycle_alert_states')}
            for cycle in cycles:
                if not active(cycle): continue
                cid = cycle['cycle_id']; old = prior.get(cid, {})
                same = old.get('basis') == basis(cycle)
                state = deepcopy(old) if same else {}
                state.update(cycle_id=cid, basis=basis(cycle), stock_key=cycle['stock_key'],
                             checked_at=now.isoformat(), status='deferred', issue=None,
                             flags=deepcopy(old.get('flags', {})) if same else {})
                try:
                    plan = cycle['adopted']['plan']; source = cycle['adopted'].get('source') or {}
                    ident = source.get('identity') or {}
                    market, symbol = cycle['stock_key'].split(':', 1)
                    stock = stocks.get(cycle['stock_key'])
                    if not ident.get('price_basis') or not ident.get('currency'):
                        raise ValueError('cycle_identity_unconfirmed')
                    if (not stock or stock.get('currency') != ident['currency'] or stock.get('price_basis') != ident['price_basis']
                            or ident.get('market', market) != market or ident.get('symbol', symbol) != symbol
                            or (ident.get('stable_id') and (stock.get('isin_code') or stock.get('isin')) != ident['stable_id'])
                            or (ident.get('exchange') and stock.get('exchange') != ident['exchange'])):
                        raise ValueError('market_identity_or_basis_changed')
                    if not account_ready(bundle, plan.get('account_alias'), now):
                        raise ValueError('account_stale_or_incomplete')
                    bars = stock.get('bars') or []
                    if not bars or bars[-1]['date'] != targets.get(market):
                        raise ValueError('completed_price_stale_or_missing')
                    dates = [b['date'] for b in bars]
                    expected = [d for d in sessions.get(market, []) if dates[0] <= d <= dates[-1]]
                    if not expected or dates != expected:
                        raise ValueError('price_sessions_incomplete')
                    if same and old.get('date'):
                        prior_bar = next((b for b in bars if b['date'] == old['date']), None)
                        if not prior_bar or old['date'] > dates[-1] or number(prior_bar['close']) != number(old['close']):
                            raise ValueError('processed_price_history_changed')
                    bar = bars[-1]; close = number(bar['close'])
                    previous = number(bars[-2]['close']) if len(bars) > 1 else None
                    pending = []; stop_active = False
                    steps = cycle.get('next_steps', [])
                    state.update(date=bar['date'], close=str(close), account_alias=plan.get('account_alias'),
                                 account_as_of=bundle['as_of'], phase=cycle['state']['phase'],
                                 actions=[action_snapshot(s) for s in steps], currency=ident['currency'],
                                 name=stock.get('name') or symbol, test_record=bool(source.get('test_record')))
                    for step in steps:
                        trigger = step.get('trigger') or {}
                        if trigger.get('kind') != 'price' or trigger.get('evaluation') != 'daily_close': continue
                        # A sloped drawing is not a fixed price trigger. Its evaluated geometry
                        # must be supplied by a separate, verified drawing monitor.
                        if step.get('drawing_ref') or trigger.get('drawing_ref'): continue
                        if not step.get('planned_quantity'): continue
                        comparator = trigger.get('comparator', trigger.get('operator'))
                        level = number(step['confirmation_price'])
                        key = str(step['kind']) + ':' + str(step.get('step', 'stop'))
                        previous_flag = state['flags'].get(key, False)
                        if previous_flag and old.get('date'):
                            for missed in (b for b in bars if old['date'] < b['date'] < bar['date']):
                                value = number(missed['close'])
                                recovered = not matched(comparator, value, level) if comparator in ('gt','gte','lt','lte') else (value <= level if comparator == 'cross_above' else value >= level)
                                if recovered: previous_flag = False; break
                        hit = matched(comparator, close, level, previous)
                        on_side = matched(comparator, close, level) if comparator in ('gt','gte','lt','lte') else (close > level if comparator == 'cross_above' else close < level)
                        state['flags'][key] = on_side
                        if step['kind'] == 'stop' and on_side:
                            stop_active = True
                        if hit and not previous_flag:
                            event_id = 'tc_alert_' + hashlib.sha256(f'{cid}|{basis(cycle)}|{bar["date"]}|{key}'.encode()).hexdigest()
                            pending.append(dict(id=event_id, kind='trade_cycle', cycle_id=cid, cycle_basis=basis(cycle),
                                stock_key=cycle['stock_key'], market=market, symbol=symbol, name=state['name'],
                                currency=ident['currency'], date=bar['date'], created_at=now.isoformat(), close=float(close),
                                threshold=float(level), action=action_snapshot(step), phase=state['phase'],
                                test_record=state['test_record'], case_id=source.get('analysis_key'),
                                account_as_of=bundle['as_of'], plan_version=cycle['adopted']['version']))
                    # Final-stop exception has priority over remaining installment signals.
                    if stop_active:
                        pending = [e for e in pending if e['action']['kind'] == 'stop']
                        state['actions'] = [s for s in state['actions'] if s['kind'] == 'stop']
                        # Preserve the stop latch, but allow pending installments to be
                        # reconsidered only after its invalidation condition clears.
                        state['flags'] = {key: value if key.startswith('stop:') else False
                                          for key, value in state['flags'].items()}
                    state['stop_active'] = stop_active
                    for event in pending:
                        db.execute('INSERT OR IGNORE INTO trade_cycle_alert_events VALUES(?,?)', (event['id'], packed(event)))
                    state.update(status='evaluated', issue=None)
                except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                    code = str(exc) if isinstance(exc, ValueError) and str(exc).replace('_', '').isalnum() else 'invalid_cycle_alert_input'
                    state.update(issue=code, status='deferred')
                db.execute('INSERT OR REPLACE INTO trade_cycle_alert_states VALUES(?,?)', (cid, packed(state)))
        return self.read()


def refresh_saved(root, market_run, targets, now=None, *, account_failed=False):
    """Use verified saved market/account inputs in the existing morning path."""
    from dashboard_data import account_view, load, checked
    from trailing_warnings import holding_series
    import tempfile
    root = Path(root); path = root/'runtime/chart-workspaces.sqlite3'
    cycles = [c for c in cycle_rows(path) if active(c)]
    if not cycles: return dict(states=[], events=[])
    store = TradeCycleAlertStore(path)
    run_dir = Path(market_run).resolve()
    if not run_dir.is_relative_to((root/'runtime').resolve()): raise ValueError('market_run_outside_runtime')
    manifest = load(run_dir/'manifest.json'); run = checked(run_dir/'run.json', manifest['output_hashes'].get('run.json'))
    source = Path(run['market_source'])/'snapshot.json'
    with tempfile.TemporaryDirectory(prefix='cycle-alert-account-', dir=root/'runtime') as folder:
        bundle = account_view(load(root/'config.json'), Path(folder), refresh=False, saved_dashboard_root=root/'runtime/dashboard')
    if account_failed: bundle = {**bundle, 'query_state': 'failed'}
    stocks = holding_series(source, manifest['input_hashes'].get(str(source)), {c['stock_key'] for c in cycles})
    return store.update(cycles, bundle, stocks, targets, now)


def delivery_events(root, events, targets, *, include_cycles=True, now=None):
    """Add eligible events and frozen plan context without touching cycle/fill records."""
    if not include_cycles: return events
    path = Path(root)/'runtime/chart-workspaces.sqlite3'
    if not path.exists(): return events
    with closing(sqlite3.connect(path)) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_cycle_alert_states'").fetchone()
    if not exists: return events
    cycles = {c['cycle_id']: c for c in cycle_rows(path) if active(c)}
    saved = TradeCycleAlertStore(path).read(); now = now or datetime.now(timezone.utc)
    states = {}
    for state in saved['states']:
        cycle = cycles.get(state['cycle_id'])
        try: age = (now-datetime.fromisoformat(state['account_as_of'])).total_seconds()
        except (KeyError, TypeError, ValueError): continue
        market = state['stock_key'].split(':', 1)[0]
        if (cycle and state['basis'] == basis(cycle) and state['status'] == 'evaluated'
                and state.get('date') == targets.get(market) and 0 <= age <= 86400 and not state.get('test_record')):
            states[state['cycle_id']] = state
    result = []
    for original in events:
        event = deepcopy(original)
        matches = [s for s in states.values() if s['stock_key'] == event.get('stock_key', event['market']+':'+event['symbol'])
                   and s['date'] == event['date'] and s.get('account_alias') == event.get('account')]
        # Account-less scenario watches cannot imply a specific account's cycle.
        if matches:
            contexts = []
            for state in matches:
                actions = state['actions']
                stops = []
                for action in actions:
                    trigger = action.get('trigger') or {}
                    comparator = trigger.get('comparator', trigger.get('operator'))
                    if (action['kind'] == 'stop' and trigger.get('kind') == event['kind']
                            and comparator in ('gt','gte','lt','lte')
                            and event.get('close') is not None and event.get('threshold') is not None
                            and matched(comparator, number(event['close']), number(event['threshold']))):
                        stops.append(action)
                contexts.append(dict(cycle_id=state['cycle_id'], phase=state['phase'], actions=stops or actions))
            event['cycle_context'] = contexts
        result.append(event)
    if include_cycles:
        for event in saved['events']:
            state = states.get(event['cycle_id'])
            if state and event['cycle_basis'] == state['basis'] and event['date'] == state['date'] and not event.get('test_record'):
                result.append(event)
    return result


def action_text(action, currency):
    from invest_agent.trading.report_format import money
    kind = action.get('kind'); step = action.get('step')
    label = '최종 손절 예외' if kind == 'stop' else ('매수' if kind == 'buy' else '매도') + (f' {step}차' if step else '')
    ratio = action.get('ratio'); quantity = action.get('planned_quantity')
    size = []
    if ratio is not None:
        ratio_text = format(Decimal(str(ratio)), 'f')
        size.append((ratio_text.rstrip('0').rstrip('.') if '.' in ratio_text else ratio_text) + '%')
    if quantity is not None: size.append(f'예정 {quantity}주')
    text = label + (' · ' + ' / '.join(size) if size else '')
    if action.get('confirmation_price') is not None:
        text += ' · 확인가격 ' + money(action['confirmation_price'], currency) + ' ' + currency
    trigger = action.get('trigger') or {}
    labels = {'gt':'초과', 'gte':'이상', 'lt':'미만', 'lte':'이하', 'cross_above':'상향 돌파', 'cross_below':'하향 이탈'}
    if trigger.get('kind') == 'price': text += ' ' + labels.get(trigger.get('comparator', trigger.get('operator')), '판정 미정')
    elif trigger.get('kind') in ('trailing', 'ma5', 'ma10'):
        text += ' · ' + {'trailing':'트레일링선', 'ma5':'5일선', 'ma10':'10일선'}[trigger['kind']]
        text += ' ' + labels.get(trigger.get('comparator', trigger.get('operator')), '판정 미정')
    else: text += ' · 조건은 사용자 판단 필요'
    return text


def context_text(event):
    lines = []
    for context in event.get('cycle_context', []):
        lines.append('승인한 매매 사이클 · 다음 계획 (' + {'buying':'매수 진행', 'holding':'보유', 'selling':'매도 진행'}.get(context['phase'], context['phase']) + ')')
        for action in context['actions']:
            lines.append(action_text(action, event['currency']))
        lines.append('접근 경고와 계획 조건 충족은 다를 수 있습니다. 계획 비율·예정 수량이며 실제 체결은 별도 기록합니다.')
    return '\n'.join(lines)


def message(event):
    from invest_agent.trading.report_format import money
    return '\n'.join(['🔔 투자에이전트 | 승인 매매 사이클 조건 확인',
        f"종목: {event.get('name') or event['symbol']} ({event['symbol']} · {event['market']})",
        f"기준: {event['date']} 최신 완료 거래일 종가",
        '종가: ' + money(event['close'], event['currency']) + ' ' + event['currency'],
        '다음 계획: ' + action_text(event['action'], event['currency']),
        f"승인 계획 v{event['plan_version']} · 조건 충족은 실제 체결이 아닙니다.",
        '매매 사이클: ' + (event.get('detail_url') or 'PC 투자 대시보드 → 종목별 매매 사이클'),
        '완료 종가 기준 · 실제 거래 기록 후 단계 변경 · 자동 주문 없음'])

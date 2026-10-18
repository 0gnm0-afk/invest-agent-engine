"""Saved scenario references and completed-close monitoring; never places orders."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from uuid import uuid4

from chart_workspace import WorkspaceStore, Conflict
from analysis_geometry import geometry
from chart_history import aggregate
from trailing_warnings import indicators, packed, stamp, equity


def utc():
    return datetime.now(timezone.utc).isoformat()


def tolerance(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10:
        raise ValueError('invalid_atr_tolerance')
    return float(value)


def levels(drawing, chart, day, boundary, purpose):
    row = geometry({'state': {'drawings': [drawing]}}, chart, {'evaluate_at': day})['drawings'][0]
    c = row['calculations']; kind = drawing['kind']
    if kind == 'channel':
        pair = (c.get('line_price'), c.get('other_line_price'))
        if None in pair: raise ValueError('drawing_projection_unavailable')
        values = {'lower': min(pair), 'upper': max(pair)}
    elif kind == 'box': values = {'lower': c['low'], 'upper': c['high']}
    elif kind == 'riskreward': values = {k: c[k] for k in ('entry', 'stop', 'target')}
    elif kind == 'horizontal': values = {'line': c['level']}
    elif kind == 'trend': values = {'line': c.get('line_price')}
    else: raise ValueError('unsupported_monitor_drawing')
    if boundary == 'both':
        selected = ('entry',) if kind == 'riskreward' and purpose == 'buy' else ('stop', 'target') if kind == 'riskreward' else tuple(values)
    else: selected = (boundary,)
    if any(k not in values for k in selected): raise ValueError('invalid_monitor_boundary')
    result = [{'name': k, 'value': values[k]} for k in selected]
    if any(v['value'] is None or not math.isfinite(v['value']) or v['value'] <= 0 for v in result):
        raise ValueError('drawing_projection_unavailable')
    return result


def line_state(close, line, width, prior_close=None, prior_line=None, side=None):
    delta = close-line
    if abs(delta) <= max(1, abs(line))*1e-10: return 'reached'
    if side == 'upper' and delta > 0: return 'breakout'
    if side == 'lower' and delta < 0: return 'breakdown'
    if side is None and prior_close is not None and prior_line is not None:
        if prior_close <= prior_line and delta > 0: return 'breakout'
        if prior_close >= prior_line and delta < 0: return 'breakdown'
    if abs(delta) <= width: return 'approach'
    return 'clear'


def account_status(bundle, monitor, now):
    try:
        if bundle.get('query_state') != 'received' or not 0 <= (now-stamp(bundle['as_of'])).total_seconds() <= 86400:
            return False, False
        accounts = bundle['accounts']
        if not accounts or any(not a.get('holdings_complete') for a in accounts): return False, False
        if monitor.get('account_alias'):
            accounts = [a for a in accounts if a.get('alias')==monitor['account_alias']]
            if not accounts: return False, False
        held = False
        for account in accounts:
            for p in account.get('positions', []):
                if not equity(p) or p.get('market') != monitor['identity']['market'] or (p.get('quote_symbol') or p.get('symbol')) != monitor['identity']['symbol']: continue
                if p.get('data_unavailable_reason'): return False, False
                stable = p.get('isin_code') or p.get('isin')
                if stable and monitor['identity'].get('stable_id') and stable != monitor['identity']['stable_id']: return False, False
                if p.get('currency') and p['currency'] != monitor['identity']['currency']: return False, False
                qty = float(p['quantity'])
                if not math.isfinite(qty) or qty < 0: return False, False
                if qty > 0: held = True
        return True, held
    except (KeyError, TypeError, ValueError): return False, False


class ScenarioStore:
    """Additional namespaced tables in the existing chart/analysis SQLite database."""
    def __init__(self, path):
        self.path = Path(path)
        self.workspace = WorkspaceStore(self.path)
        with closing(self.connect()) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS scenario_monitors(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scenario_monitor_history(id TEXT PRIMARY KEY,monitor_id TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scenario_monitor_events(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scenario_monitor_evaluations(monitor_id TEXT NOT NULL,revision INTEGER NOT NULL,day TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(monitor_id,revision,day));''')

    def connect(self): return self.workspace.connect()

    def read(self, stock_key=None, *, now=None, targets=None):
        with closing(self.connect()) as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM scenario_monitors ORDER BY rowid')]
            events = [json.loads(r[0]) for r in db.execute('SELECT payload FROM scenario_monitor_events ORDER BY rowid DESC')]
            evaluations = [json.loads(r[0]) for r in db.execute('SELECT payload FROM scenario_monitor_evaluations ORDER BY day DESC,revision DESC')]
        if stock_key is not None:
            rows = [r for r in rows if r['stock_key'] == stock_key]
            events = [r for r in events if r['stock_key'] == stock_key]
        for m in rows:
            m['name'] = m['identity'].get('name') or m['identity']['symbol']
            m['currency'] = m['identity']['currency']
            m['evaluations'] = [e for e in evaluations if e['monitor_id']==m['id']][:126]
            m['history'] = [dict(id=h['id'], reason=h['reason'], recorded_at=h['recorded_at'],
                                 revision=h['monitor']['revision'], workspace_revision=h['monitor']['workspace_revision'],
                                 drawing=h['monitor']['drawing']) for h in self.history(m['id'])]
            if m['active'] and m.get('latest'):
                if targets and m['latest']['date']!=targets.get(m['identity']['market']):
                    m.update(issue='completed_price_stale_or_missing',status='deferred')
                if now and m['purpose']=='holding':
                    try: fresh=0 <= (now-stamp(m['account_as_of'])).total_seconds() <= 86400
                    except (KeyError,TypeError,ValueError): fresh=False
                    if not fresh: m.update(issue='account_stale_or_incomplete',status='deferred')
        return dict(schema=1, monitors=rows, events=events)

    def history(self, monitor_id):
        with closing(self.connect()) as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM scenario_monitor_history WHERE monitor_id=? ORDER BY rowid', (monitor_id,))]

    def _save(self, db, m, reason=None):
        db.execute('INSERT OR REPLACE INTO scenario_monitors VALUES(?,?)', (m['id'], packed(m)))
        if reason:
            history = dict(id='mh_'+uuid4().hex, monitor_id=m['id'], reason=reason, recorded_at=utc(), monitor=deepcopy(m))
            db.execute('INSERT INTO scenario_monitor_history VALUES(?,?,?)', (history['id'], m['id'], packed(history)))

    def register(self, *, stock_key, identity, case_id, plan_record_id, plan_id, plan_version,
                 drawing_id, workspace_revision, timeframe='1d', purpose='holding', boundary='both',
                 tolerance_atr=.5, confirmed_response=None, confirmed_responses=None, account_alias=None, detail_url=None, chart=None, test_record=False):
        if purpose not in ('holding', 'buy') or timeframe not in ('1d', '1w', '1mo'): raise ValueError('invalid_monitor_purpose_or_frame')
        if type(test_record) is not bool: raise ValueError('invalid_test_record')
        if boundary is None: boundary = 'both'
        if boundary not in ('both','lower','upper','entry','stop','target','line'): raise ValueError('invalid_monitor_boundary')
        if not isinstance(identity, dict) or identity.get('currency') not in ('KRW','USD') or not identity.get('stable_id') or not identity.get('price_basis'):
            raise ValueError('monitor_identity_unconfirmed')
        if stock_key != identity.get('market','')+':'+identity.get('symbol',''): raise ValueError('monitor_identity_mismatch')
        if confirmed_response is not None and (not isinstance(confirmed_response,str) or len(confirmed_response)>4000): raise ValueError('invalid_confirmed_response')
        if account_alias is not None and (not isinstance(account_alias,str) or len(account_alias)>200): raise ValueError('invalid_account_alias')
        if confirmed_responses is not None and (not isinstance(confirmed_responses,dict) or set(confirmed_responses)-{'lower','upper','line','entry','stop','target','zone'} or any(not isinstance(v,str) or len(v)>4000 for v in confirmed_responses.values())):
            raise ValueError('invalid_confirmed_responses')
        tolerance_atr = tolerance(tolerance_atr)
        self.workspace.shared(stock_key, timeframe)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,payload FROM shared_chart_workspaces WHERE identity=?',(stock_key,)).fetchone()
            if row[0] != workspace_revision: raise Conflict('workspace_changed_elsewhere')
            drawing = next((d for d in json.loads(row[1])['drawings'] if d['id']==drawing_id), None)
            if not drawing: raise ValueError('saved_drawing_required')
            if drawing['kind'] not in ('box','channel','riskreward','horizontal','trend'): raise ValueError('unsupported_monitor_drawing')
            allowed = {'box':('both','lower','upper'), 'channel':('both','lower','upper'), 'riskreward':('both','entry','stop','target'), 'horizontal':('both','line'), 'trend':('both','line')}
            if boundary not in allowed[drawing['kind']]: raise ValueError('invalid_monitor_boundary')
            existing = [json.loads(r[0]) for r in db.execute('SELECT payload FROM scenario_monitors')]
            if any(m['active'] and (m['stock_key'],m['purpose'],m['plan_record_id'],m['drawing_id'])==(stock_key,purpose,plan_record_id,drawing_id) for m in existing):
                raise Conflict('monitor_already_registered')
            m = dict(id='sm_'+uuid4().hex, revision=1, stock_key=stock_key, identity=deepcopy(identity), case_id=case_id,
                     plan_record_id=plan_record_id, plan_id=plan_id, plan_version=plan_version,
                     drawing_id=drawing_id, workspace_revision=workspace_revision, drawing=deepcopy(drawing),
                     timeframe=timeframe, purpose=purpose, boundary=boundary, tolerance_atr=tolerance_atr, account_alias=account_alias,
                     confirmed_response=confirmed_response, confirmed_responses=deepcopy(confirmed_responses), detail_url=detail_url, test_record=test_record,
                     geometry_bars=[{k:b[k] for k in ('date','last_session') if k in b} for b in (chart or {}).get('bars',[])],
                     active=True, status='pending', issue=None, created_at=utc(), updated_at=utc(), latest=None,
                     conversion=None, event_states={})
            self._save(db,m,'registered')
        return m

    def sync_workspace(self, stock_key):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,payload FROM shared_chart_workspaces WHERE identity=?',(stock_key,)).fetchone()
            if not row: return
            versions = [(row[0],row[1])]
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chart_workspace_versions'").fetchone():
                versions = db.execute('SELECT revision,payload FROM chart_workspace_versions WHERE identity=? ORDER BY revision',(stock_key,)).fetchall()
                if not versions or versions[-1][0] != row[0]: versions.append((row[0],row[1]))
            for payload, in db.execute('SELECT payload FROM scenario_monitors').fetchall():
                m = json.loads(payload)
                if m['stock_key'] != stock_key or not m['active'] or m['workspace_revision'] == row[0]: continue
                for version,body in versions:
                    if version <= m['workspace_revision']: continue
                    drawing = next((d for d in json.loads(body)['drawings'] if d['id']==m['drawing_id']),None)
                    reason = 'saved_drawing_changed' if drawing else 'saved_drawing_deleted'
                    if drawing and drawing == m['drawing'] and m.get('issue')!='saved_drawing_deleted_or_type_changed':
                        m['workspace_revision'] = version
                        self._save(db,m)
                        continue
                    if (drawing and drawing['kind']==m['drawing']['kind'] and drawing['points']==m['drawing']['points']
                            and m.get('issue')!='saved_drawing_deleted_or_type_changed'):
                        m.update(workspace_revision=version,drawing=deepcopy(drawing),updated_at=utc())
                        self._save(db,m,'drawing_style_changed')
                        continue
                    m.update(revision=m['revision']+1, workspace_revision=version, updated_at=utc(), status='pending',
                             issue=None if drawing and drawing['kind']==m['drawing']['kind'] else 'saved_drawing_deleted_or_type_changed',
                             event_states={})
                    if drawing and drawing['kind']==m['drawing']['kind']: m['drawing']=deepcopy(drawing)
                    # Previous completed-session results remain historical, never relabelled as this drawing's results.
                    if m.get('latest'): m['latest']['superseded']=True
                    self._save(db,m,reason)

    def configure(self, monitor_id, revision, *, tolerance_atr=None, active=None):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE'); m=self._get(db,monitor_id,revision)
            before=(m['tolerance_atr'],m['active'])
            if tolerance_atr is not None: m['tolerance_atr']=tolerance(tolerance_atr)
            if active is not None:
                if type(active) is not bool: raise ValueError('invalid_active')
                m['active']=active
            if before==(m['tolerance_atr'],m['active']): return m
            m.update(revision=m['revision']+1,updated_at=utc(),status='pending' if m['active'] else 'paused',event_states={})
            if m.get('latest'): m['latest']['superseded']=True
            self._save(db,m,'settings_changed')
        return m

    def _get(self, db, monitor_id, revision):
        row=db.execute('SELECT payload FROM scenario_monitors WHERE id=?',(monitor_id,)).fetchone()
        if not row: raise ValueError('monitor_not_found')
        m=json.loads(row[0])
        if m['revision']!=revision: raise Conflict('monitor_changed_elsewhere')
        return m

    def resolve_conversion(self, monitor_id, revision, accept):
        if type(accept) is not bool: raise ValueError('invalid_conversion_decision')
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE');m=self._get(db,monitor_id,revision)
            if m['purpose']!='buy' or (m.get('conversion') or {}).get('status')!='pending': raise Conflict('conversion_not_pending')
            m['conversion'].update(status='accepted' if accept else 'declined',decided_at=utc())
            if accept:
                m.update(purpose='holding',event_states={},status='pending',revision=m['revision']+1)
                if m.get('latest'): m['latest']['superseded']=True
            # Declining changes the prompt decision, not the monitoring criteria.
            # Keep its valid evaluation/event revision and deduplication state.
            m.update(updated_at=utc())
            self._save(db,m,'holding_conversion_accepted' if accept else 'holding_conversion_declined')
        return m

    def update(self, bundle, stocks, targets, now=None, sessions=None):
        now=now or datetime.now(timezone.utc)
        if sessions is None:
            from invest_agent.trading.market_provider import completed_sessions
            sessions={market:completed_sessions(market,now) for market in targets}
        for key in {m['stock_key'] for m in self.read()['monitors'] if m['active']}: self.sync_workspace(key)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for payload, in db.execute('SELECT payload FROM scenario_monitors').fetchall():
                m=json.loads(payload)
                if not m['active']: continue
                try:
                    if m.get('issue')=='saved_drawing_deleted_or_type_changed': raise ValueError(m['issue'])
                    stock=stocks.get(m['stock_key']); ident=m['identity']; market=ident['market']
                    if not stock or any(stock.get(k)!=ident.get(k) for k in ('currency','price_basis')) or (stock.get('isin_code') or stock.get('isin'))!=ident['stable_id']:
                        raise ValueError('market_identity_or_basis_changed')
                    if ident.get('exchange') and stock.get('exchange')!=ident['exchange']: raise ValueError('market_identity_or_basis_changed')
                    bars=indicators(stock.get('bars',[]))
                    if not bars or bars[-1]['date']!=targets.get(market): raise ValueError('completed_price_stale_or_missing')
                    expected=[d for d in sessions.get(market,[]) if bars[0]['date']<=d<=bars[-1]['date']]
                    if not expected or [b['date'] for b in bars]!=expected: raise ValueError('price_sessions_incomplete')
                    if bars[-1]['atr'] is None: raise ValueError('atr_history_missing')
                    old=m.get('latest')
                    if old:
                        if bars[-1]['date']<old['date']: raise ValueError('price_date_regressed')
                        prior=next((b for b in bars if b['date']==old['date']),None)
                        if not prior or any(prior[k]!=old['bar'][k] for k in ('high','low','close')): raise ValueError('processed_price_history_changed')
                    fresh,held=account_status(bundle,m,now)
                    if fresh and m.get('account_as_of') and stamp(bundle['as_of'])<stamp(m['account_as_of']): fresh=False
                    if fresh: m['account_as_of']=bundle['as_of']
                    if m['purpose']=='holding' and (not fresh or not held): raise ValueError('account_stale_or_incomplete' if not fresh else 'holding_not_confirmed')
                    if m['purpose']=='buy' and fresh and held and m.get('conversion') is None:
                        m['conversion']=dict(status='pending',detected_at=now.isoformat(),account_as_of=bundle['as_of'])
                        m['revision']+=1; self._save(db,m,'holding_detected_confirmation_required')
                    by_day={b['date']:b for b in m['geometry_bars']}
                    for b in aggregate(stock['bars'],m['timeframe']): by_day[b['date']]={k:b[k] for k in ('date','last_session') if k in b}
                    chart=dict(timeframe=m['timeframe'],price_basis=ident['price_basis'],bars=[by_day[d] for d in sorted(by_day)])
                    bar=bars[-1]; lines=levels(m['drawing'],chart,bar['date'],m['boundary'],m['purpose'])
                    prior_bar=bars[-2] if len(bars)>1 else None
                    prior_lines={v['name']:v['value'] for v in levels(m['drawing'],chart,prior_bar['date'],m['boundary'],m['purpose'])} if prior_bar else {}
                    states={}; width=m['tolerance_atr']*bar['atr']
                    zone = m['purpose']=='buy' and m['boundary']=='both' and m['drawing']['kind'] in ('box','channel')
                    evaluation_lines = lines
                    if zone:
                        lo,hi = (line['value'] for line in lines)
                        evaluation_lines = [dict(name='zone',value=min((lo,hi),key=lambda v:abs(bar['close']-v)))]
                    for line in evaluation_lines:
                        name=line['name']; side=name if name in ('lower','upper') else None
                        if name in ('stop','target'):
                            entry=m['drawing']['points'][0]['price']; side='lower' if line['value']<entry else 'upper'
                        state=line_state(bar['close'],line['value'],width,prior_bar['close'] if prior_bar else None,prior_lines.get(name),side)
                        # Single-level crossings remain latched while on the crossed side, until recovery away from its ATR band.
                        previous=m['event_states'].get(name)
                        if old and not old.get('superseded') and previous not in (None,'clear'):
                            # Missed scheduled runs may contain a recovery and relapse. Re-arm
                            # from verified intervening closes without sending historical events.
                            for recovered in (b for b in bars if old['date']<b['date']<bar['date']):
                                historical={v['name']:v['value'] for v in levels(m['drawing'],chart,recovered['date'],m['boundary'],m['purpose'])}
                                band=m['tolerance_atr']*recovered['atr']
                                if zone:
                                    safe=historical['lower']<=recovered['close']<=historical['upper']
                                elif side=='lower': safe=recovered['close']>historical[name]+band
                                elif side=='upper': safe=recovered['close']<historical[name]-band
                                elif previous=='breakout': safe=recovered['close']<historical[name]-band
                                elif previous=='breakdown': safe=recovered['close']>historical[name]+band
                                else: safe=abs(recovered['close']-historical[name])>band
                                if safe: previous='clear';break
                        if zone:
                            before_inside = prior_bar is not None and prior_lines['lower'] <= prior_bar['close'] <= prior_lines['upper']
                            if lo <= bar['close'] <= hi: state='reached'
                            elif before_inside: state='breakout' if bar['close']>hi else 'breakdown'
                            elif min(abs(bar['close']-lo),abs(bar['close']-hi)) <= width: state='approach'
                            else: state='clear'
                            if previous=='breakout' and bar['close']>hi: state='breakout'
                            if previous=='breakdown' and bar['close']<lo: state='breakdown'
                        if not zone and side is None and previous in ('breakout','breakdown') and ((previous=='breakout' and bar['close']>line['value']) or (previous=='breakdown' and bar['close']<line['value'])):
                            state=previous
                        states[name]=state
                        if state!='clear' and state!=previous:
                            event_id='se_'+hashlib.sha256(f"{m['id']}|{m['revision']}|{bar['date']}|{name}|{state}".encode()).hexdigest()
                            label={'approach':'접근','reached':'도달','breakout':'돌파','breakdown':'이탈'}[state]
                            event=dict(id=event_id,kind='scenario',monitor_id=m['id'],monitor_revision=m['revision'],stock_key=m['stock_key'],market=market,
                                       symbol=ident['symbol'],name=ident.get('name'),currency=ident['currency'],date=bar['date'],created_at=now.isoformat(),
                                       purpose=m['purpose'],boundary=name,state=state,close=bar['close'],threshold=line['value'],atr=bar['atr'],tolerance=width,
                                       zone_lower=lo if zone else None,zone_upper=hi if zone else None,
                                       reason=f"완료 종가의 시나리오 {name} 경계 {label}",
                                       confirmed_response=(m['confirmed_responses'].get(name) if m.get('confirmed_responses') is not None else m['confirmed_response']),detail_url=m['detail_url'],
                                       case_id=m['case_id'],plan_record_id=m['plan_record_id'],drawing_id=m['drawing_id'],workspace_revision=m['workspace_revision'],test_record=m['test_record'])
                            db.execute('INSERT OR IGNORE INTO scenario_monitor_events VALUES(?,?)',(event_id,packed(event)))
                    m.update(latest=dict(date=bar['date'],close=bar['close'],atr=bar['atr'],tolerance=width,lines=lines,states=states,bar=bar,
                                         workspace_revision=m['workspace_revision'],monitor_revision=m['revision'],superseded=False),
                             event_states=states,issue=None,status='warning' if any(v!='clear' for v in states.values()) else 'monitoring')
                    evaluation = dict(monitor_id=m['id'],revision=m['revision'],checked_at=now.isoformat(),test_record=m['test_record'],**m['latest'])
                    db.execute('INSERT OR IGNORE INTO scenario_monitor_evaluations VALUES(?,?,?,?)',
                               (m['id'],m['revision'],bar['date'],packed(evaluation)))
                except (ValueError,KeyError,TypeError,OverflowError) as exc:
                    issue=str(exc) if isinstance(exc,ValueError) and str(exc).replace('_','').isalnum() else 'invalid_input'
                    m.update(issue=issue,status='deferred')
                m['checked_at']=now.isoformat();self._save(db,m)
        return self.read()


def refresh_saved(root, market_run, targets, now=None, *, account_failed=False):
    """Reuse the same immutable market/account results as trailing warnings, without network calls."""
    from dashboard_data import account_view, load, checked
    from trailing_warnings import holding_series
    import tempfile
    root=Path(root);store=ScenarioStore(root/'runtime/chart-workspaces.sqlite3')
    active=[m for m in store.read()['monitors'] if m['active']]
    if not active: return store.read()
    run_dir=Path(market_run).resolve()
    if not run_dir.is_relative_to((root/'runtime').resolve()): raise ValueError('market_run_outside_runtime')
    manifest=load(run_dir/'manifest.json');run=checked(run_dir/'run.json',manifest['output_hashes'].get('run.json'))
    source=Path(run['market_source'])/'snapshot.json'
    with tempfile.TemporaryDirectory(prefix='scenario-account-',dir=root/'runtime') as folder:
        bundle=account_view(load(root/'config.json'),Path(folder),refresh=False,saved_dashboard_root=root/'runtime/dashboard')
    if account_failed: bundle={**bundle,'query_state':'failed'}
    stocks=holding_series(source,manifest['input_hashes'].get(str(source)),{m['stock_key'] for m in active})
    return store.update(bundle,stocks,targets,now)

"""Versioned collaboration records in the existing analysis ledger. No provider IO."""
from contextlib import closing
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import secrets
import threading
import time

from analysis_geometry import geometry, VERSION as GEOMETRY_VERSION
from chart_workspace import Conflict

VERSION = 'collaboration.v1'
SECTIONS = ('identity_basis', 'business_evidence', 'judgment_invalidation',
            'purpose_plan', 'responses', 'scenario_assumptions', 'discussion_history')
KINDS = ('discussion', 'discussion_note', 'plan_version', 'plan_decision', 'risk_review')
OPS = {'collaboration_read', 'discussion_save', 'plan_save', 'risk_review_save',
       'plan_review', 'plan_decide', 'plan_input', 'discussion_note_save'}


class Forbidden(ValueError):
    pass


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('invalid_collaboration_fields')


def text(value, limit=12000, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('invalid_collaboration_text')
    return value


def choice(value, options, nullable=True):
    if value is None and nullable:
        return value
    if not isinstance(value, str) or value not in options:
        raise ValueError('invalid_collaboration_enum')
    return value


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('invalid_collaboration_integer')
    return value


def decimal(value, positive=False):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError('decimal_string_required')
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError('invalid_decimal') from None
    if not number.is_finite() or number < 0 or (positive and number == 0) or abs(number.adjusted()) > 30:
        raise ValueError('invalid_decimal')
    return value


def day(value):
    if value is not None:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError('invalid_evaluation_date')
        date.fromisoformat(value)
    return value


def array(value, limit=100):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError('invalid_collaboration_list')
    return value


def identity(snapshot):
    chart = {**snapshot['chart'], **snapshot.get('instrument', {})}
    market, symbol = snapshot['stock_key'].split(':', 1)
    return dict(stock_key=snapshot['stock_key'], market=market, symbol=symbol,
                name=chart.get('name'), currency=chart.get('currency'),
                stable_id=chart.get('isin_code') or chart.get('isin'),
                exchange=chart.get('exchange'), price_basis=chart.get('price_basis'))


def validate_plan(plan, snapshot):
    fields(plan, ('title', 'purpose', 'account_alias', 'allocation', 'conditions', 'scenarios'),
           ('title', 'purpose', 'account_alias', 'allocation', 'conditions', 'scenarios'))
    text(plan['title'], 200); text(plan['purpose'], 4000, True); text(plan['account_alias'], 200, True)
    allocation = plan['allocation']
    fields(allocation, ('quantity', 'basis'), ('quantity', 'basis'))
    decimal(allocation['quantity']); choice(allocation['basis'], ('initial', 'current_remaining', 'units'))
    currency = identity(snapshot)['currency']
    ids, priorities = set(), set()
    for c in array(plan['conditions']):
        names = ('id', 'kind', 'statement', 'action', 'evaluation', 'operator', 'price', 'currency',
                 'quantity', 'priority', 'execution_price', 'drawing_ref')
        fields(c, names, names)
        text(c['id'], 100); text(c['statement']); choice(c['kind'], ('price', 'qualitative'), False)
        if not c['id'] or c['id'] in ids: raise ValueError('duplicate_condition_id')
        ids.add(c['id'])
        choice(c['action'], ('buy', 'add', 'reduce', 'hold', 'review'), False)
        choice(c['evaluation'], ('intraday', 'daily_close', 'weekly_close'))
        choice(c['operator'], ('lt', 'lte', 'gt', 'gte'))
        choice(c['currency'], ('KRW', 'USD'))
        if c['currency'] and currency and c['currency'] != currency: raise ValueError('plan_currency_mismatch')
        decimal(c['price'], True); decimal(c['execution_price'], True)
        q = c['quantity']; fields(q, ('value', 'basis'), ('value', 'basis'))
        decimal(q['value']); choice(q['basis'], ('initial_fraction', 'remaining_fraction', 'units'))
        if q['basis'] in ('initial_fraction', 'remaining_fraction') and q['value'] is not None and Decimal(q['value']) > 1:
            raise ValueError('fraction_exceeds_one')
        if c['action'] in ('buy', 'add') and q['basis'] in ('initial_fraction', 'remaining_fraction'):
            raise ValueError('purchase_quantity_requires_units')
        if c['priority'] is not None:
            integer(c['priority'], 1)
            if c['priority'] in priorities: raise ValueError('duplicate_condition_priority')
            priorities.add(c['priority'])
        ref = c['drawing_ref']
        if c['kind'] == 'qualitative' and any(c[k] is not None for k in ('operator', 'price', 'drawing_ref')):
            raise ValueError('qualitative_condition_not_numeric')
        if ref is not None:
            names = ('drawing_id', 'workspace_revision', 'timeframe', 'price_basis', 'geometry_version', 'line')
            fields(ref, names, names)
            integer(ref['workspace_revision']); text(ref['drawing_id'], 64)
            drawing = next((d for d in snapshot['workspace']['state']['drawings'] if d['id'] == ref['drawing_id']), None)
            if not drawing or drawing['kind'] not in ('trend', 'channel', 'horizontal', 'box', 'riskreward'): raise ValueError('unsupported_plan_drawing')
            if (ref['workspace_revision'] != snapshot['workspace']['revision'] or ref['timeframe'] != snapshot['chart']['timeframe']
                    or ref['price_basis'] != snapshot['chart'].get('price_basis') or ref['geometry_version'] != GEOMETRY_VERSION):
                raise ValueError('drawing_basis_mismatch')
            lines = {'trend': ('line',), 'horizontal': ('line',), 'channel': ('line', 'other_line'),
                     'box': ('low', 'high'), 'riskreward': ('entry', 'stop', 'target')}
            choice(ref['line'], lines[drawing['kind']], False)
            if c['price'] is not None: raise ValueError('drawing_condition_cannot_freeze_constant_price')
    ids = set()
    for s in array(plan['scenarios']):
        names = ('id', 'evaluation_at', 'assumed_price', 'currency', 'response', 'price_path', 'fx_to_base', 'fx_as_of', 'cost')
        fields(s, names, names); text(s['id'], 100)
        if not s['id'] or s['id'] in ids: raise ValueError('duplicate_scenario_id')
        ids.add(s['id']); day(s['evaluation_at']); day(s['fx_as_of'])
        decimal(s['assumed_price'], True); decimal(s['fx_to_base'], True); decimal(s['cost'])
        choice(s['currency'], ('KRW', 'USD')); choice(s['response'], ('none', 'plan'))
        if s['currency'] and currency and s['currency'] != currency: raise ValueError('plan_currency_mismatch')
        if s['price_path'] is not None:
            previous = None
            for point in array(s['price_path'], 1000):
                fields(point, ('at', 'price'), ('at', 'price')); day(point['at']); decimal(point['price'], True)
                if point['at'] is None or point['price'] is None or (previous and point['at'] <= previous):
                    raise ValueError('ordered_price_path_required')
                if s['evaluation_at'] and point['at'] > s['evaluation_at']: raise ValueError('path_after_evaluation')
                previous = point['at']
    return deepcopy(plan)


def blockers(record):
    """Incomplete meaning is explicit; these are not risk limits or an execution gate."""
    p = record['plan']; out = []
    for field in ('stable_id', 'currency'):
        if not record['identity'].get(field): out.append('identity.' + field)
    for field in ('purpose', 'account_alias'):
        if not p.get(field): out.append('plan.' + field)
    for field, value in p['allocation'].items():
        if value is None: out.append('allocation.' + field)
    for c in p['conditions']:
        prefix = 'condition.' + c['id'] + '.'
        if c['kind'] == 'qualitative': out.append(prefix + 'manual_assessment_required')
        else:
            for field in ('evaluation', 'operator', 'currency', 'priority'):
                if c[field] is None: out.append(prefix + field)
            if c['price'] is None and c['drawing_ref'] is None: out.append(prefix + 'price_or_drawing')
        if c['action'] in ('buy', 'add', 'reduce'):
            for field, value in c['quantity'].items():
                if value is None: out.append(prefix + 'quantity.' + field)
            if c['execution_price'] is None: out.append(prefix + 'execution_price')
    if not p['scenarios']: out.append('scenarios')
    for s in p['scenarios']:
        for field in ('evaluation_at', 'assumed_price', 'currency', 'response', 'cost'):
            if s[field] is None: out.append('scenario.' + s['id'] + '.' + field)
        if s['currency'] == 'USD':
            for field in ('fx_to_base', 'fx_as_of'):
                if s[field] is None: out.append('scenario.' + s['id'] + '.' + field)
        if s['response'] == 'plan' and not s['price_path']: out.append('scenario.' + s['id'] + '.price_path')
    return out


class Collaboration:
    def __init__(self, service):
        self.s = service
        self.confirmations = {}
        self.lock = threading.Lock()

    def migrate(self):
        with closing(self.s.store.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS analysis_collaboration_heads (case_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)')

    def scenario_draft(self, a, case, principal, session, origin):
        """Turn an explicitly selected saved drawing into a reviewable plan draft."""
        if principal != 'browser' or not session:
            raise Forbidden('local_browser_confirmation_required')
        required = ('analysis_key', 'expected_revision', 'drawing_id', 'workspace_revision', 'title', 'purpose', 'response')
        fields(a, required + ('plan_id', 'expected_plan_version'), required)
        integer(a['expected_revision']); integer(a['workspace_revision'])
        text(a['drawing_id'], 64); text(a['title'], 200)
        text(a['purpose'], 4000, True); text(a['response'], 4000, True)
        if a.get('plan_id') is not None:
            with closing(self.s.store.connect()) as db:
                prior = [r for r in self.read_rows(case['id'], db)
                         if r.get('kind') == 'plan_version' and r.get('plan_id') == a['plan_id']]
            if not prior or not prior[-1].get('monitoring_only'):
                raise ValueError('full_plan_requires_full_editor')
        snap = self.s.capture(case)
        if snap['workspace']['revision'] != a['workspace_revision']:
            raise Conflict('workspace_changed_elsewhere')
        drawing = next((d for d in snap['workspace']['state']['drawings'] if d['id'] == a['drawing_id']), None)
        lines = {'box': ('low', 'high'), 'channel': ('line', 'other_line'),
                 'riskreward': ('entry', 'stop', 'target'), 'horizontal': ('line',), 'trend': ('line',)}
        if drawing is None or drawing['kind'] not in lines:
            raise ValueError('unsupported_plan_drawing')
        response = a['response'] or None
        conditions = []
        for n, line in enumerate(lines[drawing['kind']], 1):
            conditions.append(dict(id='drawing_' + line, kind='price', statement=response or '대응 기준 미정',
                action='review', evaluation='daily_close', operator=None, price=None,
                currency=identity(snap)['currency'], quantity=dict(value=None, basis=None), priority=n,
                execution_price=None, drawing_ref=dict(drawing_id=drawing['id'], workspace_revision=a['workspace_revision'],
                    timeframe=snap['chart']['timeframe'], price_basis=snap['chart'].get('price_basis'),
                    geometry_version=GEOMETRY_VERSION, line=line)))
        plan = dict(title=a['title'] or '차트 시나리오', purpose=a['purpose'] or None, account_alias=None,
                    allocation=dict(quantity=None, basis=None), conditions=conditions, scenarios=[])
        args = dict(plan_id=a.get('plan_id'), expected_plan_version=a.get('expected_plan_version', 0))
        integer(args['expected_plan_version'])
        if args['plan_id'] is not None:
            from analysis_service import key
            key(args['plan_id'], 'pl_')
        return self.append(case, snap, a['expected_revision'], 'plan_version',
            dict(plan=validate_plan(plan, snap), monitoring_only=True, references=[dict(kind='utterance', reference=None,
                 text=a['response'], speaker='user')] if response else []),
            principal, origin, plan_args=args)

    def read_rows(self, cid, db):
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM analysis_items WHERE case_id=? ORDER BY rowid', (cid,))]

    def revision(self, cid, db):
        row = db.execute('SELECT revision FROM analysis_collaboration_heads WHERE case_id=?', (cid,)).fetchone()
        return row[0] if row else 0

    def verify(self, record):
        if record['kind'] not in KINDS: return
        path = self.s.root / record['markdown_path']
        try: actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError: raise ValueError('record_markdown_missing') from None
        if actual != record['markdown_sha256']: raise ValueError('record_markdown_integrity_failed')
        canonical = self.markdown({k:v for k,v in record.items() if k not in ('markdown_path', 'markdown_sha256')})
        if hashlib.sha256(canonical.encode('utf-8')).hexdigest() != actual:
            raise ValueError('record_database_markdown_mismatch')

    @staticmethod
    def active(rows):
        active = {}
        for r in rows:
            if r['kind'] == 'plan_decision':
                if r['action'] == 'adopt': active[r['plan_id']] = r
                else: active.pop(r['plan_id'], None)
        return active

    def read(self, case, origin):
        with closing(self.s.store.connect()) as db:
            db.execute('BEGIN')
            rows = self.read_rows(case['id'], db); revision = self.revision(case['id'], db)
        common = [r for r in rows if r['kind'] in KINDS]
        for r in common: self.verify(r)
        current = next((r for r in reversed(common) if r['kind'] == 'discussion'), None)
        plans = {}
        for r in common:
            if r['kind'] == 'plan_version': plans[r['plan_id']] = r
        lookup = {r['id']: r for r in common}; active = self.active(common)
        return dict(contract_version=VERSION, analysis_key=case['id'], stock_key=case['stock_key'], revision=revision,
                    current=current, plans=list(plans.values()),
                    unincorporated_notes=[r for r in common if r['kind'] == 'discussion_note' and r['revision'] > (current['revision'] if current else 0)],
                    active_plans=[dict(record=lookup[d['plan_record_id']], adoption=d) for d in active.values()],
                    records=common, legacy_records=[r for r in rows if r['kind'] not in KINDS],
                    snapshots=self.s.detail(case['id'], origin)['snapshots'],
                    related_cases=[c for c in self.s.rows('analysis_cases') if c['stock_key'] == case['stock_key'] and c['id'] != case['id']],
                    links=self.s.links(case, origin))

    def references(self, cid, refs):
        for r in array(refs):
            fields(r, ('kind', 'reference', 'text', 'speaker'), ('kind', 'reference', 'text', 'speaker'))
            choice(r['kind'], ('utterance', 'record', 'source', 'session'), False)
            choice(r['speaker'], ('user', 'llm', 'source'), False)
            text(r['reference'], 2000, True); text(r['text'], 20000, True)
            if not r['reference'] and not r['text']: raise ValueError('empty_reference')
            if r['kind'] == 'utterance' and not r['text']: raise ValueError('utterance_text_required')
            if r['kind'] == 'record' and not self.s.item(cid, r['reference']): raise ValueError('record_reference_not_found')
        return deepcopy(refs)

    def save(self, op, a, case, principal, origin):
        from analysis_service import digest
        required_role = 'risk' if op == 'risk_review_save' else 'collaboration'
        if principal != required_role: raise Forbidden('collaboration_role_forbidden')
        base = ('contract_version', 'analysis_key', 'snapshot_id', 'expected_revision', 'references')
        extra = {'discussion_save': ('title', 'body', 'sections', 'open_questions'),
                 'plan_save': ('plan_id', 'expected_plan_version', 'plan'),
                 'risk_review_save': ('title', 'body', 'plan_record_ids', 'calculation')}[op]
        fields(a, base + extra, base + extra)
        if a['contract_version'] != VERSION: raise ValueError('unsupported_collaboration_contract')
        integer(a['expected_revision'])
        snap = self.s.snapshot(case['id'], a['snapshot_id'])
        refs = self.references(case['id'], a['references'])
        payload = dict(references=refs)
        if op == 'discussion_save':
            text(a['title'], 200); text(a['body'], 50000); fields(a['sections'], SECTIONS)
            payload.update(title=a['title'], body=a['body'], sections={k: text(a['sections'].get(k), 12000, True) for k in SECTIONS},
                           open_questions=[text(q, 2000) for q in array(a['open_questions'])])
        elif op == 'plan_save':
            payload['plan'] = validate_plan(a['plan'], snap)
            integer(a['expected_plan_version'])
            if a['plan_id'] is not None:
                from analysis_service import key
                key(a['plan_id'], 'pl_')
        else:
            text(a['title'], 200); text(a['body'], 50000)
            for rid in array(a['plan_record_ids']): self.plan_record(case['id'], rid)
            calculation = a['calculation']
            if calculation is not None:
                fields(calculation, ('code_version', 'input_snapshot_ids', 'assumptions', 'result'), ('code_version', 'input_snapshot_ids', 'assumptions', 'result'))
                if not text(calculation['code_version'], 200): raise ValueError('calculation_code_version_required')
                if not array(calculation['input_snapshot_ids']): raise ValueError('calculation_snapshot_required')
                for sid in calculation['input_snapshot_ids']: self.s.snapshot(case['id'], sid)
                if not isinstance(calculation['assumptions'], dict) or not isinstance(calculation['result'], dict): raise ValueError('invalid_calculation_record')
                digest(calculation)
            payload.update(title=a['title'], body=a['body'], plan_record_ids=a['plan_record_ids'], calculation=calculation)
        kind = {'discussion_save': 'discussion', 'plan_save': 'plan_version', 'risk_review_save': 'risk_review'}[op]
        return self.append(case, snap, a['expected_revision'], kind, payload, principal, origin, plan_args=a if op == 'plan_save' else None)

    def markdown(self, r):
        title = r.get('title') or r.get('plan', {}).get('title') or '계획 채택 변경'
        summary = ''
        if r['kind'] == 'discussion':
            summary = '\n'.join(f'- {k}: {r["sections"][k] if r["sections"][k] is not None else "미정"}' for k in SECTIONS)
            summary += '\n\n## 미정 질문\n' + '\n'.join('- ' + q for q in r['open_questions'])
        return (f"# {title}\n\n기록 {r['id']} · 분석 {r['case_id']} · v{r['revision']} · {r['status']}\n\n"
                + summary + '\n\n## 논의 원문·근거·변경 이력\n' + r.get('body', '')
                + '\n\n```json\n' + json.dumps(r, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n```\n')

    def append(self, case, snap, expected, kind, payload, principal, origin, plan_args=None, decision=None):
        from analysis_service import ident, now, packed, digest
        path = None
        committed = False
        try:
            with closing(self.s.store.connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                revision = self.revision(case['id'], db)
                if revision != expected: raise Conflict('collaboration_revision_changed')
                rows = self.read_rows(case['id'], db)
                area = 'collaboration' if principal == 'browser' else principal
                previous = next((r for r in reversed(rows) if r['kind'] == kind and r.get('area') == area
                                 and (kind != 'plan_decision' or r.get('plan_id') == payload.get('plan_id'))), None)
                if plan_args is not None:
                    previous = next((r for r in reversed(rows) if r['kind'] == 'plan_version' and r['plan_id'] == plan_args['plan_id']), None)
                    if (previous['plan_version'] if previous else 0) != plan_args['expected_plan_version'] or (plan_args['plan_id'] and previous is None):
                        raise Conflict('plan_version_changed')
                    payload.update(plan_id=previous['plan_id'] if previous else ident('pl_'), plan_version=(previous['plan_version'] if previous else 0) + 1,
                                   plan_sha256=digest(payload['plan']))
                if decision is not None:
                    plan = decision['plan']; active = self.active(rows)
                    latest = next(r for r in reversed(rows) if r['kind'] == 'plan_version' and r['plan_id'] == plan['plan_id'])
                    current = active.get(plan['plan_id'])
                    if payload['action'] == 'adopt' and (latest['id'] != plan['id'] or (current and current['plan_record_id'] == plan['id'])):
                        raise Conflict('plan_version_changed_or_already_adopted')
                    if payload['action'] == 'revoke' and (not current or current['plan_record_id'] != plan['id']):
                        raise Conflict('plan_not_active')
                r = dict(payload, id=ident('it_'), case_id=case['id'], kind=kind, contract_version=VERSION,
                         revision=revision + 1, previous_record_id=previous['id'] if previous else None,
                         area=area,
                         principal=principal, subject='local_ui_confirmation' if principal == 'browser' else 'agent_attributed',
                         status='draft' if kind == 'plan_version' else 'recorded',
                         identity=identity(snap), stock_key=case['stock_key'], snapshot_id=snap['id'], snapshot_sha256=digest(snap),
                         data_as_of={'captured_at': snap['captured_at'], 'market': snap['market'], 'accounts': snap['accounts'].get('as_of'),
                                     'chart_last': snap['chart']['bars'][-1]['date'] if snap['chart']['bars'] else None},
                         created_at=now(), test_record=case['test_record'], authority='record_only_not_execution_or_risk_policy')
                if kind == 'plan_version':
                    r['blockers'] = ([f'identity.{k}' for k in ('stable_id', 'currency', 'price_basis') if not r['identity'].get(k)]
                                     if r.get('monitoring_only') else blockers(r))
                content = self.markdown(r)
                path = self.s.root / 'judgments' / 'analyses' / case['id'] / (r['id'] + '.md')
                r['markdown_path'] = path.relative_to(self.s.root).as_posix()
                r['markdown_sha256'] = hashlib.sha256(content.encode('utf-8')).hexdigest()
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x', encoding='utf-8', newline='\n') as f: f.write(content)
                self.verify(r)
                db.execute('INSERT INTO analysis_items VALUES (?,?,?,?)', (r['id'], case['id'], kind, packed(r)))
                db.execute('INSERT INTO analysis_collaboration_heads VALUES (?,?) ON CONFLICT(case_id) DO UPDATE SET revision=excluded.revision', (case['id'], revision + 1))
            committed = True
        finally:
            if path is not None and not committed:
                path.unlink(missing_ok=True)
        stored = self.s.item(case['id'], r['id'])
        if stored != r: raise ValueError('record_readback_failed')
        self.verify(stored)
        return dict(contract_version=VERSION, revision=r['revision'], record=stored, verified=True, links=self.s.links(case, origin))

    def plan_record(self, cid, rid):
        record = self.s.item(cid, rid)
        if record is None or record['kind'] != 'plan_version': raise ValueError('plan_record_not_found')
        self.verify(record)
        return record

    def review(self, a, case, principal, session):
        if principal != 'browser' or not session: raise Forbidden('local_browser_confirmation_required')
        fields(a, ('analysis_key', 'plan_record_id', 'expected_revision'), ('analysis_key', 'plan_record_id', 'expected_revision'))
        integer(a['expected_revision']); plan = self.plan_record(case['id'], a['plan_record_id'])
        with closing(self.s.store.connect()) as db:
            if self.revision(case['id'], db) != a['expected_revision']: raise Conflict('collaboration_revision_changed')
        nonce = secrets.token_urlsafe(32)
        with self.lock:
            self.confirmations = {k: v for k, v in self.confirmations.items() if v['expires'] > time.monotonic()}
            if len(self.confirmations) > 500: raise ValueError('too_many_pending_confirmations')
            self.confirmations[nonce] = dict(plan=plan, revision=a['expected_revision'], session=session, expires=time.monotonic() + 300)
        return dict(contract_version=VERSION, plan=plan, revision=a['expected_revision'], confirmation=nonce)

    def decide(self, a, case, principal, session, origin):
        if principal != 'browser' or not session: raise Forbidden('local_browser_confirmation_required')
        names = ('contract_version', 'analysis_key', 'expected_revision', 'confirmation', 'action', 'reason')
        fields(a, names, names)
        if a['contract_version'] != VERSION: raise ValueError('unsupported_collaboration_contract')
        integer(a['expected_revision']); text(a['confirmation'], 100); text(a['reason'], 4000)
        choice(a['action'], ('adopt', 'revoke'), False)
        with self.lock: pending = self.confirmations.pop(a['confirmation'], None)
        if not pending or pending['session'] != session or pending['expires'] < time.monotonic(): raise Forbidden('invalid_plan_confirmation')
        plan = pending['plan']
        if plan['case_id'] != case['id'] or pending['revision'] != a['expected_revision']: raise Conflict('confirmation_basis_changed')
        if self.plan_record(case['id'], plan['id']) != plan: raise Conflict('reviewed_plan_changed')
        snap = self.s.snapshot(case['id'], plan['snapshot_id'])
        payload = dict(plan_id=plan['plan_id'], plan_record_id=plan['id'], plan_version=plan['plan_version'],
                       plan_sha256=plan['plan_sha256'], action=a['action'], reason=a['reason'], references=[])
        return self.append(case, snap, a['expected_revision'], 'plan_decision', payload, principal, origin, decision=pending)

    def input(self, a, case):
        from analysis_service import digest
        fields(a, ('analysis_key', 'plan_record_id', 'mode', 'evaluation_at'), ('analysis_key', 'plan_record_id', 'mode'))
        choice(a['mode'], ('adopted', 'hypothetical'), False); day(a.get('evaluation_at'))
        plan = self.plan_record(case['id'], a['plan_record_id'])
        with closing(self.s.store.connect()) as db: rows = self.read_rows(case['id'], db)
        adoption = self.active(rows).get(plan['plan_id'])
        if adoption and adoption['plan_record_id'] != plan['id']: adoption = None
        if a['mode'] == 'adopted' and adoption is None: raise ValueError('plan_version_not_adopted')
        if adoption: self.verify(adoption)
        snap = self.s.snapshot(case['id'], plan['snapshot_id'])
        if digest(snap) != plan['snapshot_sha256']: raise ValueError('plan_snapshot_integrity_failed')
        evaluations = []
        for c in plan['plan']['conditions']:
            ref = c['drawing_ref']
            if ref is None: continue
            result = None
            basis = None
            if a.get('evaluation_at'):
                g = geometry(snap['workspace'], snap['chart'], {**snap['context'], 'evaluate_at': a['evaluation_at']})
                result = next(d for d in g['drawings'] if d['id'] == ref['drawing_id'])
                basis = {k:v for k,v in g.items() if k != 'drawings'}
                if g['last_actual_session'] and a['evaluation_at'] > g['last_actual_session']:
                    basis['limitations'] = [*basis['limitations'], 'evaluation_after_last_actual_session_projected_calendar_not_exchange_session']
            evaluations.append(dict(condition_id=c['id'], reference=ref, evaluation_at=a.get('evaluation_at'), geometry=result,
                                    basis=basis,
                                    state='evaluation_time_required' if result is None else 'frozen_snapshot_geometry'))
        return dict(contract_version=VERSION, analysis_key=case['id'], mode=a['mode'], plan_record=plan,
                    plan_id=plan['plan_id'], plan_version=plan['plan_version'], plan_sha256=plan['plan_sha256'],
                    snapshot_id=snap['id'], snapshot_sha256=plan['snapshot_sha256'], snapshot=snap, adoption=adoption,
                    blockers=plan['blockers'], drawing_evaluations=evaluations, calculation_code_version_required=True,
                    limitations=['snapshot_holdings_are_historical_not_current_balances', 'plan_allocation_not_execution',
                                 'cross_plan_account_allocation_overlap_must_be_checked', 'missing_values_not_zero'])

    def dispatch(self, op, a, origin, principal, session=None):
        case = self.s.case(a['analysis_key'])
        if op == 'collaboration_read':
            fields(a, ('analysis_key',), ('analysis_key',)); return self.read(case, origin)
        if op == 'plan_input': return self.input(a, case)
        if op == 'plan_review': return self.review(a, case, principal, session)
        if op == 'plan_decide': return self.decide(a, case, principal, session, origin)
        if op == 'discussion_note_save':
            if principal != 'browser' or not session: raise Forbidden('local_browser_confirmation_required')
            names = ('analysis_key', 'snapshot_id', 'contract_version', 'expected_revision', 'body')
            fields(a, names, names)
            if a['contract_version'] != VERSION: raise ValueError('unsupported_collaboration_contract')
            integer(a['expected_revision']); text(a['body'], 20000)
            if not a['body'].strip(): raise ValueError('empty_discussion_note')
            snap = self.s.snapshot(case['id'], a['snapshot_id'])
            return self.append(case, snap, a['expected_revision'], 'discussion_note',
                dict(title='사용자 후속 발언', body=a['body'], references=[dict(kind='utterance', reference=None, text=a['body'], speaker='user')]),
                principal, origin)
        return self.save(op, a, case, principal, origin)

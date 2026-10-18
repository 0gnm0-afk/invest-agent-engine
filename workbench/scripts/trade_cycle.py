"""Explicit trade-cycle plans, deterministic 3x3 scenarios and user-recorded fills.

No broker, market, notification or order IO belongs in this module. The tables are
additive to the existing analysis database. Drafts and events are append-only.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
from itertools import permutations
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from chart_workspace import Conflict
from analysis_collaboration import Forbidden

VERSION = 'trade-cycle.v1'
SCENARIOS = ('bullish', 'neutral', 'bearish')
RATIOS = tuple(permutations((20, 30, 50)))


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def number(value, *, positive=False, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not 0 < len(value) <= 80:
        raise ValueError('cycle_decimal_string_required')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('cycle_invalid_decimal') from None
    if not result.is_finite() or result < 0 or (positive and result == 0) or (result and abs(result.adjusted()) > 18):
        raise ValueError('cycle_invalid_decimal')
    return result


def integer(value, minimum=0, maximum=10**12):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('cycle_invalid_integer')
    return value


def text(value, limit=4000, nullable=False):
    if value is None and nullable:
        return value
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('cycle_invalid_text')
    return value


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('cycle_invalid_fields')


def stamp(value):
    text(value, 50)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('cycle_invalid_event_time') from None
    # Date-only paths represent their explicitly ordered daily observations.
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def money(value):
    return format(value, 'f')


def validate_trigger(trigger):
    if trigger is None:
        return
    fields(trigger, ('kind', 'evaluation', 'comparator', 'operator'), ('kind', 'evaluation'))
    if trigger['kind'] not in ('price', 'trailing', 'ma5', 'ma10', 'manual') or trigger['evaluation'] != 'daily_close':
        raise ValueError('cycle_invalid_trigger')
    if 'operator' in trigger and 'comparator' in trigger:
        raise ValueError('cycle_duplicate_trigger_comparator')
    comparator = trigger.get('comparator', trigger.get('operator'))
    if trigger['kind'] != 'manual' and comparator not in ('gt', 'gte', 'lt', 'lte', 'cross_above', 'cross_below'):
        raise ValueError('cycle_invalid_trigger_comparator')
    if trigger['kind'] == 'manual' and comparator is not None:
        raise ValueError('cycle_manual_trigger_not_numeric')


def validate_step(step):
    fields(step, ('confirmation_price', 'assumed_fill_price', 'condition', 'trigger', 'label'),
           ('confirmation_price', 'assumed_fill_price', 'condition'))
    number(step['confirmation_price'], positive=True, nullable=True)
    number(step['assumed_fill_price'], positive=True, nullable=True)
    text(step['condition'], nullable=True)
    if 'label' in step:
        text(step['label'], 200, True)
    validate_trigger(step.get('trigger'))


def validate_plan(plan):
    fields(plan, ('title', 'purpose', 'account_alias', 'currency', 'budget', 'initial_holding',
                  'buy_steps', 'sell_steps', 'stop', 'scenarios', 'notes', 'rationale', 'unresolved', 'sources'),
           ('currency', 'budget', 'initial_holding', 'buy_steps', 'sell_steps', 'stop', 'scenarios'))
    for key in ('title', 'purpose', 'account_alias', 'notes', 'rationale'):
        if key in plan:
            text(plan[key], 12000 if key == 'notes' else 4000, True)
    if 'unresolved' in plan:
        if not isinstance(plan['unresolved'], list) or len(plan['unresolved']) > 100:
            raise ValueError('cycle_invalid_unresolved')
        for item in plan['unresolved']:
            text(item)
    if 'sources' in plan:
        if not isinstance(plan['sources'], list) or len(plan['sources']) > 100:
            raise ValueError('cycle_invalid_sources')
        for source in plan['sources']:
            fields(source, ('kind', 'reference', 'text'), ('kind', 'reference'))
            for item in source.values():
                text(item, 12000, True)
    if plan['currency'] not in ('USD', 'KRW', None):
        raise ValueError('cycle_invalid_currency')
    number(plan['budget'], nullable=True)
    holding = plan['initial_holding']
    fields(holding, ('quantity', 'average_price'), ('quantity', 'average_price'))
    if holding['quantity'] is not None:
        integer(holding['quantity'])
    number(holding['average_price'], positive=bool(holding['quantity']), nullable=True)
    for key in ('buy_steps', 'sell_steps'):
        if not isinstance(plan[key], list) or len(plan[key]) > 3:
            raise ValueError('cycle_at_most_three_steps')
        for step in plan[key]:
            validate_step(step)
    if plan['stop'] is not None:
        validate_step(plan['stop'])
    fields(plan['scenarios'], SCENARIOS)
    for name, scenario in plan['scenarios'].items():
        fields(scenario, ('label', 'events', 'assumption'), ('events',))
        for key in ('label', 'assumption'):
            if key in scenario:
                text(scenario[key], nullable=True)
        if not isinstance(scenario['events'], list) or len(scenario['events']) > 100:
            raise ValueError('cycle_invalid_events')
        previous, seen = None, set()
        for event in scenario['events']:
            fields(event, ('kind', 'step', 'price', 'at', 'note'), ('kind',))
            kind = event['kind']
            if kind not in ('buy', 'sell', 'stop', 'mark'):
                raise ValueError('cycle_invalid_event_kind')
            if event.get('price') is not None:
                number(event['price'], positive=True)
            if event.get('at') is not None:
                when = stamp(event['at'])
                if previous is not None and when < previous:
                    raise ValueError('cycle_path_not_chronological')
                previous = when
            if 'note' in event:
                text(event['note'], nullable=True)
            if kind in ('buy', 'sell'):
                step = integer(event.get('step'), 1, 3)
                if step > len(plan[kind + '_steps']):
                    raise ValueError('cycle_unknown_step')
                if (kind, step) in seen:
                    raise ValueError('cycle_duplicate_path_step')
                seen.add((kind, step))
            elif 'step' in event:
                raise ValueError('cycle_unexpected_step')
    return deepcopy(plan)


def needs_input(plan):
    missing = []
    for key in ('currency', 'budget'):
        if plan[key] is None:
            missing.append(key)
    holding = plan['initial_holding']
    if holding['quantity'] is None:
        missing.append('initial_holding.quantity')
    if holding['quantity'] and holding['average_price'] is None:
        missing.append('initial_holding.average_price')
    if not plan['buy_steps'] and not holding['quantity']:
        missing.append('buy_steps_or_initial_holding')
    if not plan['sell_steps']:
        missing.append('sell_steps')
    if plan['stop'] is None:
        missing.append('stop')
    for kind in ('buy', 'sell', 'stop'):
        steps = [plan['stop']] if kind == 'stop' and plan['stop'] else plan.get(kind + '_steps', [])
        for index, step in enumerate(steps, 1):
            prefix = 'stop' if kind == 'stop' else kind + '_steps.' + str(index)
            if step['assumed_fill_price'] is None:
                missing.append(prefix + '.assumed_fill_price')
            if not step['condition']:
                missing.append(prefix + '.condition')
            if (step.get('trigger') or {}).get('kind') == 'price' and step['confirmation_price'] is None:
                missing.append(prefix + '.confirmation_price')
    for name in SCENARIOS:
        scenario = plan['scenarios'].get(name)
        if not scenario or not scenario['events']:
            missing.append('scenarios.' + name + '.events')
            continue
        for i, event in enumerate(scenario['events']):
            if event['kind'] == 'mark' and event.get('price') is None:
                missing.append('scenarios.' + name + '.events.' + str(i) + '.price')
        if scenario['events'][-1]['kind'] not in ('mark', 'sell', 'stop'):
            missing.append('scenarios.' + name + '.final_mark')
    return missing


def combination_id(buy, sell):
    return 'b' + '-'.join(map(str, buy)) + '_s' + '-'.join(map(str, sell))


def ratios_for(ident):
    for buy in RATIOS:
        for sell in RATIOS:
            if ident == combination_id(buy, sell):
                return buy, sell
    raise ValueError('cycle_unknown_combination')


def ratio_blockers(previous_combination, new_combination, fills):
    """Only already executed tranche ratios are fixed; later slots can be swapped."""
    previous = ratios_for(previous_combination)
    proposed = ratios_for(new_combination)
    blocked = []
    for fill in fills:
        kind = fill['kind']
        if kind not in ('buy', 'sell'):
            continue
        side, index = (0 if kind == 'buy' else 1), fill['step'] - 1
        if previous[side][index] != proposed[side][index]:
            blocked.append('completed_' + kind + '_step_' + str(index + 1) + '_ratio_locked')
    return blocked


class _Ledger:
    def __init__(self, plan, buy, sell):
        self.plan, self.buy, self.sell = plan, buy, sell
        self.cash = number(plan['budget'])
        self.quantity = plan['initial_holding']['quantity']
        avg = number(plan['initial_holding']['average_price'] or '0')
        self.cost = avg * self.quantity
        self.initial_equity = self.cash + self.cost
        self.realized = Decimal(0)
        self.mark = avg if self.quantity else None
        self.sell_basis = None
        self.buy_completed, self.sell_completed, self.cancelled = [], [], []
        self.closed = False
        self.ledger = []

    def state(self):
        average = self.cost / self.quantity if self.quantity else Decimal(0)
        unrealized = self.quantity * (self.mark or Decimal(0)) - self.cost
        total = self.realized + unrealized
        equity = self.cash + self.quantity * (self.mark or Decimal(0))
        phase = ('closed' if self.closed else 'selling' if self.sell_basis is not None else
                 'holding' if len(self.buy_completed) >= len(self.plan['buy_steps']) else 'buying')
        return dict(phase=phase, cash=money(self.cash), quantity=self.quantity,
                    average_price=money(average), realized_pnl=money(self.realized),
                    unrealized_pnl=money(unrealized), total_pnl=money(total), equity=money(equity),
                    return_pct=money(total / self.initial_equity * 100) if self.initial_equity else None,
                    mark_price=money(self.mark) if self.mark is not None else None,
                    sell_basis=self.sell_basis, buy_completed=list(self.buy_completed),
                    sell_completed=list(self.sell_completed), cancelled_buy_steps=list(self.cancelled))

    def planned_quantity(self, kind, step, price):
        if kind == 'stop':
            return self.quantity
        if kind == 'buy':
            allocation = number(self.plan['budget']) * self.buy[step - 1] / 100
            return int((min(allocation, self.cash) / price).to_integral_value(rounding=ROUND_FLOOR))
        basis = self.sell_basis if self.sell_basis is not None else self.quantity
        if step == 3:
            return self.quantity
        return min(self.quantity, int((Decimal(basis) * self.sell[step - 1] / 100).to_integral_value(rounding=ROUND_FLOOR)))

    def apply(self, event, *, actual=False, projection=False):
        kind, step = event['kind'], event.get('step')
        if kind == 'mark':
            self.mark = number(event['price'], positive=True)
            self.ledger.append(dict(event=deepcopy(event), status='marked', state=self.state()))
            return
        reason = None
        completed = self.buy_completed if kind == 'buy' else self.sell_completed
        if self.closed:
            reason = 'cycle_closed'
        elif kind == 'buy' and self.sell_basis is not None:
            reason = 'buy_cancelled_after_selling_started'
        elif kind in ('buy', 'sell') and step in completed:
            reason = 'already_recorded' if projection else 'duplicate_step'
        if reason:
            if actual:
                raise ValueError('cycle_' + reason)
            self.ledger.append(dict(event=deepcopy(event), status='skipped', reason=reason, state=self.state()))
            return
        definition = self.plan['stop'] if kind == 'stop' else self.plan[kind + '_steps'][step - 1]
        price = number(event.get('price') or definition['assumed_fill_price'], positive=True)
        expected = self.planned_quantity(kind, step, price)
        quantity = event['quantity'] if actual else expected
        if actual:
            integer(quantity, 1)
            if kind in ('buy', 'sell') and step != len(completed) + 1:
                raise ValueError('cycle_fill_step_out_of_order')
        elif kind in ('buy', 'sell') and step != len(completed) + 1:
            raise ValueError('cycle_scenario_step_out_of_order')
        if kind == 'buy':
            if quantity * price > self.cash:
                raise ValueError('cycle_insufficient_cash')
            self.cash -= quantity * price
            self.cost += quantity * price
            self.quantity += quantity
            self.buy_completed.append(step)
        else:
            if quantity > self.quantity:
                raise ValueError('cycle_insufficient_quantity')
            if actual and (kind == 'stop' or step == 3) and quantity != self.quantity:
                raise ValueError('cycle_final_exit_requires_all_remaining')
            if self.sell_basis is None:
                self.sell_basis = self.quantity
                self.cancelled = [i for i in range(1, len(self.plan['buy_steps']) + 1) if i not in self.buy_completed]
            average = self.cost / self.quantity if self.quantity else Decimal(0)
            sold_cost = self.cost if quantity == self.quantity else quantity * average
            self.realized += quantity * price - sold_cost
            self.cash += quantity * price
            self.cost -= sold_cost
            self.quantity -= quantity
            if kind == 'sell':
                self.sell_completed.append(step)
            if not self.quantity:
                self.cost = Decimal(0)
                self.closed = True
        self.mark = price
        self.ledger.append(dict(event=deepcopy(event), status='recorded' if actual else 'assumed',
                                quantity=quantity, price=money(price),
                                confirmation_price=definition['confirmation_price'],
                                planned_quantity=expected, deviation_quantity=quantity - expected,
                                state=self.state()))


def validate_fill(fill, plan):
    fields(fill, ('kind', 'step', 'quantity', 'price', 'at', 'note'), ('kind', 'quantity', 'price', 'at'))
    if fill['kind'] not in ('buy', 'sell', 'stop'):
        raise ValueError('cycle_invalid_fill_kind')
    integer(fill['quantity'], 1)
    number(fill['price'], positive=True)
    stamp(fill['at'])
    if 'note' in fill:
        text(fill['note'], nullable=True)
    if fill['kind'] == 'stop':
        if 'step' in fill:
            raise ValueError('cycle_unexpected_step')
    else:
        integer(fill.get('step'), 1, 3)
        if fill['step'] > len(plan[fill['kind'] + '_steps']):
            raise ValueError('cycle_unknown_step')
    return deepcopy(fill)


def _replay(plan, buy, sell, fills):
    ledger = _Ledger(plan, buy, sell)
    previous = None
    for fill in fills:
        fill = validate_fill(fill, plan)
        when = stamp(fill['at'])
        if previous is not None and when < previous:
            raise ValueError('cycle_fills_not_chronological')
        previous = when
        ledger.apply(fill, actual=True)
    return ledger


def _outcomes(plan, buy, sell, fills):
    out = {}
    for name in SCENARIOS:
        ledger = _replay(plan, buy, sell, fills)
        for event in plan['scenarios'][name]['events']:
            ledger.apply(event, projection=bool(fills))
        out[name] = dict(ledger.state(), ledger=ledger.ledger)
    return out


def calculate(plan, fills=None, adopted_combination_id=None):
    """Enumerate all 36 ratio pairs and all three explicitly ordered paths.

    Scenario labels do not imply probabilities. Missing assumptions stay visible;
    there is no price invention and no legacy 1.5%/6% risk filter.
    """
    plan = validate_plan(plan)
    fills = [] if fills is None else fills
    if not isinstance(fills, list) or len(fills) > 7:
        raise ValueError('cycle_invalid_fills')
    for fill in fills:
        validate_fill(fill, plan)
    if adopted_combination_id is not None:
        ratios_for(adopted_combination_id)
    missing = needs_input(plan)
    result = dict(contract_version=VERSION, needs_input=missing, combinations=[], representatives={},
                  combination_count=36, result_count=108, evaluated_result_count=0,
                  assumptions=['정수주·통화별 계산이며 수수료·세금·환율은 포함하지 않습니다.',
                               '매수는 최초 예산, 매도는 최초 매도 직전 수량 기준입니다. 3차 매도와 최종 무효화는 잔여 전량입니다.',
                               '상승·횡보·하락은 사용자 경로 가정이며 발생확률이나 매매 지시가 아닙니다.'])
    with localcontext() as context:
        context.prec = 50
        for buy in RATIOS:
            for sell in RATIOS:
                ident = combination_id(buy, sell)
                adoption_blockers = list(missing)
                if fills and adopted_combination_id:
                    adoption_blockers += ratio_blockers(adopted_combination_id, ident, fills)
                elif fills:
                    adoption_blockers.append('current_adopted_combination_required_for_adoption_check')
                outcome = None if missing else _outcomes(plan, buy, sell, fills)
                result['combinations'].append(dict(id=ident, buy_ratios=list(buy),
                                                   sell_ratios=list(sell), outcomes=outcome,
                                                   pareto=False, duplicate_of=None,
                                                   adoptable=None if fills and not adopted_combination_id else not adoption_blockers,
                                                   adoption_blockers=adoption_blockers))
        if missing:
            return result
        result['evaluated_result_count'] = 108
        unique, signatures = [], {}
        # An executable equivalent is the canonical duplicate when fills lock slots.
        for item in sorted(result['combinations'], key=lambda row: (row['adoptable'] is False, row['id'])):
            signature = tuple(Decimal(str(item['outcomes'][name][key])) for name in SCENARIOS
                              for key in ('cash', 'quantity', 'average_price', 'realized_pnl', 'unrealized_pnl'))
            if signature in signatures:
                item['duplicate_of'] = signatures[signature]
            else:
                signatures[signature] = item['id']
                unique.append(item)
        candidates = [item for item in unique if item['adoptable'] is not False]
        vectors = {item['id']: tuple(Decimal(item['outcomes'][name]['total_pnl']) for name in SCENARIOS) for item in candidates}
        for item in candidates:
            vector = vectors[item['id']]
            item['pareto'] = not any(all(a >= b for a, b in zip(other, vector)) and any(a > b for a, b in zip(other, vector))
                                     for ident, other in vectors.items() if ident != item['id'])
        frontier = [item for item in candidates if item['pareto']]
        upside = sorted(frontier, key=lambda item: (-vectors[item['id']][0], -min(vectors[item['id']]), item['id']))[0]
        defensive = sorted(frontier, key=lambda item: (-vectors[item['id']][2], -min(vectors[item['id']]), item['id']))[0]
        best = [max(v[i] for v in vectors.values()) for i in range(3)]
        worst = [min(v[i] for v in vectors.values()) for i in range(3)]
        def regret(item):
            v = vectors[item['id']]
            return max((best[i] - v[i]) / (best[i] - worst[i]) if best[i] != worst[i] else Decimal(0) for i in range(3))
        balanced = sorted(frontier, key=lambda item: (regret(item), -min(vectors[item['id']]), item['id']))[0]
        result['representatives'] = dict(upside=upside['id'], defensive=defensive['id'], balanced=balanced['id'])
        result['unique_count'] = len(unique)
        result['pareto_ids'] = [item['id'] for item in frontier]
        result['selection_explanation'] = dict(
            duplicates='세 경로의 현금·잔여수량·평단·실현/미실현손익이 같은 조합을 묶습니다. 원본 36개는 모두 유지합니다.',
            pareto='완료 단계 비율을 보존하는 채택 가능 조합 중 세 경로 손익이 모두 같거나 나쁘고 하나 이상 나쁜 조합은 대표 후보에서 제외합니다.',
            upside='파레토 후보 중 상승 경로 손익이 가장 큰 조합입니다.',
            defensive='파레토 후보 중 하락 경로 손익이 가장 큰 조합입니다.',
            balanced='각 경로의 최선·최악 범위로 환산한 최선 대비 손익 차이 중 가장 큰 값을 최소화합니다. 발생확률 가중치는 쓰지 않습니다.',
            tie_break='동률은 세 경로 중 최저 손익, 다음 조합 ID 순으로 고정합니다.')
        return result


def progress(plan, combination, fills=None):
    plan = validate_plan(plan)
    missing = needs_input(plan)
    if missing:
        raise ValueError('cycle_needs_input:' + ','.join(missing))
    buy, sell = ratios_for(combination)
    with localcontext() as context:
        context.prec = 50
        ledger = _replay(plan, buy, sell, fills or [])
        state = ledger.state()
        next_steps = []
        if not ledger.closed:
            candidates = []
            if ledger.sell_basis is None and len(ledger.buy_completed) < len(plan['buy_steps']):
                candidates.append(('buy', len(ledger.buy_completed) + 1))
            if ledger.quantity and len(ledger.sell_completed) < len(plan['sell_steps']):
                candidates.append(('sell', len(ledger.sell_completed) + 1))
            if ledger.quantity:
                candidates.append(('stop', None))
            for kind, step in candidates:
                definition = plan['stop'] if kind == 'stop' else plan[kind + '_steps'][step - 1]
                price = number(definition['assumed_fill_price'], positive=True)
                row = dict(deepcopy(definition), kind=kind, step=step,
                           ratio=None if kind == 'stop' else (buy if kind == 'buy' else sell)[step - 1],
                           planned_quantity=ledger.planned_quantity(kind, step, price),
                           quantity_basis='initial_budget' if kind == 'buy' else 'remaining_all' if kind == 'stop' or step == 3 else 'first_sell_quantity')
                next_steps.append(row)
        return dict(state=state, next_steps=next_steps, ledger=ledger.ledger)


class TradeCycleStore:
    """Immutable draft/event rows and a revisioned head in the analysis database."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS trade_cycles (
                id TEXT PRIMARY KEY, stock_key TEXT NOT NULL, revision INTEGER NOT NULL,
                draft_version INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trade_cycle_drafts (
                cycle_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                sha256 TEXT NOT NULL, PRIMARY KEY(cycle_id,version));
            CREATE TABLE IF NOT EXISTS trade_cycle_events (
                cycle_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                sha256 TEXT NOT NULL, PRIMARY KEY(cycle_id,sequence));
            ''')

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.execute('PRAGMA busy_timeout=15000')
        return db

    @staticmethod
    def _verified(payload, sha):
        value = json.loads(payload)
        if digest(value) != sha:
            raise ValueError('cycle_saved_record_integrity_failed')
        return value

    def _read(self, db, cycle_id):
        row = db.execute('SELECT stock_key,revision,draft_version,payload FROM trade_cycles WHERE id=?', (cycle_id,)).fetchone()
        if row is None:
            raise ValueError('cycle_not_found')
        head = json.loads(row[3])
        drafts = [self._verified(r[0], r[1]) for r in db.execute(
            'SELECT payload,sha256 FROM trade_cycle_drafts WHERE cycle_id=? ORDER BY version', (cycle_id,))]
        events = [self._verified(r[0], r[1]) for r in db.execute(
            'SELECT payload,sha256 FROM trade_cycle_events WHERE cycle_id=? ORDER BY sequence', (cycle_id,))]
        if not drafts or len(drafts) != row[2] or row[1] != len(drafts) + len(events):
            raise ValueError('cycle_saved_head_integrity_failed')
        adopted_events = [event for event in events if event['kind'] == 'adopt']
        fills = [event['fill'] for event in events if event['kind'] == 'fill']
        def adoption(event):
            version = event['draft_version']
            draft = drafts[version - 1]
            if draft['version'] != version or draft['cycle_id'] != cycle_id:
                raise ValueError('cycle_saved_draft_integrity_failed')
            return dict(deepcopy(draft), combination_id=event['combination_id'], adopted_at=event['recorded_at'])
        adopted = adoption(adopted_events[-1]) if adopted_events else None
        original = adoption(adopted_events[0]) if adopted_events else None
        result = dict(contract_version=VERSION, cycle_id=cycle_id, stock_key=row[0], revision=row[1],
                      draft_version=row[2], created_at=head['created_at'], draft=drafts[-1], drafts=drafts,
                      adopted=adopted, original_plan=original, fills=fills, events=events,
                      state=None, next_steps=[], original_outcomes=None, current_outcomes=None)
        if adopted:
            result.update(progress(adopted['plan'], adopted['combination_id'], fills))
            with localcontext() as context:
                context.prec = 50
                buy, sell = ratios_for(original['combination_id'])
                result['original_outcomes'] = _outcomes(original['plan'], buy, sell, [])
                buy, sell = ratios_for(adopted['combination_id'])
                result['current_outcomes'] = _outcomes(adopted['plan'], buy, sell, fills)
        return result

    def read(self, cycle_id):
        text(cycle_id, 100)
        with closing(self.connect()) as db:
            db.execute('BEGIN')
            return self._read(db, cycle_id)

    def list(self, stock_key=None):
        if stock_key is not None:
            text(stock_key, 100)
        with closing(self.connect()) as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT id FROM trade_cycles' + (' WHERE stock_key=?' if stock_key else '') + ' ORDER BY rowid DESC',
                              (stock_key,) if stock_key else ()).fetchall()
            return dict(contract_version=VERSION, cycles=[self._read(db, row[0]) for row in rows])

    def save_draft(self, stock_key, plan, cycle_id=None, expected_revision=0, source=None):
        text(stock_key, 100)
        if not re.fullmatch(r'(KR|US):[^\s:]{1,80}', stock_key):
            raise ValueError('cycle_invalid_stock_key')
        integer(expected_revision)
        plan = validate_plan(plan)
        if source is not None and (not isinstance(source, dict) or len(packed(source)) > 100000):
            raise ValueError('cycle_invalid_source')
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if cycle_id is None:
                if expected_revision != 0:
                    raise Conflict('cycle_revision_changed')
                if not needs_input(plan):
                    calculate(plan)
                cycle_id = 'cy_' + uuid4().hex
                version = 1
                db.execute('INSERT INTO trade_cycles VALUES(?,?,?,?,?)',
                           (cycle_id, stock_key, 1, 1, packed(dict(created_at=utc()))))
            else:
                current = self._read(db, cycle_id)
                if current['revision'] != expected_revision:
                    raise Conflict('cycle_revision_changed')
                if current['stock_key'] != stock_key:
                    raise ValueError('cycle_identity_changed')
                # A revised path can start at the next unfilled tranche. Replay
                # the immutable actual fills before checking its future events.
                if not needs_input(plan):
                    calculate(plan, current['fills'],
                              current['adopted']['combination_id'] if current['adopted'] else None)
                version = current['draft_version'] + 1
                db.execute('UPDATE trade_cycles SET revision=revision+1,draft_version=? WHERE id=?', (version, cycle_id))
            draft = dict(cycle_id=cycle_id, version=version, plan=plan, source=deepcopy(source), recorded_at=utc())
            db.execute('INSERT INTO trade_cycle_drafts VALUES(?,?,?,?)', (cycle_id, version, packed(draft), digest(draft)))
            return self._read(db, cycle_id)

    @staticmethod
    def _browser(principal):
        if principal != 'browser':
            raise Forbidden('local_browser_confirmation_required')

    def _event(self, db, cycle, event):
        sequence = len(cycle['events']) + 1
        value = dict(event, sequence=sequence, cycle_id=cycle['cycle_id'], recorded_at=utc())
        db.execute('INSERT INTO trade_cycle_events VALUES(?,?,?,?)', (cycle['cycle_id'], sequence, packed(value), digest(value)))
        db.execute('UPDATE trade_cycles SET revision=revision+1 WHERE id=?', (cycle['cycle_id'],))

    def adopt(self, cycle_id, draft_version, combination_id, expected_revision, principal=None):
        self._browser(principal)
        integer(draft_version, 1); integer(expected_revision)
        ratios_for(combination_id)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            cycle = self._read(db, cycle_id)
            if cycle['revision'] != expected_revision or cycle['draft_version'] != draft_version:
                raise Conflict('cycle_review_revision_changed')
            plan = cycle['draft']['plan']
            missing = needs_input(plan)
            if missing:
                raise ValueError('cycle_needs_input:' + ','.join(missing))
            if cycle['state'] and cycle['state']['phase'] == 'closed':
                raise ValueError('cycle_closed_create_new_cycle_for_reentry')
            if cycle['adopted']:
                old = cycle['adopted']['plan']
                if any(old.get(key) != plan.get(key) for key in ('currency', 'budget', 'initial_holding', 'account_alias')):
                    raise ValueError('cycle_initial_allocation_immutable')
                locked = ratio_blockers(cycle['adopted']['combination_id'], combination_id, cycle['fills'])
                if locked:
                    raise ValueError('cycle_completed_ratios_immutable:' + ','.join(locked))
            progress(plan, combination_id, cycle['fills'])
            calculate(plan, cycle['fills'])
            for row in db.execute('SELECT id FROM trade_cycles WHERE stock_key=? AND id<>?', (cycle['stock_key'], cycle_id)):
                other = self._read(db, row[0])
                if other['adopted'] and other['state']['phase'] != 'closed' and other['adopted']['plan'].get('account_alias') == plan.get('account_alias'):
                    raise Conflict('cycle_active_stock_account_exists')
            self._event(db, cycle, dict(kind='adopt', draft_version=draft_version, combination_id=combination_id))
            return self._read(db, cycle_id)

    def record_fill(self, cycle_id, fill, expected_revision, principal=None):
        self._browser(principal)
        integer(expected_revision)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            cycle = self._read(db, cycle_id)
            if cycle['revision'] != expected_revision:
                raise Conflict('cycle_revision_changed')
            if not cycle['adopted']:
                raise ValueError('cycle_not_adopted')
            plan = cycle['adopted']['plan']
            fill = validate_fill(fill, plan)
            new_fills = cycle['fills'] + [fill]
            progress(plan, cycle['adopted']['combination_id'], new_fills)
            self._event(db, cycle, dict(kind='fill', fill=fill))
            return self._read(db, cycle_id)

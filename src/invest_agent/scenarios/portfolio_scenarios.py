"""Portfolio what-if arithmetic, not a ledger, forecast, order, or adoption API.

Inputs are ephemeral projections from frozen shared records/account snapshots.
All money arithmetic is Decimal. Returned strings retain calculation precision.
"""
from copy import deepcopy
from decimal import Decimal
from invest_agent.scenarios.risk_values import number, instant, fresh, fingerprint
from invest_agent.scenarios.risk_conditions import evaluate

VERSION = 'portfolio-scenarios-v2'
ZERO = Decimal(0)


def _current_fx(snapshot, currency):
    base = snapshot['base_currency']
    current = snapshot['fx'][currency]
    before = number(current['rate'], positive=True)
    if currency == base and before != 1:
        raise ValueError('base_currency_fx_must_be_one')
    if not fresh(current['as_of'], snapshot['evaluated_at'], snapshot['max_age_seconds']):
        raise ValueError('fx_stale')
    return before


def _fx(snapshot, scenario, currency):
    before = _current_fx(snapshot, currency)
    after = number(scenario['fx_to_base'][currency], positive=True)
    if currency == snapshot['base_currency'] and after != 1:
        raise ValueError('base_currency_fx_must_be_one')
    return before, after


def _final_price(position, scenario):
    item = scenario.get('prices', {}).get(position['id'])
    if item is None:
        if scenario.get('common_change_pct') is None:
            raise ValueError('scenario_price_missing')
        item = {'change_pct': scenario['common_change_pct'], 'horizon': scenario['horizon']}
    if instant(item.get('horizon')) != instant(scenario['horizon']) and scenario.get('simultaneous_horizon') is not True:
        raise ValueError('scenario_horizon_mismatch')
    if (item.get('price') is None) == (item.get('change_pct') is None):
        raise ValueError('choose_price_or_change')
    if item.get('price') is not None:
        return number(item['price'])
    change = number(item['change_pct'], signed=True)
    if change < -1:
        raise ValueError('change_below_minus_one')
    return number(position['price']) * (1 + change)


def _summary(rows, issues, complete):
    included = [r for r in rows if r.get('before') is not None and r.get('after') is not None]
    before = sum((number(r['before'], signed=True) for r in included), ZERO)
    after = sum((number(r['after'], signed=True) for r in included), ZERO)
    full = bool(complete and not issues and len(included) == len(rows))
    return {'state': 'complete' if full else 'partial' if included else 'unavailable',
            'baseline_total': str(before) if full else None, 'total': str(after) if full else None,
            'known_baseline': str(before), 'known_subtotal': str(after),
            'change': str(after-before) if included else None,
            'change_pct': str((after-before)/before) if included and before > 0 else None,
            'included': [r['id'] for r in included], 'missing': issues, 'components': rows}


def compare(snapshot, scenario):
    """No IO. A missing component remains missing and cannot become zero risk."""
    evidence = {'calculator_version': VERSION, 'snapshot_id': snapshot.get('id'),
                'snapshot_hash': fingerprint(snapshot), 'scenario_hash': fingerprint(scenario),
                'scenario_id': scenario.get('id'), 'scenario_version': scenario.get('version'),
                'horizon': scenario.get('horizon'), 'base_currency': snapshot.get('base_currency'),
                'plan_refs': deepcopy(scenario.get('plan_refs', []))}
    issues = list(snapshot.get('issues', []))
    try:
        if not snapshot.get('id') or not snapshot.get('base_currency'):
            raise ValueError('snapshot_identity_required')
        if not scenario.get('id') or type(scenario.get('version')) is not int:
            raise ValueError('scenario_identity_required')
        if not fresh(snapshot['as_of'], snapshot['evaluated_at'], snapshot['max_age_seconds']):
            raise ValueError('account_snapshot_stale')
        if instant(scenario['horizon']) < instant(snapshot['evaluated_at']):
            raise ValueError('scenario_horizon_before_baseline')
        ids = [r['id'] for r in snapshot['positions'] + snapshot['cash']]
        if any(not x for x in ids) or len(ids) != len(set(ids)):
            raise ValueError('duplicate_component_identity')
        holdings = [(p.get('account'), p.get('market'), p.get('symbol')) for p in snapshot['positions']]
        if len(holdings) != len(set(holdings)):
            raise ValueError('duplicate_holding_identity')
        if snapshot.get('complete') is not True:
            issues.append('account_inventory_incomplete')
    except (ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'snapshot_or_horizon_missing'
        empty = _summary([], [reason], False)
        return {**evidence, 'baseline': empty, 'no_response': empty, 'response': {**empty, 'ledger': []}}
    rows, prepared = [], {}
    for p in snapshot['positions']:
        row = {'id': p['id'], 'kind': 'stock', 'account': p.get('account'),
               'symbol': p.get('symbol'), 'currency': p.get('currency'), 'before': None, 'after': None}
        try:
            if not p.get('account') or not p.get('source_ref') or not p.get('price_basis'):
                raise ValueError('position_provenance_missing')
            if not fresh(p['price_as_of'], snapshot['evaluated_at'], snapshot['max_age_seconds']):
                raise ValueError('price_stale')
            qty, price = number(p['quantity']), number(p['price'])
            before_fx = _current_fx(snapshot, p['currency'])
            row['before'] = str(qty*price*before_fx)
            _, after_fx = _fx(snapshot, scenario, p['currency'])
            final = _final_price(p, scenario)
            row.update(quantity=str(qty), final_price=str(final), before=str(qty*price*before_fx), after=str(qty*final*after_fx))
            prepared[p['id']] = {'position': p, 'quantity': qty, 'initial': qty, 'final': final,
                                 'before_fx': before_fx, 'after_fx': after_fx, 'before': qty*price*before_fx}
        except (ValueError, KeyError, TypeError) as exc:
            row['reason'] = str(exc) if isinstance(exc, ValueError) else 'position_inputs_missing'
            issues.append(p['id'] + ':' + row['reason'])
        rows.append(row)
    cash = {}
    for c in snapshot['cash']:
        row = {'id': c['id'], 'kind': c.get('kind'), 'currency': c.get('currency'), 'account': c.get('account'),
               'before': None, 'after': None}
        try:
            if c.get('disjoint') is not True or not c.get('source_ref'):
                raise ValueError('cash_overlap_or_provenance_unverified')
            if c.get('kind') not in ('cash', 'cma', 'liability'):
                raise ValueError('cash_classification_unconfirmed')
            amount = number(c['amount']) * (-1 if c['kind'] == 'liability' else 1)
            before_fx = _current_fx(snapshot, c['currency'])
            row['before'] = str(amount*before_fx)
            _, after_fx = _fx(snapshot, scenario, c['currency'])
            row.update(amount=str(amount), before=str(amount*before_fx), after=str(amount*after_fx))
            if c['kind'] == 'cash':
                key = (c['account'], c['currency'])
                cash[key] = cash.get(key, ZERO) + amount
        except (ValueError, KeyError, TypeError) as exc:
            row['reason'] = str(exc) if isinstance(exc, ValueError) else 'cash_inputs_missing'
            issues.append(c['id'] + ':' + row['reason'])
        rows.append(row)
    no_response = _summary(rows, issues, snapshot.get('complete'))
    response = _respond(snapshot, scenario, prepared, cash, rows, issues)
    baseline_rows = [{**r, 'after': r['before']} for r in rows]
    baseline_issues = list(snapshot.get('issues', [])) + [r['id'] + ':' + r['reason'] for r in rows if r['before'] is None]
    baseline = _summary(baseline_rows, baseline_issues, snapshot.get('complete'))
    return {**evidence, 'baseline': baseline, 'no_response': no_response, 'response': response}


def _respond(snapshot, scenario, prepared, cash, rows, issues):
    rows = deepcopy(rows)
    quantities = {key: p['quantity'] for key, p in prepared.items()}
    starting_cash, deltas, ledger = dict(cash), {}, []
    response = scenario.get('response')
    try:
        if not isinstance(response, dict) or not isinstance(response.get('steps'), list):
            raise ValueError('response_path_and_steps_required')
        if response.get('blockers'):
            raise ValueError('shared_plan_inputs_incomplete:' + ';'.join(response['blockers']))
        if response.get('cost_policy') not in ('excluded', 'explicit', 'record_total'):
            raise ValueError('cost_assumption_required')
        path = response.get('path')
        if not isinstance(path, list):
            raise ValueError('price_path_required')
        nodes = {p['id']: p for p in path}
        if len(nodes) != len(path):
            raise ValueError('duplicate_path_step')
        times = [instant(p['at']) for p in path]
        if times != sorted(times):
            raise ValueError('price_path_order_required')
        horizon, baseline = instant(scenario['horizon']), instant(snapshot['evaluated_at'])
        if any(t < baseline or t > horizon for t in times):
            raise ValueError('path_outside_scenario_period')
        steps = response['steps']
        if len({s['id'] for s in steps}) != len(steps):
            raise ValueError('duplicate_response_step')
        refs = {(p['id'], p['version']) for p in scenario.get('plan_refs', [])}
        allocations, last_times, last_cash_times = {}, {}, {}
        for step in steps:
            plan = step['plan_ref']
            if (plan['id'], plan['version']) not in refs:
                raise ValueError('response_plan_version_mismatch')
            pid = step['position_id']
            if pid not in prepared:
                raise ValueError('response_position_unavailable')
            p = prepared[pid]
            group = (pid, plan['id'], plan['version'])
            allocated = number(step['allocated_quantity'])
            if group in allocations and allocations[group] != allocated:
                raise ValueError('allocation_changed_within_plan')
            allocations[group] = allocated
            if sum((v for k, v in allocations.items() if k[0] == pid), ZERO) > p['initial']:
                raise ValueError('purpose_allocations_overlap')
            node = nodes[step['path_id']]
            at = instant(node['at'])
            if at < last_times.get(pid, baseline):
                raise ValueError('response_order_or_fill_overlap')
            observation = deepcopy(node['observations'][pid])
            observation['evaluated_at'] = node['at']
            if step['condition'].get('kind') == 'drawing' and step['condition']['drawing'].get('chart'):
                # Evaluate the exact frozen A chart, never a replacement supplied by a caller.
                observation['chart'] = deepcopy(step['condition']['drawing']['chart'])
                observation['drawing_revision'] = step['condition']['drawing']['revision']
            if instant(observation['as_of']) != at:
                raise ValueError('path_observation_time_mismatch')
            if observation.get('currency') != p['position']['currency'] or observation.get('price_basis') != p['position']['price_basis']:
                raise ValueError('position_condition_basis_mismatch')
            observed = evaluate(step['condition'], observation)
            if observed['matched'] is None:
                raise ValueError('response_condition_deferred:' + observed['reason'])
            if not observed['matched']:
                ledger.append({'step_id': step['id'], 'state': 'not_triggered', 'plan_ref': plan})
                last_times[pid] = at
                continue
            fill = step['fill']
            fill_at = instant(fill['at'])
            if fill_at < at or fill_at > horizon:
                raise ValueError('fill_time_outside_condition_and_horizon')
            if fill.get('currency') != p['position']['currency']:
                raise ValueError('fill_currency_mismatch')
            if not fill.get('assumption'):
                raise ValueError('execution_assumption_required')
            if fill.get('price') is None:
                raise ValueError('fill_price_required')
            fill_price = number(fill['price'], positive=True)
            fraction = step['quantity']
            value = number(fraction['value'])
            prior_sold = sum((number(x.get('quantity', '0')) * (1 if x.get('side') == 'sell' else -1)
                              for x in ledger if x.get('allocation_key') == list(group)), ZERO)
            remaining = allocated - prior_sold
            if fraction['basis'] == 'shares':
                qty = value
            elif fraction['basis'] in ('initial', 'remaining') and value <= 1:
                qty = (allocated if fraction['basis'] == 'initial' else remaining) * value
            else:
                raise ValueError('quantity_basis_or_fraction_invalid')
            if qty <= 0:
                raise ValueError('positive_response_quantity_required')
            fee = number(fill['fee']) if response['cost_policy'] == 'explicit' else ZERO
            key = (p['position']['account'], p['position']['currency'])
            # Different holdings share this cash balance. Never fund an earlier
            # fill with proceeds from a later fill already processed in this list.
            if fill_at < last_cash_times.get(key, baseline):
                raise ValueError('cash_flow_fill_order_or_overlap')
            if step['side'] == 'sell':
                if qty > quantities[pid] or qty > remaining:
                    raise ValueError('quantity_oversold')
                if fee > qty*fill_price:
                    raise ValueError('fee_exceeds_proceeds')
                quantities[pid] -= qty
                delta = qty*fill_price-fee
            elif step['side'] == 'buy':
                if snapshot.get('complete') is not True or issues:
                    raise ValueError('buy_cash_inventory_unconfirmed')
                delta = -(qty*fill_price+fee)
                if cash.get(key, ZERO)+delta < 0:
                    raise ValueError('insufficient_scenario_cash')
                quantities[pid] += qty
            else:
                raise ValueError('unsupported_response_action')
            cash[key] = cash.get(key, ZERO)+delta
            deltas[key] = deltas.get(key, ZERO)+delta
            ledger.append({'step_id': step['id'], 'state': 'assumed_fill', 'plan_ref': plan, 'allocation_key': list(group),
                           'side': step['side'], 'quantity': str(qty), 'condition_price': observation['price'],
                           'fill_price': str(fill_price), 'fill_at': fill['at'], 'cash_delta': str(delta), 'fee': str(fee)})
            last_times[pid] = fill_at
            last_cash_times[key] = fill_at
        if response['cost_policy'] == 'record_total':
            costs = response.get('total_costs', [])
            if len({c['id'] for c in costs}) != len(costs):
                raise ValueError('duplicate_scenario_total_cost')
            for cost in costs:
                amount = number(cost['amount'])
                key = (cost['account'], cost['currency'])
                if cash.get(key, ZERO) < amount:
                    raise ValueError('insufficient_cash_for_total_cost')
                cash[key] = cash.get(key, ZERO) - amount
                deltas[key] = deltas.get(key, ZERO) - amount
                ledger.append({'state': 'scenario_total_cost', 'source_ref': cost['id'], 'fee': str(amount), 'currency': cost['currency']})
        for row in rows:
            if row['id'] in prepared:
                p = prepared[row['id']]
                row['quantity'] = str(quantities[row['id']])
                row['after'] = str(quantities[row['id']]*p['final']*p['after_fx'])
        for (account, currency), delta in deltas.items():
            _, rate = _fx(snapshot, scenario, currency)
            rows.append({'id': 'response_cash/'+account+'/'+currency, 'kind': 'response_cash', 'account': account,
                         'currency': currency, 'amount': str(delta), 'before': '0', 'after': str(delta*rate)})
        result = _summary(rows, list(issues), snapshot.get('complete'))
        result.update(ledger=ledger, cost_policy=response['cost_policy'],
                      remaining_quantities={k: str(v) for k, v in quantities.items()},
                      cash_changes=[{'account': k[0], 'currency': k[1], 'before': str(starting_cash.get(k, ZERO)),
                                     'after': str(cash[k]), 'delta': str(v)} for k, v in deltas.items()])
        return result
    except (ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'response_input_missing'
        return {**_summary([], [reason] + list(issues), False), 'state': 'deferred', 'ledger': [],
                'cost_policy': response.get('cost_policy') if isinstance(response, dict) else None}

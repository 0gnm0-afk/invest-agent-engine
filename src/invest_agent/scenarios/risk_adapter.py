"""Adapter for caller-supplied collaboration.v1 records; no authentication implementation."""
from copy import deepcopy
import hashlib
import json
from invest_agent.scenarios.risk_values import fingerprint, number, instant

CONTRACT = 'collaboration.v1'
FRAMES = {'intraday': 'intraday', 'daily_close': '1d', 'weekly_close': '1w'}
BASES = {'units': 'shares', 'initial_fraction': 'initial', 'remaining_fraction': 'remaining'}


def shared_hash(value):
    # A's existing analysis_service.digest encoding, not B's compact input hash.
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_plan(rpc, analysis_key, record_id, *, mode, evaluation_at=None):
    args = {'analysis_key': analysis_key, 'plan_record_id': record_id, 'mode': mode}
    if evaluation_at is not None:
        args['evaluation_at'] = evaluation_at
    value = rpc('plan_input', args)
    validate_input(value)
    return value


def validate_input(value):
    if value.get('contract_version') != CONTRACT:
        raise ValueError('unsupported_collaboration_contract')
    record = value['plan_record']
    if record.get('kind') != 'plan_version' or record.get('case_id') != value.get('analysis_key'):
        raise ValueError('plan_record_identity_mismatch')
    if (record['plan_id'], record['plan_version']) != (value['plan_id'], value['plan_version']):
        raise ValueError('plan_version_mismatch')
    if shared_hash(record['plan']) != value['plan_sha256'] or record['plan_sha256'] != value['plan_sha256']:
        raise ValueError('plan_hash_mismatch')
    if shared_hash(value['snapshot']) != value['snapshot_sha256'] or record['snapshot_sha256'] != value['snapshot_sha256']:
        raise ValueError('snapshot_hash_mismatch')
    if record['snapshot_id'] != value['snapshot_id'] or value['snapshot']['id'] != value['snapshot_id']:
        raise ValueError('snapshot_identity_mismatch')
    if value.get('mode') not in ('adopted', 'hypothetical'):
        raise ValueError('explicit_input_mode_required')
    if value['mode'] == 'adopted':
        adoption = value.get('adoption') or {}
        if adoption.get('action') != 'adopt' or adoption.get('plan_record_id') != record['id'] or adoption.get('plan_version') != record['plan_version']:
            raise ValueError('exact_adoption_required')
    # These hashes detect corruption. Authentication remains A's trusted RPC boundary.


def project_plan(value, snapshot, *, scenario_id, assumptions):
    """Explicit account assumptions complete a calculation, never mutate the plan.

    assumptions: horizon, path_observations keyed by ISO date, fills keyed by
    condition ID. No date-only path is silently treated as an executable close.
    """
    validate_input(value)
    r, plan = value['plan_record'], value['plan_record']['plan']
    identity = r['identity']
    ref = {'id': r['plan_id'], 'version': r['plan_version'], 'record_id': r['id'],
           'analysis_key': value['analysis_key'], 'plan_hash': value['plan_sha256'],
           'snapshot_id': value['snapshot_id'], 'snapshot_hash': value['snapshot_sha256'], 'mode': value['mode']}
    result = {'ref': ref, 'position_id': None, 'conditions': [], 'price': None, 'path': [], 'steps': [],
              'blockers': [], 'response_blockers': [], 'record_blockers': deepcopy(value.get('blockers', [])),
              'assumptions_hash': fingerprint(assumptions), 'response_mode': None}
    result['limitations'] = deepcopy(value.get('limitations', []))
    for drawing in value.get('drawing_evaluations', []):
        result['limitations'].extend(drawing.get('basis', {}).get('limitations', []))
    stable = identity.get('stable_id')
    candidates = [p for p in snapshot['positions'] if stable and p.get('stable_id') == stable and
                  p.get('account') == plan.get('account_alias') and p.get('market') == identity.get('market') and
                  p.get('currency') == identity.get('currency')]
    if len(candidates) != 1:
        result['blockers'].append('account_or_stable_instrument_link_missing_or_ambiguous')
        return result
    p = candidates[0]
    result['position_id'] = p['id']
    scenarios = [s for s in plan['scenarios'] if s['id'] == scenario_id]
    if len(scenarios) != 1:
        result['blockers'].append('explicit_scenario_selection_required')
        return result
    s = scenarios[0]
    result['response_mode'] = s['response']
    try:
        horizon = assumptions['horizon']
        if instant(horizon).date().isoformat() != s['evaluation_at'] and assumptions.get('simultaneous_horizon') is not True:
            raise ValueError('scenario_horizon_mismatch')
        if not s.get('evaluation_at'):
            raise ValueError('scenario_evaluation_date_missing')
        if s.get('currency') != p['currency']:
            raise ValueError('scenario_currency_mismatch')
        number(s.get('assumed_price'))
        result['price'] = {'price': s['assumed_price'], 'horizon': horizon, 'source_ref': r['id'],
                           'source_evaluation_date': s['evaluation_at']}
    except (ValueError, KeyError, TypeError) as exc:
        result['blockers'].append(str(exc) if isinstance(exc, ValueError) else 'scenario_horizon_required')
    for c in plan['conditions']:
        condition = {'id': c['id'], 'kind': c['kind'], 'threshold': c['price'], 'operator': c['operator'],
                     'currency': c['currency'], 'frame': FRAMES.get(c['evaluation']),
                     'price_basis': identity.get('price_basis'), 'statement': c['statement'], 'action': c['action']}
        if c.get('drawing_ref'):
            drawing = c['drawing_ref']
            condition.update(kind='drawing', drawing={'id': drawing['drawing_id'], 'revision': drawing['workspace_revision'],
                'geometry_version': drawing['geometry_version'], 'workspace': deepcopy(value['snapshot']['workspace']),
                'price_scale': value['snapshot'].get('context', {}).get('price_scale'),
                'chart': deepcopy(value['snapshot']['chart']),
                'edge': {'line': 'line_price', 'other_line': 'other_line_price'}.get(drawing['line'])})
            condition['price_basis'] = drawing['price_basis']
        result['conditions'].append(condition)
    if s['response'] == 'none':
        return result
    if s['response'] != 'plan':
        result['response_blockers'].append('scenario_response_mode_required')
        return result
    allocation = plan['allocation']
    if allocation['basis'] not in ('units', 'initial', 'current_remaining') or allocation['quantity'] is None:
        result['response_blockers'].append('allocation_basis_required')
    resolved_basis = assumptions.get('allocation_basis') if allocation['basis'] == 'units' else allocation['basis']
    if any(c['quantity']['basis'] in ('initial_fraction', 'remaining_fraction') for c in plan['conditions']) and resolved_basis not in ('initial', 'current_remaining'):
        result['response_blockers'].append('allocation_lifecycle_assumption_required')
    if resolved_basis == 'current_remaining' and any(c['quantity']['basis'] == 'initial_fraction' for c in plan['conditions']):
        result['response_blockers'].append('initial_allocation_unknown_for_remaining_basis')
    if assumptions.get('cost_policy') == 'record_total':
        if s.get('cost') is None:
            result['response_blockers'].append('scenario_total_cost_required')
        else:
            result['total_cost'] = {'id': r['id'], 'account': p['account'], 'currency': s['currency'], 'amount': s['cost']}
    if not s['price_path']:
        result['response_blockers'].append('declared_price_path_required')
        return result
    for point in s['price_path']:
        supplied = assumptions.get('path_observations', {}).get(point['at'])
        if not supplied:
            result['response_blockers'].append('path_timing_and_observation_required:' + point['at'])
            continue
        observation = deepcopy(supplied)
        if observation.get('price') is not None and number(observation['price']) != number(point['price']):
            result['response_blockers'].append('path_price_conflicts_with_record:' + point['at'])
            continue
        observation['price'] = point['price']
        result['path'].append({'id': r['id']+'/'+point['at'], 'at': observation.get('as_of'),
                               'observations': {p['id']: observation}})
    for c, condition in zip(plan['conditions'], result['conditions']):
        if c['action'] in ('review', 'hold'):
            continue
        if c['kind'] == 'qualitative':
            result['response_blockers'].append('qualitative_trade_condition:' + c['id'])
            continue
        fill = assumptions.get('fills', {}).get(c['id'])
        if not fill or not fill.get('path_date') or c['execution_price'] is None:
            result['response_blockers'].append('fill_and_path_assumption_required:' + c['id'])
            continue
        if c['priority'] is None:
            result['response_blockers'].append('condition_order_required:' + c['id'])
            continue
        if fill.get('price') is not None and number(fill['price']) != number(c['execution_price']):
            result['response_blockers'].append('fill_price_conflicts_with_record:' + c['id'])
            continue
        result['steps'].append({'id': r['id']+'/'+c['id'], 'position_id': p['id'], 'plan_ref': ref,
          'priority': c['priority'], 'allocated_quantity': allocation['quantity'], 'path_id': r['id']+'/'+fill['path_date'],
          'side': 'sell' if c['action'] == 'reduce' else 'buy', 'condition': condition,
          'quantity': {'basis': BASES.get(c['quantity']['basis']), 'value': c['quantity']['value']},
          'fill': {**deepcopy(fill), 'price': c['execution_price'], 'currency': c['currency']}})
    return result


def save_review(rpc, context, result, assumptions, *, title, body, related_contexts=()):
    """Explicit save only. Conflicts propagate to reread; never silently retry."""
    validate_input(context)
    if (fingerprint(assumptions.get('account_snapshot')) != result.get('snapshot_hash') or
            fingerprint(assumptions.get('scenario')) != result.get('scenario_hash')):
        raise ValueError('frozen_calculation_inputs_required')
    record = context['plan_record']
    contexts = [context, *related_contexts]
    for other in contexts:
        validate_input(other)
        if other['analysis_key'] != context['analysis_key']:
            raise ValueError('review_context_case_mismatch')
    args = {'contract_version': CONTRACT, 'analysis_key': context['analysis_key'],
            'snapshot_id': context['snapshot_id'], 'expected_revision': assumptions['expected_revision'],
            'title': title, 'body': body, 'plan_record_ids': [c['plan_record']['id'] for c in contexts],
            'calculation': {'code_version': result['calculator_version'],
                            'input_snapshot_ids': list(dict.fromkeys(c['snapshot_id'] for c in contexts)),
                            'assumptions': deepcopy(assumptions), 'result': deepcopy(result)},
            'references': [{'kind': 'record', 'reference': record['id'], 'text': None, 'speaker': 'source'}]}
    saved = rpc('risk_review_save', args)
    if saved.get('contract_version') != CONTRACT or saved.get('verified') is not True or not saved.get('record', {}).get('id'):
        raise ValueError('risk_review_save_not_verified')
    reread = rpc('collaboration_read', {'analysis_key': context['analysis_key']})
    if not any(r == saved['record'] for r in reread.get('records', [])):
        raise ValueError('risk_review_reread_missing')
    return saved


def assemble(projections, assumptions):
    """Combine explicitly selected alternatives; never sum two alternatives for one holding."""
    prices, refs, paths, steps, blockers, seen = {}, [], [], [], [], set()
    for p in projections:
        refs.append(p['ref'])
        blockers.extend(p['blockers'] + p['response_blockers'])
        pid = p['position_id']
        if pid in seen and assumptions.get('combine_allocations') is not True:
            raise ValueError('select_one_scenario_per_holding')
        if pid:
            seen.add(pid)
        if p['price'] is not None and pid:
            if pid in prices and (prices[pid]['price'], prices[pid]['horizon']) != (p['price']['price'], p['price']['horizon']):
                raise ValueError('conflicting_final_prices_for_same_holding')
            prices[pid] = p['price']
        paths.extend(p['path'])
        steps.extend(p['steps'])
    paths.sort(key=lambda p: instant(p['at']))
    times = {p['id']: instant(p['at']) for p in paths}
    steps.sort(key=lambda s: (times.get(s['path_id'], instant(assumptions['horizon'])), s['priority']))
    return {'id': assumptions['id'], 'version': assumptions['version'], 'horizon': assumptions['horizon'],
            'fx_to_base': deepcopy(assumptions.get('fx_to_base', {})), 'prices': prices, 'plan_refs': refs,
            'response': {'path': paths, 'steps': steps, 'blockers': blockers, 'cost_policy': assumptions.get('cost_policy'),
                         'total_costs': [p['total_cost'] for p in projections if p.get('total_cost')]}}


def account_projection(bundle, *, evaluated_at):
    """Consume an already loaded normalized broker snapshot. Never call a provider.

    Unreconciled native cash/CMA observations and absent stable IDs stay visible
    but cannot be silently turned into investable cash or a matched plan.
    """
    if bundle.get('schema_version') != 1 or bundle.get('source') not in ('synthetic', 'broker_export'):
        raise ValueError('unsupported_saved_account_snapshot')
    source_hash = shared_hash(bundle)
    result = {'id': source_hash, 'as_of': bundle.get('as_of'), 'evaluated_at': evaluated_at,
              'max_age_seconds': str(number(bundle.get('max_age_hours'), positive=True)*3600),
              'base_currency': bundle.get('base_currency'), 'complete': bundle.get('accounts_complete') is True,
              'issues': [], 'positions': [], 'cash': [], 'fx': {}}
    for currency, rate in bundle.get('fx_to_base', {}).items():
        result['fx'][currency] = {'rate': rate, 'as_of': (bundle.get('fx_observation') or {}).get('as_of')
                                  if currency != bundle.get('base_currency') else bundle.get('as_of')}
    seen = set()
    for a in bundle['accounts']:
        alias = a['alias']
        if not alias or alias in seen:
            raise ValueError('duplicate_account_alias')
        seen.add(alias)
        for field in ('holdings_complete', 'cash_complete', 'liabilities_complete'):
            if a.get(field) is not True:
                result['complete'] = False
                result['issues'].append(alias + ':' + field + '_unconfirmed')
        disjoint = a.get('cash_reconciliation', {}).get('cash_excludes_cash_equivalents') is True
        for currency, amount in a.get('cash', {}).items():
            result['cash'].append({'id': alias+'/cash/'+currency, 'account': alias, 'currency': currency, 'amount': amount,
                                   'kind': 'cash', 'disjoint': disjoint, 'source_ref': source_hash})
        for currency, amount in a.get('liabilities', {}).items():
            result['cash'].append({'id': alias+'/liability/'+currency, 'account': alias, 'currency': currency,
                                   'amount': amount, 'kind': 'liability', 'disjoint': True, 'source_ref': source_hash})
        for p in a['positions']:
            pid = alias+'/'+str(p.get('market'))+'/'+p['symbol']
            # Broker's explicit CMA marker; no ticker/name heuristic reclassification.
            if p.get('instrument_class_hint') == 'CMA_note_from_broker_label':
                result['cash'].append({'id': pid, 'account': alias, 'currency': 'KRW', 'kind': 'cma',
                                      'amount': p.get('observed_evaluation_krw'), 'disjoint': disjoint, 'source_ref': source_hash})
                continue
            result['positions'].append({'id': pid, 'account': alias, 'market': p.get('market'),
              'symbol': p['symbol'], 'stable_id': p.get('isin_code') or p.get('isin'), 'currency': p.get('currency'),
              'quantity': p.get('quantity'), 'price': None if p.get('data_unavailable_reason') else p.get('price'),
              'price_basis': p.get('price_basis'), 'price_as_of': p.get('price_as_of') or bundle.get('as_of'),
              'source_ref': source_hash})
    return result

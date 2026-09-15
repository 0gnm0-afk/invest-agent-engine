"""Reconcile observed holdings into the existing frozen policy, never adopt stops."""
from copy import deepcopy
from datetime import datetime

from .contracts import digest
from .portfolio import number
from .risk_budget import equity_snapshot


def plan_fills(plan: dict) -> list[dict]:
    """Include initial reductions and archived revisions without rewriting them."""
    fills: list[dict] = []
    versions = [plan]
    while versions:
        version = versions.pop()
        versions.extend(version.get('history', []))
        tranches = [version.get('initial_reduction', {}), *version.get('tranches', [])]
        fills.extend(fill for tranche in tranches for fill in tranche.get('actual_fills', []))
    return fills


def account_equity(group: dict, bundle: dict, expected_sessions: dict, evaluated_at: str) -> dict | None:
    """Adapt explicit broker equity; residual assets never imply buying power."""
    allocation = bundle.get('broker_allocation', {})
    allocation_ok = (allocation.get('state') == 'available' and allocation.get('currency') == 'KRW'
                     and allocation.get('checks') == 'exact_component_sums_zero_debt_no_foreign_unsettled'
                     and allocation.get('confirmation_hash') and allocation.get('as_of') == bundle.get('as_of'))
    if not expected_sessions or not bundle.get('as_of'):
        return None
    rows = []
    any_equity = False
    for account in bundle.get('accounts', []):
        if account.get('alias') not in group['included_account_ids']:
            continue
        row = {'account_id': account['alias'], 'currency': account.get('equity_currency', bundle.get('base_currency', 'KRW')),
               'source': account.get('source_ref', bundle.get('source')), 'data_as_of': account.get('as_of', bundle['as_of']),
               'expected_session': max(expected_sessions.values()), 'cash_complete': account.get('cash_complete') is True,
               'available_cash': account.get('available_cash')}
        if account.get('net_liquidation_value') is not None:
            row['net_liquidation_value'] = account['net_liquidation_value']
            any_equity = True
        elif account.get('equity_components'):
            row['equity_components'] = account['equity_components']
            any_equity = True
        elif allocation_ok:
            parts = [p for p in allocation.get('rows', []) if p.get('account') == account['alias']]
            if parts and all(p.get('kind') in {'broker_holding', 'residual_assets_not_buying_power'} for p in parts):
                row['net_liquidation_value'] = str(sum(number(p['value_krw']) for p in parts))
                row['currency'] = 'KRW'
                row['source'] = 'NH verified nonoverlapping net assets:' + allocation['confirmation_hash']
                any_equity = True
        rows.append(row)
    if not any_equity:
        return None
    fx = {}
    evidence = bundle.get('fx_observation', {})
    if evidence.get('as_of') and evidence.get('basis'):
        fx = {ccy: {'fx_rate': rate, 'fx_source': evidence['basis'], 'fx_as_of': evidence['as_of']}
              for ccy, rate in bundle.get('fx_to_base', {}).items() if ccy != 'KRW'}
    raw = {'equity_snapshot_id': 'account-equity:' + digest([group, bundle, expected_sessions])[:32],
           'snapshot_type': bundle.get('snapshot_type', 'INTRADAY_PROVISIONAL'),
           'data_as_of': bundle['as_of'], 'created_at': evaluated_at, 'expected_sessions': expected_sessions,
           'accounts': rows, 'fx': fx}
    return equity_snapshot(group, raw)


def reconcile(frozen: dict, account_input: dict, evaluated_at: str, expected_sessions: dict | None = None) -> dict:
    result = deepcopy(frozen)
    bundle = account_input.get('payload') or {}
    accounts = bundle.get('accounts', [])
    reports = []
    for group in result.get('strategy', []):
        gid = group['strategy_group_id']
        errors = []
        changed = []
        for alias in group['included_account_ids']:
            matches = [a for a in accounts if a.get('alias') == alias]
            if account_input.get('state') != 'loaded' or len(matches) != 1:
                errors.append('ACCOUNT_MISSING_OR_DUPLICATE:' + alias)
                continue
            account = matches[0]
            as_of = account.get('as_of', bundle.get('as_of'))
            try:
                stamp = datetime.fromisoformat(as_of)
                current = datetime.fromisoformat(evaluated_at)
                if stamp.tzinfo is None or stamp > current:
                    raise ValueError('stale')
                if expected_sessions:
                    if stamp.date().isoformat() < max(expected_sessions.values()):
                        raise ValueError('Account predates latest completed session')
                elif (current-stamp).total_seconds() > float(number(bundle['max_age_hours'], positive=True))*3600:
                    raise ValueError('stale')
            except (ValueError, TypeError, KeyError):
                errors.append('STALE_ACCOUNT_DATA:' + alias)
                continue
            if bundle.get('source') not in {'broker_export', 'user_input', 'synthetic'}:
                errors.append('ACCOUNT_SOURCE_UNAVAILABLE:' + alias)
                continue
            owned = [p for p in result.get('position', []) if p.get('strategy_group_id') == gid and p['account_alias'] == alias]
            rows = deepcopy(account.get('positions', []))
            keys = [(r.get('market'), r.get('symbol')) for r in rows]
            if len(keys) != len(set(keys)):
                errors.append('DUPLICATE_HOLDINGS:' + alias)
                continue
            if account.get('holdings_complete') is True and all(
                r.get('market') in {'KR', 'US'} and r.get('symbol') and not r.get('data_unavailable_reason') for r in rows
            ):
                # A complete broker/user balance confirms an absent holding is
                # zero; it is not an inferred fill at any plan price.
                rows.extend({'market': p['market'], 'symbol': p['symbol'], 'currency': p['currency'],
                             'quantity': '0', 'average_cost': p['average_cost'], 'absence_confirmed': True}
                            for p in owned if number(p['current_quantity']) > 0 and (p['market'], p['symbol']) not in keys)
            observed = set()
            for row in rows:
                key = (row.get('market'), row.get('symbol'))
                observed.add(key)
                try:
                    if row.get('data_unavailable_reason') or key[0] not in {'KR', 'US'} or not key[1] or not row.get('currency'):
                        raise ValueError('Unusable holding')
                    quantity, cost = number(row['quantity']), number(row['average_cost'])
                    targets = [p for p in owned if (p['market'], p['symbol']) == key and number(p['current_quantity']) > 0]
                    if len(targets) > 1:
                        raise ValueError('Ambiguous position')
                    position = targets[0] if targets else None
                    if position and ((bundle['source'] == 'synthetic') != (position.get('source') == 'synthetic')):
                        raise ValueError('Source mismatch')
                    # Closed lifecycles count too: old balances cannot recreate sold holdings.
                    related = [p for p in owned if (p['market'], p['symbol']) == key]
                    ids = {p['position_id'] for p in related}
                    fills = [f for kind in ('buy_plan', 'sell_plan') for plan in result.get(kind, [])
                             if plan.get('position_id') in ids for f in plan_fills(plan)]
                    if any(datetime.fromisoformat(f['actual_at']) > stamp for f in fills):
                        raise ValueError('Account predates fill')
                    if any(p.get('account_data_as_of') and datetime.fromisoformat(p['account_data_as_of']) > stamp
                           for p in related):
                        raise ValueError('Account predates reconciliation')
                    if position is None:
                        if quantity == 0:
                            continue
                        position = {'position_id': 'account:' + digest([gid, alias, *key, as_of])[:24],
                                    'strategy_group_id': gid, 'account_alias': alias, 'market': key[0], 'symbol': key[1],
                                    'currency': row['currency'], 'source': bundle['source'],
                                    'current_quantity': '0', 'average_cost': '0',
                                    'initial_stop_price': None, 'current_protection_price': None, 'trigger_mode': None}
                        result.setdefault('position', []).append(position)
                    if position['currency'] != row['currency']:
                        raise ValueError('Currency mismatch')
                    if number(position['current_quantity']) != quantity or number(position['average_cost']) != cost:
                        changed.append(position['position_id'])
                    position.update(current_quantity=str(quantity), average_cost=str(cost), account_data_as_of=as_of,
                                    reconciliation_source_ref=account.get('source_ref') or bundle['source'])
                    if row.get('absence_confirmed'):
                        position['closure_evidence'] = 'COMPLETE_ACCOUNT_BALANCE'
                    if row.get('name'):
                        position['name'] = row['name']
                except (ValueError, KeyError, TypeError):
                    errors.append('HOLDING_RECONCILIATION_REQUIRED:' + alias + ':' + str(key[1]))
            # Partial/ambiguous exports cannot establish zero inventory.
            for position in owned:
                if number(position['current_quantity']) > 0 and (position['market'], position['symbol']) not in observed:
                    errors.append('HOLDING_MISSING:' + position['position_id'])
            if account.get('holdings_complete') is not True:
                errors.append('HOLDINGS_INCOMPLETE:' + alias)
        for kind in ('buy_plan', 'sell_plan'):
            for plan in result.get(kind, []):
                if plan.get('position_id') in changed and plan['status'] not in {'CANCELLED', 'COMPLETED'}:
                    plan.update(status='NEEDS_REAPPROVAL', reapproval_reason='ACCOUNT_INVENTORY_CHANGED')
        state = next((s for s in result.get('strategy_state', []) if s['strategy_group_id'] == gid), None)
        if state is None:
            state = {'strategy_group_id': gid}
            result.setdefault('strategy_state', []).append(state)
        state['account_reconciliation_errors'] = errors
        try:
            new_equity = account_equity(group, bundle, expected_sessions or {}, evaluated_at) if not errors else None
        except (ValueError, KeyError, TypeError):
            errors.append('ACCOUNT_EQUITY_ADAPTER_UNAVAILABLE')
            new_equity = None
        if new_equity is not None and state.get('equity_snapshot_id') != new_equity['equity_snapshot_id']:
            for previous_snapshot in result.get('budget_snapshots', []):
                if previous_snapshot['strategy_group_id'] == gid:
                    new_equity['errors'].extend(e for e in previous_snapshot.get('errors', [])
                                                if e == 'LEGACY_BUY_RESERVATIONS_UNRECONCILED')
            state.update(equity_snapshot_id=new_equity['equity_snapshot_id'], equity_reconciliation_required=False)
            # New balances may already include these executions. Require a
            # reconciliation instead of silently resetting or spending twice.
            if state.get('unreconciled_buy_executions') or state.get('cash_spent_since_snapshot'):
                state['cash_reconciliation_required'] = True
                new_equity['errors'].append('CASH_RECONCILIATION_REQUIRED')
            result.setdefault('equity_updates', []).append(new_equity)
            result['budget_snapshots'] = [s for s in result.get('budget_snapshots', []) if s['strategy_group_id'] != gid] + [deepcopy(new_equity)]
        for snapshot in result.get('budget_snapshots', []):
            if snapshot['strategy_group_id'] == gid:
                snapshot.setdefault('errors', []).extend(errors)
        reports.append({'strategy_group_id': gid, 'errors': errors, 'changed_positions': changed,
                        'state': 'partial' if errors else 'available'})
    result['account_reconciliation'] = reports
    return result

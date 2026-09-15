"""Existing policy observations; never an order gate or an adoption action."""
from decimal import Decimal

from .portfolio import number


def inspect(bundle, portfolio, plans=()):
    from .cash_assets import is_cash
    groups={}
    issues=[]
    accounts={a['alias']:a for a in bundle['accounts']}
    reviewed={(p['account'],p['symbol']):p for p in portfolio['positions']}
    identity_complete=True
    positions=[]
    for account in bundle['accounts']:
        for pos in account['positions']:
            if is_cash(pos, bundle):
                continue
            positions.append((account['alias'],pos))
            if pos.get('data_unavailable_reason'):
                identity_complete=False
                issues.append({'rule':'DATA','account':account['alias'],'symbol':pos['symbol'],'name':pos.get('name'),'reason':'holding_values_unavailable'})
                continue
            if number(pos['quantity'])==0:
                continue
            market=pos.get('market')
            symbol=pos.get('quote_symbol') or pos['symbol']
            name=pos.get('name')
            if market not in ('KR','US'):
                identity_complete=False
                issues.append({'rule':'Q-01','account':account['alias'],'symbol':pos['symbol'],'name':name,'reason':'market_identity_required'})
                continue
            key=(market,symbol)
            group=groups.setdefault(key,{'market':market,'symbol':symbol,'name':name,'accounts':[],
                'market_value_base':Decimal(0),'unrealized_pnl_base':Decimal(0),'complete':True})
            if not group.get('name') and name:
                group['name']=name
            group['accounts'].append(account['alias'])
            row=reviewed[(account['alias'],pos['symbol'])]
            if row.get('market_value_base') is None:
                group['complete']=False
            else:
                group['market_value_base']+=number(row['market_value_base'])
                group['unrealized_pnl_base']+=Decimal(row['unrealized_pnl_base'])
    if portfolio['stale']:
        issues.append({'rule':'DATA','reason':'stale_account_snapshot'})
    if portfolio['equity_base'] is None:
        issues.append({'rule':'DATA','reason':'incomplete_equity_or_fx'})
    count=len(groups) if identity_complete else None
    if count is not None and count>5:
        issues.append({'rule':'Q-01','reason':'more_than_five_held_instruments'})
    equity=number(portfolio['equity_base']) if portfolio['equity_base'] is not None else None
    instruments=[]
    for group in groups.values():
        complete=group.pop('complete')
        weight=group['market_value_base']/equity if complete and equity and identity_complete else None
        above=weight is not None and weight>Decimal('0.20')
        instruments.append({**group,'market_value_base':str(group['market_value_base']) if complete else None,
            'unrealized_pnl_base':str(group['unrealized_pnl_base']) if complete else None,
            'weight':str(weight) if weight is not None else None,'above_basic_weight':above,
            'observation':'reallocation_review_required_not_hard_cap' if above else 'within_basic_weight' if weight is not None else 'unknown'})
    for row in portfolio['positions']:
        if row.get('quantity') is not None and number(row['quantity'])==0:
            continue
        if row.get('state')=='stop_breached':
            issues.append({'rule':'Q-04','account':row['account'],'symbol':row['symbol'],'reason':'adopted_stop_reached_execution_unknown'})
        elif row.get('stop_exposure_base') is None:
            issues.append({'rule':'Q-04','account':row['account'],'symbol':row['symbol'],'reason':'adopted_stop_or_price_basis_unavailable'})
    plan_checks=[]
    for plan in plans:
        reasons=[]
        if (plan['source']=='synthetic')!=(bundle['source']=='synthetic'):
            reasons.append('plan_account_source_mismatch')
        account=accounts.get(plan['account_alias'])
        if account is None:
            reasons.append('account_not_in_snapshot')
        matches=[p for alias,p in positions if alias==plan['account_alias'] and p.get('market')==plan['market']
                 and (p.get('quote_symbol') or p['symbol'])==plan['symbol']]
        if len(matches)>1:
            reasons.append('ambiguous_position_identity')
        elif matches:
            pos=matches[0]
            if pos.get('data_unavailable_reason'):
                plan_checks.append({'plan_id':plan['plan_id'],'state':'needs_input',
                    'reasons':['holding_values_unavailable'],'Q02B':'not_evaluated_no_exception_granted'})
                continue
            if pos['currency']!=plan['currency']:
                reasons.append('currency_mismatch')
            if number(pos['quantity'])!=number(plan['inventory_from_records']):
                reasons.append('recorded_inventory_differs_from_account')
            stop=pos.get('adopted_stop')
            if not stop or not stop.get('adoption_ref') or stop.get('price_basis')!='executable_raw':
                reasons.append('current_adopted_stop_required')
            elif number(stop['price'])!=number(plan['stop_price']) or stop['adoption_ref']!=plan['adoption_ref']:
                reasons.append('current_stop_differs_from_original_plan_recalculation_required')
        elif number(plan['inventory_from_records'])>0:
            reasons.append('recorded_holding_missing_from_account')
        if portfolio['stale']:
            reasons.append('stale_account_snapshot')
        reasons.extend(plan.get('alerts',[]))
        # Ledger equality is only an observation, not evidence of broker event reconciliation.
        reasons.extend(['broker_orders_and_fills_not_reconciled','GDD_adoption_not_connected'])
        plan_checks.append({'plan_id':plan['plan_id'],'state':'needs_input','reasons':reasons,
                            'Q02B':'not_evaluated_no_exception_granted'})
    return {'schema_version':1,'authority':'observation_only_no_gate_approval',
        'basic_policy':{'max_held_instruments':5,'basic_position_weight':'0.20','weight_is_hard_cap':False},
        'held_instrument_count':count,'identity_complete':identity_complete,
        'instruments':sorted(instruments,key=lambda p:(p['market'],p['symbol'])),
        'issues':issues,'plan_checks':plan_checks,
        'notes':['Q20A: target/RR absence does not block this review.',
                 'Q21: stops are never moved automatically.',
                 'GDD is not required to produce a morning report; formal plan readiness remains separate.']}

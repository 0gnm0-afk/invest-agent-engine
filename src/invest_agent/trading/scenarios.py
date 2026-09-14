"""Read-only alternatives bound to a specific account and reservation snapshot."""
from decimal import ROUND_FLOOR, Decimal

from .contracts import digest
from .portfolio import number, size_preview


def split(quantity, step, fractions):
    weights=[number(v,positive=True) for v in fractions]
    if not weights or sum(weights)!=1:
        raise ValueError('split_fractions_must_sum_to_one')
    parts=[(quantity*w/step).to_integral_value(rounding=ROUND_FLOOR)*step for w in weights]
    parts[-1]+=quantity-sum(parts)
    return [str(v) for v in parts]


def evaluate(bundle, reviewed, inputs, plans=(), capacity=None):
    if inputs.get('schema_version')!=1 or inputs.get('source') not in ('synthetic','user_input'):
        raise ValueError('invalid_scenario_source')
    if (inputs['source']=='synthetic')!=(bundle['source']=='synthetic'):
        raise ValueError('scenario_account_source_mismatch')
    if inputs.get('account_snapshot_hash')!=digest(bundle):
        raise ValueError('scenario_account_snapshot_changed')
    account_hash=digest(bundle)
    if capacity is not None:
        if capacity.get('reconciled') and capacity.get('account_snapshot_hash')!=account_hash:
            raise ValueError('capacity_account_snapshot_changed')
        bundle={**bundle,'risk_capacity':capacity}
    requests=inputs['scenarios']
    if not isinstance(requests,list) or len({r['scenario_id'] for r in requests})!=len(requests):
        raise ValueError('duplicate_or_invalid_scenario_list')
    output=[]
    for request in requests:
        ident={k:request.get(k) for k in ('scenario_id','account_alias','market','symbol','side')}
        try:
            row=_one(bundle,reviewed,request,plans)
        except (ValueError,KeyError,TypeError) as exc:
            row={'state':'needs_input','reason':str(exc)}
        output.append({**ident,**row,'authority':'independent_scenario_no_adoption_no_reservation',
                       'formal_readiness':'not_evaluated_GDD_and_Q02B_not_connected'})
    return {'state':'available' if all(r['state'] in ('preview','blocked') for r in output) else 'partial',
            'rows':output,'account_snapshot_hash':account_hash,
            'note':'Each row is an independent alternative from the same snapshot; do not combine quantities across rows.'}


def _one(bundle,reviewed,request,plans):
    if reviewed['stale']:
        raise ValueError('fresh_account_snapshot_required')
    account=next((a for a in bundle['accounts'] if a['alias']==request['account_alias']),None)
    if account is None: raise ValueError('account_missing')
    market,symbol=request['market'],request['symbol']
    if market not in ('KR','US'): raise ValueError('market_required')
    positions=[p for p in account['positions'] if p.get('market')==market and (p.get('quote_symbol') or p['symbol'])==symbol]
    if len(positions)>1: raise ValueError('ambiguous_position')
    pos=positions[0] if positions else None
    currency=pos['currency'] if pos else request['currency']
    if request.get('currency',currency)!=currency: raise ValueError('currency_mismatch')
    step=number(request['quantity_step'],positive=True)
    entry=number(request['entry_price'],positive=True)
    capacity=bundle['risk_capacity']
    if capacity.get('reconciled') is not True or not capacity.get('source_ref'):
        raise ValueError('reconciled_capacity_required')
    expected={p['plan_id']:p['event_count'] for p in plans}
    if any((p['source']=='synthetic')!=(bundle['source']=='synthetic') for p in plans):
        raise ValueError('plan_account_source_mismatch')
    if capacity.get('covered_plan_event_counts')!=expected:
        raise ValueError('reservation_snapshot_changed')
    # Capacity is supplied by a reconciler, net of broker orders AND local commitments.
    # It is never inferred from gross account cash or a plan's cumulative spent budget.
    capacity_account=capacity['accounts'][account['alias']]
    qty=number(pos['quantity']) if pos else Decimal(0)
    if request['side']=='sell':
        amount=number(request['quantity'],positive=True)
        if amount%step: raise ValueError('quantity_step_mismatch')
        available=number(capacity_account['available_to_sell'][symbol])
        if amount>min(qty,available):
            return {'state':'blocked','reason':'insufficient_unreserved_sale_quantity'}
        costs=number(request['cost_per_share'])+number(request['slippage_per_share'])
        return {'state':'preview','currency':currency,'quantity':str(amount),
                'split_quantities':split(amount,step,request['split_fractions']),
                'post_quantity':str(qty-amount),'estimated_proceeds':str(amount*max(entry-costs,Decimal(0))),
                'estimated_realized_pnl':str(amount*(entry-number(pos['average_cost'])-costs))}
    if request['side']!='buy': raise ValueError('unsupported_side')
    if capacity.get('purchase_blockers'):
        raise ValueError('purchase_capacity_incomplete: '+', '.join(capacity['purchase_blockers']))
    if reviewed['equity_base'] is None or not reviewed['stop_exposure_complete']:
        raise ValueError('complete_equity_fx_and_stops_required')
    if any(p.get('market') not in ('KR','US') for a in bundle['accounts'] for p in a['positions'] if number(p['quantity'])>0):
        raise ValueError('portfolio_market_identity_required')
    policy=reviewed['policy_review']
    held={(r['market'],r['symbol']) for r in policy['instruments']}
    if (market,symbol) not in held and len(held)>=5:
        return {'state':'blocked','reason':'new_instrument_exceeds_five_position_structure'}
    stop=pos.get('adopted_stop') if pos else request.get('adopted_stop')
    if not stop or not stop.get('adoption_ref') or stop.get('price_basis')!='executable_raw':
        raise ValueError('adopted_executable_stop_required')
    fx=number(bundle['fx_to_base'][currency],positive=True)
    stop_price=number(stop['price'],positive=True)
    price=number(pos['price'],positive=True) if pos else entry
    if price<=stop_price: return {'state':'blocked','reason':'adopted_stop_reached'}
    instrument=next((r for r in policy['instruments'] if (r['market'],r['symbol'])==(market,symbol)),None)
    if instrument and Decimal(instrument['unrealized_pnl_base'])<0:
        return {'state':'blocked','reason':'loss_position_addition_Q02B_not_enabled'}
    value=number(instrument['market_value_base']) if instrument else Decimal(0)
    own_value=qty*price*fx
    own_risk=max(price-stop_price,Decimal(0))*qty*fx
    instrument_risk=Decimal(0)
    for a in bundle['accounts']:
        for p in a['positions']:
            if p.get('market')==market and (p.get('quote_symbol') or p['symbol'])==symbol:
                row=next(r for r in reviewed['positions'] if r['account']==a['alias'] and r['symbol']==p['symbol'])
                instrument_risk+=number(row['stop_exposure_base'])
    pending=capacity['instruments'].get(market+':'+symbol)
    if pending is None and capacity.get('complete_instrument_coverage') is True:
        pending={'reserved_loss_base':'0','reserved_value_base':'0'}
    if pending is None: raise ValueError('instrument_reservation_capacity_required')
    pending_risk=number(pending['reserved_loss_base'])
    pending_value=number(pending['reserved_value_base'])
    if pending_risk>number(capacity['reserved_loss_base']):
        raise ValueError('inconsistent_reserved_risk')
    position_budget=number(request['position_loss_budget_base'])-instrument_risk+own_risk-pending_risk
    portfolio_budget=number(request['portfolio_loss_budget_base'])-number(capacity['reserved_loss_base'])
    value_cap=number(request['position_value_cap_base'])-value+own_value-pending_value
    if min(position_budget,portfolio_budget,value_cap)<0:
        return {'state':'blocked','reason':'no_remaining_budget'}
    preview=size_preview({'entry':str(entry),'stop':str(stop_price),'existing_quantity':str(qty),
        'current_price':str(price),'average_cost':pos['average_cost'] if pos else '0',
        'available_cash':capacity_account['orderable_cash'][currency],
        'position_loss_budget':str(position_budget/fx),
        'other_portfolio_risk':str((number(reviewed['known_stop_exposure_base'])-own_risk)/fx),
        'portfolio_loss_budget':str(portfolio_budget/fx),'position_value_cap':str(value_cap/fx),
        'slippage_per_share':request['slippage_per_share'],'cost_per_share':request['cost_per_share'],
        'quantity_step':str(step),'split_fractions':request['split_fractions'],
        'snapshot_stale':False,'open_orders_reconciled':True})
    if preview['state']!='preview': return preview
    amount=number(preview['quantity'])
    # A conservative post-trade reference: new shares marked at scenario entry.
    stress=number(request['slippage_per_share'])+number(request['cost_per_share'])
    equity=number(reviewed['equity_base'])-amount*stress*fx
    if equity<=0: return {'state':'blocked','reason':'nonpositive_post_trade_equity'}
    added_risk=amount*(entry-stop_price+stress)*fx
    return {**preview,'currency':currency,'base_currency':bundle['base_currency'],
            'quantity_is_upper_bound':True,'adopted_stop_ref':stop['adoption_ref'],
            'post_instrument_weight':str((value+amount*entry*fx)/equity),
            'post_portfolio_stop_exposure_base':str(number(reviewed['known_stop_exposure_base'])+added_risk),
            'committed_portfolio_stop_exposure_base':str(number(reviewed['known_stop_exposure_base'])+number(capacity['reserved_loss_base'])+added_risk),
            'basic_weight_review_required':(value+amount*entry*fx)/equity>Decimal('0.20')}

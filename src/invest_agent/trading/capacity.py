"""Reconcile normalized working orders with local commitments, without broker writes."""
from datetime import datetime, timezone
from decimal import Decimal

from .contracts import digest
from .portfolio import number


def reconcile(bundle, orders, plans=(), now=None):
    now=now or datetime.now(timezone.utc)
    if orders.get('schema_version')!=1 or orders.get('source') not in ('synthetic','broker_export'):
        raise ValueError('invalid_order_snapshot_source')
    if orders['source']!=bundle['source'] or orders.get('account_snapshot_hash')!=digest(bundle):
        raise ValueError('order_account_snapshot_mismatch')
    if not orders.get('source_ref') or orders.get('orders_complete') is not True:
        raise ValueError('complete_working_order_snapshot_required')
    account_time=datetime.fromisoformat(bundle['as_of'])
    order_time=datetime.fromisoformat(orders['as_of'])
    if account_time.tzinfo is None or order_time.tzinfo is None:
        raise ValueError('timezone_required')
    max_age=number(bundle['max_age_hours'],positive=True)*3600
    if any(t>now or Decimal(str((now-t).total_seconds()))>max_age for t in (account_time,order_time)):
        raise ValueError('stale_or_future_account_or_orders')
    if Decimal(str(abs((account_time-order_time).total_seconds())))>number(orders['max_snapshot_skew_seconds']):
        raise ValueError('account_order_time_skew')
    accounts={a['alias']:a for a in bundle['accounts']}
    exported={a['alias']:a for a in orders['accounts']}
    if len(exported)!=len(orders['accounts']) or set(exported)!=set(accounts):
        raise ValueError('order_accounts_incomplete_or_duplicate')
    positions={}
    for alias,account in accounts.items():
        for pos in account['positions']:
            key=(alias,pos.get('market'),pos.get('quote_symbol') or pos['symbol'])
            if key in positions: raise ValueError('ambiguous_position_identity')
            positions[key]=pos
    local={};purchase_blockers=[]
    for plan in plans:
        if (plan['source']=='synthetic')!=(bundle['source']=='synthetic'):
            raise ValueError('plan_account_source_mismatch')
        key=(plan['account_alias'],plan['market'],plan['symbol'])
        if key[0] not in accounts: raise ValueError('plan_account_missing')
        pos=positions.get(key)
        if pos and pos['currency']!=plan['currency']: raise ValueError('plan_position_currency_mismatch')
        if number(plan['inventory_from_records'])!=number(pos['quantity'] if pos else '0'):
            raise ValueError('plan_inventory_not_reconciled')
        purchase_blockers.extend(plan.get('alerts',[]))
        tranches={t['tranche_id']:t for t in plan['tranches']}
        for rid,reservation in plan['reservations'].items():
            quantity=number(reservation['remaining'])
            if not quantity: continue
            if rid in local: raise ValueError('duplicate_local_reservation')
            tranche=tranches[reservation['tranche_id']]
            local[rid]={'key':key,'currency':plan['currency'],'side':tranche['side'],'quantity':quantity,
                'price':number(tranche['price'],positive=True),
                'stress':number(plan['cost_per_share'])+number(plan['slippage_per_share']),
                'adopted_stop':{'price':plan['stop_price'],'adoption_ref':plan['adoption_ref'],'price_basis':'executable_raw'}}
    capacities={};broker_ids=set();matched=set();commitments=[]
    for alias,export in exported.items():
        # Supplied broker amounts are already net of all listed broker working orders.
        if export.get('balance_basis')!='net_of_broker_working_orders':
            raise ValueError('net_broker_balance_basis_required')
        cash={k:number(v) for k,v in export['orderable_cash'].items()}
        sells={k:number(v) for k,v in export['available_to_sell'].items()}
        capacities[alias]={'cash':cash,'sell_by_key':sells}
        for order in export['working_orders']:
            oid=(alias,order['order_id'])
            if not order['order_id'] or oid in broker_ids: raise ValueError('duplicate_broker_order')
            broker_ids.add(oid)
            key=(alias,order['market'],order['symbol'])
            if key[1] not in ('KR','US') or order['side'] not in ('buy','sell'):
                raise ValueError('invalid_order_identity_or_side')
            q=number(order['remaining_quantity'],positive=True)
            if key in positions and positions[key]['currency']!=order['currency']:
                raise ValueError('order_position_currency_mismatch')
            item={'key':key,'currency':order['currency'],'side':order['side'],'quantity':q,
                  'price':number(order['price'],positive=True),
                  'stress':number(order['cost_per_share'])+number(order['slippage_per_share']),
                  'adopted_stop':order.get('adopted_stop')}
            if order.get('price_basis')!='executable_raw': raise ValueError('executable_order_price_required')
            rid=order.get('local_reservation_id')
            if rid:
                if rid not in local or rid in matched: raise ValueError('ambiguous_or_unknown_order_reservation_link')
                reserved=local[rid]
                if any(item[k]!=reserved[k] for k in ('key','currency','side','quantity','price')):
                    raise ValueError('order_reservation_contents_differ')
                item['stress']=max(item['stress'],reserved['stress'])
                if item['adopted_stop'] is None: item['adopted_stop']=reserved['adopted_stop']
                matched.add(rid)
            if item['side']=='buy':
                # Stress cash not already reserved by the broker must still be held locally.
                reserved_cash=number(order['cash_reserved'])
                cash[item['currency']]-=max(q*(item['price']+item['stress'])-reserved_cash,Decimal(0))
            commitments.append(item)
    for rid,item in local.items():
        if rid in matched: continue
        account=capacities[item['key'][0]]
        if item['side']=='buy':
            account['cash'][item['currency']]-=item['quantity']*(item['price']+item['stress'])
        else:
            account['sell_by_key'][item['key'][1]+':'+item['key'][2]]-=item['quantity']
        commitments.append(item)
    # Sale capacity must be backed by held quantity even when the provider says it is available.
    total_sales={}
    for item in commitments:
        if item['side']=='sell': total_sales[item['key']]=total_sales.get(item['key'],Decimal(0))+item['quantity']
    for key,quantity in total_sales.items():
        if quantity>number(positions.get(key,{}).get('quantity','0')):
            raise ValueError('sale_commitments_exceed_holdings')
    instruments={};reserved_risk=Decimal(0)
    for poskey in positions:
        if poskey[1] in ('KR','US'):
            instruments.setdefault(poskey[1]+':'+poskey[2],{'reserved_loss_base':Decimal(0),'reserved_value_base':Decimal(0)})
    for item in commitments:
        if item['side']!='buy': continue  # Pending sales do not release buy risk budgets.
        alias,market,symbol=item['key']
        row=instruments.setdefault(market+':'+symbol,{'reserved_loss_base':Decimal(0),'reserved_value_base':Decimal(0)})
        pos=positions.get(item['key'])
        if pos and pos['currency']!=item['currency']: raise ValueError('order_position_currency_mismatch')
        stop=pos.get('adopted_stop') if pos and number(pos['quantity'])>0 else item['adopted_stop']
        if not stop or not stop.get('adoption_ref') or stop.get('price_basis')!='executable_raw':
            purchase_blockers.append('pending_purchase_stop_unavailable:'+market+':'+symbol)
            continue
        if item['currency'] not in bundle['fx_to_base']:
            purchase_blockers.append('pending_purchase_fx_unavailable:'+item['currency'])
            continue
        fx=number(bundle['fx_to_base'][item['currency']],positive=True)
        stop_price=number(stop['price'],positive=True)
        if item['price']<=stop_price:
            purchase_blockers.append('pending_purchase_at_or_below_stop:'+market+':'+symbol)
        risk=item['quantity']*(max(item['price']-stop_price,Decimal(0))+item['stress'])*fx
        value=item['quantity']*item['price']*fx
        row['reserved_loss_base']+=risk;row['reserved_value_base']+=value;reserved_risk+=risk
    output_accounts={}
    for alias,capacity in capacities.items():
        if any(v<0 for v in [*capacity['cash'].values(),*capacity['sell_by_key'].values()]):
            raise ValueError('negative_capacity_after_local_commitments')
        sale_output={}
        for identity,amount in capacity['sell_by_key'].items():
            market,symbol=identity.split(':',1)
            key=(alias,market,symbol)
            # Amount is now net of broker AND unlinked local sale commitments.
            if amount+total_sales.get(key,Decimal(0))>number(positions.get(key,{}).get('quantity','0')):
                raise ValueError('sale_capacity_inconsistent_with_holdings')
            if symbol in sale_output: raise ValueError('ambiguous_sale_capacity_symbol')
            sale_output[symbol]=str(amount)
        output_accounts[alias]={'orderable_cash':{k:str(v) for k,v in capacity['cash'].items()},'available_to_sell':sale_output}
    return {'reconciled':True,'source_ref':orders['source_ref'],'order_snapshot_hash':digest(orders),
        'account_snapshot_hash':digest(bundle),'as_of':orders['as_of'],
        'covered_plan_event_counts':{p['plan_id']:p['event_count'] for p in plans},
        'accounts':output_accounts,'reserved_loss_base':str(reserved_risk) if not purchase_blockers else None,
        'known_reserved_loss_base':str(reserved_risk),'complete_instrument_coverage':True,
        'instruments':{k:{f:str(v) for f,v in row.items()} for k,row in instruments.items()},
        'purchase_blockers':sorted(set(purchase_blockers)),
        'coverage':{'broker_orders':len(broker_ids),'linked_reservations':len(matched),'local_only_reservations':len(local)-len(matched)},
        'authority':'normalized_snapshot_reconciliation_no_broker_api_or_orders'}

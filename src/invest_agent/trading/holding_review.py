"""Read-only holding risk and warning facts. Recommendations cannot adopt rules."""
from copy import deepcopy
from decimal import Decimal

from .portfolio import number
from .structure_math import sma


def validate_rule(rule):
    if not isinstance(rule,dict) or rule.get('line_type') not in ('SMA','EMA'):
        raise ValueError('Explicit SMA or EMA rule required')
    if rule.get('timeframe') not in ('daily','weekly') or rule.get('breach_basis') not in ('intraday','daily_close','weekly_close'):
        raise ValueError('Explicit timeframe and breach basis required')
    period=rule.get('period')
    if isinstance(period,bool) or not isinstance(period,int) or not 1<=period<=1000:
        raise ValueError('Invalid rule period')
    return {k:rule[k] for k in ('line_type','timeframe','period','breach_basis')}


def reference_line(context, rule, *, through=None):
    rule=validate_rule(rule)
    bars=context[rule['timeframe']+'_bars']
    if through: bars=[b for b in bars if b['date']<=through]
    period=rule['period']
    if len(bars)<period: return None
    if rule['line_type']=='SMA': return Decimal(str(sma(bars,period)[-1]))
    value=sum((Decimal(str(b['close'])) for b in bars[:period]),Decimal(0))/period
    alpha=Decimal(2)/(period+1)
    for bar in bars[period:]: value=Decimal(str(bar['close']))*alpha+value*(1-alpha)
    return value


def approved_value(policy,key):
    entry=policy.get(key,{})
    if not isinstance(entry,dict) or entry.get('approved_by_user') is not True or not entry.get('source_ref'):
        return None
    return entry.get('value')


def proximity(context, price, stop, policy):
    period=approved_value(policy,'atr_period')
    multiple=approved_value(policy,'warning_atr_multiple')
    result={'atr_period':period,'atr_value':None,'atr_as_of':context.get('as_of'),
            'atr_price_basis':context.get('price_basis'),'atr_method':'Wilder_seed_mean_TR',
            'warning_atr_multiple':multiple,'distance_to_stop_price':None,
            'distance_to_stop_atr':None,'proximity_state':'unavailable','proximity_reason':'atr_parameters_unapproved'}
    if price is not None and stop is not None:
        distance=price-stop; result['distance_to_stop_price']=str(distance)
        if distance<=0: result.update(proximity_state='breached',proximity_reason='reference_price_at_or_below_line')
    if isinstance(period,bool) or not isinstance(period,int) or not 1<=period<=1000 or multiple is None:
        return result
    multiplier=number(multiple,positive=True)
    bars=context.get('daily_bars',[])
    trs=[max(Decimal(str(b['high']))-Decimal(str(b['low'])),
             abs(Decimal(str(b['high']))-Decimal(str(a['close']))),
             abs(Decimal(str(b['low']))-Decimal(str(a['close']))))
         for a,b in zip(bars,bars[1:]) if a.get('complete') is not False and b.get('complete') is not False]
    if len(trs)<period:
        result['proximity_reason']='atr_history_missing'; return result
    value=sum(trs[:period],Decimal(0))/period
    for tr in trs[period:]: value=(value*(period-1)+tr)/period
    result['atr_value']=str(value)
    if price is None or stop is None:
        result['proximity_reason']='adopted_protection_or_price_missing'; return result
    if value<=0:
        result['proximity_reason']='atr_zero'; return result
    result['distance_to_stop_atr']=str((price-stop)/value)
    if price<=stop: return result
    result.update(proximity_state='approaching' if price-stop<=multiplier*value else 'normal',
                  proximity_reason='approved_atr_distance_comparison')
    return result


def evaluate_position(position, context, equity, fx, policy):
    result={'technical_context':context,'recommended_protection_rules':[],
            'adopted_stop':deepcopy(position.get('adopted_stop')),
            'adopted_stop_reference_price':None,'risk_to_adopted_stop_amount':None,
            'risk_to_adopted_stop_pct_of_equity':None,'total_trade_pnl_at_stop':None,
            'distance_to_stop':None,'risk_calculation_complete':False,
            'risk_reason':'adopted_protection_rule_missing','protection_state':'unavailable',
            'weakness_state':'unavailable','weakness_reasons':[], 'authority':'warning_only_no_orders',
            'excludes_costs':True,'execution_note':'기준선까지의 참조 위험액입니다. 확인 시점·갭·슬리피지·비용에 따라 실제 손익은 달라집니다.',
            'position_stop_risk_limit_pct':approved_value(policy,'position_stop_risk_limit_pct'),
            'limit_evaluation_state':'unavailable','limit_reason':'position_risk_limit_unapproved'}
    bars=context.get('daily_bars',[])
    price=Decimal(str(bars[-1]['close'])) if bars else None
    basis='latest_completed_daily_close' if bars else 'unavailable'
    stamp=context.get('as_of')
    quote=position.get('verified_quote',{})
    if quote.get('verified') is True and quote.get('currency')==position.get('currency') and quote.get('as_of') and quote.get('source_ref'):
        from datetime import datetime
        try:
            timestamp=datetime.fromisoformat(quote['as_of'])
            observed_through = datetime.fromisoformat(position['quote_validation_as_of'])
            if timestamp.tzinfo is not None and timestamp <= observed_through and (not stamp or timestamp.date().isoformat()>=stamp):
                price=number(quote['price'],positive=True);basis='verified_current_price';stamp=quote['as_of']
        except (ValueError,KeyError,TypeError): pass
    result.update(current_reference_price=str(price) if price is not None else None,price_basis=basis,price_as_of=stamp)
    stop=None;adopted=position.get('adopted_stop') or {}
    if adopted.get('adoption_ref'):
        try:
            rule=adopted.get('rule')
            stop=reference_line(context,rule) if rule else number(adopted['price'],positive=True) if adopted.get('price_basis')=='executable_raw' else None
            mode=rule['breach_basis'] if rule else adopted.get('breach_basis','intraday')
            result['risk_reason']='protection_line_or_price_missing'
            if stop is not None and price is not None:
                result['adopted_stop_reference_price']=str(stop)
                result['distance_to_stop']=str(price-stop)
                check_bars=context.get('weekly_bars',[]) if mode=='weekly_close' else bars
                checked=number(check_bars[-1]['close'],positive=True) if check_bars else None
                line_at_check=reference_line(context,rule,through=check_bars[-1]['date']) if rule and check_bars else stop
                breached=(price<=stop) if mode=='intraday' else checked is not None and line_at_check is not None and checked<line_at_check
                result.update(protection_state='adopted_protection_breached' if breached else 'maintained' if checked is not None else 'unavailable',
                              protection_check_as_of=stamp if mode=='intraday' else check_bars[-1]['date'] if check_bars else None,
                              breach_basis=mode)
                qty=number(position['quantity']);cost=number(position['average_cost'])
                if fx is None:
                    result['risk_reason']='missing_fx'
                else:
                    result['total_trade_pnl_at_stop']=str((stop-cost)*qty*number(fx,positive=True))
                    if price<=stop or breached:
                        result['risk_reason']='breached_unresolved' if breached else 'reference_price_below_line_awaiting_close_confirmation'
                    else:
                        risk=(price-stop)*qty*number(fx,positive=True)
                        result.update(risk_to_adopted_stop_amount=str(risk),risk_calculation_complete=True,risk_reason=None)
                        if equity is not None and number(equity)>0:
                            result['risk_to_adopted_stop_pct_of_equity']=str(risk/number(equity))
        except (ValueError,KeyError,TypeError): result['risk_reason']='adopted_rule_or_position_data_invalid'
    result['proximity']=proximity(context,price,stop,policy)
    if bars:
        reasons=[]
        for ma in context.get('moving_averages',[]):
            if ma['timeframe']=='daily' and ma['value'] is not None and bars[-1]['close']<ma['value'] and (ma.get('slope_one_bar') or 0)<0:
                reasons.append(f"daily_SMA{ma['period']}_below_and_falling")
        rs=context.get('relative_strength') or {}
        if rs.get('value') is not None and rs.get('prior_value') is not None and rs['value']<rs['prior_value']:
            reasons.append('relative_strength_lower_than_previous_session')
        volume=context.get('volume') or {}
        if len(bars)>1 and bars[-1]['close']<bars[-2]['close'] and (volume.get('ratio_to_prior_20') or 0)>1:
            reasons.append('down_close_with_volume_above_prior_20_average')
        if result['proximity']['proximity_state']=='approaching':reasons.append('approaching_user_adopted_protection')
        result.update(weakness_state='adopted_protection_breached' if result['protection_state']=='adopted_protection_breached' else 'weakness_warning' if reasons else 'normal',weakness_reasons=reasons)
    limit=result['position_stop_risk_limit_pct'];risk_pct=result['risk_to_adopted_stop_pct_of_equity']
    if limit is not None:
        limit=number(limit,positive=True)
        result['limit_reason']='risk_unavailable' if risk_pct is None else None
        if risk_pct is not None:result['limit_evaluation_state']='above_approved_limit' if Decimal(risk_pct)>limit else 'within_approved_limit'
    return result


def attach(result,bundle,market,policy=None):
    from .technical_context import build
    output=deepcopy(result);policy=policy or {}
    raw={(a['alias'],p['symbol']):p for a in bundle.get('accounts',[]) for p in a['positions']}
    series={s['symbol']:s for s in market.get('snapshot',{}).get('series',[])}
    contexts={r['symbol']:r.get('technical_context') for r in market.get('screen',{}).get('rows',[])}
    equity=output.get('managed_equity_base')
    for row in output.get('positions',[]):
        pos=deepcopy(raw[(row['account'],row['symbol'])]);symbol=pos.get('quote_symbol') or pos['symbol']
        if not output.get('stale') and bundle.get('as_of') and pos.get('price') is not None and (
                bundle.get('source')=='synthetic' or pos.get('price_basis')=='broker_last_price_observation'):
            pos['verified_quote']={'verified':True,'price':pos['price'],'currency':pos['currency'],
                                   'as_of':bundle['as_of'],'source_ref':'validated_account_snapshot'}
            pos['quote_validation_as_of']=bundle['as_of']
        context=contexts.get(symbol) or build(series.get(symbol,{'symbol':symbol,'market':pos.get('market')}),market.get('snapshot',{}))
        facts=evaluate_position(pos,context,equity,bundle.get('fx_to_base',{}).get(pos['currency']),policy)
        row.update(facts)
        row['average_cost'] = pos.get('average_cost')
        row['stop_exposure_base']=facts['risk_to_adopted_stop_amount']
        row['stop_price']=facts['adopted_stop_reference_price']
        row['stop_pnl_before_costs_base']=facts['total_trade_pnl_at_stop']
        if facts['protection_state']=='adopted_protection_breached':row['state']='stop_breached'
    rows=output.get('positions',[])
    known=[p for p in rows if p['risk_to_adopted_stop_amount'] is not None]
    summed=sum((Decimal(p['risk_to_adopted_stop_amount']) for p in known),Decimal(0))
    complete=len(known)==len(rows) and equity is not None
    output.update(calculated_stop_risk_amount=str(summed) if known or not rows else None,
                  stop_risk_coverage={'calculated':len(known),'total':len(rows)},aggregate_complete=complete,
                  positions_without_protection=sum(not p.get('adopted_stop') for p in rows),
                  account_stop_risk_pct=str(summed/number(equity)) if complete and number(equity)>0 else None,
                  known_stop_exposure_base=str(summed) if known or not rows else None,stop_exposure_complete=complete,
                  account_total_stop_risk_limit_pct=approved_value(policy,'account_total_stop_risk_limit_pct'),
                  limit_evaluation_state='unavailable',limit_reason='account_risk_limit_unapproved',
                  holding_review_policy=deepcopy(policy))
    limit=output['account_total_stop_risk_limit_pct']
    if limit is not None:
        value=number(limit,positive=True)
        output['limit_reason']='aggregate_risk_incomplete' if output['account_stop_risk_pct'] is None else None
        if output['account_stop_risk_pct'] is not None:
            output['limit_evaluation_state']='above_approved_limit' if Decimal(output['account_stop_risk_pct'])>value else 'within_approved_limit'
    return output

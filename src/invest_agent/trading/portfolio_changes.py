"""Describe daily observations without inferring fills or changing adopted stops."""
from datetime import datetime
from decimal import Decimal


def identity(row):
    return row['account'],row.get('market'),row.get('quote_symbol') or row['symbol'],row['currency']


def compare(current, previous):
    available=(previous.get('state')=='available' and previous.get('source')==current.get('source')
               and previous.get('base_currency')==current.get('base_currency'))
    old={identity(p):p for p in previous.get('positions',[])} if available else {}
    seen=set();rows=[]
    for row in current['positions']:
        key=identity(row);seen.add(key);before=old.get(key)
        code='no_baseline'
        if row.get('market') not in ('KR','US'):
            code='identity_unconfirmed'
        elif current['stale']:
            code='current_data_stale'
        elif available and previous.get('stale'):
            code='previous_data_stale'
        elif row.get('quantity') is not None and Decimal(row['quantity'])==0:
            code='zero_quantity_observation_no_execution_inference'
        elif available and before is None:
            code='new_observation'
        elif before is not None:
            current_time=datetime.fromisoformat(current['as_of']);previous_time=datetime.fromisoformat(previous['as_of'])
            if current_time<previous_time:
                code='older_snapshot'
            elif current_time==previous_time:
                fields=('state','quantity','stop_price','adoption_ref','stop_distance_pct')
                code='same_observation' if all(row.get(k)==before.get(k) for k in fields) else 'same_time_revision'
            elif before.get('quantity') is not None and Decimal(before['quantity'])==0:
                code='positive_quantity_observed'
            elif row.get('stop_distance_pct') is None:
                code='current_stop_or_price_unavailable'
            elif before.get('stop_distance_pct') is None:
                code='stop_now_available'
            elif Decimal(row['stop_price'])!=Decimal(before['stop_price']) or row.get('adoption_ref')!=before.get('adoption_ref'):
                code='stop_adoption_changed'
            elif row['state']=='stop_breached':
                code='breach_persists' if before['state']=='stop_breached' else 'new_stop_breach'
            elif before['state']=='stop_breached':
                code='above_stop_again_execution_unknown'
            else:
                distance=Decimal(row['stop_distance_pct']);prior_distance=Decimal(before['stop_distance_pct'])
                code='closer_to_stop' if distance<prior_distance else 'farther_from_stop' if distance>prior_distance else 'distance_unchanged'
        rows.append({'account':row['account'],'market':row.get('market'),'symbol':row['symbol'],'change':code,
                     'previous_distance':before.get('stop_distance_pct') if before else None,
                     'current_distance':row.get('stop_distance_pct'),
                     'previous_quantity':before.get('quantity') if before else None,'current_quantity':row.get('quantity'),
                     'previous_stop_ref':before.get('adoption_ref') if before else None,'current_stop_ref':row.get('adoption_ref')})
    for key,row in old.items():
        if key not in seen:
            rows.append({'account':row['account'],'market':row.get('market'),'symbol':row['symbol'],
                         'change':'not_observed_no_execution_inference','previous_distance':row.get('stop_distance_pct'),
                         'current_distance':None,'previous_quantity':row.get('quantity'),'current_quantity':None})
    return {'state':'available' if available else 'no_baseline',
            'previous_run_id':previous.get('run_id'),'previous_report_date':previous.get('report_date'),
            'previous_as_of':previous.get('as_of'),'current_as_of':current['as_of'],'rows':rows,
            'authority':'observation_only_no_stop_adoption_or_execution_inference'}

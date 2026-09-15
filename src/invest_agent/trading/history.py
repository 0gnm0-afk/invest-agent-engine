"""Select a comparable, verified previous morning observation."""
import json

from .contracts import digest


def selection_key(snapshot):
    entry=snapshot.get('market_input',{})
    if entry.get('state')!='loaded': return None
    value=entry['payload']
    if snapshot.get('market_mode')=='live':
        return {'mode':'live','request':value}
    coverage=value.get('coverage',{})
    return {'mode':'replay','source':value.get('source'),'profile':value.get('profile'),
            'scope':coverage.get('scope'),
            'listing_limits':{k:v.get('limit') for k,v in (coverage.get('listings') or {}).items()}}


def previous_morning(store, snapshot, report_date, version):
    key=selection_key(snapshot)
    if key is None: return {'state':'unavailable','reason':'current_market_input_unavailable'}
    skipped=0
    query="""SELECT r.run_id,r.report_date,r.input_json,r.code_version,a.payload_json,a.payload_hash
        FROM runs r JOIN artifacts a ON a.run_id=r.run_id AND a.step_key='market'
        WHERE r.state IN ('succeeded','partial') AND r.report_date<?
        ORDER BY r.report_date DESC,r.created_at DESC"""
    for row in store.db.execute(query,(report_date,)):
        if row['code_version']!=version: continue
        try:
            prior=json.loads(row['input_json'])
            if prior.get('workflow')!='morning' or selection_key(prior)!=key: continue
            result=json.loads(row['payload_json'])
            if digest(result)!=row['payload_hash']:
                skipped+=1;continue
            if result.get('state') not in ('available','partial') or not result.get('screen'): continue
            return {'state':'available','run_id':row['run_id'],'report_date':row['report_date'],
                    'rows':result['screen']['rows'],'session_dates':result['screen']['session_dates'],
                    'artifact_hash':row['payload_hash'],'skipped_invalid_artifacts':skipped}
        except (ValueError,KeyError,TypeError):
            skipped+=1
    return {'state':'unavailable','reason':'no_comparable_previous_morning','skipped_invalid_artifacts':skipped}


def previous_portfolio(store, snapshot, report_date, version):
    current=snapshot.get('account_input',{})
    if current.get('state')!='loaded':
        return {'state':'unavailable','reason':'current_account_input_unavailable'}
    current=current['payload']
    skipped=0
    query="""SELECT r.run_id,r.report_date,r.code_version,a.payload_json,a.payload_hash
        FROM runs r JOIN artifacts a ON a.run_id=r.run_id AND a.step_key='portfolio'
        WHERE r.state IN ('succeeded','partial') AND r.report_date<?
        ORDER BY r.report_date DESC,r.created_at DESC"""
    for row in store.db.execute(query,(report_date,)):
        if row['code_version']!=version: continue
        try:
            payload=json.loads(row['payload_json'])
            if digest(payload)!=row['payload_hash']:
                skipped+=1;continue
            result=payload.get('result')
            if not result or result.get('source')!=current.get('source') or result.get('base_currency')!=current.get('base_currency'):
                continue
            return {'state':'available','run_id':row['run_id'],'report_date':row['report_date'],
                    'artifact_hash':row['payload_hash'],'skipped_invalid_artifacts':skipped,
                    'as_of':result['as_of'],'stale':result['stale'],'positions':result['positions'],
                    'source':result['source'],'base_currency':result['base_currency']}
        except (ValueError,KeyError,TypeError):
            skipped+=1
    return {'state':'unavailable','reason':'no_comparable_previous_account','skipped_invalid_artifacts':skipped}

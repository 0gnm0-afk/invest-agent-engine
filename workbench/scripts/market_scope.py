"""Explicit user-approved market exclusions; never infer consent from a failure."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

POLICY = Path(__file__).with_name('market_exclusions.json')


def load(path=None, *, user_registry=None):
    """Explicit policy file only; no personal automatic registry discovery."""
    path=Path(path or POLICY)
    payload=json.loads(path.read_text(encoding='utf-8'))
    if payload.get('schema')!=1 or payload.get('scope')!='market_symbol_until_user_restores':
        raise ValueError('invalid_market_exclusion_policy')
    records=list(payload.get('records',[]))
    if user_registry is not None:
        records+=json.loads(Path(user_registry).read_text(encoding='utf-8')).get('records',[])
    return {**payload,'records':records,'lifecycle_records':payload.get('lifecycle_records',[]),'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}



def match(row, policy=None, *, completed_session=None):
    policy = policy if policy is not None else load()
    as_of = completed_session if completed_session is not None else policy.get('completed_sessions', {}).get(
        row['market'], datetime.now(timezone.utc).date().isoformat())
    for found in policy['records']:
        if (found['market'],found['symbol']) != (row['market'],row['symbol']):
            continue
        if found.get('identity_bound') and row.get('isin_code') and found.get('isin_code') != row['isin_code']:
            continue
        exchange = row.get('listing_exchange_family') or row.get('exchange')
        if found.get('approval_id') and exchange and found.get('exchange') != exchange:
            continue
        if found.get('dual_provider_rule'):
            if not row.get('isin_code') or found.get('isin_code') != row['isin_code'] or as_of < found.get('effective_session', ''):
                continue
        return found
    from market_quality import lifecycle_record
    ended = lifecycle_record(row, as_of, policy.get('lifecycle_records', []))
    if ended:
        return {**ended, 'reason':'confirmed_trading_ended', 'approved_on':'2026-10-02',
                'approval':'user_approved_automatic_confirmed_trading_ended',
                'evidence_run':ended['source_url'], 'lifecycle_rule':'confirmed_ended_exact_identity_v1'}
    # Type/size alone never creates an exclusion. Only diagnosed records apply.
    return None


def partition(rows, policy, *, completed_sessions=None):
    active, excluded = [], []
    for row in rows:
        record = match(row, policy, completed_session=(completed_sessions.get(row['market'], '')
                       if completed_sessions is not None else None))
        if record:
            excluded.append({**record, 'observed_isin_code': row.get('isin_code'), 'exchange':row.get('listing_exchange_family',row.get('exchange'))})
        else:
            active.append(row)
    return active, excluded


def apply_snapshot(snapshot, policy):
    """Create a new active-scope snapshot, retaining an explicit exclusion audit."""
    out = deepcopy(snapshot)
    active, excluded = partition(out['universe'], policy, completed_sessions={
        k:v[-1] for k,v in out.get('sessions', {}).items() if v})
    if not active:
        raise ValueError('market_scope_empty_after_exclusions')
    keys = {(r['market'],r['symbol']) for r in active}
    out['universe'] = active
    for name in ('series','errors'):
        out[name] = [r for r in out[name] if (r['market'],r['symbol']) in keys]
    prior = {(r['market'],r['symbol']):r for r in out.get('scope_exclusions',[])}
    prior.update({(r['market'],r['symbol']):r for r in excluded})
    out['scope_exclusions'] = list(prior.values())
    out['scope_policy'] = {k:policy[k] for k in ('scope','path','sha256')}
    cov = out['coverage']
    cov.update(scope='user_approved_active_market_scope',requested=len(active),received=len(out['series']),
               excluded=len(prior),provider_listed_count=len(active)+len(prior))
    refresh_coverage(out)
    for exchange,meta in (cov.get('listings') or {}).items():
        meta.update(requested=sum(r.get('listing_exchange_family')==exchange for r in active),
                    approved_excluded=sum(r.get('market')==('KR' if exchange in ('KOSPI','KOSDAQ') else 'US')
                        and (r.get('exchange')==exchange) for r in prior.values()))
    out['collection_stats'].update(requested=len(active),received=len(out['series']),failed=len(out['errors']),
                                   completed=len(out['series'])+len(out['errors']),approved_excluded=len(prior))
    return out


def refresh_coverage(snapshot):
    """Recompute active completeness after qualified provider recovery."""
    cov = snapshot['coverage']
    active = snapshot['universe']
    complete = (bool(cov.get('listings')) and all(m.get('state')=='available' and m.get('limit') is None for m in cov['listings'].values())
                and not cov['scope_errors'] and not snapshot['errors'] and len(active)==len(snapshot['series'])
                and not any(str(r.get('market_cap_quality','')).startswith('unavailable') for r in active))
    cov['active_scope_complete'] = bool(complete)
    cov['market_wide'] = False if snapshot.get('scope_exclusions') else cov['market_wide']


def analysis_pending(screen, snapshot):
    """Short, otherwise valid new histories are analysis waiting, not missing quotes."""
    series = {(r['market'],r['symbol']):r for r in snapshot['series']}
    rows = []
    for row in screen['rows']:
        reasons = row.get('universe',{}).get('universe_rejection_reasons',[])
        item = series.get((row['market'],row['symbol']))
        allowed = {'unavailable_insufficient_liquidity_bars', 'market_cap_below_minimum'}
        if (row['state'] != 'unavailable' or 'unavailable_insufficient_liquidity_bars' not in reasons
                or not set(reasons) <= allowed or not item):
            continue
        days = [b['date'] for b in item['bars']]
        sessions = snapshot['sessions'][row['market']]
        if not days or not 0 < len(days) < 20 or days[-1] != sessions[-1] or days != [d for d in sessions if d >= days[0]]:
            continue
        rows.append({'market':row['market'],'symbol':row['symbol'],'reason':'new_listing_history_accumulating',
                     'available_bars':len(days),'liquidity_required_bars':20,
                     'known_rejections':[r for r in reasons if r != 'unavailable_insufficient_liquidity_bars']})
    return rows

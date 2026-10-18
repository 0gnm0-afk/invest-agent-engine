"""Frozen, user-approved Other assignments, separate from provider taxonomy."""
from copy import deepcopy


def apply(groups, snapshot):
    policy = snapshot.get('user_classification')
    if not policy:
        return groups
    if policy.get('schema') != 1 or not policy.get('approval') or not policy.get('approved_on'):
        raise ValueError('invalid_user_classification_policy')
    records = {}
    for row in policy['records']:
        key = tuple(row.get(k) for k in ('market','symbol','isin_code','exchange'))
        if key in records or not all(key) or key[0] != 'US' or key[3] not in ('NASDAQ','NYSE'):
            raise ValueError('invalid_user_classification_identity')
        records[key] = row
    fallback = policy.get('unmatched_common_stock_other') or {}
    if fallback.get('enabled') and (not fallback.get('approval') or not fallback.get('approved_on')
            or set(fallback.get('reasons', [])) != {'new_listing_classification_not_acquired','classification_not_acquired_for_current_identity'}):
        raise ValueError('invalid_unmatched_other_policy')
    universe = {tuple(r.get(k) for k in ('market','symbol','isin_code')):r for r in snapshot.get('universe', [])}
    result = []
    for row in groups:
        key = tuple(row.get(k) for k in ('market','symbol','isin_code','exchange'))
        decision = records.get(key)
        authority = policy
        listing = universe.get(key[:3], {})
        if (not decision and fallback.get('enabled') and row.get('group_state') == 'unmatched'
                and row.get('reason') in fallback['reasons'] and key[0] == 'US'
                and listing.get('security_type') == 'STOCK' and listing.get('isin_code')
                and listing.get('listing_exchange_family') == key[3]):
            decision = {'previous_reason':row['reason']}
            authority = fallback
        if not decision:
            result.append(row)
            continue
        result.append({**row, 'group_state':'matched', 'group_code':'USER_OTHER_US',
            'group_name':'기타', 'group_taxonomy':'US_USER_OTHER', 'reason':None,
            'mapping_version':'user_other_'+authority['approved_on'].replace('-','')+'_v1',
            'user_classification_previous':deepcopy(row),
            'user_classification_reason':decision['previous_reason'],
            'evidence':[{'source':'user_decision','approved_on':authority['approved_on'],
                         'approval':authority['approval'],'source_run':authority.get('source_run')}],
            'classification_freshness':{'state':'user_defined','approved_on':authority['approved_on']}})
    return result

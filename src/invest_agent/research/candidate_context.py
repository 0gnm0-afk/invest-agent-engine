"""Explain existing candidates; never select, remove, or rescore them."""
from copy import deepcopy


def enrich(screen, report, groups):
    result = deepcopy(screen)
    mapping = {(r['market'], r['symbol']): r for r in groups}
    observations = {(r['market'], r['group_code']): r for r in report['observations']}
    for row in result['rows']:
        if row['state'] != 'candidate':
            continue
        group = mapping.get((row['market'], row['symbol']))
        observation = observations.get((row['market'], group.get('group_code'))) if group else None
        context = {'state': 'unavailable', 'reason': 'classification_not_acquired',
                   'authority': 'research_context_only', 'classification_applied_at': report['applied_at'],
                   'price_as_of': report['markets'][row['market']]['completed_session']}
        if group:
            context.update(taxonomy=group['group_taxonomy'], group_code=group['group_code'],
                           group_name=group['group_name'], reason=group.get('reason'), evidence=group['evidence'])
        if group and group['group_state'] == 'matched' and observation:
            context.update(state='available', reason=None,
                           horizons={h: {k: v for k, v in value.items() if k != 'index'}
                                     for h, value in observation['horizons'].items()},
                           trend=observation['trend'], breadth_sma60=observation['breadth_sma60'],
                           activity={k:v for k,v in observation['activity'].items() if k != 'ratios'})
            context['warnings'] = ['small_sample_' + h for h, m in context['horizons'].items() if m['used'] < 5]
        row['sector_context'] = context
    # A separate research view; the engine's rows, membership and ordering stay intact.
    research = {}
    for market in ('KR', 'US'):
        candidates = [r for r in result['rows'] if r['state'] == 'candidate' and r['market'] == market]
        def rs(row):
            return row['sector_context'].get('horizons', {}).get('63', {}).get('relative_strength')
        ordered = sorted(enumerate(candidates), key=lambda pair:
                         (rs(pair[1]) is None, -rs(pair[1]) if rs(pair[1]) is not None else 0, pair[0]))
        research[market] = [{'market':market, 'symbol':r['symbol'], 'sector_rs63':rs(r),
                             'reason':'sector_rs63_descending' if rs(r) is not None else
                             r['sector_context'].get('reason') or 'sector_rs63_unavailable',
                             'original_candidate_position':i + 1}
                            for i, r in ordered]
    result['research_order'] = research
    result['research_order_policy'] = 'Within each market: sector RS63 descending, unavailable last, engine order for ties. No score or signal.'
    return result

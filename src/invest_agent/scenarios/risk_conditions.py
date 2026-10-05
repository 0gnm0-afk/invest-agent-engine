"""Evaluate only explicitly declared conditions, independently of order execution."""
from invest_agent.scenarios.risk_values import number, instant, fresh

OPERATORS = {'lt': lambda a, b: a < b, 'lte': lambda a, b: a <= b,
             'gt': lambda a, b: a > b, 'gte': lambda a, b: a >= b}


def drawing_level(condition, observation):
    """Use the existing chart geometry and a frozen drawing revision at this session."""
    from invest_agent.scenarios.analysis_geometry import geometry
    saved = condition['drawing']
    workspace, chart = saved['workspace'], observation['chart']
    if workspace['revision'] != saved['revision'] or observation.get('drawing_revision') != saved['revision']:
        raise ValueError('drawing_revision_changed')
    if saved['geometry_version'] != 'drawing-tools-linear-v1':
        raise ValueError('drawing_geometry_version_mismatch')
    if chart['timeframe'] != condition['frame'] or chart['price_basis'] != condition['price_basis']:
        raise ValueError('drawing_basis_mismatch')
    value = geometry(workspace, chart, {'evaluate_at': observation['session'], 'price_scale': saved['price_scale']})
    row = next(r for r in value['drawings'] if r['id'] == saved['id'])
    if row['limitations']:
        raise ValueError('drawing_projection_unavailable')
    fields = {'horizontal': 'level', 'line': 'line_price', 'channel': saved.get('edge')}
    field = fields.get(row['kind'])
    if field not in ('level', 'line_price', 'other_line_price'):
        raise ValueError('drawing_edge_required')
    return number(row['calculations'][field], positive=True)


def evaluate(condition, observation):
    result = {'condition_id': condition.get('id'), 'state': 'deferred', 'matched': None,
              'reason': None, 'threshold': None, 'snapshot_id': observation.get('snapshot_id'),
              'as_of': observation.get('as_of')}
    try:
        if not condition.get('id'):
            raise ValueError('condition_id_required')
        if condition.get('kind') == 'qualitative':
            raise ValueError('qualitative_review_required')
        if condition.get('kind') not in ('price', 'drawing'):
            raise ValueError('condition_kind_unresolved')
        if not condition.get('currency') or not condition.get('price_basis'):
            raise ValueError('condition_basis_required')
        if condition.get('frame') not in ('intraday', '1d', '1w', '1mo'):
            raise ValueError('condition_frame_required')
        if condition.get('operator') not in OPERATORS:
            raise ValueError('condition_operator_required')
        at = instant(observation['as_of'])
        if not observation.get('snapshot_id') or not fresh(observation['as_of'], observation['evaluated_at'], observation['max_age_seconds']):
            raise ValueError('observation_missing_or_stale')
        if condition.get('valid_from') and at < instant(condition['valid_from']):
            raise ValueError('condition_not_yet_valid')
        if condition.get('valid_until') and at > instant(condition['valid_until']):
            raise ValueError('condition_expired')
        for key in ('currency', 'price_basis', 'frame'):
            if observation.get(key) != condition[key]:
                raise ValueError(key + '_mismatch')
        if condition['frame'] != 'intraday' and observation.get('complete') is not True:
            raise ValueError('completed_bar_required')
        if not observation.get('session') or observation.get('session') != observation.get('expected_session'):
            raise ValueError('completed_session_mismatch')
        threshold = drawing_level(condition, observation) if condition['kind'] == 'drawing' else number(condition.get('threshold'), positive=True)
        matched = OPERATORS[condition['operator']](number(observation.get('price')), threshold)
        result.update(state='matched' if matched else 'clear', matched=matched, threshold=str(threshold))
    except (ValueError, KeyError, TypeError, StopIteration, IndexError) as exc:
        result['reason'] = str(exc) if isinstance(exc, ValueError) else 'condition_input_incomplete'
    return result

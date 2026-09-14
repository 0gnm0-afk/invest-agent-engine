"""Presentation facts derived from existing Python states; never reclassify them."""
from copy import deepcopy
import re

STAGE_NAMES = {
    'S0': '하락 추세가 이어지는 상태',
    'S1': '하락 추세선 부근까지 접근한 상태',
    'S2': '하락 추세선을 종가로 넘어선 상태',
    'S3': '추세선 돌파 후 조정에서도 이전 저점을 지킨 상태',
    'S4': '하락 흐름에서 상승 흐름으로의 전환 조건이 확인된 상태',
}


def strip_explanations(value):
    if isinstance(value, dict):
        return {k: strip_explanations(v) for k, v in value.items()
                if k not in {'stage_explanation_ko', 'fallback', 'fallback_text', 'python_fallback'}}
    if isinstance(value, list):
        return [strip_explanations(v) for v in value]
    return value


def user_text(text):
    """Legacy frozen report presentation only; never passed to the model."""
    text = re.sub(r'S0[~～]S4|S1[~～]S4', '가격 구조 관찰 단계', text)
    text = re.sub(r'(?<![A-Za-z0-9])S[0-4](?![A-Za-z0-9])', lambda m: STAGE_NAMES[m[0]], text)
    text = re.sub(r'\bLL\b', '이전 저점 갱신', text)
    text = re.sub(r'\bHL\b', '이전보다 높은 저점', text)
    text = text.replace('1-2-3 전환 완성', STAGE_NAMES['S4']).replace('1-2-3 완성', STAGE_NAMES['S4'])
    return text


def fallback(structure, currency):
    """Only for reports without a successful model response."""
    from invest_agent.trading.report_format import money
    lines = []
    for period, raw in (structure or {}).get('windows', {}).items():
        view = window_facts(raw) if 'stage_code' not in raw else raw
        facts = view['structure_facts']
        lines.append(f"{period}거래일: {view['stage_display_name_ko']}.")
        for date_key, price_key, event in (
                ('breakout_date', 'breakout_close', '하락 추세선을 종가로 넘었습니다'),
                ('retest_confirmed_at', 'retest_low', '이후 조정 저점이 이전 저점 이상에서 확정됐습니다'),
                ('completion_date', 'completion_close', '그 뒤 반등 고점을 종가로 넘었습니다')):
            if facts.get(date_key):
                lines.append(f"{facts[date_key]} · {money(facts.get(price_key), currency)}: {event}.")
    return '\n\n'.join(lines)


def window_facts(window):
    stage = window.get('stage')
    available = window.get('data_state') == 'available'
    facts = {k: deepcopy(v) for k, v in window.items()
             if k not in {'stage', 'stage_label', 'stage_explanation_ko'}}
    facts.update(
        trendline_close_break_confirmed=bool(window.get('breakout_date')) if available else None,
        prior_low_defended_on_confirmed_retest=bool(window.get('retest_confirmed_at')) if available else None,
        rebound_high_close_break_confirmed=bool(window.get('completion_date')) if available else None,
    )
    # These are observation conditions, not new entry or order levels.
    if stage in ('S0', 'S1'):
        next_check = {'condition': 'completed_close_above_declining_trendline',
                      'current_reference_price': window.get('trendline_value_latest'),
                      'reference_changes_each_bar': True}
    elif stage == 'S2':
        next_check = {'condition': 'confirmed_retest_low_not_below_reference_low',
                      'reference_price': window.get('reference_low_price'), 'right_completed_bars': 2}
    elif stage == 'S3':
        next_check = {'condition': 'completed_close_above_rebound_high',
                      'reference_price': window.get('completion_level'),
                      'reference_date': window.get('completion_source_date')}
    elif stage == 'S4':
        next_check = {'condition': 'transition_conditions_already_confirmed', 'additional_stage_condition': None}
    else:
        next_check = {'condition': 'valid_structure_data_required', 'reason': window.get('reason') or window.get('data_state')}
    return {'stage_code': stage, 'stage_display_name_ko': STAGE_NAMES.get(stage, '가격 구조 확인 자료가 부족한 상태'),
            'structure_facts': facts, 'next_confirmation': next_check}


def structure_view(structure):
    return {'primary_window': (structure or {}).get('primary_window', 126),
            'windows': {key: window_facts(value) for key, value in (structure or {}).get('windows', {}).items()}}


def context_view(context):
    """Copy the shared context, adding facts without mutating its source."""
    from invest_agent.trading.trendlines import analyze
    result = deepcopy(context)
    result['sperandeo'] = structure_view(context.get('sperandeo'))
    result['trendlines'] = analyze(context.get('daily_bars', []))
    # Use the existing chart helpers for the weekly list. Missing long history
    # stays explicit; this adapter never changes collection or protection options.
    from invest_agent.trading.chart_history import weekly_bars
    from invest_agent.trading.weekly_chart import calculate
    bars = context.get('daily_bars', [])
    if bars and context.get('market') in ('KR', 'US'):
        weeks = weekly_bars(bars, bars[-1]['date'])
        weekly = calculate(weeks, context['market'])
        result['weekly_bars'] = weeks
        result['moving_averages'] = [m for m in result['moving_averages'] if m['timeframe'] != 'weekly']
        for label, values in weekly['lines'].items():
            value = values[-1] if values else None
            prior = values[-2] if len(values) > 1 else None
            result['moving_averages'].append({'line_type':'SMA', 'timeframe':'weekly',
                'period':int(label.split()[-1]), 'value':value, 'prior_value':prior,
                'slope_one_bar':None if value is None or prior is None else value-prior,
                'as_of':weeks[-1]['date'] if weeks else None,
                'state':'available' if value is not None else 'unavailable_insufficient_bars'})
        result['weekly_chart_history'] = weekly['history']
    for average in result.get('moving_averages', []):
        bars = result.get(average['timeframe'] + '_bars', [])
        close = bars[-1]['close'] if bars else None
        value = average.get('value')
        average['close_relation'] = (None if close is None or value is None else
                                     'above' if close > value else 'below' if close < value else 'equal')
    return result

"""S0-S4 transitions using the same recent resistance as the approach screener."""
import recent_trendlines as recent
from invest_agent.trading.sperandeo import pivots, PIVOT_RIGHT, LABELS


def analyze(raw,as_of=None):
    bars=recent.clean(raw,as_of)
    empty=dict(version=recent.VERSION,primary_window=60,windows={},candidate=False,candidate_reasons=[])
    if not bars:return empty
    dates={b['date']:i for i,b in enumerate(bars)};n=len(bars)-1
    states=[]
    for line in recent.candidates(bars):
        a=dates[line['anchor_a_date']];b=dates[line['anchor_b_date']];known=dates[line['confirmed_at']]
        broken=dates.get(line['first_close_break_date'])
        if broken is not None and n-broken>20:continue
        stop_at=broken if broken is not None else len(bars)
        low=min(range(b+1,stop_at),key=lambda i:bars[i]['low'])
        eps=bars[low]['low']*1e-10
        if broken is not None and any(v['low']<bars[low]['low']-eps for v in bars[broken:]):continue
        stage='S1' if 0<=line['gap_atr']<=.5 else 'S0';entered=known
        state=dict(data_state='available',analysis_as_of=bars[-1]['date'],
            anchor_high_a_date=line['anchor_a_date'],anchor_high_a_price=line['anchor_a_price'],
            anchor_high_b_date=line['anchor_b_date'],anchor_high_b_price=line['anchor_b_price'],
            anchor_b_confirmed_at=line['confirmed_at'],reference_low_date=bars[low]['date'],reference_low_price=bars[low]['low'],
            trendline_value_latest=line['value_latest'],slope_per_bar=line['slope_per_bar'],trendline=line,
            latest_close=bars[-1]['close'],atr14=line['atr14'])
        if broken is not None:
            stage='S2';entered=broken
            state.update(breakout_date=bars[broken]['date'],bars_since_breakout=n-broken)
            retest=next((i for i in pivots(bars,'low') if i>broken and bars[i]['low']>=bars[low]['low']-eps),None)
            if retest is not None:
                known_retest=retest+PIVOT_RIGHT
                # A later lower low invalidates this retest, even above reference low.
                if any(v['low']<bars[retest]['low']-eps for v in bars[known_retest+1:]):continue
                peak=max(range(broken,retest),key=lambda i:bars[i]['high'])
                stage='S3';entered=known_retest
                state.update(retest_pivot_date=bars[retest]['date'],retest_confirmed_at=bars[known_retest]['date'],
                    retest_low=bars[retest]['low'],completion_level=bars[peak]['high'],completion_source_date=bars[peak]['date'])
                finish=next((i for i in range(known_retest,n+1) if bars[i]['close']>bars[peak]['high']),None)
                if finish is not None:
                    stage='S4';entered=finish
                    state.update(completion_date=bars[finish]['date'],completion_close=bars[finish]['close'],bars_since_completion=n-finish)
        state.update(stage=stage,stage_label=LABELS[stage],stage_entered_at=bars[entered]['date'],bars_in_stage=n-entered)
        states.append(state)
    if not states:return empty
    # Fresh confirmed reversal remains available while a newer line is still forming.
    state=next((s for s in states if s['stage'] in ('S3','S4')),states[0])
    return {**empty,'windows':{'60':state},'candidate':state['stage']!='S0',
            'candidate_reasons':['sperandeo_60_'+state['stage']] if state['stage']!='S0' else []}

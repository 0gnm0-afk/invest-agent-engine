"""Independent confirmed-pivot chart lines, separate from Sperandeo S0-S4."""
from .sperandeo import pivots, PIVOT_RIGHT, MIN_ANCHOR_GAP, EPSILON_RELATIVE, SPERANDEO_WINDOWS

def valid_pair(bars, a, b, key):
    """Check the entire formation interval, including B's confirmation candles."""
    if b-a < MIN_ANCHOR_GAP or b+PIVOT_RIGHT >= len(bars):
        return False
    slope=(bars[b][key]-bars[a][key])/(b-a)
    if (key=='high' and slope>=0) or (key=='low' and slope<=0):
        return False
    eps=max(v['high'] for v in bars)*EPSILON_RELATIVE
    for i in range(a,b+PIVOT_RIGHT+1):
        value=bars[a][key]+slope*(i-a)
        if (key=='high' and bars[i]['high']>value+eps) or (key=='low' and bars[i]['low']<value-eps):
            return False
    return True

def line(bars, key):
    points=pivots(bars,key)
    # Extreme confirmed A first; most recent valid B for that A. Each side is independent.
    anchors=sorted(points,key=lambda i:((-bars[i][key] if key=='high' else bars[i][key]),i))
    for a in anchors:
        for b in reversed(points):
            if not valid_pair(bars,a,b,key):
                continue
            slope=(bars[b][key]-bars[a][key])/(b-a)
            value=lambda i:bars[a][key]+slope*(i-a)
            known=b+PIVOT_RIGHT
            eps=max(v['high'] for v in bars)*EPSILON_RELATIVE
            beyond=lambda i,k:(bars[i][k]>value(i)+eps if key=='high' else bars[i][k]<value(i)-eps)
            breach=next((i for i in range(known+1,len(bars)) if beyond(i,key)),None)
            close_break=next((i for i in range(known+1,len(bars)) if beyond(i,'close')),None)
            return {'state':'available','anchor_a_date':bars[a]['date'],'anchor_a_price':bars[a][key],
                'anchor_b_date':bars[b]['date'],'anchor_b_price':bars[b][key],
                'anchor_a_confirmed_at':bars[a+PIVOT_RIGHT]['date'],'confirmed_at':bars[known]['date'],
                'slope_per_bar':slope,'value_latest':value(len(bars)-1),
                'first_price_breach_date':bars[breach]['date'] if breach is not None else None,
                'first_close_break_date':bars[close_break]['date'] if close_break is not None else None,
                'latest_close_beyond_line':beyond(len(bars)-1,'close'),
                'authority':'observation_only_no_stage_or_policy_change'}
    return {'state':'no_valid_anchors'}

def analyze(bars):
    # Incomplete observations can never confirm a pivot.
    bars=[b for b in bars if b.get('complete') is not False]
    windows={}
    for p in SPERANDEO_WINDOWS:
        if len(bars)<p:
            windows[str(p)]={'state':'insufficient_history','available_bars':len(bars),'required_bars':p,
                'upper_trendline':{'state':'insufficient_history'},'lower_trendline':{'state':'insufficient_history'}}
        else:
            selected=bars[-p:]
            windows[str(p)]={'state':'evaluated','as_of':selected[-1]['date'],
                'upper_trendline':line(selected,'high'),'lower_trendline':line(selected,'low')}
    return {'primary_window':126,'windows':windows}

"""Completed-bar momentum entry observations. No broker, data fetch or orders.

Chart approximation; operational thresholds are explicit, not profitability claims.
"""
from copy import deepcopy
from math import isfinite
from statistics import mean

DEFAULTS = {
    'rs_min': 80., 'volume_multiple': 1.5, 'volume_window': 50,
    'dry_volume_ratio': .75, 'max_entry_age': 2, 'event_lookback': 10,
    'max_extension': .05, 'max_extension_atr': 2., 'max_structure_risk': .08,
    'close_range_min': .6, 'prior_advance': .30, 'max_base_depth': .15,
    'max_vcp_depth': .35, 'contraction_ratio': .8, 'max_final_depth': .10,
    'max_handle_depth': .15, 'market_filter': True,
}
PATTERNS = {'range_breakout':'조정 범위 돌파', 'vcp':'변동성 축소 후 돌파',
            'cup_handle':'컵 손잡이 돌파', 'sperandeo_s4':'스페란데오 S4 확정'}


def settings(overrides=None):
    cfg=deepcopy(DEFAULTS)
    if set(overrides or {})-set(cfg): raise ValueError('unknown_momentum_setting')
    cfg.update(overrides or {})
    for key in ('volume_window','max_entry_age','event_lookback'):
        if type(cfg[key]) is not int or cfg[key] < (0 if key=='max_entry_age' else 1):
            raise ValueError('invalid_momentum_setting:'+key)
    if cfg['volume_window']<20 or cfg['volume_window']>200 or cfg['event_lookback']>20 or cfg['event_lookback']<=cfg['max_entry_age']:
        raise ValueError('invalid_momentum_windows')
    if type(cfg['market_filter']) is not bool: raise ValueError('invalid_market_filter')
    for key in set(cfg)-{'volume_window','max_entry_age','event_lookback','market_filter'}:
        if type(cfg[key]) not in (int,float) or not isfinite(cfg[key]) or cfg[key]<=0:
            raise ValueError('invalid_momentum_setting:'+key)
    if not 0<cfg['rs_min']<=100 or cfg['volume_multiple']<1:
        raise ValueError('invalid_momentum_threshold')
    for key in ('dry_volume_ratio','max_extension','max_structure_risk','close_range_min',
                'max_base_depth','max_vcp_depth','contraction_ratio','max_final_depth','max_handle_depth'):
        if cfg[key]>=1: raise ValueError('invalid_momentum_fraction:'+key)
    return cfg


def completed(bars,as_of=None):
    result=[b for b in bars if b.get('complete',True) and (as_of is None or b['date']<=as_of)]
    for i,b in enumerate(result):
        if i and b['date']<=result[i-1]['date']: raise ValueError('non_increasing_dates')
        for key in ('open','high','low','close','volume'):
            x=b.get(key)
            if type(x) not in (int,float) or not isfinite(x) or x < (0 if key=='volume' else 1e-15):
                raise ValueError('invalid_'+key)
        if b['high']<max(b['open'],b['close'],b['low']) or b['low']>min(b['open'],b['close']):
            raise ValueError('invalid_ohlc_range')
    return result


def sma(bars,n,end=None):
    end=len(bars) if end is None else end
    return mean(b['close'] for b in bars[end-n:end]) if end>=n else None


def atr(bars,end=None):
    end=len(bars) if end is None else end
    if end<15:return None
    return mean(max(bars[i]['high']-bars[i]['low'],abs(bars[i]['high']-bars[i-1]['close']),
                    abs(bars[i]['low']-bars[i-1]['close'])) for i in range(end-14,end))


def features(bars):
    if len(bars)<253:return {'state':'insufficient_history','available_bars':len(bars),'required_bars':253}
    c=bars[-1]['close'];av={str(n):sma(bars,n) for n in (20,50,150,200)}
    ma200=[sma(bars,200,len(bars)-d) for d in range(20,-1,-1)]
    lo=min(b['low'] for b in bars[-252:]);hi=max(b['high'] for b in bars[-252:])
    returns={str(n):c/bars[-n-1]['close']-1 for n in (42,63,84,252)}
    checks={'above_150_200':c>av['150'] and c>av['200'], 'ma150_above_200':av['150']>av['200'],
        'ma200_rising_month':ma200[-1]>ma200[0] and sum(y>x for x,y in zip(ma200,ma200[1:]))>=15,
        'ma50_above_150_200':av['50']>av['150'] and av['50']>av['200'],
        'above_50':c>av['50'], 'above_year_low_30pct':c>=lo*1.3, 'within_year_high_25pct':c>=hi*.75}
    return {'state':'available','available_bars':len(bars),'close':c,'ma':av,'atr14':atr(bars),
        'year_low':lo,'year_high':hi,'returns':returns,'template_checks':checks,'template_pass':all(checks.values()),
        'momentum_2_3_4m':all(returns[str(n)]>0 for n in (42,63,84))}


def market_context(bars,as_of=None):
    bars=completed(bars,as_of)
    if len(bars)<70:return {'state':'unavailable','supportive':False,'available_bars':len(bars)}
    c=bars[-1]['close'];m=sma(bars,50)
    return {'state':'available','as_of':bars[-1]['date'],'supportive':c>m and m>sma(bars,50,len(bars)-20),
            'close':c,'ma50':m,'ma50_20_sessions_ago':sma(bars,50,len(bars)-20),
            'basis':'price_above_rising_50d_proxy_not_official_follow_through_day'}


def depth(bars):return 1-min(b['low'] for b in bars)/max(b['high'] for b in bars)


def advance(bars,start):
    prior=bars[max(0,start-126):start+1]
    return bars[start]['high']/min(b['low'] for b in prior)-1 if len(prior)>=30 else None


def pattern(kind,bars,start,end,pivot,stop,metrics):
    return {'kind':kind,'label':PATTERNS[kind],'base_start':bars[start]['date'],
        'base_end':bars[end-1]['date'],'pivot':pivot,'structure_stop':stop,
        'known_at':bars[end-1]['date'],'metrics':metrics,'approximation':True}


def setups(bars,end,cfg):
    """Only bars strictly before end choose the base, pivot and structural low."""
    if end<70:return []
    base_vol=mean(b['volume'] for b in bars[end-cfg['volume_window']:end])
    if base_vol<=0:return []
    final_vol=mean(b['volume'] for b in bars[end-5:end]);dry=final_vol/base_vol
    result=[]
    if dry<=cfg['dry_volume_ratio']:
        for size in (20,40,60):
            start=end-size;chunk=bars[start:end];rise=advance(bars,start)
            widths=[depth(chunk[:10]),depth(chunk[-10:])]
            if rise is not None and rise>=cfg['prior_advance'] and depth(chunk)<=cfg['max_base_depth'] and widths[-1]<=widths[0]*cfg['contraction_ratio']:
                result.append(pattern('range_breakout',bars,start,end,max(b['high'] for b in chunk),
                    min(b['low'] for b in chunk[-10:]),{'base_bars':size,'depth':depth(chunk),
                    'widths':widths,'volume_dry_ratio':dry,'prior_advance':rise}))
        for block in (10,15,20):
            start=end-3*block;chunks=[bars[start+i*block:start+(i+1)*block] for i in range(3)]
            widths=[depth(c) for c in chunks];lows=[min(b['low'] for b in c) for c in chunks]
            rise=advance(bars,start);pivot=max(b['high'] for b in chunks[-1])
            if (rise is not None and rise>=cfg['prior_advance'] and widths[1]<=widths[0]*cfg['contraction_ratio']
                and widths[2]<=widths[1]*cfg['contraction_ratio'] and widths[-1]<=cfg['max_final_depth']
                and lows[0]<lows[1]<=lows[2] and depth(bars[start:end])<=cfg['max_vcp_depth']
                and pivot>=.95*max(b['high'] for b in bars[start:end])):
                result.append(pattern('vcp',bars,start,end,pivot,lows[-1],{'block_bars':block,
                    'widths':widths,'lows':lows,'volume_dry_ratio':dry,'prior_advance':rise}))
        for handle in (5,10,15,20):
            rim=end-handle;hb=bars[rim:end]
            if depth(hb)>cfg['max_handle_depth'] or any(bars[i]['close']<=sma(bars,50,i+1) for i in range(rim,end)):continue
            for size in (35,50,65,90,126,180,252,325):
                start=rim-size
                if start<30:continue
                left=max(range(start,start+max(3,size//5)),key=lambda i:bars[i]['high'])
                cup=bars[left:rim];top=bars[left]['high'];bottom=min(range(left,rim),key=lambda i:bars[i]['low'])
                low=bars[bottom]['low'];d=1-low/top;span=rim-left
                dwell=[i for i in range(left,rim) if bars[i]['low']<=low+(top-low)*.2]
                rise=advance(bars,left);pivot=max(b['high'] for b in hb)
                if (35<=span<=325 and .12<=d<=.33 and .2*span<=bottom-left<=.8*span
                    and len(dwell)>=5 and dwell[-1]-dwell[0]>=5
                    and rise is not None and rise>=cfg['prior_advance']
                    and max(b['high'] for b in bars[rim-5:rim])>=top*.95
                    and min(b['low'] for b in hb)>(top+low)/2
                    and pivot>=top*.95 and mean(b['volume'] for b in hb)<mean(b['volume'] for b in cup)):
                    result.append(pattern('cup_handle',bars,left,end,pivot,min(b['low'] for b in hb),
                        {'cup_bars':span,'cup_depth':d,'bottom_date':bars[bottom]['date'],'left_rim':top,
                         'handle_start':bars[rim]['date'],'handle_bars':handle,'handle_depth':depth(hb),
                         'bottom_dwell_bars':len(dwell),'volume_dry_ratio':dry,'prior_advance':rise}))
                    break
    return result


def entry_event(bars,index,setup,cfg):
    bar=bars[index];pivot=setup['pivot'];stop=setup['structure_stop'];last=bars[-1]
    volbase=mean(b['volume'] for b in bars[index-cfg['volume_window']:index]);vol=bar['volume']/volbase if volbase else None
    v=atr(bars,index);extension=last['close']/pivot-1;risk=1-stop/last['close'];age=len(bars)-1-index
    upper=min(pivot*(1+cfg['max_extension']),pivot+cfg['max_extension_atr']*v,stop/(1-cfg['max_structure_risk'])) if v else pivot
    checks={'fresh_breakout':age<=cfg['max_entry_age'],
        'close_crossed_pivot':bars[index-1]['close']<=pivot<bar['close'],
        'volume_expansion':vol is not None and vol>=cfg['volume_multiple'],
        'strong_breakout_close':bar['close']>bar['open'] and bar['high']>bar['low'] and (bar['close']-bar['low'])/(bar['high']-bar['low'])>=cfg['close_range_min'],
        'pivot_held':all(b['close']>pivot for b in bars[index:]),
        'structure_held':all(b['low']>stop for b in bars[index:]),
        'ma20_held':all(bars[i]['close']>=sma(bars,20,i+1) for i in range(index,len(bars))),
        'not_extended':0<extension<=cfg['max_extension'] and v is not None and (last['close']-pivot)<=cfg['max_extension_atr']*v,
        'bounded_structure_risk':0<risk<=cfg['max_structure_risk']}
    return {**setup,'breakout_date':bar['date'],'breakout_close':bar['close'],'age_sessions':age,
        'volume_ratio':vol,'volume_baseline':volbase,'extension_pct':extension,'structure_risk_pct':risk,
        'entry_zone':{'lower_exclusive':pivot,'upper_inclusive':upper},'latest_close':last['close'],
        'checks':checks,'failed_conditions':[k for k,v in checks.items() if not v],'entry_pass':all(checks.values())}


def sperandeo_events(bars,structure,cfg):
    result=[];dates={b['date']:i for i,b in enumerate(bars)}
    for period,w in (structure or {}).get('windows',{}).items():
        if w.get('stage')!='S4' or w.get('completion_date') not in dates:continue
        index=dates[w['completion_date']]
        if index<cfg['volume_window'] or index<len(bars)-cfg['event_lookback']:continue
        pivot=w.get('completion_level');stop=w.get('retest_low')
        if not pivot or not stop:continue
        if w.get('retest_confirmed_at','9999')>w['completion_date']:continue
        setup={'kind':'sperandeo_s4','label':PATTERNS['sperandeo_s4'],'pivot':pivot,'structure_stop':stop,
            'base_start':w['anchor_high_a_date'],'base_end':w['retest_confirmed_at'],'known_at':w['retest_confirmed_at'],
            'metrics':{'window':period,'retest_date':w.get('retest_pivot_date'),'completion_level_date':w.get('completion_source_date')},
            'approximation':False}
        result.append(entry_event(bars,index,setup,cfg))
    return result


def analyze(bars,rs_percentile,market,structure=None,overrides=None,as_of=None):
    cfg=settings(overrides);bars=completed(bars,as_of);f=features(bars)
    result={'state':f['state'],'as_of':bars[-1]['date'] if bars else None,'features':f,
        'main_candidate':False,'sperandeo_candidate':False,'events':[],'rejections':[],
        'rs_percentile':rs_percentile,'market':market,'authority':'chart_entry_review_only',
        'unverified':['quarterly_earnings_sales','annual_growth_roe','new_products_management',
                      'institutional_sponsorship','code33','historical_leadership_profile'],
        'source_basis':'documented_operational_assumptions'}
    if f['state']!='available':return result
    common={'relative_strength':rs_percentile is not None and rs_percentile>=cfg['rs_min'],
            'positive_2_3_4m':f['momentum_2_3_4m'],
            'market_supportive':not cfg['market_filter'] or bool(market.get('supportive'))}
    result['common_checks']=common
    result['rejections']=[k for k,v in common.items() if not v]+[k for k,v in f['template_checks'].items() if not v]
    main=[]
    # Base geometry is frozen before each actual close breakout, never fitted using future bars.
    for index in range(len(bars)-1,max(cfg['volume_window'],len(bars)-cfg['event_lookback'])-1,-1):
        if bars[index]['close']<=bars[index-1]['close']:continue
        for setup in setups(bars,index,cfg):
            if bars[index-1]['close']<=setup['pivot']<bars[index]['close']:
                event=entry_event(bars,index,setup,cfg);event['route']='pdf_momentum';main.append(event)
    spr=sperandeo_events(bars,structure,cfg)
    for event in spr:event['route']='sperandeo'
    result['events']=main+spr
    main_ok=[e for e in main if e['entry_pass']]
    spr_ok=[e for e in spr if e['entry_pass']]
    spr_trend=f['close']>f['ma']['50'] and f['close']>f['ma']['200'] and f['template_checks']['ma200_rising_month']
    result['sperandeo_trend_pass']=spr_trend
    result['main_candidate']=all(common.values()) and f['template_pass'] and bool(main_ok)
    result['sperandeo_candidate']=all(common.values()) and spr_trend and bool(spr_ok)
    result['candidate']=result['main_candidate'] or result['sperandeo_candidate']
    active=(main_ok if result['main_candidate'] else [])+(spr_ok if result['sperandeo_candidate'] else [])
    result['active_events']=active
    result['selected_event']=min(active,key=lambda e:(e['age_sessions'],e['structure_risk_pct'],e['kind'])) if active else None
    if not main:result['rejections'].append('no_confirmed_pattern_breakout')
    elif not main_ok:result['rejections']+=sorted({c for e in main for c in e['failed_conditions']})
    if not active:
        forming=setups(bars,len(bars),cfg)
        result['forming_setups']=forming
        if forming:result['rejections'].append('setup_only_not_entry')
    result['state']='entry_candidate' if active else 'not_selected'
    return result

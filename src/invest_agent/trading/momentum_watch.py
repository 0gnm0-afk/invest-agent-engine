"""Observation stages before entry; keeps strict entry analysis unchanged."""
from copy import deepcopy
from math import isfinite
from statistics import mean
from . import momentum_entry as entry

PHASES = {'entry':'진입 조건 충족','ready':'돌파 대기','watch':'관찰 시작'}
ORDER = {'entry':0,'ready':1,'watch':2}
DEFAULTS = {'rs_min':70.,'sperandeo_rs_min':60.,'max_pivot_gap':.15,'ready_pivot_gap':.05,
            'max_base_depth':.30,'max_structure_risk':.20,'max_volume_ratio':1.2,'s3_max_age':20}

def settings(overrides=None):
    cfg={**DEFAULTS,**(overrides or {})}
    if set(cfg)!=set(DEFAULTS):raise ValueError('unknown_watch_setting')
    for k,v in cfg.items():
        if type(v) not in (int,float) or not isfinite(v) or v<=0:raise ValueError('invalid_watch_setting:'+k)
    if type(cfg['s3_max_age']) is not int or cfg['s3_max_age']>60:raise ValueError('invalid_s3_age')
    if any(not 0<cfg[k]<=100 for k in ('rs_min','sperandeo_rs_min')):raise ValueError('invalid_watch_rs')
    if not 0<cfg['ready_pivot_gap']<=cfg['max_pivot_gap']<1:raise ValueError('invalid_watch_gap')
    if not 0<cfg['max_base_depth']<1 or not 0<cfg['max_structure_risk']<1:raise ValueError('invalid_watch_risk')
    return cfg

def signal(setup,bars,phase,route,pending,warnings):
    c=bars[-1]['close'];pivot=setup['pivot'];stop=setup['structure_stop']
    return {**deepcopy(setup),'route':route,'phase':phase,'phase_label':PHASES[phase],
        'breakout_date':None,'breakout_close':None,'entry_zone':None,'age_sessions':None,
        'entry_pass':False,'latest_close':c,'pivot_gap_pct':pivot/c-1,'extension_pct':c/pivot-1,
        'structure_risk_pct':1-stop/c,'volume_ratio':None,'pending':pending,'warnings':warnings}

def key(s):
    return (ORDER[s['phase']],s.get('age_sessions') if s.get('age_sessions') is not None else 999,
            abs(s.get('pivot_gap_pct',s.get('extension_pct',0))),s['structure_risk_pct'],s['kind'])

def analyze(bars,strict,structure=None,overrides=None,as_of=None):
    cfg=settings(overrides);bars=entry.completed(bars,as_of);f=entry.features(bars)
    result={'signals':[],'watch_checks':{},'watch_rejections':[],'watch_settings':cfg}
    if f['state']!='available':return result
    c=f['close'];rs=strict.get('rs_percentile');signals=[]
    market_warning=[] if strict.get('market',{}).get('supportive') else ['시장 추세 미충족/결측: 관찰은 가능하나 진입 확인 필요']
    for e in strict.get('active_events',[]):
        signals.append({**deepcopy(e),'phase':'entry','phase_label':PHASES['entry'],'pending':[],
                        'warnings':[],'pivot_gap_pct':e['pivot']/c-1})
    checks={'watch_rs':rs is not None and rs>=cfg['rs_min'],
        'long_uptrend':c>f['ma']['200'] and f['template_checks']['ma200_rising_month'],
        'near_or_above_50':c>=.95*f['ma']['50'],
        'positive_3m':f['returns']['63']>0,'near_year_high':f['template_checks']['within_year_high_25pct']}
    result['watch_checks']=checks
    result['watch_rejections']=[k for k,v in checks.items() if not v]
    end=len(bars)-1  # today's OHLC never chooses yesterday's pivot or structural low
    formed=entry.setups(bars,end,entry.settings(strict.get('entry_settings')))
    preliminary=[]
    for size in (20,40,60):
        start=end-size;chunk=bars[start:end];rise=entry.advance(bars,start)
        hi=max(b['high'] for b in chunk);lo=min(b['low'] for b in chunk)
        width=entry.depth(chunk);first=entry.depth(chunk[:10]);last=entry.depth(chunk[-10:])
        vbase=mean(b['volume'] for b in bars[end-50:end]);dry=mean(b['volume'] for b in chunk[-5:])/vbase if vbase else None
        if (rise is not None and rise>=.30 and .03<=width<=cfg['max_base_depth'] and last<=first
            and hi>lo and (c-lo)/(hi-lo)>=.6 and dry is not None and dry<=cfg['max_volume_ratio']):
            preliminary.append(entry.pattern('range_breakout',bars,start,end,hi,min(b['low'] for b in chunk[-10:]),
                {'base_bars':size,'depth':width,'widths':[first,last],'prior_advance':rise,'volume_dry_ratio':dry,'preliminary':True}))
    if checks and all(checks.values()):
        for setup in formed+preliminary:
            gap=setup['pivot']/c-1;risk=1-setup['structure_stop']/c
            if not (0<=gap<=cfg['max_pivot_gap'] and 0<risk<=cfg['max_structure_risk'] and bars[-1]['low']>setup['structure_stop']):continue
            mature=not setup['metrics'].get('preliminary')
            ready=mature and gap<=cfg['ready_pivot_gap'] and f['template_pass'] and rs>=strict.get('entry_settings',{}).get('rs_min',80)
            pending=['피벗 종가 돌파','돌파 거래량·종가 강도 확인','돌파 후 유지·이격·구조 위험 확인']
            if not mature:pending.insert(0,'조정 폭·거래량 추가 수축 및 패턴 완성')
            if not f['template_pass']:pending.append('전체 상승 추세 템플릿 충족')
            if rs<80:pending.append('상대강도 80 이상 확인')
            if risk>.08:pending.append('진입 전 구조 위험 8% 이내로 축소')
            s=signal(setup,bars,'ready' if ready else 'watch','pdf_momentum',pending,market_warning)
            s['label']=({'range_breakout':'조정 범위 형성','vcp':'변동성 축소 형성','cup_handle':'컵 손잡이 형성'}[setup['kind']] if mature else '상승 후 조정 안정화')
            signals.append(s)
    # S3 is an already-confirmed retest; S1/S2 or broken/stale retests do not qualify.
    dates={b['date']:i for i,b in enumerate(bars)}
    s_checks={'s3_rs':rs is not None and rs>=cfg['sperandeo_rs_min'],
              's3_recovery':c>f['ma']['50'] and c>=f['ma']['20'] and f['ma']['50']>entry.sma(bars,50,len(bars)-10)}
    result['sperandeo_watch_checks']=s_checks
    for period,w in (structure or {}).get('windows',{}).items():
        when=w.get('retest_confirmed_at');pivot=w.get('completion_level');stop=w.get('retest_low')
        if w.get('stage')!='S3' or when not in dates or not pivot or not stop or not all(s_checks.values()):continue
        index=dates[when];gap=pivot/c-1;risk=1-stop/c
        if not (len(bars)-1-index<=cfg['s3_max_age'] and 0<=gap<=cfg['max_pivot_gap'] and 0<risk<=cfg['max_structure_risk']):continue
        if any(b['low']<stop for b in bars[index:]):continue
        setup={'kind':'sperandeo_s3','label':'스페란데오 S3 재시험 확인','base_start':w['anchor_high_a_date'],
               'base_end':when,'known_at':when,'pivot':pivot,'structure_stop':stop,'metrics':{'window':period,'retest_age':len(bars)-1-index},'approximation':False}
        signals.append(signal(setup,bars,'ready' if gap<=cfg['ready_pivot_gap'] else 'watch','sperandeo',
                              ['반등 고점 종가 돌파로 S4 확인','거래량·돌파 유지·진입 위험 확인'],market_warning))
    # Keep one strongest state per route, with every matched pattern still available.
    signals.sort(key=key);result['signals']=signals
    if not signals:result['watch_rejections'].append('no_intact_watch_structure')
    result['phase']=signals[0]['phase'] if signals else 'excluded'
    return result

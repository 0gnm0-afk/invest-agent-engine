"""Completed-close approach to an intact descending resistance; observation only."""
from copy import deepcopy
from collections import Counter
from pathlib import Path
import hashlib
import json
import math
import recent_trendlines as recent
from invest_agent.trading.market import usable_tail, check_bars
from invest_agent.trading.momentum_entry import completed
from invest_agent.trading.momentum_watch import signal, PHASES
from invest_agent.trading.store import instance_lock
from atomic_io import atomic_write

ROUTE = 'downtrend_approach'
LABEL = '하락 추세선 근접'
DEFAULTS = dict(approach_atr=.5, window=60, min_trend_bars=10, limit_per_market=10)


def settings(overrides=None):
    cfg = {**DEFAULTS, **(overrides or {})}
    if set(cfg) != set(DEFAULTS):
        raise ValueError('unknown_downtrend_setting')
    for key in ('window', 'min_trend_bars', 'limit_per_market'):
        if type(cfg[key]) is not int:
            raise ValueError('invalid_downtrend_setting:' + key)
    if not 10 <= cfg['min_trend_bars'] <= cfg['window'] <= 252 or not 0 <= cfg['limit_per_market'] <= 50:
        raise ValueError('invalid_downtrend_window_or_limit')
    if type(cfg['approach_atr']) not in (int, float) or not math.isfinite(cfg['approach_atr']) or not 0 < cfg['approach_atr'] <= 3:
        raise ValueError('invalid_downtrend_approach_atr')
    return cfg


def atr14(bars):
    """Wilder ATR, seeded with the first 14 TRs requiring a previous close."""
    ranges = [max(b['high']-b['low'], abs(b['high']-a['close']), abs(b['low']-a['close']))
              for a,b in zip(bars,bars[1:])]
    if len(ranges) < 14:
        return None
    value = sum(ranges[:14])/14
    for tr in ranges[14:]:
        value = (value*13+tr)/14
    return value


def analyze(raw, overrides=None, *, as_of=None):
    cfg = settings(overrides)
    try:
        full = completed(raw, as_of)
    except (ValueError, TypeError, KeyError):
        return dict(signals=[], state='invalid_history')
    result = dict(signals=[], state='no_intact_near_line')
    if len(full) < cfg['min_trend_bars']+1:
        return {**result, 'state':'insufficient_history'}
    # Never fill, reorder or coerce missing prices into a valid line.
    for i,b in enumerate(full):
        if i and b['date'] <= full[i-1]['date']:
            return {**result, 'state':'invalid_history'}
        if any(type(b.get(k)) not in (int,float) or not math.isfinite(b[k]) or b[k] <= 0 for k in ('open','high','low','close')):
            return {**result, 'state':'invalid_prices'}
        if not b['low'] <= min(b['open'],b['close']) <= max(b['open'],b['close']) <= b['high']:
            return {**result, 'state':'invalid_prices'}
    volatility = atr14(full)
    if not volatility or volatility <= 0:
        return {**result, 'state':'atr_unavailable'}
    bars = full[-min(cfg['window'],recent.WINDOW):]
    line = recent.select(full,window=cfg['window'],min_trend_bars=cfg['min_trend_bars'])
    if line is None or not 0 <= line['gap_atr'] <= cfg['approach_atr']:
        return result
    stop = min(b['low'] for b in bars if b['date'] >= line['anchor_b_date'])
    setup = dict(kind=ROUTE, label=LABEL+' · 돌파 전', base_start=line['anchor_a_date'],
                 base_end=bars[-2]['date'], known_at=line['confirmed_at'], pivot=line['value_latest'],
                 structure_stop=stop, metrics=line, trendline=line, approximation=True,
                 source_basis='user_requested_downtrend_approach', approach_atr=cfg['approach_atr'])
    warnings = ['관찰 후보이며 상승·돌파를 보장하지 않습니다.', '참고 저점은 확정 손절 기준이 아닙니다.']
    if line['wick_breach']:
        warnings.append('장중 고가 침범 이력 있음 · 종가 돌파는 없음')
    item = signal(setup, bars, 'watch', ROUTE,
                  ['하락 추세선 아래 '+str(cfg['approach_atr'])+' ATR 이내 · 종가 돌파 전 관찰'], warnings)
    return dict(state='approach', signals=[item])


def scan(review, snapshot, cfg):
    series = {(s['market'],s['symbol']):s for s in snapshot['series']}
    matches = []
    for obs in review['observations']:
        # Preserve the existing common universe, price quality and history gate.
        if obs.get('features',{}).get('state') != 'available':
            continue
        key = (obs['market'],obs['symbol'])
        raw = completed(series[key]['bars'],review['session_dates'][key[0]])
        sessions = snapshot['sessions'][key[0]]
        bars = usable_tail(raw,sessions)
        check_bars(bars,sessions)
        if not bars or bars[-1]['date'] != review['session_dates'][key[0]]:
            continue
        found = analyze(bars,cfg,as_of=review['session_dates'][key[0]])
        if found['signals']:
            matches.append(dict(market=key[0],symbol=key[1],signal=found['signals'][0]))
    return matches


def merge(review, matches, cfg):
    out = deepcopy(review)
    out['config']['watchlist'][ROUTE] = cfg
    # Reconfiguration must remove obsolete route membership, without touching other routes.
    for obs in out['observations']:
        obs['signals'] = [s for s in obs.get('signals',[]) if s['route'] != ROUTE]
        obs['candidate'] = bool(obs['signals'])
        obs['phase'] = obs['signals'][0]['phase'] if obs['signals'] else 'excluded'
    for group in out['markets'].values():
        kept=[]
        for entry in group['entries']:
            entry['origins'] = [r for r in entry['origins'] if r != ROUTE]
            entry['route_signals'].pop(ROUTE,None)
            entry['selected_by'] = [r for r in entry['selected_by'] if r != ROUTE]
            entry['selected'] = bool(entry['selected_by'])
            entry.pop(ROUTE+'_rank',None)
            entry['reasons'] = [r for r in entry['reasons'] if not r.startswith(LABEL+' · 선까지 ')]
            if entry['origins']: kept.append(entry)
        group['entries'] = kept
    observations = {(o['market'],o['symbol']):o for o in out['observations']}
    for found in matches:
        market,symbol = found['market'],found['symbol']
        obs = observations[(market,symbol)]
        sig = deepcopy(found['signal'])
        obs['signals'] = [s for s in obs.get('signals',[]) if s['route'] != ROUTE]+[sig]
        obs['candidate'] = True
        if obs.get('phase') == 'excluded': obs['phase'] = 'watch'
        group = out['markets'][market]
        entry = next((e for e in group['entries'] if e['symbol']==symbol),None)
        if entry is None:
            entry = {k:obs.get(k) for k in ('market','symbol','name','as_of','price_basis','provider')}
            entry.update(analysis=obs,entry_signal=sig,phase='watch',entry_candidate=False,
                         origins=[],route_signals={},selected=False,selected_by=[],reasons=[])
            group['entries'].append(entry)
        if ROUTE not in entry['origins']: entry['origins'].append(ROUTE)
        entry['route_signals'][ROUTE] = sig
        entry['reasons'].append(LABEL+' · 선까지 '+format(sig['trendline']['gap_atr'],'.2f')+' ATR · 종가 돌파 전')
    for group in out['markets'].values():
        entries = group['entries']
        ordered = sorted([e for e in entries if ROUTE in e['origins']],key=lambda e:(e['route_signals'][ROUTE]['trendline']['gap_atr'],e['symbol']))
        cap = cfg['limit_per_market']
        for rank,e in enumerate(ordered,1):
            e[ROUTE+'_rank'] = rank
            if rank <= cap:
                e['selected'] = True
                if ROUTE not in e['selected_by']: e['selected_by'].append(ROUTE)
        group[ROUTE] = dict(candidate_count=len(ordered),selected_count=min(cap,len(ordered)),
                            symbols=[e['symbol'] for e in ordered],selected_symbols=[e['symbol'] for e in ordered[:cap]],
                            phase_counts=dict(watch=len(ordered)))
        group.update(candidate_count=len(entries), selected_count=sum(e['selected'] for e in entries),
                     selected_symbols=[e['symbol'] for e in entries if e['selected']],
                     overlap_symbols=[e['symbol'] for e in entries if len(e['origins'])>1],
                     phase_counts={p:sum(e['phase']==p for e in entries) for p in PHASES})
    out['candidate_count'] = sum(g['candidate_count'] for g in out['markets'].values())
    return out


def augment(review,snapshot,options=None):
    cfg = settings(options)
    return merge(review,scan(review,snapshot,cfg),cfg)


def cached(root,review,snapshot,source_hash,options=None):
    """Project a new route on verified saved prices without changing old artifacts."""
    cfg = settings(options)
    signature = dict(source=source_hash,config=cfg,review=review,
                     code={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                           {Path(__file__),Path(recent.__file__),Path(recent.trendln.__file__),
                            Path(completed.__code__.co_filename),Path(check_bars.__code__.co_filename)}})
    key = hashlib.sha256(json.dumps(signature,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    folder = Path(root)/'runtime/downtrend-approach'/key
    path = folder/'matches.json'
    with instance_lock(folder):
        if path.exists():
            record = json.loads(path.read_text(encoding='utf-8'))
            matches = record['matches']
            digest = hashlib.sha256(json.dumps(matches,sort_keys=True).encode()).hexdigest()
            if record['sha256'] != digest: raise ValueError('downtrend_cache_hash_mismatch')
        else:
            matches = scan(review,snapshot,cfg)
            record = dict(matches=matches,sha256=hashlib.sha256(json.dumps(matches,sort_keys=True).encode()).hexdigest())
            atomic_write(path,json.dumps(record,ensure_ascii=False,allow_nan=False))
    return merge(review,matches,cfg)

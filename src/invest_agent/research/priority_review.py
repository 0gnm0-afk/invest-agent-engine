"""Offline review queue; original screen rows are never mutated."""
from collections import Counter
from copy import deepcopy
import hashlib
import html
import json
import math
from pathlib import Path

from invest_agent.trading import trendlines
from invest_agent.trading.market import usable_tail, check_bars
from invest_agent.trading.structure_math import atr

DEFAULTS = {
    'limit_per_market': 15, 'recent_stage_bars': 5, 'intermediate_stage_bars': 20,
    'near_ma_atr': 1.0, 'recent_line_bars': 5, 'resistance_approach_atr': 0.5,
    'trend_window': 126, 'sector_positive_rs': 0.0, 'sector_breadth_fraction': 0.5,
    'sort_fields': ['recency_band', 'ma_band', 'line_band', 'sector_rs_band',
                    'sector_trend_band', 'sector_breadth_band', 'stage_age',
                    'sector_rs63_desc', 'original_position'],
}
POLICY = ('기본 비교 순서는 최근 단계 구간 → 장기 SMA 근접 → 저항선 접근/최근 종가 돌파·지지선 이탈 구별 → '
          '섹터 RS63·절대추세·참여 폭 → 단계 경과 세션 → RS63 → 원순서. '
          '가중 합산 점수 없음. 활동비는 방향 없는 거래활동이므로 설명만 제공합니다. '
          '수익률 예측·매수/매도 신호·실제 자금 순유입이 아닙니다.')
LABELS = {
    'no_valid_anchors': '유효한 확정 앵커 쌍 없음', 'insufficient_history': '기간 자료 부족',
    'unavailable': '자료 없음', 'analysis_error': '추세선 계산 오류',
    'resistance_approach': '하락 저항선 접근', 'resistance_recent_break': '하락 저항선 최근 종가 돌파',
    'resistance_old_break': '하락 저항선 과거 종가 돌파', 'resistance_retraced': '저항선 돌파 후 재하회',
    'resistance_wick_breach': '저항선 고가 침범·종가 돌파 없음', 'resistance_below': '하락 저항선 아래',
    'support_recent_loss': '상승 지지선 최근 종가 이탈', 'support_old_loss': '상승 지지선 과거 종가 이탈',
    'support_reclaimed': '지지선 이탈 후 회복', 'support_wick_breach': '지지선 저가 침범·종가 이탈 없음',
    'support_intact': '상승 지지선 유지',
}

def settings(overrides=None):
    cfg = deepcopy(DEFAULTS)
    overrides = overrides or {}
    if set(overrides) - set(cfg):
        raise ValueError('unknown_priority_settings')
    cfg.update(overrides)
    for key in ('limit_per_market', 'recent_stage_bars', 'intermediate_stage_bars', 'recent_line_bars'):
        if type(cfg[key]) is not int or cfg[key] < 0:
            raise ValueError('invalid_priority_setting:'+key)
    if cfg['intermediate_stage_bars'] < cfg['recent_stage_bars'] or cfg['trend_window'] not in (63,126,252):
        raise ValueError('invalid_priority_windows')
    for key in ('near_ma_atr','resistance_approach_atr','sector_positive_rs','sector_breadth_fraction'):
        if not isinstance(cfg[key],(int,float)) or not math.isfinite(cfg[key]):
            raise ValueError('invalid_priority_setting:'+key)
    if cfg['near_ma_atr'] < 0 or cfg['resistance_approach_atr'] < 0 or not 0 <= cfg['sector_breadth_fraction'] <= 1:
        raise ValueError('invalid_priority_threshold')
    fields = cfg['sort_fields']
    if not isinstance(fields,list) or not fields or len(set(fields)) != len(fields) or set(fields)-set(DEFAULTS['sort_fields']):
        raise ValueError('invalid_priority_sort_fields')
    return cfg

def line_observation(bars, line, side, cfg):
    if line['state'] != 'available':
        return {'state':line['state'], 'label':LABELS.get(line['state'],line['state']), 'reason':line['state']}
    dates = {b['date']:i for i,b in enumerate(bars)}
    broken = line['first_close_break_date']
    age = len(bars)-1-dates[broken] if broken else None
    beyond = line['latest_close_beyond_line']
    distance = (bars[-1]['close']-line['value_latest'])
    volatility = atr(bars)[-1]
    distance_atr = distance/volatility if volatility else None
    if side == 'upper':
        if broken:
            state = ('resistance_recent_break' if age <= cfg['recent_line_bars'] else 'resistance_old_break') if beyond else 'resistance_retraced'
        elif line['first_price_breach_date']:
            state = 'resistance_wick_breach'
        elif distance_atr is not None and -cfg['resistance_approach_atr'] <= distance_atr <= 0:
            state = 'resistance_approach'
        else:
            state = 'resistance_below'
    else:
        if broken:
            state = ('support_recent_loss' if age <= cfg['recent_line_bars'] else 'support_old_loss') if beyond else 'support_reclaimed'
        else:
            state = 'support_wick_breach' if line['first_price_breach_date'] else 'support_intact'
    return {'state':state,'label':LABELS[state], 'bars_since_first_close_break':age,
            'first_close_break_date':broken, 'first_price_breach_date':line['first_price_breach_date'],
            'confirmed_at':line['confirmed_at'], 'distance_to_line_atr':distance_atr,
            'latest_close_beyond_line':beyond,
            'basis':'current_window_confirmed_anchors_not_historical_selection_backtest'}

def candidate_bars(snapshot, row, by_series):
    item = by_series[(row['market'],row['symbol'])]
    bars = usable_tail(item['bars'],snapshot['sessions'][row['market']])
    check_bars(bars,snapshot['sessions'][row['market']])
    return bars

def build(screen, snapshot, overrides=None):
    cfg = settings(overrides)
    by_series = {(s['market'],s['symbol']):s for s in snapshot['series']}
    markets = {}
    for market in ('KR','US'):
        candidates = [r for r in screen['rows'] if r['market']==market and r['state']=='candidate']
        entries = []
        for position,row in enumerate(candidates,1):
            active = [(p,w) for p,w in row.get('sperandeo',{}).get('windows',{}).items()
                      if w.get('stage') in ('S1','S2','S3','S4') and w.get('bars_in_stage') is not None]
            active.sort(key=lambda pair:(pair[1]['bars_in_stage'], {'126':0,'63':1,'252':2}[pair[0]]))
            period,stage = active[0] if active else (None,{})
            age = stage.get('bars_in_stage')
            taver = row.get('taver',{})
            distances = [(p,v['distance_to_ma_atr']) for p,v in taver.get('lines',{}).items()
                         if v.get('distance_to_ma_atr') is not None]
            nearest = min(distances,key=lambda p:abs(p[1])) if distances else (None,None)
            near = nearest[1] is not None and abs(nearest[1]) <= cfg['near_ma_atr']
            try:
                bars = candidate_bars(snapshot,row,by_series)
                lines = trendlines.analyze(bars)
                window = lines['windows'][str(cfg['trend_window'])]
                upper = line_observation(bars,window['upper_trendline'],'upper',cfg)
                lower = line_observation(bars,window['lower_trendline'],'lower',cfg)
            except Exception as exc:
                reason = type(exc).__name__
                lines = {'state':'analysis_error','reason':reason}
                upper = lower = {'state':'analysis_error','label':LABELS['analysis_error'],'reason':reason}
            ctx = row.get('sector_context',{})
            horizons = ctx.get('horizons',{})
            rs = horizons.get('63',{}).get('relative_strength')
            trend = ctx.get('trend',{})
            trend_values = [trend.get(k) for k in ('distance_sma20','distance_sma60','sma20_change5')]
            breadth = ctx.get('breadth_sma60',{}).get('fraction')
            axes = {
                'recency_band': 3 if age is None else 0 if age<=cfg['recent_stage_bars'] else 1 if age<=cfg['intermediate_stage_bars'] else 2,
                'ma_band':0 if near else 2 if nearest[1] is None else 1,
                'line_band':2 if lower['state'] in ('support_recent_loss','support_old_loss') else
                            0 if upper['state'] in ('resistance_approach','resistance_recent_break') else 1,
                'sector_rs_band':2 if rs is None else 0 if rs>cfg['sector_positive_rs'] else 1,
                'sector_trend_band':2 if any(v is None for v in trend_values) else 0 if all(v>0 for v in trend_values) else 1,
                'sector_breadth_band':2 if breadth is None else 0 if breadth>=cfg['sector_breadth_fraction'] else 1,
                'stage_age':age if age is not None else 10**9,
                'sector_rs63_desc':-rs if rs is not None else 0,
                'original_position':position,
            }
            reasons = [f"{period or '?'}세션 {stage.get('stage','결측')} 진입 후 {age if age is not None else '결측'}세션",
                       f"장기 SMA{nearest[0] or '?'} 거리 {round(nearest[1],2) if nearest[1] is not None else '결측'} ATR",
                       upper['label'], lower['label'],
                       f"섹터 RS63 {rs:.2%}" if rs is not None else '섹터 RS63 결측·원후보 유지']
            entry = {'market':market,'symbol':row['symbol'],'name':row.get('name',''), 'as_of':row.get('as_of'),
                     'original_candidate_position':position,'axes':axes,'reasons':reasons,
                     'stage':{'window':period,'stage':stage.get('stage'),'entered_at':stage.get('stage_entered_at'),'age_bars':age},
                     'taver':{'nearest_period':nearest[0],'nearest_distance_atr':nearest[1], 'near':near,
                              'primary_period':taver.get('primary_ma_period'),'primary_state':taver.get('primary_ma_state')},
                     'sector':{'name':ctx.get('group_name'),'state':ctx.get('state'),'reason':ctx.get('reason'),
                               'rs63':rs,'rs126':horizons.get('126',{}).get('relative_strength'),
                               'trend':{k:v for k,v in trend.items() if k not in ('members','exclusions')},
                               'breadth_fraction':breadth,'breadth_used':ctx.get('breadth_sma60',{}).get('used'),
                               'activity_median':ctx.get('activity',{}).get('median_ratio'),
                               'activity_used':ctx.get('activity',{}).get('used'),'warnings':ctx.get('warnings',[])},
                     'price_basis':row.get('price_basis'),'provider':row.get('technical_context',{}).get('provider'),
                     'data_missing':row.get('technical_context',{}).get('missing'),
                     'upper':upper,'lower':lower,'trendlines':lines}
            entry['sort_key'] = [axes[f] for f in cfg['sort_fields']]
            entries.append(entry)
        entries.sort(key=lambda e:(e['sort_key'],e['original_candidate_position']))
        selected = entries[:cfg['limit_per_market']]
        cutoff = selected[-1] if selected else None
        for rank,entry in enumerate(entries,1):
            entry.update(rank=rank, selected=rank<=len(selected))
            if entry['selected']:
                entry['selection_reason'] = f"시장별 검토 순위 {rank}, 상한 {cfg['limit_per_market']} 이내"
            else:
                first = next((f for f in cfg['sort_fields'] if cutoff and entry['axes'][f]!=cutoff['axes'][f]),'original_position')
                entry['selection_reason'] = (f"우선 목록 상한 {cfg['limit_per_market']} 밖({rank}위); 전체 후보 유지. "
                    + (f"경계 {cutoff['symbol']}와 첫 차이 {first}: {entry['axes'][first]} / {cutoff['axes'][first]}" if cutoff else '설정 상한 0'))
        markets[market] = {'candidate_count':len(candidates),'selected_count':len(selected),
            'selected_symbols':[e['symbol'] for e in selected], 'entries':entries,
            'distribution':{field:dict(Counter(str(e['axes'][field]) for e in entries))
                            for field in ('recency_band','ma_band','line_band','sector_rs_band')}}
    return {'schema':1,'authority':'human_chart_review_order_only','policy':POLICY+' 현재 적용 sort_fields: '+', '.join(cfg['sort_fields'])+' (앞 축부터 비교, 마지막 동률은 원순서).','config':cfg,
            'session_dates':screen['session_dates'],'markets':markets}

def confirmed_overlay(price,bars,offset,result):
    """Reuse engine line coordinates; draw actionable segment only after confirmation."""
    dates = {b['date']:i for i,b in enumerate(bars)}
    window = result['windows'][str(result['primary_window'])]
    for key,label,color in [('upper_trendline','Resistance','#b66a00'),('lower_trendline','Support','#008575')]:
        line = window[key]
        if line['state'] != 'available':
            continue
        a = dates[line['anchor_a_date']]
        known = dates[line['confirmed_at']]
        end = dates[line['first_price_breach_date']] if line['first_price_breach_date'] else len(bars)-1
        xs = list(range(max(offset,known),end+1))
        price.plot([i-offset for i in xs],[line['anchor_a_price']+line['slope_per_bar']*(i-a) for i in xs],
                   color=color,linestyle='--',linewidth=1.4,marker='D' if len(xs)==1 else None,
                   markersize=4,label=label+' known '+line['confirmed_at'])
        for anchor in ('a','b'):
            i = dates[line[f'anchor_{anchor}_date']]
            if i >= offset:
                price.scatter([i-offset],[line[f'anchor_{anchor}_price']],s=24,facecolors='none',edgecolors=color)
                price.annotate('past '+anchor.upper(),(i-offset,line[f'anchor_{anchor}_price']),
                               xytext=(3,8),textcoords='offset points',fontsize=7,color=color)

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def charts(review,screen,snapshot,output,draw=None):
    from invest_agent.trading import charts as engine_charts
    from invest_agent.trading.chart_history import calculate
    draw = draw or engine_charts.draw
    rows = {(r['market'],r['symbol']):r for r in screen['rows']}
    series = {(s['market'],s['symbol']):s for s in snapshot['series']}
    results = []
    folder = output/'charts'
    folder.mkdir(exist_ok=True)
    for market in review['markets'].values():
        for entry in market['entries']:
            if not entry['selected']:
                continue
            key = (entry['market'],entry['symbol'])
            stem = entry['market']+'-'+hashlib.sha256('|'.join(key).encode()).hexdigest()[:16]
            record = {'market':key[0],'symbol':key[1],'as_of':entry['as_of'],'state':'failed'}
            try:
                row = rows[key]
                bars = candidate_bars(snapshot,row,series)
                data = calculate(bars,key[0])
                if entry['trendlines'].get('state') == 'analysis_error':
                    raise ValueError(entry['trendlines']['reason'])
                data['trendlines'] = deepcopy(entry['trendlines'])
                data['trendlines']['primary_window'] = review['config']['trend_window']
                png = folder/(stem+'.png')
                draw(bars,png,f"{entry['symbol']} {entry['name']} | {entry['as_of']} | {row['currency']} | SYNTHETIC observation only",
                     row['taver']['primary_ma_period'],chart_data=data,trend_renderer=confirmed_overlay)
                write(folder/(stem+'.json'),{'entry':entry,'history':data['history'],
                    'display_rule':'Line starts at B confirmation; hollow past anchors are retrospective. Stops at first price breach.'})
                esc = html.escape
                notes = [*entry['reasons'],entry['selection_reason'],
                    '빈 원은 현재 시점에서 확인한 과거 앵커입니다. 선은 B 확정일부터 첫 고가/저가 침범까지만 표시합니다. 당일 확정이면 마름모 한 점으로 표시합니다.',
                    '현재 기간으로 선택한 선이며 당시 선택 가능했던 선을 재현한 백테스트가 아닙니다.',
                    '저항선 종가 돌파와 지지선 종가 이탈은 별개 관찰입니다. 과거 침범 뒤 연장선 거리는 관찰용입니다.',
                    f"가격 기준: {entry['price_basis']} / 공급자: {entry['provider']} / 결측: {entry['data_missing']}",
                    '합성 시세·합성 분류·가상 평일 세션입니다. 실제 시장·기업·성과를 나타내지 않습니다.',
                    f"표시 이력: {data['history']['state']} ({data['history']['available_daily_bars']}/{data['history']['required_daily_bars']} 일봉)",
                    '63/126/252 기간별 선의 유효성·앵커·확정시점은 아래 상세에 보존합니다.']
                body = ''.join('<li>'+esc(str(n))+'</li>' for n in notes)
                detail = esc(json.dumps(entry['trendlines'],ensure_ascii=False,indent=2))
                page = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>'+esc(entry['symbol'])+' 검토 차트</title>'
                    '<style>body{font-family:system-ui;margin:24px;line-height:1.6}img{max-width:100%}pre{white-space:pre-wrap}</style>'
                    '<a href="../report.html">보고서로</a><h1>'+esc(entry['market']+' '+entry['symbol']+' '+entry['name'])+'</h1>'
                    '<p>자료 완료 세션 '+esc(entry['as_of'])+' · 조사 편의용 · 수익률 예측/매매 신호 아님</p>'
                    '<img src="'+stem+'.png" alt="확정 이후 추세선과 장기 이동평균 차트"><ul>'+body+'</ul>'
                    '<details><summary>기간별 추세선·유효한 선이 없는 사유</summary><pre>'+detail+'</pre></details></html>')
                (folder/(stem+'.html')).write_text(page,encoding='utf-8')
                record.update(state='available',path='charts/'+stem+'.html',image='charts/'+stem+'.png',metadata='charts/'+stem+'.json',
                              image_sha256=hashlib.sha256(png.read_bytes()).hexdigest())
            except Exception as exc:
                record['reason'] = type(exc).__name__
            results.append(record)
    return {'requested':len(results),'available':sum(r['state']=='available' for r in results),
            'failed':sum(r['state']=='failed' for r in results),'rows':results}

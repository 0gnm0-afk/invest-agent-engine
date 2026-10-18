"""PDF momentum screener, separate from the preserved historical S-stage screen."""
from bisect import bisect_left,bisect_right
from copy import deepcopy
import csv
import hashlib
import html
import json
from pathlib import Path
from invest_agent.trading import momentum_entry as model
from invest_agent.trading.market import usable_tail,check_bars
from display_names import stock_label
from display_format import money, currency_for, price_text, display_analysis

SOURCE={'title':'최고의 주식을 고르는 대가들의 노하우','publisher':'DB금융투자','date':'2023-08-21',
        'pdf_pages':45,'sha256':'26ddc0f066941c5406f59d8da2dad84681548a9a55e93ad63c417955e91149d7'}
ROUTES={'pdf_momentum':'추세, 조정, 돌파','sperandeo':'스페란데오 S4'}
REASONS={'not_selected':'진입 조건 미충족','relative_strength':'12개월 상대강도 부족/결측','positive_2_3_4m':'2·3·4개월 상승 조건 미충족',
'market_supportive':'시장 추세 지지 부족/결측','above_150_200':'150·200일선 아래','ma150_above_200':'150/200일선 역전',
'ma200_rising_month':'200일선 한 달 상승 미충족','ma50_above_150_200':'50/150/200일선 정렬 미충족',
'above_50':'50일선 아래','above_year_low_30pct':'52주 저점 대비 30% 미달','within_year_high_25pct':'52주 고점에서 25% 초과 이격',
'no_confirmed_pattern_breakout':'확인된 조정 패턴 돌파 없음','setup_only_not_entry':'준비 모양만 존재·돌파 미확인',
'fresh_breakout':'돌파 시점이 오래됨','close_crossed_pivot':'종가 피벗 돌파 미확인','volume_expansion':'돌파 거래량 부족',
'strong_breakout_close':'돌파봉 종가/몸통 약함','pivot_held':'돌파 후 피벗 종가 재하회','structure_held':'구조 저점 훼손',
'ma20_held':'돌파 후 20일선 종가 하회','not_extended':'허용 진입 범위 초과','bounded_structure_risk':'구조 무효화선까지 8% 초과/비정상',
'common_gate_not_passed':'공통 유니버스 제외','insufficient_history':'253봉 이력 부족','analysis_error':'계산 불가'}


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def ordered_structure(structure):
    """Preserve engine period order across sorted-key JSON storage and replay."""
    if not structure:
        return structure
    return {**structure, 'windows': dict(sorted(structure.get('windows', {}).items(),
                                              key=lambda item: int(item[0])))}


def build(screen,snapshot,overrides):
    if 'watchlist' in overrides:
        import watch_review
        old={k:v for k,v in overrides.items() if k!='watchlist'}
        return watch_review.augment(build(screen,snapshot,old),screen,snapshot,overrides['watchlist'])
    allowed={'strategy','limit_per_market','sperandeo_limit_per_market','taver_enabled','momentum','trendline_policy'}
    if set(overrides)-allowed:raise ValueError('unknown_pdf_priority_setting')
    if overrides.get('taver_enabled',False) is not False:raise ValueError('taver_excluded_from_pdf_strategy')
    cfg={'strategy':'pdf_momentum','limit_per_market':10,'sperandeo_limit_per_market':10,'taver_enabled':False,**overrides}
    if cfg.get('trendline_policy') not in (None,'trendln-0.1.18-v1'):raise ValueError('unknown_trendline_policy')
    cfg['momentum']=model.settings(cfg.get('momentum'))
    for k in ('limit_per_market','sperandeo_limit_per_market'):
        if type(cfg[k]) is not int or not 0<=cfg[k]<=50:raise ValueError('invalid_'+k)
    by_series={(s['market'],s['symbol']):s for s in snapshot['series']};prepared={};errors={};returns={'KR':[],'US':[]}
    for row in screen['rows']:
        key=(row['market'],row['symbol'])
        if not row.get('universe',{}).get('universe_pass') or row['state']=='unavailable':continue
        try:
            item=by_series[key];sessions=snapshot['sessions'][key[0]]
            raw=model.completed(item['bars'],screen['session_dates'][key[0]])
            bars=usable_tail(raw,sessions);check_bars(bars,sessions);f=model.features(bars)
            prepared[key]=(bars,f)
            if f['state']=='available':returns[key[0]].append(f['returns']['252'])
        except (ValueError,KeyError,IndexError) as exc:errors[key]=str(exc)
    for values in returns.values():values.sort()
    markets={}
    for market,symbol in [('KR','^KS11'),('US','^GSPC')]:
        try:
            item=snapshot['benchmarks'][symbol];sessions=snapshot['sessions'][market]
            bars=model.completed(item['bars'],screen['session_dates'][market]);check_bars(bars[-70:],sessions[-70:])
            context=model.market_context(bars,screen['session_dates'][market])
            context.update(symbol=symbol,proxy=item.get('benchmark_proxy'),provider=item.get('provider'))
        except (ValueError,KeyError,IndexError) as exc:context={'state':'unavailable','supportive':False,'reason':str(exc),'symbol':symbol}
        markets[market]=context
    observations=[]
    for row in screen['rows']:
        key=(row['market'],row['symbol']);identity={k:row.get(k) for k in ('market','symbol','name','as_of','price_basis')}
        if key not in prepared:
            state='analysis_error' if key in errors else 'common_gate_not_passed'
            observations.append({**identity,'candidate':False,'main_candidate':False,'sperandeo_candidate':False,'state':state,
                'rejections':[errors[key]] if key in errors else row.get('universe',{}).get('universe_rejection_reasons',[row.get('reason',state)])})
            continue
        bars,f=prepared[key];pool=returns[key[0]];rs=None
        if f['state']=='available' and len(pool)>1:
            value=f['returns']['252'];rs=100*((bisect_left(pool,value)+bisect_right(pool,value)-1)/2)/(len(pool)-1)
        try:
            structure=ordered_structure(row.get('sperandeo'))
            if cfg.get('trendline_policy')=='trendln-0.1.18-v1':
                from recent_sperandeo import analyze as recent_structure
                structure=recent_structure(bars,as_of=screen['session_dates'][key[0]])
            obs=model.analyze(bars,rs,markets[key[0]],structure,cfg['momentum'],as_of=screen['session_dates'][key[0]])
            if cfg.get('trendline_policy'):obs['recent_structure']=structure
        except (ValueError,KeyError,IndexError) as exc:
            obs={'state':'analysis_error','candidate':False,'main_candidate':False,'sperandeo_candidate':False,'rejections':[str(exc)]}
        obs.update(identity,rs_population=len(pool),provider=by_series[key].get('provider'),
                   history_truncated=len(bars)<len(model.completed(by_series[key]['bars'],screen['session_dates'][key[0]])))
        observations.append(obs)
    groups={}
    for market in ('KR','US'):
        candidates=[o for o in observations if o['market']==market and (o['main_candidate'] or o['sperandeo_candidate'])]
        entries={}
        for o in candidates:
            event=o['selected_event']
            entries[o['symbol']]={k:o.get(k) for k in ('market','symbol','name','as_of','price_basis','provider')}
            entries[o['symbol']].update(analysis=o,entry_signal=event,selected=False,selected_by=[],
                origins=[route for route,key in [('pdf_momentum','main_candidate'),('sperandeo','sperandeo_candidate')] if o[key]],
                reasons=[event['label']+' · '+event['breakout_date'],
                    f"RS {o['rs_percentile']:.1f} / {o['rs_population']}종목 · 거래량 {event['volume_ratio']:.2f}배",
                    f"피벗 {event['pivot']:.2f} · 이격 {event['extension_pct']:.1%} · 구조 위험 {event['structure_risk_pct']:.1%}",
                    '재무·기관·질적 조건 미검증'])
        group={}
        for route,cap in [('pdf_momentum',cfg['limit_per_market']),('sperandeo',cfg['sperandeo_limit_per_market'])]:
            selected=sorted([e for e in entries.values() if route in e['origins']],key=lambda e:(
                min(x['age_sessions'] for x in e['analysis']['active_events'] if x['route']==route),
                min(x['structure_risk_pct'] for x in e['analysis']['active_events'] if x['route']==route),
                -e['analysis']['rs_percentile'],e['symbol']))
            for rank,e in enumerate(selected,1):
                e[route+'_rank']=rank
                if rank<=cap:e['selected']=True;e['selected_by'].append(route)
            group[route]={'candidate_count':len(selected),'selected_count':min(len(selected),cap),
                'symbols':[e['symbol'] for e in selected],'selected_symbols':[e['symbol'] for e in selected[:cap]]}
        group.update(entries=list(entries.values()),candidate_count=len(entries),selected_count=sum(e['selected'] for e in entries.values()),
            selected_symbols=[e['symbol'] for e in entries.values() if e['selected']],
            overlap_symbols=[e['symbol'] for e in entries.values() if len(e['origins'])>1])
        groups[market]=group
    return {'schema':3,'strategy':'pdf_momentum','authority':'chart_entry_review_only','source':SOURCE,
        'config':cfg,'session_dates':screen['session_dates'],'market_context':markets,'markets':groups,
        'observations':observations,'taver_state':'disabled','legacy_sperandeo_candidate_count':screen['candidate_count'],
        'candidate_count':sum(g['candidate_count'] for g in groups.values()),
        'policy':'추세·상대강도 → 조정/거래량 축소 → 거래량 돌파 → 최근 0~2세션 유지·추격/구조 위험 제한. 준비 단계는 선정하지 않습니다.',
        'limitations':['차트 진입 검토 후보이며 즉시 상승·수익을 보장하지 않습니다.','CAN SLIM 재무·기관·신제품 및 코드33은 미검증입니다.',
            'RS는 현재 공통 유니버스의 253봉 가용 종목별 252세션 수익률 백분위이며 공식 IBD 점수가 아닙니다.',
            '패턴은 일봉 기하 근사입니다. 역사적 구성 백테스트·수익성 검증은 아닙니다.']}


def draw(bars,path,entry):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from matplotlib.patches import Rectangle
    from invest_agent.trading.charts import _TITLE_FONT
    currency=currency_for(entry);event=entry['entry_signal'];dates={b['date']:i for i,b in enumerate(bars)}
    start=max(0,min(len(bars)-100,dates.get(event['base_start'],len(bars)-160)-10));shown=bars[start:]
    fig,(ax,vol)=plt.subplots(2,1,figsize=(14,8),sharex=True,gridspec_kw={'height_ratios':[4,1]})
    try:
        for i,b in enumerate(shown):
            color='#12806a' if b['close']>=b['open'] else '#c54848'
            ax.vlines(i,b['low'],b['high'],color=color,lw=.7)
            ax.add_patch(Rectangle((i-.32,min(b['open'],b['close'])),.64,max(abs(b['close']-b['open']),b['close']*.0002),color=color))
            vol.bar(i,b['volume'],color=color,width=.65)
        for n,color in [(20,'#dd9500'),(50,'#2271b2'),(150,'#8254a8'),(200,'#687780')]:
            xs=[i-start for i in range(start,len(bars)) if i+1>=n]
            ax.plot(xs,[model.sma(bars,n,x+start+1) for x in xs],lw=1,color=color,label=f'SMA {n}')
        known=dates.get(event['known_at'],start)-start
        if event.get('trendline') or event.get('resistance_trendline'):
            line=event.get('trendline') or event['resistance_trendline'];a=dates[line['anchor_a_date']]-start;b=dates[line['anchor_b_date']]-start
            ax.plot([a,b],[line['anchor_a_price'],line['anchor_b_price']],color='#dd9500',label='Descending resistance')
            ax.plot([b,len(shown)-1],[line['anchor_b_price'],line['value_latest']],color='#dd9500',ls='--',label='Resistance extension')
        if not event.get('trendline'):
            ax.hlines(event['pivot'],max(known,0),len(shown)-1,color='#1d4ed8',ls='--',label=f"Pivot {money(event['pivot'],currency)}")
        ax.hlines(event['structure_stop'],max(known,0),len(shown)-1,color='#c54848',ls=':',label=f"Structure invalidation {money(event['structure_stop'],currency)}")
        zone=event.get('entry_zone')
        if zone:ax.fill_between([max(known,0),len(shown)-1],zone['lower_exclusive'],zone['upper_inclusive'],color='#3b82f6',alpha=.1,label='Entry review zone')
        ax.axvspan(max(0,dates.get(event['base_start'],start)-start),max(0,dates.get(event['base_end'],start)-start),color='#999',alpha=.08,label='Pre-breakout base')
        if event.get('breakout_date'):
            i=dates[event['breakout_date']]-start
            ax.scatter(i,event['breakout_close'],marker='^',s=90,color='#111827',zorder=5,label='Confirmed close breakout')
        else:
            ax.set_xlim(-1,len(shown)+10)
            for level,color in ([(event['structure_stop'],'#c54848')] if event.get('trendline') else [(event['pivot'],'#1d4ed8'),(event['structure_stop'],'#c54848')]):
                ax.hlines(level,max(known,0),len(shown)+5,color=color,ls='--',lw=1)
                ax.text(len(shown)+1,level,money(level,currency),color=color,fontsize=8)
            ax.scatter(len(shown)-1,bars[-1]['close'],marker='o',s=35,color='#111827',label='Observation only')
        ax.set_title(stock_label(entry)+' | '+event.get('phase_label','진입 조건 충족')+' · '+event['label']+' | '+entry['as_of'],fontfamily=_TITLE_FONT)
        ax.set_ylabel(currency)
        from matplotlib.ticker import FuncFormatter
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: money(value,currency)))
        ax.legend(loc='upper left',fontsize=8);ax.grid(alpha=.15)
        xs=[i-start for i in range(start,len(bars)) if i>=50]
        vol.plot(xs,[sum(b['volume'] for b in bars[x+start-50:x+start])/50 for x in xs],color='#111',lw=1,label='Prior 50-day volume')
        vol.legend(fontsize=8);vol.set_ylabel('Volume');vol.grid(alpha=.15)
        ticks=list(range(0,len(shown),max(1,len(shown)//8)));vol.set_xticks(ticks,[shown[i]['date'] for i in ticks],rotation=20)
        fig.tight_layout();fig.savefig(path,dpi=120)
    finally:plt.close(fig)


def charts(review,screen,snapshot,output,draw_fn=None):
    draw_fn=draw_fn or draw;folder=output/'charts';folder.mkdir(exist_ok=True)
    series={(s['market'],s['symbol']):s for s in snapshot['series']};rows=[]
    for group in review['markets'].values():
        for e in group['entries']:
            if not e['selected']:continue
            stem=e['market']+'-'+hashlib.sha256((e['market']+'|'+e['symbol']).encode()).hexdigest()[:16]
            row={'market':e['market'],'symbol':e['symbol'],'as_of':e['as_of'],'state':'failed'}
            try:
                b=usable_tail(model.completed(series[(e['market'],e['symbol'])]['bars'],e['as_of']),snapshot['sessions'][e['market']])
                image=folder/(stem+'.png');draw_fn(b,image,e);write(folder/(stem+'.json'),{'entry':e,'source':({'basis':'user_requested_base_watch_operational_assumptions','rules':'B1-B6'} if e['entry_signal']['route']=='base_watch' else {'basis':'user_requested_downtrend_approach'} if e['entry_signal']['route']=='downtrend_approach' else SOURCE)})
                esc=html.escape
                body='<h1>'+esc(stock_label(e))+'</h1><p>완료 세션 '+esc(e['as_of'])+' · '+esc(' / '.join('바닥·눌림 횡보' if x=='base_watch' else '스페란데오' if x=='sperandeo' and review.get('schema')==4 else '하락 추세선 근접' if x=='downtrend_approach' else ROUTES[x] for x in e['origins']))+'</p>'
                body+='<p>'+esc(e.get('entry_signal',{}).get('phase_label','진입 조건 충족'))+' · 재무/기관/질적 조건 미검증 · 자동 주문 없음</p><img style="max-width:100%" src="'+stem+'.png">'
                body+='<ul>'+''.join('<li>'+esc(price_text(x,currency_for(e)))+'</li>' for x in e['reasons'])+'</ul>'
                body+='<p>구조 무효화 참고선은 자동 손절 주문이 아닙니다. 갭·슬리피지·현재 장중 가격을 반영하지 않습니다.</p>'
                body+='<details><summary>조건·원수치·패턴 근거·미검증 항목</summary><pre>'+esc(json.dumps(display_analysis(e['analysis'],currency_for(e)),ensure_ascii=False,indent=2))+'</pre></details>'
                (folder/(stem+'.html')).write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>'+esc(stock_label(e))+'</title><style>body{font:15px system-ui;margin:24px}pre{white-space:pre-wrap}</style><a href="../report.html">보고서</a>'+body+'</html>',encoding='utf-8')
                row.update(state='available',path='charts/'+stem+'.html',image='charts/'+stem+'.png',metadata='charts/'+stem+'.json',image_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
            except Exception as exc:row['reason']=type(exc).__name__+': '+str(exc)
            rows.append(row)
    return {'requested':len(rows),'available':sum(r['state']=='available' for r in rows),'failed':sum(r['state']=='failed' for r in rows),'rows':rows}


def report_lines(review,chart_result):
    if review.get('schema')==4:
        import watch_review
        return watch_review.report_lines(review,chart_result)
    refs={(r['market'],r['symbol']):r for r in chart_result['rows']}
    lines=['','## 적극적 진입 검토 — 차트 조건 충족','',review['policy'],
        '테이버 선정 제외. 재무·기관·신제품·코드33 미검증. 후보가 없으면 조건을 완화해 채우지 않습니다.',
        '[전체 판정·탈락 이유](entry-scan.html) · [계산 원수치](priority-review.json) · [기준·출처](screening-rules.html)','']
    for market,g in review['markets'].items():
        context=review['market_context'][market]
        lines += [f"### {market} · 고유 후보 {g['candidate_count']}개 / 우선 {g['selected_count']}개",'',
            '시장 맥락: '+('상승 추세 참고 조건 충족' if context.get('supportive') else '상승 추세 참고 조건 미충족/결측')+' · '+str(context.get('symbol'))+' · '+str(context.get('as_of','자료 없음')),'']
        by_symbol={e['symbol']:e for e in g['entries']}
        for route in ROUTES:
            group=g[route];lines += [f"#### {ROUTES[route]}: 후보 {group['candidate_count']} / 표시 {group['selected_count']}",'']
            if not group['selected_symbols']:
                lines += ['현재 적극적 진입 조건을 모두 충족한 종목이 없습니다.',''];continue
            lines+=['| 종목·차트 | 돌파 시점·패턴 | RS / 거래량 | 피벗·허용 상단 | 현재 이격 / 구조 위험 |','|---|---|---|---|---|']
            for symbol in group['selected_symbols']:
                e=by_symbol[symbol];v=min([x for x in e['analysis']['active_events'] if x['route']==route],key=lambda x:(x['age_sessions'],x['structure_risk_pct']))
                ref=refs.get((market,symbol),{});name=stock_label(e);link=f"[{name}]({ref['path']})" if ref.get('state')=='available' else name+' (차트 생성 실패)'
                lines.append(f"| {link} | {v['breakout_date']} · {v['label']} | {e['analysis']['rs_percentile']:.1f} / {v['volume_ratio']:.2f}배 | {money(v['pivot'],currency_for(e))} ~ {money(v['entry_zone']['upper_inclusive'],currency_for(e))} | {v['extension_pct']:.1%} / {v['structure_risk_pct']:.1%} |")
            lines.append('')
    lines += [f"차트 {chart_result['available']}/{chart_result['requested']} · 실패 {chart_result['failed']}. 중복 경로는 동일 종목 차트로 연결합니다.",'']
    return lines


def write_outputs(output,review):
    if review.get('schema')==4:
        import watch_review
        return watch_review.write_outputs(output,review)
    esc=html.escape
    with (output/'candidates.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['market','symbol','name','as_of','origins','selected','pattern','breakout_date','pivot','entry_upper','structure_stop','rs_percentile','volume_ratio','unverified'])
        for group in review['markets'].values():
            for e in group['entries']:
                v=e['entry_signal'];writer.writerow([e['market'],e['symbol'],e['name'],e['as_of'],','.join(e['origins']),e['selected'],v['kind'],v['breakout_date'],v['pivot'],v['entry_zone']['upper_inclusive'],v['structure_stop'],e['analysis']['rs_percentile'],v['volume_ratio'],'financials_institutions_qualitative'])
    rows=[]
    for o in review['observations']:
        selected=o.get('main_candidate') or o.get('sperandeo_candidate')
        reason='; '.join(REASONS.get(k,k) for k in o.get('rejections',[]))
        state='차트 진입 후보' if selected else REASONS.get(o['state'],o['state'])
        rows.append('<tr data-candidate="'+str(bool(selected)).lower()+'"><td>'+esc(o['market'])+'</td><td>'+esc(stock_label(o))+'</td><td>'+esc(state)+'</td><td>'+esc(reason)+'</td><td><details><summary>근거</summary><pre>'+esc(json.dumps(display_analysis(o,currency_for(o)),ensure_ascii=False,indent=2))+'</pre></details></td></tr>')
    page='<!doctype html><html lang="ko"><meta charset="utf-8"><title>전체 진입 판정</title><style>body{font:14px system-ui;margin:24px}td{padding:8px;border-bottom:1px solid #ccc}pre{white-space:pre-wrap;max-width:750px}</style><a href="report.html">보고서</a><h1>전체 진입 판정·제외 이유</h1><p>자료 완료 세션 '+esc(str(review['session_dates']))+'</p><input id="q" placeholder="종목명/심볼 검색"><label><input type="checkbox" id="active">진입 후보만</label><table><thead><tr><th>시장</th><th>종목</th><th>상태</th><th>조건</th><th>원수치</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table><script>const q=document.getElementById("q"),a=document.getElementById("active");function filter(){for(const r of document.querySelectorAll("tbody tr")){r.hidden=(!r.textContent.toLowerCase().includes(q.value.toLowerCase()))||(a.checked&&r.dataset.candidate!=="true");}}q.addEventListener("input",filter);a.addEventListener("change",filter);</script></html>'
    (output/'entry-scan.html').write_text(page,encoding='utf-8')
    rules=Path(__file__).resolve().parents[1]/'docs/pdf-momentum-rules.md'
    (output/'screening-rules.html').write_text('<!doctype html><meta charset="utf-8"><title>판정 기준</title><a href="report.html">보고서</a><pre style="white-space:pre-wrap;font:15px system-ui">'+esc(rules.read_text(encoding='utf-8'))+'</pre>',encoding='utf-8')

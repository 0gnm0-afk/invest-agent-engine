"""Watch-stage selection and screener-first saved reports."""
from copy import deepcopy
from collections import Counter
import csv,html,json
from pathlib import Path
from invest_agent.trading import momentum_watch as watch, momentum_entry as engine, base_structure as base
from invest_agent.trading.market import usable_tail
from display_names import stock_label
from momentum_review import ordered_structure
from display_format import money, currency_for, display_analysis

def signal_key(s):return base.rank(s) if s['route']=='base_watch' else watch.key(s)
def rs_label(rs):return f'{rs:.1f}' if rs is not None else '미산출'

LABELS=watch.PHASES
ROUTES={'pdf_momentum':'추세, 조정, 돌파','sperandeo':'스페란데오','base_watch':'바닥·눌림 횡보','downtrend_approach':'하락 추세선 근접'}
WHY={'watch_rs':'관찰 상대강도 미달','long_uptrend':'장기 상승 추세 미충족','near_or_above_50':'50일선에서 크게 이탈',
     'positive_3m':'3개월 상승 미충족','near_year_high':'52주 고점에서 크게 이탈','no_intact_watch_structure':'유효한 조정/재시험 구조 없음'}

def augment(strict,screen,snapshot,options):
    options=dict(options);enabled=options.pop('enabled',True)
    if enabled is not True:raise ValueError('watchlist_enabled_must_be_true')
    downtrend_options=options.pop('downtrend_approach',None)
    base_options=options.pop('base_structure',None)
    base_cfg=None;base_cap=0
    if base_options is not None:
        base_options=dict(base_options);base_cap=base_options.pop('limit_per_market',10)
        if type(base_cap) is not int or not 0<=base_cap<=50:raise ValueError('invalid_base_limit')
        base_cfg=base.settings(base_options)
    cfg=watch.settings(options);out=deepcopy(strict);out['schema']=4;out['config']['watchlist']={'enabled':True,**cfg}
    if base_cfg is not None:out['config']['watchlist']['base_structure']={**base_cfg,'limit_per_market':base_cap}
    series={(x['market'],x['symbol']):x for x in snapshot['series']};rows={(x['market'],x['symbol']):x for x in screen['rows']}
    for o in out['observations']:
        o['entry_candidate']=bool(o.get('main_candidate') or o.get('sperandeo_candidate'))
        o.update(signals=[],phase='excluded',candidate=False)
        if o.get('features',{}).get('state')!='available':continue
        k=(o['market'],o['symbol']);bars=usable_tail(engine.completed(series[k]['bars'],o['as_of']),snapshot['sessions'][k[0]])
        o['entry_settings']=strict['config']['momentum']
        result=watch.analyze(bars,{**o,'market':out['market_context'][k[0]]},o.get('recent_structure',ordered_structure(rows[k].get('sperandeo'))),cfg,as_of=o['as_of']);o.update(result)
        if base_cfg is not None:
            result=base.analyze(bars,base_cfg,as_of=o['as_of'],market=out['market_context'][k[0]])
            o['base_observation']=result
            # Existing signals win ties, preserving their representative chart and phase.
            o['signals']=sorted(o['signals']+result['signals'],key=lambda s:(watch.ORDER[s['phase']],s['route']=='base_watch',signal_key(s)))
            o['phase']=o['signals'][0]['phase'] if o['signals'] else 'excluded'
        if o.get('recent_structure'):
            structure=next(iter(o['recent_structure']['windows'].values()),{})
            for sig in o['signals']:
                if sig['route']=='sperandeo' and structure.get('trendline'):
                    sig['resistance_trendline']=structure['trendline']
        o['candidate']=bool(o['signals'])
    groups={}
    for market in ('KR','US'):
        entries=[]
        for o in out['observations']:
            if o['market']!=market or not o['candidate']:continue
            signals=o['signals'];best=signals[0];routes={r:min([x for x in signals if x['route']==r],key=signal_key) for r in ROUTES if any(x['route']==r for x in signals)}
            entry={k:o.get(k) for k in ('market','symbol','name','as_of','price_basis','provider')}
            entry.update(analysis=o,entry_signal=best,phase=best['phase'],entry_candidate=o['entry_candidate'],
                origins=list(routes),route_signals=routes,selected=False,selected_by=[],
                reasons=[LABELS[best['phase']]+' · '+best['label'],f"RS {rs_label(o.get('rs_percentile'))} · 피벗 거리 {best['pivot_gap_pct']:.1%} · 구조 위험 {best['structure_risk_pct']:.1%}",
                         *best['pending'],*best['warnings'],'재무·기관·질적 조건 미검증'])
            if 'base_watch' in routes:
                b=routes['base_watch']
                entry['reasons'] += [b['label']+f" · {b['metrics']['base_bars']}봉 · 하단 {b['structure_stop']:.2f} / 상단 {b['pivot']:.2f}",
                    f"횡보 하단 거리 {b['structure_risk_pct']:.1%} · 상단 여지 {b['range_upside_pct']:.1%} · 거리비 {b['range_reward_risk']:.2f}배 (기대수익 아님)"]
            entries.append(entry)
        group={}
        for route,cap in [('pdf_momentum',out['config']['limit_per_market']),('sperandeo',out['config']['sperandeo_limit_per_market']),('base_watch',base_cap)]:
            ordered=sorted([e for e in entries if route in e['origins']],key=lambda e:(signal_key(e['route_signals'][route]),-(e['analysis']['rs_percentile'] if e['analysis']['rs_percentile'] is not None else -1),e['symbol']))
            for n,e in enumerate(ordered,1):
                e[route+'_rank']=n
                if n<=cap:e['selected']=True;e['selected_by'].append(route)
            group[route]={'candidate_count':len(ordered),'selected_count':min(cap,len(ordered)),
                'symbols':[e['symbol'] for e in ordered],'selected_symbols':[e['symbol'] for e in ordered[:cap]],
                'phase_counts':dict(Counter(e['route_signals'][route]['phase'] for e in ordered))}
        group.update(entries=entries,candidate_count=len(entries),selected_count=sum(e['selected'] for e in entries),
                     selected_symbols=[e['symbol'] for e in entries if e['selected']],overlap_symbols=[e['symbol'] for e in entries if len(e['origins'])>1],
                     phase_counts={p:sum(e['phase']==p for e in entries) for p in LABELS},entry_candidate_count=sum(e['entry_candidate'] for e in entries))
        groups[market]=group
    out['limitations'][0]='관찰·진입 단계를 구분한 차트 후보이며 지지 유지·상승·수익을 보장하지 않습니다.'
    out.update(markets=groups,candidate_count=sum(g['candidate_count'] for g in groups.values()),
        entry_candidate_count=sum(g['entry_candidate_count'] for g in groups.values()),
        policy='관찰 시작 → 돌파 대기 → 진입 조건 충족. 관찰 단계에는 시장·거래량·진입 위험의 미충족 조건을 함께 표시합니다.')
    if downtrend_options is not None:
        from downtrend_approach import augment as add_downtrend
        out=add_downtrend(out,snapshot,downtrend_options)
    return out

def report_lines(review,charts):
    lines=['# 스크리너 — 관찰할 종목부터 진입 조건까지','',review['policy'],'']
    for market,g in review['markets'].items():
        lines += [f"## {market} · 전체 후보 {g['candidate_count']} · 진입 조건 충족 {g['entry_candidate_count']}"]
        for phase,label in LABELS.items():lines += [f"- {label}: {g['phase_counts'][phase]}"]
    return lines

CSS='''body{margin:0;background:#f4f6fa;color:#172331;font:15px/1.55 system-ui}main{max-width:1300px;margin:0 auto;padding:30px}h1{font-size:28px;margin:0 0 10px}h2{margin-top:30px}a{color:#225ab8}nav{display:flex;flex-wrap:wrap;gap:18px;margin:18px 0}.cards{display:flex;flex-wrap:wrap;gap:12px}.card{background:white;padding:18px;min-width:155px;border:1px solid #d9e0eb;border-radius:10px}.card strong{font-size:28px;display:block}.note{padding:14px;background:#fff5da;border-left:4px solid #b88516}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #dce2ea;padding:12px}th{background:#e9eef7;white-space:nowrap}.tag{padding:3px 8px;border-radius:5px;background:#e8effa;white-space:nowrap}.muted{color:#586675}summary{cursor:pointer}pre{white-space:pre-wrap;max-width:900px}input,select{font:inherit;padding:8px;margin:4px}small{display:block}'''
esc=lambda x:html.escape(str(x),quote=True)

def table(entries,refs,phase=None):
    selected=[e for e in entries if e['selected'] and (phase is None or e['phase']==phase)]
    if not selected:return '<p class="muted">이 단계의 우선 표시 종목이 없습니다.</p>'
    text='<div class="scroll"><table><thead><tr><th>종목 / 경로</th><th>관찰 단계·모양</th><th>피벗 / 거리</th><th>구조 무효화 / 위험</th><th>횡보 하단·상단 / 거리비</th><th>다음 확인·주의</th></tr></thead><tbody>'
    for e in selected:
        currency=currency_for(e);s=e['entry_signal'];ref=refs.get((e['market'],e['symbol']),{});name=esc(stock_label(e))
        if ref.get('state')=='available':name='<a href="'+esc(ref['path'])+'">'+name+'</a>'
        else:name+=' <small>차트 생성 실패</small>'
        b=e.get('route_signals',{}).get('base_watch')
        base_text=(esc(b['label'])+f"<br>하단 {money(b['structure_stop'],currency)} / 상단 {money(b['pivot'],currency)}<br>하단 거리 {b['structure_risk_pct']:.1%} · 상단 여지 {b['range_upside_pct']:.1%}<br>거리비 {b['range_reward_risk']:.2f}배") if b else '—'
        pending=s['pending'] or ['완료봉 기준 진입 조건 충족·다음 세션 유지 확인']
        text+='<tr><td>'+name+'<small>'+esc(' / '.join(ROUTES[x] for x in e['origins']))+'</small></td><td><span class="tag">'+LABELS[s['phase']]+'</span><br>'+esc(s['label'])+'</td><td>'+f"{money(s['pivot'],currency)}<br>{s['pivot_gap_pct']:.1%}"+'</td><td>'+f"{money(s['structure_stop'],currency)}<br>{s['structure_risk_pct']:.1%}"+'</td><td>'+base_text+'</td><td>'+ '<br>'.join(esc(x) for x in pending+s['warnings'])+'</td></tr>'
    return text+'</tbody></table></div>'

def render(run,output,screening=None,observation=None):
    review=json.loads((output/'priority-review.json').read_text(encoding='utf-8'));charts=json.loads((output/'priority-charts.json').read_text(encoding='utf-8')) if (output/'priority-charts.json').is_file() else {'rows':[],'available':0,'requested':sum(g['selected_count'] for g in review['markets'].values()),'failed':sum(g['selected_count'] for g in review['markets'].values())}
    refs={(r['market'],r['symbol']):r for r in charts['rows']}
    body='<h1>스크리너 — 관찰할 종목부터 진입 조건까지</h1><p>'+review['policy']+'</p>'
    body+='<p class="muted">가격 완료 세션: 한국 '+esc(review['session_dates']['KR'])+' · 미국 '+esc(review['session_dates']['US'])+' · 저장 자료 검토</p>'
    if run.get('approved_exclusions') or run.get('analysis_pending'):
        body+='<p class="muted">사용자 승인 수집 제외 '+str(len(run.get('approved_exclusions',[])))+'종목 · 신규 이력 분석 대기 '+str(len(run.get('analysis_pending',[])))+'종목. 원자료와 제외 사유는 보존합니다.</p>'
    body+='<nav><a href="entry-scan.html">전체 후보·제외 사유 검색</a><a href="candidates.csv">관찰 포함 후보 CSV</a><a href="screening-rules.html">단계별 판정 기준</a><a href="sectors/observation.html">섹터 상태·추이</a></nav>'
    body+='<p class="note">관찰 시작·돌파 대기는 매수 조건 충족을 뜻하지 않습니다. 재무·기관·질적 조건은 미검증이며 구조 위험은 현재 종가에서 참고 저점까지의 거리입니다. 횡보 거리비는 상단까지의 거리÷하단까지의 거리이며 기대수익률이 아닙니다. 지지선 이탈·갭 위험은 남아 있습니다.</p>'
    for market,g in review['markets'].items():
        body+='<section><h2>'+('한국' if market=='KR' else '미국')+f" · 관찰 포함 {g['candidate_count']}종목</h2><div class=\"cards\">"
        for phase,label in LABELS.items():body+='<div class="card">'+label+'<strong>'+str(g['phase_counts'][phase])+'</strong></div>'
        body+='</div><p>시장 맥락: '+('상승 추세 조건 충족' if review['market_context'][market].get('supportive') else '주의 — 상승 추세 조건 미충족/결측')+' · 관찰 후보의 일괄 탈락 조건으로 쓰지 않습니다.</p>'
        body+=f"<p class=\"muted\">PDF 메인 {g['pdf_momentum']['candidate_count']} · 스페란데오 {g['sperandeo']['candidate_count']} · 바닥/눌림 {g.get('base_watch',{}).get('candidate_count',0)} · 중복 {len(g['overlap_symbols'])}. 우선 차트 {g['selected_count']}종목, 나머지는 전체 목록에서 확인합니다.</p>"
        for phase,label in LABELS.items():body+='<h3>'+label+'</h3>'+table(g['entries'],refs,phase)
        body+='</section>'
    body+=f"<p>차트 {charts['available']}/{charts['requested']} · 실패 {charts['failed']}</p>"
    body+='<details><summary>자료 품질·실행 기록·기존 판정</summary><p>원자료 처리 상태 '+esc(run['state'])+' · 수신 '+str(run.get('quality',{}).get('received','?'))+' · 실패 '+str(run.get('quality',{}).get('failed','?'))+'</p><p>기존 S단계 감사 후보 '+str(review['legacy_sperandeo_candidate_count'])+'개는 현재 관찰 후보와 별도입니다.</p><nav><a href="quality.json">자료 품질</a><a href="run.json">실행 기록</a><a href="priority-review.json">새 판정 원수치</a><a href="screen.json">기존 S단계 감사 자료</a></nav></details>'
    (output/'report.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>관찰 후보 스크리너</title><style>'+CSS+'</style><main>'+body+'</main></html>',encoding='utf-8')
    (output/'report.md').write_text('\n'.join(report_lines(review,charts))+'\n\n[전체 후보·제외 사유](entry-scan.html) · [섹터](sectors/observation.html)\n',encoding='utf-8')

def write_outputs(output,review):
    with (output/'candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['market','symbol','name','as_of','phase','entry_candidate','origins','route_phases','selected','pattern','pivot','structure_stop','structure_risk_pct','pivot_gap_pct','pending','warnings','base_pattern','base_floor','base_ceiling','base_risk_pct','range_upside_pct','range_reward_risk'])
        for g in review['markets'].values():
            for e in g['entries']:
                s=e['entry_signal'];b=e.get('route_signals',{}).get('base_watch',{});writer.writerow([e['market'],e['symbol'],e['name'],e['as_of'],e['phase'],e['entry_candidate'],','.join(e['origins']),json.dumps({r:v['phase'] for r,v in e['route_signals'].items()}),e['selected'],s['kind'],s['pivot'],s['structure_stop'],s['structure_risk_pct'],s['pivot_gap_pct'],'; '.join(s['pending']),'; '.join(s['warnings']),b.get('kind'),b.get('structure_stop'),b.get('pivot'),b.get('structure_risk_pct'),b.get('range_upside_pct'),b.get('range_reward_risk')])
    rows=[]
    for o in review['observations']:
        s=o.get('signals',[None])[0] if o.get('signals') else None;phase=o.get('phase','excluded')
        reason='; '.join((s['pending']+s['warnings']) if s else [WHY.get(k,k) for k in o.get('watch_rejections',o.get('rejections',[]))])
        if not s and o.get('base_observation',{}).get('windows'):
            near=min(o['base_observation']['windows'],key=lambda x:len(x['rejections']))
            labels={'preceding_move':'선행 하락/상승 후 눌림 미확인','bounded_range':'횡보 폭 기준 미충족',
                'sideways_closes':'횡보보다 방향성 이동','not_directional_range':'범위 내 한 방향 이동 지속','recent_stabilization':'최근5봉 평균가격 하락 지속','floor_stable':'저점 하락 지속','range_not_expanding':'변동폭 확대',
                'repeated_support':'떨어진 날짜의 반복 지지 부족','floor_intact':'횡보 하단 이탈',
                'bounded_floor_distance':'하단 거리 과다/비정상','not_chased':'상단에서 과도한 이격'}
            reason+='; 횡보 '+str(near['base_bars'])+'봉: '+', '.join(labels[k] for k in near['rejections'])
        if not reason and not s:reason=o['state']
        label=LABELS.get(phase,'대상 제외')
        rows.append('<tr data-phase="'+phase+'"><td>'+esc(o['market'])+'</td><td>'+esc(stock_label(o))+'</td><td>'+label+'</td><td>'+esc(' / '.join(dict.fromkeys(x['label'] for x in o.get('signals',[]))))+'</td><td>'+esc(reason)+'</td><td><details><summary>계산 근거</summary><pre>'+esc(json.dumps(display_analysis(o,currency_for(o)),ensure_ascii=False,indent=2))+'</pre></details></td></tr>')
    script='''const q=document.getElementById('q'),p=document.getElementById('phase');function filter(){for(const r of document.querySelectorAll('tbody tr')){const phase=r.dataset.phase;r.hidden=!r.textContent.toLowerCase().includes(q.value.toLowerCase())||(p.value==='candidates'?phase==='excluded':p.value!=='all'&&p.value!==phase);}}q.addEventListener('input',filter);p.addEventListener('change',filter);filter();'''
    page='<h1>전체 관찰 후보·단계·제외 이유</h1><a href="report.html">스크리너 보고서</a><p>완료 세션 '+esc(review['session_dates'])+'</p><input id="q" placeholder="종목명·심볼 검색"><select id="phase"><option value="candidates">관찰 포함 후보만</option>'+''.join('<option value="'+k+'">'+v+'</option>' for k,v in LABELS.items())+'<option value="excluded">대상 제외</option><option value="all">전체 대상</option></select><div class="scroll"><table><thead><tr><th>시장</th><th>종목</th><th>단계</th><th>모양</th><th>다음 확인 / 제외 이유</th><th>근거</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    (output/'entry-scan.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><title>관찰 후보 전체 목록</title><style>'+CSS+'</style><main>'+page+'</main><script>'+script+'</script></html>',encoding='utf-8')
    rules=Path(__file__).resolve().parents[1]/'docs/pdf-momentum-rules.md'
    (output/'screening-rules.html').write_text('<!doctype html><meta charset="utf-8"><a href="report.html">보고서</a><pre style="white-space:pre-wrap">'+esc(rules.read_text(encoding='utf-8'))+'</pre>',encoding='utf-8')

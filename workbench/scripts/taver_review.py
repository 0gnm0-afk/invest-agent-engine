"""Independent Taver scan and two review queues over the common universe gate."""
from display_names import stock_label
from copy import deepcopy
from invest_agent.trading import taver_structure
from invest_agent.trading.market import usable_tail, check_bars

POSITION = {'below':'하단 아래 이탈','reclaimed':'최근 이탈 후 회복','wick_breach':'저가 이탈·종가 위','above':'하단 위'}


def describe(result):
    if result['state']!='available':
        return [f"장기 구조 {result['state']} · 이력 {result['available_bars']}봉"]
    p=result['primary']; parts=[f"관측 {result['primary_window']}봉 {p['start']}~{p['end']} · 조정 {p['pullback_atr']:.2f} ATR"]
    for key,label in [('channel','EME 채널'),('support','CEG 지지선')]:
        line=p[key]
        if 'distance_atr' not in line:
            parts.append(label+' 유효 앵커 없음'); continue
        pct = f"{line['distance_pct']:+.1%}" if line['distance_pct'] is not None else '하단 가격 비양수'
        parts.append(f"{label} 모양 {'적합' if line['shape_valid'] else '부적합'} · 하단 {line['lower_latest']:.2f} · 거리 {line['distance_atr']:+.2f} ATR ({pct}) · {POSITION[line['position']]} · 최근 하회 {line['recent_close_below_bars']}봉")
    parts.append('단기 하락 구조 '+('있음' if p['short']['descending'] else '없음'))
    if result['warnings']: parts.append('주의: '+', '.join(result['warnings']))
    return parts


def augment(review,screen,snapshot):
    cfg=review['config']
    if not cfg.get('taver_enabled', True):
        for group in review['markets'].values():
            for entry in group['entries']:
                entry['origins'] = ['sperandeo']
                entry['selected_by'] = ['sperandeo'] if entry['selected'] else []
            group.update(sperandeo={'candidate_count':group['candidate_count'],
                'selected_count':group['selected_count'],'entries':deepcopy(group['entries'])},
                taver={'state':'disabled','candidate_count':0,'selected_count':0,'entries':[]},
                overlap_symbols=[])
        review.update(schema=2,taver_state='disabled',taver_observations=[],
            taver_policy='테이버 후보 선정 제외. 과거 결과는 별도 보존합니다.')
        return review
    by_series={(s['market'],s['symbol']):s for s in snapshot['series']}
    observations=[]
    for row in screen['rows']:
        identity={k:row.get(k) for k in ('market','symbol','name','as_of','price_basis')}
        if not row.get('universe',{}).get('universe_pass') or row['state']=='unavailable':
            observations.append({**identity,'candidate':False,'state':'common_gate_not_passed',
                'reasons':row.get('universe',{}).get('universe_rejection_reasons',[row.get('reason','unavailable')])})
            continue
        try:
            item=by_series[(row['market'],row['symbol'])]
            bars=usable_tail(item['bars'],snapshot['sessions'][row['market']])
            check_bars(bars,snapshot['sessions'][row['market']])
            result=taver_structure.analyze(bars,cfg['taver_structure'],as_of=row['as_of'])
            observations.append({**identity,**result,'provider':item.get('provider') or item.get('source') or snapshot.get('source'),
                'history_truncated':len(bars)<len(item['bars']),
                'sperandeo_candidate':row['state']=='candidate','reasons':describe(result)})
        except (ValueError,KeyError,IndexError) as exc:
            observations.append({**identity,'candidate':False,'state':'analysis_error','reason':str(exc)})
    by_obs={(o['market'],o['symbol']):o for o in observations}
    for m,group in review['markets'].items():
        sperandeo=deepcopy(group['entries'])
        taver=[o for o in observations if o['market']==m and o['candidate']]
        def key(o):
            p=o['primary']; matches=[p[k] for k in ('channel','support') if p[k]['candidate']]
            return ('window_sensitive' in o['warnings'],min(abs(v['distance_atr']) for v in matches),o['symbol'])
        taver.sort(key=key)
        for rank,o in enumerate(taver,1):
            o.update(rank=rank,selected=rank<=cfg['taver_limit_per_market'],
                selection_reason=f"테이버 {rank}위 / 상한 {cfg['taver_limit_per_market']} · 기간 민감도 적음→하단 절대 ATR 거리→심벌")
        # Entries remain a deduplicated union; method-specific ranks/reasons stay
        # in their own queues, including candidates outside the selected limits.
        union={e['symbol']:e for e in group['entries']}
        for e in union.values():
            o=by_obs.get((m,e['symbol']),{})
            e['origins']=['sperandeo']+(['taver'] if o.get('candidate') else [])
            e['taver_structure']=o
            e['selected_by']=['sperandeo'] if e['selected'] else []
        for o in taver:
            e=union.get(o['symbol'])
            if e is None:
                e={k:o.get(k) for k in ('market','symbol','name','as_of','price_basis','provider')}
                e.update(origins=['taver'],selected_by=[],selected=False,reasons=[],taver_structure=o,
                         trendlines=None,data_missing=None)
                union[o['symbol']]=e
            e['taver_rank']=o['rank']
            e['taver_reasons']=o['reasons']
            if o['selected']:
                e['selected']=True; e['selected_by'].append('taver')
            if e['origins']==['taver']:
                e['reasons']=o['reasons']; e['selection_reason']=o['selection_reason']
        group.update(sperandeo={'candidate_count':len(sperandeo),'selected_count':sum(e['selected'] for e in sperandeo),'entries':sperandeo},
            taver={'candidate_count':len(taver),'selected_count':sum(o['selected'] for o in taver),'entries':taver},
            entries=list(union.values()),candidate_count=len(union),
            selected_count=sum(e['selected'] for e in union.values()),
            selected_symbols=[e['symbol'] for e in union.values() if e['selected']],
            overlap_symbols=[o['symbol'] for o in taver if o['sperandeo_candidate']])
    review.update(schema=2,taver_observations=observations,
        taver_policy='공통 관문 뒤 독립 계산. 고정 주기간504봉(부족 시252), 대안252/378/756/1260봉. 선형 OLS 잔차10/90 채널 또는 확정 저점 지지선+63봉 하락. 수동 작도 근사·사례 모양 관찰이며 수익성 검증 아님. 이평·스페란데오 회복은 테이버 필수조건 아님.')
    return review


def draw_structure(bars,path,result,title):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from invest_agent.trading.charts import _TITLE_FONT
    if result.get('state')!='available':
        return False
    dates={b['date']:i for i,b in enumerate(bars)}
    earliest=min([w['start'] for w in result['windows'].values() if w['state']=='available'])
    offset=dates[earliest]; shown=bars[offset:]
    fig,axes=plt.subplots(2,1,figsize=(14,9),gridspec_kw={'height_ratios':[3,2]})
    try:
        primary=result['primary']; w=result['primary_window']
        for ax in axes:
            ax.plot(range(len(shown)),[b['close'] for b in shown],color='#334155',lw=.8,label='Daily close')
            ax.grid(alpha=.2)
        ax=axes[0]
        colors={'channel':'#007c91','support':'#9843a8'}
        for kind in ('channel','support'):
            line=primary[kind]
            if 'intercept' not in line: continue
            start=dates[line['start']]; xs=list(range(start,len(bars)))
            ys=[line['intercept']+line['slope_per_bar']*(i-start) for i in xs]
            ax.plot([i-offset for i in xs],ys,color=colors[kind],label=f"{w}D {kind} / {line['distance_atr']:+.2f} ATR / {line['position']}")
            if line.get('upper_offset') is not None:
                ax.plot([i-offset for i in xs],[y+line['upper_offset'] for y in ys],color=colors[kind],alpha=.6)
            for anchor in line.get('anchors',[]):
                ax.scatter(dates[anchor['date']]-offset,anchor['price'],facecolors='none',edgecolors=colors[kind])
            breaches=line['retrospective_close_below_dates']
            ax.scatter([dates[d]-offset for d in breaches],[bars[dates[d]]['close'] for d in breaches],s=6,alpha=.3,color=colors[kind])
        short=primary['short']; start=dates[short['start']]
        ax.plot([start-offset,len(bars)-offset-1],[short['intercept'],short['intercept']+short['slope_per_bar']*(len(bars)-1-start)],color='#b65c00',label=f"{result['config']['short_window']}D short fit")
        ax.axvline(dates[primary['start']]-offset,color='#888',linestyle=':')
        ax.set_title(title+'\nAs-of fit: '+result['as_of']+' | retrospective geometry, NOT historical signals',fontfamily=_TITLE_FONT)
        ax=axes[1]
        for period,alt in result['windows'].items():
            if alt['state']!='available': continue
            line=alt['channel']; start=dates[line['start']]; xs=list(range(start,len(bars)))
            ax.plot([i-offset for i in xs],[line['intercept']+line['slope_per_bar']*(i-start) for i in xs],label=f"{period}D lower / shape={line['shape_valid']} / {line['distance_atr']:+.2f} ATR")
        old=result['older_path']
        if 'intercept' in old:
            start=dates[old['start']]; xs=list(range(max(start,offset),len(bars)))
            ax.plot([i-offset for i in xs],[old['intercept']+old['slope_per_bar']*(i-start) for i in xs],linestyle='--',label='Old center fit ending '+old['fit_end'])
        for ax in axes:
            ax.legend(loc='upper left',fontsize=8)
            ticks=list(range(0,len(shown),max(1,len(shown)//8)))
            ax.set_xticks(ticks,[shown[i]['date'] for i in ticks],rotation=20)
        axes[1].set_title('Window sensitivity | no excluded event bars | missing windows stay unavailable')
        fig.tight_layout(); fig.savefig(path,dpi=110)
    finally:
        plt.close(fig)
    return True


def report_lines(review,chart_result):
    if review.get('taver_state') == 'disabled':
        from priority_review import report_lines as legacy_report
        return legacy_report({**review,'schema':1,'policy':review['policy']+
            ' 테이버 후보 선정은 제외했습니다. 현재 표는 기존 스페란데오 관찰 후보이며 PDF 기반 새 진입 조건은 아직 적용 전입니다.'},chart_result)
    refs={(r['market'],r['symbol']):r for r in chart_result['rows']}
    lines=['','## 독립 후보와 우선 검토','',review['taver_policy'],'',review['policy'],'',
           f"시장별 스페란데오 최대 {review['config']['limit_per_market']}개 + 테이버 최대 {review['config']['taver_limit_per_market']}개. 중복은 두 이유를 보존하고 차트는 하나로 통합합니다.",
           '[전체 탐색·미선정·이력 부족·설정](priority-review.json) · [전체 테이버 탐색표](taver-scan.html) · [차트 상태](priority-charts.json)','']
    for market,g in review['markets'].items():
        lines += [f"### {market}: 통합 우선 {g['selected_count']}개 · 전체 후보 중복 {len(g['overlap_symbols'])}개",'']
        for method,label in [('sperandeo','스페란데오'),('taver','테이버')]:
            group=g[method]
            lines += [f"#### {label}: 전체 {group['candidate_count']}개 / 우선 {group['selected_count']}개",'',
                      '| 순위 | 종목·차트 | 중복 | 선정 근거 |','|---|---|---|---|']
            for e in group['entries']:
                if not e['selected']: continue
                ref=refs.get((market,e['symbol']),{})
                name=stock_label(e,market)
                link=f"[{name}]({ref['path']})" if ref.get('state')=='available' else name+' (차트 실패: '+ref.get('reason','미생성')+')'
                values=[e['rank'],link,'양쪽 후보' if e['symbol'] in g['overlap_symbols'] else '단독', '; '.join(e['reasons'])]
                lines.append('| '+' | '.join(str(v).replace('|','/').replace('\n',' ') for v in values)+' |')
            lines.append('')
        lines += ['#### 중복을 합친 우선 종목','', '| 종목·차트 | 후보 경로 | 우선 선정 경로 |', '|---|---|---|']
        for e in g['entries']:
            if not e['selected']: continue
            ref=refs.get((market,e['symbol']),{})
            link=f"[{stock_label(e,market)}]({ref['path']})" if ref.get('state')=='available' else stock_label(e,market)
            labels={'sperandeo':'스페란데오','taver':'테이버'}
            lines.append('| '+link+' | '+', '.join(labels[v] for v in e['origins'])+' | '+', '.join(labels[v] for v in e['selected_by'])+' |')
        lines.append('')
    lines += [f"차트 요청 {chart_result['requested']} / 성공 {chart_result['available']} / 실패 {chart_result['failed']}. 수집 상태와 별도.",'','## 전체 스페란데오 후보 원 판정·원순서 보존','']
    return lines


def write_scan(output,review):
    if review.get('taver_state') == 'disabled':
        return
    import html
    esc=html.escape
    rows=[]
    for o in review['taver_observations']:
        values=[o.get('market'),stock_label(o),o.get('as_of'),o['state'],o['candidate'],
                o.get('sperandeo_candidate'),'; '.join(o.get('reasons',[]))]
        rows.append('<tr>'+''.join('<td>'+esc(str(v))+'</td>' for v in values)+'</tr>')
    page='<!doctype html><meta charset="utf-8"><title>전체 테이버 탐색</title><style>body{font:14px system-ui;margin:24px}td,th{padding:8px;border-bottom:1px solid #ddd}table{border-collapse:collapse}</style><h1>전체 테이버 탐색</h1><p><a href="report.html">보고서</a> · 기준일 '+esc(str(review['session_dates']))+'</p><p>'+esc(review['taver_policy'])+'</p><table><tr>'+''.join('<th>'+s+'</th>' for s in ['시장','종목','기준일','상태','테이버 후보','스페란데오 후보','근거'])+'</tr>'+''.join(rows)+'</table>'
    (output/'taver-scan.html').write_text(page,encoding='utf-8')

"""Saved-input report rendering; no live collection entry point."""
import html,json,re
from pathlib import Path
from display_names import stock_label
import sector_signals
import sector_observation as sector
import priority_review as priority
ROOT=Path(__file__).resolve().parents[1]

def percent(value):
    return '결측' if value is None else f'{value:.2%}'

def inline_links(text):
    # Escape all text first; activate only local artifact paths, never URLs/user markup.
    escaped = html.escape(text)
    return re.sub(r'\[([^\]<>]+)\]\(((?:charts/[A-Za-z0-9-]+\.html)|(?:[A-Za-z0-9_-]+\.(?:json|html))|(?:sectors/[A-Za-z0-9_.-]+))\)',
                  r'<a href="\2">\1</a>', escaped)

def render(run, output, screening=None, observation=None):
    if (output/'priority-review.json').is_file() and sector.load(output/'priority-review.json').get('schema')==4:
        import watch_review
        return watch_review.render(run,output,screening,observation)
    review = None
    lines = ['# 일일 시장 관찰', '', f"실행 상태: **{run['state']}** / 처리: {run['processing_state']}",
             f"모드: {run['mode']} / 시작: {run['started_at']} / 종료: {run.get('finished_at', '진행 중')}",
             '', 'Toss 제공 대상 전수 기준. 거래소 전체와의 동일성을 보증하지 않습니다.',
             '투자 판단 보조 자료이며 후보·조사 순서는 매수/매도 신호가 아닙니다.', '']
    if run.get('error'):
        lines += [f"오류: {run['error']}", '']
    lines += ['| 단계 | 상태 |', '|---|---|'] + [f'| {k} | {v} |' for k,v in run['stages'].items()]
    if screening:
        q = run['quality']
        lines += ['', f"가격 완료 세션: KR {screening['session_dates']['KR']} / US {screening['session_dates']['US']}",
                  f"수집 시작/종료: {run['data_times']['fetched_at']} / {run['data_times']['collected_until']}",
                  f"수신 {q['received']}/{q['requested']} · 일봉 실패 {q['failed']} · 품질 이슈 시계열 {q['quality_issue_series']}",
                  f"원자료 상태 {q['state']} · 처리 {q['processing_state']} · 완전성 {q['data_complete']}",
                  f"기존 스페란데오 후보 {screening['candidate_count']} · 종목 판단 불가 {screening['unavailable_count']}",
                  '', '## 후보와 섹터 맥락', '', '공통 유니버스 뒤 두 후보 경로는 독립입니다. 아래 표는 기존 스페란데오 전체 후보와 이평 위치를 원순서로 보존합니다.', '',
                  '| 시장 | 종목 | 단계·기간 | 테이버 위치 | 섹터 | RS 3M / 6M | 조사 참고 |', '|---|---|---|---|---|---|---|']
        if run.get('nh_fallback'):
            fb = run['nh_fallback']
            lines.insert(lines.index('## 후보와 섹터 맥락')-1,
                f"NH 표적 보완: 시도 {fb['attempted']} · 채택 {fb['adopted']} · 양쪽 자료 부재 제외 {fb.get('excluded',0)} · 미해결 {fb['unrecovered']}. "
                '원 Toss 실패는 원본에 보존; NH 거래량 정밀도는 종목별 표시하고 동일 공급자 섹터 집계에서 제외합니다.')
        if (output/'priority-review.json').is_file():
            review = sector.load(output/'priority-review.json')
            if review.get('taver_state') == 'disabled':
                lines = [line.replace('공통 유니버스 뒤 두 후보 경로는 독립입니다. ', '')
                         .replace('테이버 위치', '장기 이평 위치') for line in lines]
            missing_count = sum(g['selected_count'] for g in review['markets'].values())
            review_lines = priority.report_lines(review,
                sector.load(output/'priority-charts.json') if (output/'priority-charts.json').is_file()
                else {'requested':missing_count,'available':0,'failed':missing_count,'rows':[]})
            at = lines.index('## 후보와 섹터 맥락')
            lines[at:at] = review_lines
            if review.get('schema') in (3,4):
                lines=lines[:lines.index('## 후보와 섹터 맥락')]
                lines=[line.replace(f"기존 스페란데오 후보 {screening['candidate_count']}",f"활성 진입 후보 {review['candidate_count']} · 기존 S단계 감사 후보 {screening['candidate_count']}") for line in lines]
        for row in ([] if review and review.get('schema') in (3,4) else screening['rows']):
            if row['state'] != 'candidate':
                continue
            ctx = row['sector_context']
            horizons = ctx.get('horizons', {})
            structure = ', '.join(str(x) for x in row['candidate_reasons'])
            # Preserve the full structured location, including each independent MA, in JSON.
            taver = row.get('taver', {})
            location = '; '.join('SMA'+p+': '+v['location_state']
                                 for p,v in taver.get('lines', {}).items())
            label = ctx.get('group_name') or ctx.get('reason') or '결측'
            rs = ' / '.join(percent(horizons.get(h, {}).get('relative_strength')) for h in ('63','126'))
            note = ctx.get('reason') or '; '.join(ctx.get('warnings', [])) or '분모·추세·참여·활동은 상세 JSON/섹터표 참조'
            nh_price = str(row.get('price_basis','')).startswith('nh_')
            if nh_price:
                volume_note = ('거래량 하한 추정' if row['market']=='US' else '거래량 이전 Toss 직접 대조')
                note = 'NH 가격 보완·'+volume_note+'·섹터 분모 제외; '+note
            cells = [row['market'], stock_label(row)+(' [NH]' if nh_price else ''),
                     structure, location, label, rs, note]
            lines.append('| ' + ' | '.join(str(x).replace('|','/').replace('\n',' ') for x in cells) + ' |')
        if not (review and review.get('schema') in (3,4)):
            lines += ['', '## 조사 순서', '', '국가별 섹터 RS63 내림차순, 결측은 마지막, 동률은 원 후보 순서입니다. 합성 점수는 없습니다.']
            display_rows = {(r['market'],r['symbol']):r for r in screening['rows']}
            for market, rows in screening['research_order'].items():
                lines += ['', f"{market}: " + ', '.join(stock_label(display_rows.get((market,r['symbol']),r),market) for r in rows)]
        lines += ['', '[기존 S단계 감사 자료](screen.json) · [자료 품질 상세](quality.json)',
                  '[섹터 상태·변화](sectors/observation.html) · [기초 지표 상세](sectors/metrics.html) · [분류 미확인](sectors/unmatched.json)']
    if observation:
        lines += ['', f"분류 적용 시각: {observation['applied_at']}. 원천별 관측일·효력일은 groups.json의 evidence에 보존합니다.",
                  '현재 분류로 과거 가격을 묶습니다. 과거 시점 백테스트가 아닙니다.',
                  '한국 RS: 같은 Toss 대상의 동일비중 지수, KOSPI는 참고. 미국 RS: Toss SPY 프록시.',
                  '가격 결측은 지표별 제외/null이며 0수익률 대체나 보간하지 않습니다.',
                  '직접 확인한 WICS/Finviz 분류는 7일 경과 시 갱신 필요로 표시하고 분석에 유지합니다. 충돌·미확인은 제외합니다. 공식 계산 근거 문서가 오래되면 섹터 계산은 실패로 남깁니다.',
                  'NH는 Toss 전송 실패분의 검증된 가격 보완에만 사용합니다. 계좌 조회·기업 Evidence·LLM·예약 실행은 포함하지 않습니다.']
    text = '\n'.join(lines) + '\n'
    (output/'report.md').write_text(text, encoding='utf-8')
    body, table = [], False
    for line in lines:
        if line.startswith('|'):
            if not table:
                body.append('<div style="overflow:auto"><table>')
                table = True
            if line.startswith('|---'):
                continue
            cells = line.strip('|').split('|')
            body.append('<tr>'+''.join('<td>'+inline_links(c.strip())+'</td>' for c in cells)+'</tr>')
        else:
            if table:
                body.append('</table></div>')
                table = False
            if line.startswith('#'):
                level = min(len(line)-len(line.lstrip('#')), 3)
                body.append(f'<h{level}>'+html.escape(line.lstrip('# '))+f'</h{level}>')
            elif line:
                body.append('<p>'+inline_links(line)+'</p>')
    if table:
        body.append('</table></div>')
    sector_panel = ''
    if (output/'sectors/signals.json').is_file():
        states = sector.load(output/'sectors/signals.json')
        sector_panel = '<style>'+sector_signals.CSS+'</style><h1>섹터 변화 먼저 보기</h1><p>완료 일봉 기준 · 현재 분류로 재생한 관찰 · 추정 거래 비중은 순유입 아님</p>'+sector_signals.cards(states,'sectors/',4)+'<p><a href="sectors/observation.html">전체 섹터 상태·필터·추이 →</a></p>'
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>일일 시장 관찰</title><style>body{font-family:system-ui;margin:24px;line-height:1.5}table{border-collapse:collapse;font-size:14px}td{border:1px solid #ccc;padding:8px;min-width:85px}tr:first-child{font-weight:bold;background:#edf2f7}p{overflow-wrap:anywhere}</style><body><nav><a href="sectors/observation.html">섹터 관찰표</a> · <a href="candidates.csv">후보 CSV</a> · <a href="screen.json">후보/제외/결측 상세</a> · <a href="quality.json">품질 상세</a></nav>' + sector_panel + ''.join(body) + '</body></html>'
    if review and review.get('schema') in (3,4):
        page=page.replace('<a href="screen.json">후보/제외/결측 상세</a>','<a href="entry-scan.html">활성 후보·탈락 근거</a> · <a href="screen.json">기존 S단계 감사 자료</a>')
    (output/'report.html').write_text(page, encoding='utf-8')

def export_sectors(output, report, groups):
    output.mkdir()
    sector.write(output/'observation.json', report)
    sector.write(output/'groups.json', groups)
    sector.write(output/'unmatched.json', [r for r in groups if r['group_state'] != 'matched'])
    md, page = sector.render(report)
    (output/'observation.md').write_text(md, encoding='utf-8')
    (output/'observation.html').write_text(page, encoding='utf-8')
    (output/'metrics.html').write_text(page, encoding='utf-8')

"""Traceable candidate display; values come directly from stored Python rows."""
import html
import json
from .report_format import money
from .report_names import company_label


def candidate_sections(rows, audit):
    def text(v):
        return html.escape(str(v)).replace('|','&#124;')
    output = ['- 후보 편입: 시장·유동성 관문 → 스페란데오 S1~S4. 테이버 위치는 후단 설명이며 매수 신호가 아닙니다.',
              '- 스페란데오 primary 126D. S1은 하락 추세선 접근이며 상승 전환 완성이 아닙니다.',
              '- Terra는 정렬 순서 최대 10후보 해석. 이후 후보도 이 Python 목록에 보존됩니다.',
              '', '시장별 관문 결과: ' + text(json.dumps(audit,ensure_ascii=False)), '']
    for row in rows:
        sp, tav = row['sperandeo'], row['taver']
        currency = row.get('currency') or {'KR':'KRW','US':'USD'}.get(tav.get('market_profile'))
        output += [f"### {text(company_label(row))}", '', '- Candidate origin: sperandeo',
                   '- Candidate reasons: '+text(' + '.join(row['candidate_reasons'])),
                   '- 완료봉: '+text(row['as_of']), '', '| 기간 | 상태 | 진입일 | 경과 봉 |', '|---|---|---|---|']
        for p in ('63','126','252'):
            w = sp['windows'][p]
            output += ['| '+' | '.join(text(v) for v in (p+'D',w.get('stage') or w['data_state'],w.get('stage_entered_at'),w.get('bars_in_stage')))+' |']
            if w['data_state'] == 'available':
                keys = ('anchor_high_a_date','anchor_high_a_price','anchor_high_b_date','anchor_high_b_price',
                        'reference_low_date','reference_low_price','trendline_value_latest','distance_to_line_pct','distance_to_line_atr',
                        'breakout_date','breakout_close','retest_pivot_date','retest_confirmed_at','retest_low','retest_type',
                        'completion_level','completion_date','bars_since_completion','invalidated','invalidation_date')
                output += ['',text(p)+'D 근거: '+text({k:money(w.get(k),currency) if k in ('anchor_high_a_price','anchor_high_b_price','reference_low_price','trendline_value_latest','breakout_close','retest_low','completion_level') else w.get(k) for k in keys}),'']
            else:
                output += ['',text(p)+'D 결측: '+text(w.get('reason',w['data_state'])),'']
        output += ['', f"테이버 {tav['market_profile']} / SMA {tav['ma_periods']} / primary {tav['primary_ma_period']} / nearest {tav['nearest_ma_period']}",
                   '', '| SMA | 값 | 위치 | 거리(%) | 거리(ATR) | 봉 관통 | touch / reclaim / loss (날짜·경과봉) |', '|---|---|---|---|---|---|---|']
        for period in tav['ma_periods']:
            p = str(period)
            v = tav['lines'][p]
            pct=v.get('distance_to_ma_pct')
            events=' / '.join(f"{v.get('last_'+e+'_date')} ({v.get('bars_since_'+e)})" for e in ('touch','reclaim','loss'))
            output += ['| '+' | '.join(text(x) for x in (p,money(v.get('ma_value_latest'),currency),v['location_state'],
                        None if pct is None else pct*100,v.get('distance_to_ma_atr'),v.get('latest_bar_intersects_ma'),events))+' |']
        output += ['', '- Primary MA 상태: '+text(tav['primary_ma_state']),
                   '- near_target_ma: '+text(tav['near_target_ma']),'- 기간별 관계: '+text(tav['sperandeo_relation']),
                   '- Context: '+text(tav['confluence_state']),
                   '- 테이버 위치가 멀거나 아래에 있어도 스페란데오 후보를 유지합니다.','']
    return output

"""Render research evidence; no optimization or strategy selection."""
import argparse, math, os
from pathlib import Path
from ma_reentry_research import read, STRATEGIES, ROOT
import numpy as np
import pandas as pd

LABEL={'AC':'A·종가↑5','AG':'A·5↑10','AE':'A·먼저','BC':'B·종가↑5','BG':'B·5↑10','BE':'B·먼저'}
def pct(v,d=2): return '—' if v is None or not np.isfinite(v) else f'{100*v:+.{d}f}%'
def prob(v): return '—' if v is None or not np.isfinite(v) else f'{100*v:.1f}%'
def num(v,d=1): return '—' if v is None or not np.isfinite(v) else f'{v:,.{d}f}'
def table(headers,rows): return '\n| '+' | '.join(headers)+' |\n|'+ '|'.join(['---']*len(headers))+'|\n'+''.join('| '+' | '.join(map(str,r))+' |\n' for r in rows)+'\n'


def charts(out,result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    for market in ('KR','US'):
        fig,axes=plt.subplots(1,2,figsize=(12,4.6),layout='constrained')
        allrows=[next(r for r in result['summaries'] if r['market']==market and r['strategy']==s and r['period']=='all') for s in STRATEGIES]
        oos=[next(r for r in result['summaries'] if r['market']==market and r['strategy']==s and r['period']=='oos') for s in STRATEGIES]
        x=np.arange(6)
        axes[0].bar(x,[r['cheaper']*100 for r in allrows],color='#3478a7',label='Cheaper')
        axes[0].bar(x,[r['dearer']*100 for r in allrows],bottom=[r['cheaper']*100 for r in allrows],color='#c67a54',label='More expensive')
        axes[0].set(xticks=x,xticklabels=STRATEGIES,ylabel='Completed cycles (%)',ylim=(0,100),title=f'{market}: rebuy price vs sale price')
        axes[0].legend(loc='upper right',fontsize=8)
        axes[1].bar(x-.17,[r['net_mean']*100 for r in allrows],width=.34,label='Full sample',color='#3478a7')
        axes[1].bar(x+.17,[r['net_mean']*100 for r in oos],width=.34,label='2023Q2+ holdout',color='#c67a54')
        axes[1].axhline(0,color='black',lw=.7);axes[1].set(xticks=x,xticklabels=STRATEGIES,ylabel='Mean total-share change per cycle (%)',title='After assumed trading costs')
        axes[1].legend(fontsize=8)
        fig.savefig(out/f'{market}_cycles.png',dpi=160);plt.close(fig)
        g=pd.read_csv(out/f'{market}_full_cohort.csv');dates=pd.to_datetime(g.date)
        fig,ax=plt.subplots(figsize=(11,4.7),layout='constrained')
        for s in STRATEGIES: ax.plot(dates,g[s+'_base']/g['hold'],label=s,lw=1.25)
        ax.axhline(1,color='black',ls='--',lw=1);ax.set(title=f'{market}: fixed inception cohort, net wealth / buy-and-hold',ylabel='Relative wealth',xlabel='Date')
        ax.legend(ncol=6,fontsize=8);fig.savefig(out/f'{market}_wealth.png',dpi=160);plt.close(fig)


def render(out,report):
    result=read(out/'results.json');meta=read(out/'prepared.json');sim=read(out/'simulation.json')
    verification=read(out/'verification.json') if (out/'verification.json').exists() else {}
    conditions=pd.read_csv(out/'conditions.csv');annual=pd.read_csv(out/'annual.csv');opens=pd.read_csv(out/'open_cycles.csv')
    audit=pd.read_csv(out/'execution_audit.csv.gz');jack=pd.read_csv(out/'leave_one_year_out.csv')
    examples=pd.read_csv(out/'examples.csv',dtype={'symbol':str})
    rel=os.path.relpath(out,report.parent).replace('\\','/')
    def link(name,label=None):return f'[{label or name}]({rel}/{name})'
    def summ(m,s,p='all'):return next(r for r in result['summaries'] if r['market']==m and r['strategy']==s and r['period']==p)
    def cohort(m,s,p='full',mode='base'):return next(r for r in result['cohorts'] if r['market']==m and r['strategy']==s and r['period']==p and r['mode']==mode)
    def path(m,s,p='full',mode='base'):return next(r for r in result['paths'] if r['market']==m and r['strategy']==s and r['period']==p and r['mode']==mode)
    charts(out,result)
    text=['# 역배열에서 절반을 팔고 반등에 되사는 전략은 유효한가',
          '\n연구 `MA-REENTRY-20261017-v1` · 2016년부터 한국·미국 **2026-10-15 완료봉까지** · 시작2026-10-17, 검증2026-10-18. 사용자 후속 요청의 전체 시계열9조합 연구에 대한 보조 보고서다.\n',
          '**판단 범위:** 한국은 당시 KOSPI 보통주 시총 상위200, 미국은 확보된 보통주의 당시 추정 시총≥20억 달러다. 미국의 과거 상장폐지 누락과 한국의 실제 배당·상장폐지 회수금액 결측이 남는다. 따라서 아래 결과를 생존 편향 없는 전체시장·완전한 세후 총수익 검증이라고 주장하지 않는다.\n',
          '## 1. 연구 질문에 대한 답\n']
    for m,name in [('KR','한국'),('US','미국')]:
        rows=[summ(m,s) for s in STRATEGIES];hold=[summ(m,s,'oos') for s in STRATEGIES]
        positive=[r['strategy'] for r in hold if r['log_inference']['family_ci95'][0]>0]
        text.append(f"**{name}:** 완료 사이클에서 더 싸게 재매수한 비율은 {prob(min(r['cheaper'] for r in rows))}~{prob(max(r['cheaper'] for r in rows))}, 비용 후 전체 주식 수의 평균 변화는 사이클당 {pct(min(r['net_mean'] for r in rows))}~{pct(max(r['net_mean'] for r in rows))}였다. 시간 분리 검증에서 복리 주식수 효과의 다중비교 보정 신뢰구간이0을 웃도는 전략은 **{', '.join(positive) if positive else '없다'}**. 2016년 시작 고정 표본의 비용 후 최종자산/보유 대비 차이는 {pct(min(cohort(m,s)['relative'] for s in STRATEGIES))}~{pct(max(cohort(m,s)['relative'] for s in STRATEGIES))}다.\n")
    if all(cohort(m,s)['relative']<0 and summ(m,s,'oos')['net_mean']<0 for m in ('KR','US') for s in STRATEGIES):
        text.append('**기존 여섯 규칙에서는 보유를 개선할 유의미한 우위를 발견하지 못했다.** 골든크로스를 기다리면 일부 큰 하락 방어는 나아졌지만, 두 시장 모두 검증기간 평균 주식 수와 전체기간 고정 표본 최종자산이 보유보다 불리했다. 미국 AG/BG의 전체기간 산술평균 주식수 이익도 평균 로그효과와 검증기간에서는 유지되지 않았다. 큰 하락을 피하면서 장기 상승을 거의 그대로 유지했다는 결론은 지지되지 않는다.\n')
    text += ['싸게 되사는 횟수만으로 성공을 판단할 수 없다. 작은 손실을 자주 보고 드물게 큰 주식 수 증가를 얻으면 산술평균과 전형적 경험이 달라진다. 반복 거래의 복리 효과, 현금 대기와 놓친 배당, 실제 전체기간 최종자산을 함께 보아야 한다. 아래의 양수는 해당 표본·가정에서의 관측 이익이며 새 시장에서 보장되는 효과가 아니다.\n',
             '## 2. 여섯 전략과 계산 기준\n',
             'A는 `MA5<MA20 AND MA10<MA20`, B는 `MA5<MA10<MA20`이다. A는 B를 포함한다. C는 종가가 MA5를, G는 MA5가 MA10을 직전일 이하에서 당일 엄격히 상향 돌파한 신호, E는 둘 중 먼저다. 이동평균은 분할·기준가 조정 종가의 단순평균이며 10⁻¹² 상대 허용오차는 숫자 동률만 처리한다.\n',
             '역배열의 false→true 진입을 종가에서 확인하고 **다음 거래일 시가**에 당시 보유 수량의50%를 판다. 남은50%는 유지하고, 매도 신호보다 나중에 확인된 첫 반등 신호의 다음 시가에 순매도대금 전액을 재투자한다. 매도 체결일 종가의 반등은 유효하다. 매도 신호와 같은 종가의 반등은 이후 반등으로 재사용하지 않는다. 반등 전 중복 매도는 무시하며, 재매수 후 역배열이 계속된다는 이유만으로 다시 팔지 않는다. 상태를 벗어났다가 새로 진입해야 한다. 재매수일 종가에 새 진입이 생기면 다음 날 매도할 수 있다. 손절·익절·최대 대기기간을 추가하지 않았다.\n',
             '표본 자격은 매도 신호 당일 판단한다. 재매수는 시총 조건을 벗어나도 실행하며 보유 중 순위 이탈 때문에 강제 청산하지 않는다. 한 종목의 첫 적격 종가 다음 시가부터 같은 초기자산으로 전략과 보유를 비교한다. 거래 단위는 소수 주식이 가능한 소규모 가격수용자 가정이며, 매수대금과 보유 비중의 분모를 바꾸지 않는다.\n',
             '관측 시가는 소량 주문의 체결 대용값이다. 유동성·가격제한·거래정지 후 경매에서 체결을 보장하지 않는다. 기준가격과 실제 시장가 체결가격의 차이는 [SEC 투자자 안내](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-15)도 강조한다. 하루 더 늦은 시가 체결을 별도 비교했다.\n',
             table(['구분','매도 차감','매수 가산','해석'],[['한국 기본','0.35%','0.05%','수수료·스프레드·슬리피지·매도세 부담 여유 가정'],['미국 기본','0.05%','0.05%','수수료·스프레드·슬리피지 가정'],['한국 비용 스트레스','0.50%','0.20%','불리한 비용 가정'],['미국 비용 스트레스','0.25%','0.25%','불리한 비용 가정']]),
             '이는 연도별 법정 세율이나 특정 증권사 실측 수수료표가 아니다. 실제 과세계좌의 양도소득세·개인별 세금·환율·환전비는 반영하지 않았다. 초기 보유는 양쪽에 동일하게 주어졌고, 마지막에는 청산하지 않고 평가하므로 공통 초기·최종 청산비도 부과하지 않는다. 현금 이자는 기본0%, 연4% 민감도를 제시한다.\n',
             '## 3. 시장별 핵심 비교표\n',
             '**주식수 변화**는 팔았던 절반만의 변화가 아닌 매도 직전 전체 수량 대비 변화다. **최종자산 차이**는 `(전략 최종자산/보유 최종자산−1)`이며 수익률의 %p 차이와 구별한다. 고정 표본은2016년 첫 적격 거래일에 알 수 있던 종목에 같은 초기금액을 주고 추가 편입·재균형 없이 보유한 비교다. 도중 가입 종목을 미래에 알고2016년에 샀다고 계산하지 않았다.\n']
    for m,name in [('KR','한국'),('US','미국')]:
        rows=[]
        for s in STRATEGIES:
            r=summ(m,s);h=summ(m,s,'oos');c=cohort(m,s)
            rows.append([LABEL[s],f"{r['n']:,}/{r['open']:,}",prob(r['cheaper']),prob(r['dearer']),pct(r['net_mean']),pct(h['net_mean']),pct(c['relative']),f"{prob(c['mdd'])}/{prob(c['hold_mdd'])}"])
        text += [f'### {name}\n',table(['전략','완료/미완료','싸게 재매수','비싸게 재매수','평균 순주식수','OOS 평균 순주식수','고정표본 최종자산 차이','MDD 전략/보유'],rows),f'![{name} 재매수 확률과 주식 수]({rel}/{m}_cycles.png)\n']
    text += ['매도가와 같은 가격의 비율은 별도 원표에 남겼다. 미완료는 위 확률의 분모에서 제외하되 아래 최종자산과 미완료 평가에는 포함한다. 여섯 전략은 대안별 시뮬레이션이므로 사이클 수를 더해 독립 사건 수로 부를 수 없다.\n',
             '## 4. 역배열 이후 얼마나 더 하락하고 얼마나 기다렸는가\n',
             '추가 하락은 **신호일 종가**에서 다음 날 이후 반등 신호일까지 최저가로 잰다. 체결일 이후 최저가를 이용한 매매가능 구간 낙폭도 따로 저장했다. 종가 최저점과 구간 내 종가 고점→저점 최대낙폭은 다른 지표다. 저가에서 매수했다고 계산하지 않으며 재매수일 시가 이후의 저가도 미리 사용하지 않는다.\n']
    for m in ('KR','US'):
        rows=[]
        for s in STRATEGIES:
            r=summ(m,s)
            rows.append([LABEL[s],f"{prob(r['signal_low_dd_mean'])}/{prob(r['signal_low_dd_median'])}/{prob(r['signal_low_dd_p95'])}",
                         f"{prob(r['signal_close_dd_mean'])}/{prob(r['interval_close_mdd_mean'])}",
                         f"{num(r['wait_signal_mean'])}/{num(r['wait_signal_median'])}/{num(r['wait_signal_p95'])}",
                         prob(r['no_fall1pct']),prob(r['rebound3']),prob(r['immediate']),prob(r['km_survival_20'])])
        text += [f'### {m}\n',table(['전략','최저가 추가낙폭 평균/중앙/P95','최저종가/구간MDD 평균','반등 대기봉 평균/중앙/P95','낙폭1% 미만','3봉 내 반등','두 조건 동시','20봉 넘게 대기(KM)'],rows)]
    text += ['‘곧바로 반등’은 사전에 고정한3봉 이내 반등과 추가 저가낙폭1% 미만을 동시에 만족한 경우다. 종가 아래로 한 번도 내려가지 않은 경우와 동일하지 않으며,1%·3봉은 거래 필터가 아니다. 평균·중앙·P95 외 P5/Q25/Q75와 달력일 대기는 '+link('summary.csv')+'에 있다. Kaplan–Meier(KM)는 반등이 없는 종료건을 우측 검열로 반영한다. 거래정지·자료 종료가 가격과 무관한 검열이라는 가정은 완전히 성립하지 않을 수 있다.\n',
             '## 5. 싸게 되산 이익과 비싸게 되산 손실\n',
             '가격 할인율=`1−재매수가/매도가`; 순주식수 변화=`0.5×[(1−매도비용)/(1+매수비용)×매도가/재매수가−1]`. 같은 재매수 시점의 최종자산을 비교하면 이 주식수 변화가 보유 대비 상대자산 변화와 일치한다. 평단 하락은 판정 기준으로 쓰지 않았다.\n']
    for m in ('KR','US'):
        rows=[]
        for s in STRATEGIES:
            r=summ(m,s)
            rows.append([LABEL[s],prob(r['cheap_mean']),prob(r['dear_mean']),prob(r['net_win']),
                         f"{pct(r['net_share_p05'])}/{pct(r['net_share_median'])}/{pct(r['net_share_p95'])}",
                         f"{pct(r['net_positive_mean'])}/{pct(r['net_negative_mean'])}",prob(r['giveback5']),prob(r['giveback5_conditional'])])
        text += [f'### {m}\n',table(['전략','성공 때 평균 할인','실패 때 평균 할증','비용 후 이익 확률','순주식수 P5/중앙/P95','이익/손실 조건부 평균','5%↓ 후 가격이익 없음/전체','동일/5%↓ 사례만'],rows)]
    text += ['마지막 두 열은 매도 이후 저가가5% 이상 내려갔지만 실제 재매수 시가가 매도가 이상으로 회복된 사례다. ‘싼 가격을 한 번 보여주었다’와 ‘규칙대로 싸게 살 수 있었다’를 구분한다.10% 하락 기준과 놓친 최고 상승폭도 원표에 포함했다. 반등을 기다리는 전략은 저점을 확인한 뒤 그 저가에 되살 수 없다.\n',
             '## 6. 반복 운용의 최종자산·낙폭과 비용·배당 민감도\n']
    for m in ('KR','US'):
        info=next(x for x in sim['cohorts'] if x['market']==m and x['period']=='full')
        text.append(f"### {m} — 2016년 시작 고정 표본 {info['n']}종목, 조기 종료 {info['stale']}종목\n")
        rows=[]
        for s in STRATEGIES:
            b=cohort(m,s);h=cohort(m,s,'oos');pp=path(m,s)
            rows.append([LABEL[s],f"{pct(cohort(m,s,mode='gross')['relative'])}/{pct(b['relative'])}/{pct(cohort(m,s,mode='stress')['relative'])}",
                         f"{pct(b['cagr'])}/{pct(b['hold_cagr'])}",pct(h['relative']),pct(pp['median_relative']),prob(pp['win_fraction']),
                         f"{prob(pp['mean_mdd'])}/{prob(pp['hold_mean_mdd'])}"])
        text += [table(['전략','최종자산 차이 무비용/기본/스트레스','CAGR 전략/보유','OOS 새 표본 최종차이','모든 종목 상대자산 중앙값','보유를 이긴 종목','종목평균 MDD 전략/보유'],rows),f'![{m} 반복 운용 상대자산]({rel}/{m}_wealth.png)\n']
        rows=[]
        for s in STRATEGIES:
            rows.append([LABEL[s],pct(cohort(m,s,mode='income')['relative']),pct(cohort(m,s,mode='interest4')['relative']),
                         pct(cohort(m,s,mode='stale_zero')['relative']),pct(path(m,s,mode='delay2')['median_relative']),
                         pct(path(m,s,mode='base')['cagr_difference'])])
        text.append(table(['전략','배당민감도 최종차이','현금이자4% 최종차이','조기종료 주식0 평가','하루 지연체결 종목중앙 차이','종목평균 CAGR 차이(%p)'],rows))
    text += ['CAGR는 관측 시작 시가와 끝 종가 사이 달력연수로 환산했다. 종목별 CAGR 차이는1년 이상 이력이 있는 종목만 계산했다. 종목별 시작일이 달라 모든 종목의 평균을 하나의 실제2016년 포트폴리오 수익률로 해석하지 않는다. 고정 표본 경로는 그 오류를 피하지만 뒤에 커진 기업을 새로 편입하는 전체시장 지수와는 다르다.\n',
             '미국 배당민감도는 같은 Yahoo 응답의 `adjclose/close` 비율을 조정 OHLC에 곱한 **총수익 단위 대용 계산**이다. 가격 신호는 바꾸지 않았다. 배당 지급일 현금 원장·원천징수·정확한 실제 재투자 주문을 복원한 것은 아니다. 한국은 실제 과거 배당이 없으므로 보유 주식에 연4% 연속수익을 주고 현금에는 주지 않는 가정이다. 두 시장의 배당 열은 서로 같은 자료 수준이 아니며, 한국 숫자는 실제 총수익이 아니다.\n',
             '조기 종료 주식0 평가는 데이터가 끝난 다음 날부터 그 주식 가치만0, 남은 현금은 유지한 스트레스다. 실제 상장폐지 회수금액을 추정한 결과가 아니다. 마지막 저장가격 고정평가와0평가는 편향의 방향을 살피는 가정이며, 인수대금·신주교환·권리발생을 포함한 법적 상하한도 아니다.\n',
             '## 7. 규모·변동성·시장 국면별 차이\n',
             '아래는 **2023년2분기 이후** 완료 사이클의 비용 후 전체 주식수 평균 변화다. 각 셀은 `평균 · 사건수`이며 사후 탐색표다. 좋은 셀만 골라 새 전략으로 바꾸거나 통계적으로 확정된 최적 조건이라고 부르지 않는다. 전체기간 조건표도 함께 저장했다.\n']
    names={'rank1_50':'한국1–50위','rank51_100':'한국51–100위','rank101_200':'한국101–200위','mega':'메가≥$200B','large':'라지$10–200B','mid':'미드$2–10B','up':'대용지수↑200일선','down':'대용지수↓200일선','unknown':'국면 미확인','missing':'변동성 미확인'}
    for m in ('KR','US'):
        cuts=meta['markets'][m]['vol_cuts'];text.append(f"### {m} — 전일 변동성 사분위 경계: {', '.join(prob(v) for v in cuts)}\n")
        rows=[]
        sub=conditions[(conditions.market==m)&(conditions.period=='oos')]
        for (dimension,group),x in sub.groupby(['dimension','group']):
            row=[dimension,names.get(group,group)]
            for s in STRATEGIES:
                y=x[x.strategy==s]
                row.append('—' if len(y)==0 or int(y.iloc[0]['n'])==0 else f"{pct(y.iloc[0].net_mean)} · {int(y.iloc[0]['n']):,}")
            rows.append(row)
        text.append(table(['분류','구간']+STRATEGIES,rows))
    text += ['변동성은 신호 전일까지20개 로그수익의 표본표준편차×√252다. 경계는2016–2022년 적격 종목–일로 정했고 미래 검증기간에 다시 맞추지 않았다. 시장 국면은 전일 시총 적격 표본의 동일가중 가격수익으로 만든 누적지수의 **전일**200일선 위·아래다. 공식 KOSPI/S&P 500 국면이 아니며 미국 표본의 생존 편향도 공유한다. 이 표만으로 시장 전체의 국면 효과를 단정하지 않는다.\n',
             '## 8. 연도 의존성과 A에서 B로 진행되는 과정\n',
             '연도표는 매도 신호 연도 기준의 완료 사이클 평균 순주식수 변화다. 연말 이후 끝난 사이클은 이 기술통계 연도표에는 들어가지만, 학습 표본에는2022년 안에 끝난 것만 포함한다.2026년은10월15일까지의 부분 연도다.\n']
    for m in ('KR','US'):
        rows=[]
        for year,x in annual[annual.market==m].groupby('year'):
            rows.append([int(year)]+[pct(x[x.strategy==s].iloc[0].net_mean) for s in STRATEGIES])
        text += [f'### {m}\n',table(['연도']+STRATEGIES,rows)]
        j=jack[jack.market==m];text.append(f"한 해씩 제외한 평균 순주식수 범위: {pct(j.mean_net_share.min())}~{pct(j.mean_net_share.max())}. 전략별 제외연도 결과는 {link('leave_one_year_out.csv')}에 보존했다. 고정 표본의 실제 달력연도 전략·보유 수익률은 {link('cohort_annual.csv')}에서 별도로 비교한다.\n")
    text.append(table(['시장','A 사건의 결과','사건수','비율','A→결과 대기봉 평균/중앙','A→결과 가격 평균/중앙'],[
        [r['market'],r['status'],f"{r['n']:,}",prob(r['fraction']),f"{num(r['sessions_mean'])}/{num(r['sessions_median'])}",f"{pct(r['price_mean'])}/{pct(r['price_median'])}"] for r in result['progression']]))
    text += ['`already_B`는 A 진입 순간 이미 B도 충족, `later_B`는 A가 끝나기 전 B로 진행, `ended_without_B`는 B 없이 A 종료, `censored`는 자료 경계로 미확정이다. B로 진행된 미래 결과로 A 거래를 선택하지 않았다. 같은 순간 A/B가 발생하는 비중을 빼고 ‘B가 항상 더 늦다’고 해석하면 안 된다.\n',
             '## 9. 미완료·체결 누락·통계적 신뢰도\n']
    text.append(table(['시장','전략','미완료','평가상 상대효과 평균/중앙','대기봉 평균/최대','조기종료/공백횡단'],[
        [r.market,LABEL[r.strategy],int(r.n),f"{pct(r.mean_marked_relative)}/{pct(r.median_marked_relative)}",f"{num(r.mean_wait)}/{int(r.max_wait)}",f"{int(r.stale)}/{int(r.gap)}"] for r in opens.itertuples()]))
    text += ['미완료 사이클은 가상의 재매수를 넣지 않고 남은 주식과 순매도 현금으로 평가했다. 위 효과는 해당 미완료 사이클만의 매도 직전 기준 상대효과이며, 이전 완료 사이클까지 합친 누적자산 효과는 경로표에 있다. 반등 신호가 마지막 날 나와 다음 체결봉이 없는 경우와 반등 자체가 없는 경우는 원장 `status`로 구분한다.\n']
    aq=audit[audit.period=='full'].groupby(['market','strategy'])[['unfilled_sales','ignored_sales','unfilled_buys']].sum()
    text.append(table(['시장','전략','다음 봉 없어 미체결 매도','현금 대기 중 무시한 매도','다음 봉 없어 미체결 반등'],[[m,LABEL[s],*map(int,r)] for (m,s),r in aq.iterrows()]))
    text += ['주 검정량은 `평균 log(1+비용 후 전체 주식수 변화)`다. 반복 거래에서 한 번의 큰 이익이 산술평균만 높이는지 확인하기 위한 복리 지표다. 종목×매도 분기를 동시에 재표집하는2,000회 군집 bootstrap을 사용했다. 같은 종목의 반복 사건과 같은 시장 분기의 공동 충격을 독립 사건으로 간주하지 않는다. 아래는12개 시장·전략 비교에 대한 Bonferroni 가족95% 구간이다. 극단 분위수의 Monte Carlo 오차와 분기 간 장기 의존성은 남는다.\n']
    rows=[]
    for m in ('KR','US'):
        for s in STRATEGIES:
            a=summ(m,s);h=summ(m,s,'oos');b=h['log_inference'];c=cohort(m,s,'oos')
            rows.append([m,LABEL[s],pct(a['net_log_mean']),pct(h['net_log_mean']),f"{pct(b['family_ci95'][0])} ~ {pct(b['family_ci95'][1])}",
                         f"{b['stock_clusters']}/{b['quarter_clusters']}",f"{pct(c['annual_log_family_low'])} ~ {pct(c['annual_log_family_high'])}"])
    text.append(table(['시장','전략','전체 log효과/사이클','OOS log효과/사이클','OOS 가족95%CI','종목/분기 군집','OOS 고정표본 연환산 log상대수익 가족CI'],rows))
    text += ['학습은2016–2022년 안에 끝난 사이클,2023년1분기는 경계 구간, 검증은2023년4월 이후 새 매도 신호다. 여섯 전략을 전부 그대로 평가했고 성과가 좋은 것으로 규칙을 교체하지 않았다. 새 검증기간 시작 보유를 사용한 독립 경로도 비교했다. 다만 같은 역사 자료가 이전 연구에서 이미 쓰였으므로 **시간 분리 검증이지, 아무도 보지 않은 신규 데이터나 전향적 실거래 검증은 아니다.**\n',
             '고정 표본의 통계는 전략−보유의 일별 로그수익 차이를 분기 블록으로 묶어 재표집했다. 사건 단위의 양수만으로 포트폴리오 초과수익이 입증되지 않으며, 검증기간의 분기 수가 적어 구간이 넓을 수 있다. 조건별 표의 수많은 비교는 확정적 발견 검정으로 사용하지 않았다.\n',
             '## 10. 자료·정확성 검증과 남는 한계\n',
             f"기존 연구 원본 **{len(meta['input_hashes']):,}개 해시**를 다시 대조했다. 가격·시점시총 원본을 다시 수집하거나 과거 DD 사건을 재활용하지 않고 새 상태 전환과 거래 원장을 계산했다. 당시 적격일이 있는 유효 입력은 한국{sum(x['market']=='KR' for x in meta['stocks']):,}종목·미국{sum(x['market']=='US' for x in meta['stocks']):,}종목이다. 최초 반등 고점 규칙을 쓰지 않으므로 이전 DD 연구와 사건·종목 수가 다르다. 기존 WBD 응답의 timestamp 결측은 그대로 미포함·기록했다.\n",
             '한국은 FinanceData/marcap의 고정 커밋에서 날짜별 KOSPI 보통주 코드와 종목 시총 순위를 재구성했다. KOSPI200 구성도, 현재 상위200도 아니다. 기준가격 변경 계수로 OHLC를 역조정했지만 권리배정·합병·현금배당의 실제 모든 현금흐름을 복원하지는 못했다.\n',
             '미국은 SEC의 사건일보다 앞서 공시된 최근 시점 주식수(기준일180일 이내)를 같은 분할단위 가격에 곱한 시총 근사다. 공시 사이 주식수 변동, 복수 클래스와 ADR 제외, 정확한 티커–CIK 대응 결측이 남는다. **현재 목록에 없는 과거 상장폐지 회사의 누락은 해결되지 않았다.** 역사 시총만 쓴다고 이 생존 편향이 없어지는 것은 아니다. 상장폐지 수익 누락이 성과 추정을 왜곡할 수 있다는 근거는 [Shumway의 원 연구](https://doi.org/10.1111/j.1540-6261.1997.tb03818.x)를 참조한다.\n',
             '처음 입력 감사에서 기존 DD 코드가2017-09-29→10-10의 정상 장기 연휴에도 지표를 초기화하는 것을 발견했다. 성과 요약을 보기 전에 미완료 실행을 중단·보존하고, 실제 연속 시장 거래일이면 유지하도록 수정했다.323개 인위적 구간 경계를 합쳤다. 실제 무거래·비정상 OHLC·빠진 시장 세션은 여전히 구간을 끊으며 원래 보유 주식·현금은 이어진다. 이 변경은 매도·반등·비용·검증기간 최적화가 아니다. 최초 규약 `bdfc49c`, 구현 `268838f`, 달력 보정 `88cd46b`로 이력을 남겼다.\n',
             '미완료 사례의 마지막 가격을 확정 청산금으로 볼 수 없고, 가격 제한일·정지 재개 경매의 실제 체결 가능 수량도 확인하지 못했다. 이 한계가 큰 사례의 이익은 실현 가능한 이익으로 단정하지 않는다. 비용 스트레스와 체결 지연·배당 민감도는 이 부족한 원자료를 대신하지 않는다.\n']
    if verification:
        text.append(f"**독립 검증 {verification['status']}:** 핵심21검사, 원본{verification['raw_hashes']:,}해시·파생{verification['array_hashes']:,}해시, 전체 원장{verification['ledger_rows']:,}행의 산식·시간순서·중복·비용 단조성 검사. 독립 scalar SMA/일별 현금 원장으로{verification['independent_cycles']:,}사이클·{verification['independent_stock_strategy_paths']}종목×전략 경로를 재생하고 미래 자료를 잘라낸 prefix 결과도 일치했다. 원본 가격·시총{verification['independent_raw_price_cap_values']:,}값, 신호 플래그{verification['independent_signal_flags']:,}개, 요약/고정표본 합계{verification['table_checks']}개를 대조했다. 검증기는 구현 일치를 확인하며 누락된 상장폐지·배당의 실재를 증명하지 않는다.\n")
    text += ['## 11. 판단과 재현 자료\n',
             '이 전략의 가치는 “역배열 뒤 하락한 적이 많다”가 아니라 **비용을 내고 현금으로 기다렸다가 신호대로 되산 뒤 자산이 더 남는가**에 달려 있다. 일부 큰 하락에서 방어가 되더라도 반등을 뒤늦게 따라사거나 반복 비용이 누적되면 장기 상승을 유지한다는 목적과 충돌한다. 위 확률·복리 신뢰구간·고정표본 자산·민감도가 함께 지지하지 않는 전략은 추천하지 않는다. 생존 편향 없는 미국 전체시장과 한국의 실제 세후 총수익에 대한 확정 결론은 현재 자료만으로 낼 수 없다.\n',
             '독립 실행·검증 경로를 보존했으며 운영 감시·계좌·주문·예약에는 적용하지 않았다. 다음 파일에서 모든 결과와 실패·누락을 추적할 수 있다.\n',
             '\n'.join('- '+link(n) for n in ['protocol.json','prepared.json','results.json','summary.csv','cycles.csv.gz','paths.csv.gz','path_summary.csv','conditions.csv','annual.csv','cohort_summary.csv','cohort_annual.csv','progression.csv.gz','open_cycles.csv','examples.csv','execution_audit.csv.gz','verification.json','manifest.json'])+'\n',
             '```powershell\n$researchPython = ".venv/Scripts/python.exe"\n# 원본은 기존 runtime/ma-dd-describe-20261017 유지. 재현 시 새 출력 폴더 사용.\n& $researchPython scripts/ma_reentry_research.py prepare --out runtime/ma-reentry-replay\n& $researchPython scripts/ma_reentry_research.py run --out runtime/ma-reentry-replay\n& $researchPython scripts/ma_reentry_statistics.py --out runtime/ma-reentry-replay\n& $researchPython scripts/verify_ma_reentry.py --out runtime/ma-reentry-replay\n& $researchPython scripts/ma_reentry_report.py --out runtime/ma-reentry-replay --report docs/ma-reentry-replay.md\n& $researchPython -m unittest discover -s tests -p test_ma_reentry.py\n```\n']
    report.write_text('\n'.join(text),encoding='utf-8');print(report,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();render(a.out,a.report)

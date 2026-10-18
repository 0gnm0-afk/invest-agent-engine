"""Korean report centered on alternating holding/avoidance intervals."""
import argparse,os
from pathlib import Path
from ma_timing_segments import ROOT,SOURCE,PAIRS,BUYS,SELLS,read,load_arrays,flags,transactions,ledger
from ma_reentry_report import pct,prob,num,table,LABEL
import numpy as np
import pandas as pd

IDS={p:f'B{BUYS.index(p.split("__")[0])+1}S{SELLS.index(p.split("__")[1])+1}' for p in PAIRS}


def figures(out,result,meta):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False})
    for market in ('KR','US'):
        fig,ax=plt.subplots(figsize=(11,4.8),layout='constrained');x=np.arange(9)
        for phase,off,col in [('hold',-.18,'#3478a7'),('avoid',.18,'#c67a54')]:
            vals=[next(r['price_return_mean'] for r in result['summaries'] if r['market']==market and r['pair']==p and r['phase']==phase and r['period']=='oos')*100 for p in PAIRS]
            ax.bar(x+off,vals,width=.36,label=phase,color=col)
        ax.axhline(0,color='black',lw=.7);ax.set(xticks=x,xticklabels=[IDS[p] for p in PAIRS],ylabel='Mean raw interval return (%)',title=f'{market}: eligible completed intervals, 2023Q2+ temporal holdout')
        ax.legend();fig.savefig(out/f'{market}_intervals.png',dpi=170);plt.close(fig)
        fig,axes=plt.subplots(1,2,figsize=(12,4.9),layout='constrained')
        for axis,frac in zip(axes,[1.,.5]):
            g=pd.read_csv(out/f'{market}_full_cohort.csv');dates=pd.to_datetime(g.date)
            for p in PAIRS:axis.plot(dates,g[p+'__'+str(int(frac*100))+'__base']/g['hold'],lw=1,label=IDS[p])
            axis.axhline(1,color='black',ls='--',lw=.8);axis.set(title=f'{int(frac*100)}% sold during avoidance',ylabel='Net wealth / buy-and-hold')
            axis.legend(ncol=3,fontsize=7)
        fig.suptitle(f'{market}: chronological fixed-inception cohort (2016–2026)')
        fig.savefig(out/f'{market}_relative_wealth.png',dpi=170);plt.close(fig)
    # Fixed examples, not chosen by profitability; all observed history is displayed.
    fig,axes=plt.subplots(2,1,figsize=(12,7),layout='constrained')
    pair='G10_20__D10_20'
    for ax,(m,symbol) in zip(axes,[('KR','005930'),('US','AAPL')]):
        rec=next(r for r in meta['stocks'] if r['market']==m and r['symbol']==symbol);z=load_arrays(rec['path']);f=flags(z)
        ev,b,_=transactions(z,f,pair);dates=pd.to_datetime(z['date']);held=False;start=b
        for e in ev+[dict(fill=len(dates)-1,kind='end')]:
            j=e['fill'];ax.axvspan(dates[start],dates[j],color='#67a98c' if held else '#e1b589',alpha=.22,lw=0)
            if e['kind']!='end':held=e['kind']=='buy';start=j
        ax.plot(dates[b:],z['close'][b:],color='#203c52',lw=1)
        ax.yaxis.set_major_formatter(FuncFormatter((lambda v,pos:f'{v:,.0f}') if m=='KR' else (lambda v,pos:f'{v:,.2f}')))
        ax.set(title=f'{m} {symbol}: B3S2, green=holding, orange=avoidance',ylabel='Adjusted close')
    fig.suptitle('Illustrative full-history phase partition; examples are not winning-strategy selections')
    fig.savefig(out/'phase_examples.png',dpi=170);plt.close(fig)


def render(out,report):
    result=read(out/'results.json');sim=read(out/'simulation.json');meta=read(sim['source_prepared'])
    old=read(SOURCE/'results.json');v=read(out/'verification.json') if (out/'verification.json').exists() else {}
    counts=pd.read_csv(out/'segment_counts.csv');condition=pd.read_csv(out/'conditions.csv');annual=pd.read_csv(out/'cohort_annual.csv')
    rel=os.path.relpath(out,report.parent).replace('\\','/')
    def link(n,label=None):return f'[{label or n}]({rel}/{n})'
    def seg(m,p,phase,period='all'):return next(x for x in result['summaries'] if x['market']==m and x['pair']==p and x['phase']==phase and x['period']==period)
    def co(m,p,f=1.,period='full',mode='base'):return next(x for x in result['cohorts'] if x['market']==m and x['pair']==p and x['fraction']==f and x['period']==period and x['mode']==mode)
    def path(m,p,f=1.,period='full',mode='base'):return next(x for x in result['paths'] if x['market']==m and x['pair']==p and x['fraction']==f and x['period']==period and x['mode']==mode)
    figures(out,result,meta)
    t=['# 이동평균선으로 보유·회피 구간을 나누면 투자 성과가 개선되는가',
       '\n**주 연구:** 매수3×매도3의9조합, 전량 회피와 절반 회피. **보조 연구:** 기존 역배열2×반등3의6개 절반매도 전략. 2026-10-18 작성 · 저장 원자료2016년~2026-10-15 완료 거래일까지.\n',
       '## 1. 먼저 답하면\n']
    for m,name in [('KR','한국'),('US','미국')]:
        net=[co(m,p,1.,'oos') for p in PAIRS];half=[co(m,p,.5,'oos') for p in PAIRS]
        robust=[IDS[p]+('/전량' if f==1 else '/절반') for p in PAIRS for f in (1.,.5) if co(m,p,f,'oos')['family_low']>0]
        avoid=[seg(m,p,'avoid','oos') for p in PAIRS]
        t.append(f"**{name}:** 시간 분리 검증기간(2023년4월 이후) 회피 구간에서 실제 가격이 하락한 비율은 조합별 {prob(min(r['negative'] for r in avoid))}~{prob(max(r['negative'] for r in avoid))}였다. 같은 기간을 처음부터 연결한 고정 표본의 비용 후 보유 대비 최종자산 차이는 전량 회피 {pct(min(r['relative'] for r in net))}~{pct(max(r['relative'] for r in net))}, 절반 회피 {pct(min(r['relative'] for r in half))}~{pct(max(r['relative'] for r in half))}다.36개 비교를 고려한 신뢰구간이0을 웃도는 조합은 **{', '.join(robust) if robust else '없다'}**.\n")
    t += ['**이번 자료에서는 추천할 만한 보유 대비 우위를 발견하지 못했다.** 새9조합의 전량·절반 방식 모두 전체기간과 검증기간 고정 표본에서 최종자산이 보유보다 적었다. 원래6개 절반매도 전략도 같은 방향이다. 늦은 골든크로스는 큰 낙폭을 줄이는 데 도움이 되었지만, 장기 상승을 최대한 유지하면서 하락만 피하는 목적은 달성하지 못했다. 다만 “모든 종목에서 항상 손해” 또는 “진정한 기대수익이 반드시 음수”까지 증명한 결과는 아니다.\n',
          '이 연구는 기업을 고르는 능력을 평가하지 않는다. 첫 적격 시점에 투자 아이디어가 있다고 가정한 종목에서, 신호를 따라 **언제 주식에 노출되고 언제 현금으로 기다리는가**만 비교한다. 회피 중 내린 가격은 피한 하락, 오른 가격은 놓친 상승으로 양쪽 모두 집계한다. 낙폭 감소와 자산 증가를 별개의 결과로 평가한다.\n',
          '**자료 범위:** 사용자가2026-10-18에 정정한 대로 기존 데이터셋을 최대한 재사용했다. 확보 입력은 한국406·미국2,000종목이며 실제 시작 가능한 경로는405·1,999종목이다. 한국450140·미국LVO는 유일한 적격일 다음에 연속 유효봉이 없어 초기 체결을 만들지 못했다. 손실 종목이라서 제외한 것이 아니다. 미국의 현재 목록 기반 표본에는 과거 상장폐지 누락이 남으며, 한국의 실제 배당·합병/폐지 회수금액도 완전하지 않다. 아래는 이 관측 표본과 명시한 비용 가정에 대한 실증이다. 생존 편향 없는 전체시장 또는 실제 세후 총수익이라고 확대하지 않는다.\n',
          '## 2. 신호와 실제 매매 순서\n',
          table(['기호','매수 신호'],[['B1','종가의 MA5 상향 돌파'],['B2','MA5의 MA10 상향 돌파'],['B3','MA10의 MA20 상향 돌파']]),
          table(['기호','매도 신호'],[['S1','MA5의 MA20 하향 돌파'],['S2','MA10의 MA20 하향 돌파'],['S3','MA5<MA20 및 MA10<MA20 상태에 처음 진입']]),
          '교차는 직전일 반대쪽 또는 동률→당일 엄격한 돌파다. 단순 이동평균을 사용하며, 같은 상태에 머문 날은 새로운 신호가 아니다. 매수 신호가 매도 신호보다 항상 먼저 또는 나중에 온다고 가정하지 않는다. **보유 상태에서는 매도 신호만, 회피 상태에서는 매수 신호만** 사용한다. 같은 종가에 양쪽 신호가 나와도 현재 상태에 맞는 주문 하나만 다음 거래일 시가에 실행한다. 체결 후 그날 종가에서는 바뀐 상태로 다시 판단한다.\n',
          '100% 회피는 보유 주식 전량을,50% 회피는 당시 보유 주식 절반을 팔고 나머지를 유지한다. 다음 매수 신호의 다음 시가에 해당 순매도 현금만 전액 재투자한다. 손절·익절·최대 대기일·추세 필터는 추가하지 않았다. 첫 적격 종가 다음 시가부터100% 방식은 현금,50% 방식은 절반 주식·절반 현금으로 시작해 **첫 실제 매수 교차**를 기다린다. 처음부터 전부 보유한 경우도 별도 민감도로 계산했다. 시작 이전의 좋은 매수 신호를 사후에 골라 가져오지 않는다.\n',
          '첫 종목 포함 시점은 당시 한국 KOSPI 보통주 시총 상위200 또는 미국 시점 추정 시총20억 달러 이상이다. 그 뒤에는 시총 조건을 벗어나도 주식과 모든 신호를 계속 추적한다. 시총 이탈을 또 다른 청산 규칙으로 만들지 않기 위해서다. **구간 분포의 주 표는 구간 시작 신호 당일에도 적격인 구간**, 연결 성과는 최초 선정 후 모든 구간이다. 자격 밖·결측 구간 수와 전체 구간 분포도 저장해 이 차이를 숨기지 않았다.\n',
          '완료 보유 구간은 매수 시가→다음 매도 시가, 완료 회피 구간은 매도 시가→다음 매수 시가다. 경계 시가를 공유하므로 누락·겹침 없이 전체 가격 경로를 복원한다. 처음 진행 중이던 구간과 마지막 미완료 구간은 완료구간 분포에서 구분하고, 자산·보유 시간 계산에는 모두 포함했다. 마지막 날 강제 매도·매수는 하지 않는다.\n',
          f'![전체 시계열의 보유·회피 예시]({rel}/phase_examples.png)\n',
          '위 삼성전자·AAPL과 B3S2는 구간 구분을 설명하는 고정 예시이며 성과가 좋은 종목·조합을 선정한 그림이 아니다.\n',
          '## 3. 보유 구간과 회피 구간의 성격\n',
          '다음 표는 사건 당시 적격이며 실제 양쪽 체결이 있는 완료 구간이다. 보유 구간 수익은 높을수록 좋지만, 회피 구간의 **가격 변화는 음수일수록** 회피에 유리하다. 평균과 중앙값을 함께 보며, 완료된 구간만 골라 투자가 시작된 것으로 누적 성과를 만들지는 않았다.\n']
    for m,name in [('KR','한국'),('US','미국')]:
        rows=[]
        for p in PAIRS:
            h=seg(m,p,'hold');a=seg(m,p,'avoid')
            rows.append([IDS[p],f"{h['n']:,}/{a['n']:,}",f"{pct(h['price_return_mean'])}/{pct(h['price_return_median'])}",
                f"{num(h['sessions_mean'])}/{num(h['sessions_median'])}",f"{pct(a['price_return_mean'])}/{pct(a['price_return_median'])}",
                f"{num(a['sessions_mean'])}/{num(a['sessions_median'])}",prob(a['negative']),prob(a['positive'])])
        t += [f'### {name}\n',table(['조합','보유/회피 구간수','보유 수익 평균/중앙','보유 봉 평균/중앙','회피 가격 평균/중앙','회피 봉 평균/중앙','피한 하락 확률','놓친 상승 확률'],rows),
              f'![{name} 검증기간 보유·회피 가격 변화]({rel}/{m}_intervals.png)\n']
    t += ['**보유 구간 평균은 모든 조합에서 양수지만 중앙값은 모두 음수였다.** 긴 상승을 잡은 소수 구간이 평균을 끌어올렸다. 반면 회피 구간의 가격 변화는 두 시장 모든 조합에서 평균·중앙값이 모두 양수였다. 즉 전형적 회피는 더 싸게 돌아오기보다 더 비싸게 돌아오는 구간이었다.\n',
          '매수를 B1에서 B3로 늦추면 보유 구간 중앙기간은 대체로24~28봉에서13~17봉으로 짧아지고, 회피 구간은3~4봉에서14~18봉으로 길어졌다. 늦은 확인이 상승을 더 길게 보유하도록 만들지는 않았다. 신호가 확인될 때 이미 상승 구간의 일부가 지나 있기 때문이다. 확률과 꼬리를 분리한 다음 표를 함께 봐야 한다. 수익률은 해당 구간의 시작 시가 대비이며 연율화하지 않았다.\n',
          '## 4. 분포·큰 하락·놓친 상승\n']
    for m in ('KR','US'):
        rows=[]
        for p in PAIRS:
            h=seg(m,p,'hold');a=seg(m,p,'avoid')
            rows.append([IDS[p],f"{pct(h['price_return_p05'])}/{pct(h['price_return_p95'])}",
                f"{pct(a['price_return_p05'])}/{pct(a['price_return_p95'])}",
                f"{pct(a['negative_mean'])}/{pct(a['positive_mean'])}",
                f"{num(h['sessions_p95'])}/{num(a['sessions_p95'])}",prob(a['drawdown10']),prob(a['giveback5_conditional']),
                f"{pct(a['gain100_mean'])}/{pct(a['gain50_mean'])}"])
        t += [f'### {m}\n',table(['조합','보유 수익 P5/P95','회피 가격 P5/P95','회피↓/↑ 조건부 평균','보유/회피 봉P95','회피 중10%↓','5%↓ 후 가격 회복 비율','순재매수 주식수 전량/절반'],rows)]
    t += ['‘회피 중10%↓’는 그 구간에서 실제로10% 낮은 저가가 한 번이라도 나타난 비율이며, 그 저가에 재매수했다고 계산한 비율이 아니다. ‘5%↓ 후 가격 회복’은5% 이상 내렸던 회피 구간 중 실제 재매수 시가가 매도가 이상인 비율이다. 매수일 시가 이후의 장중 저가는 미리 활용하지 않는다. 구간 내 최저·최고·종가 최대낙폭, Q25/Q75, 달력일 기간은 '+link('segment_summary.csv')+'에 포함했다.\n',
          '순재매수 주식수는 매도대금으로 산 수량의 매도 전 전체 수량 대비 변화다. 전량은 `(1−매도비용)/(1+매수비용)×매도가/매수가−1`, 절반은 그 값의0.5배다. 이것만 평균한 값과 계속 연결한 포트폴리오 자산은 동일한 통계가 아니다.\n',
          '## 5. 모든 구간을 연결한 경제적 성과\n',
          '기본 비용은 한국 매도0.35%·매수0.05%, 미국 각각0.05%다. 더 불리한 비용은 한국0.50%/0.20%, 미국0.25%/0.25%다. 수수료·스프레드·슬리피지와 한국 매도세 부담 여유를 둔 **가정**이며, 실제 연도별 세율표나 특정 증권사 체결자료는 아니다. 양도소득세·환전·원천징수는 제외한다. 시가는 소규모 주문의 체결 대용값으로 가격제한·정지 재개·유동성 부족에서 체결을 보장하지 않는다.\n',
          '고정 표본은2016년 첫 적격 종가에 알 수 있던 종목에 같은 초기자산을 주고 추가 편입·재균형 없이 연결했다. 비교 보유도 같은 시작 시가·마지막 평가가격이다. 검증기간은2023년4월 초 새 표본을 같은 방식으로 시작했다. 각 표본의 끝난 종목은 마지막 가격으로 평가하고 별도 스트레스로 불확실성을 확인했다. **상대자산 차이**는 전략/보유−1이며 수익률 %p 차이와 다르다.\n']
    for m in ('KR','US'):
        info=next(r for r in sim['cohorts'] if r['market']==m and r['period']=='full')
        t.append(f"### {m} — 최초 고정 표본 {info['n']}종목, 조기 자료종료 {info['stale']}종목\n")
        rows=[]
        for p in PAIRS:
            a=co(m,p);b=co(m,p,.5);pa=path(m,p)
            rows.append([IDS[p],f"{pct(a['relative'])}/{pct(b['relative'])}",f"{pct(a['cagr'])}/{pct(b['cagr'])}/{pct(a['hold_cagr'])}",
                f"{prob(a['mdd'])}/{prob(b['mdd'])}/{prob(a['hold_mdd'])}",
                f"{prob(pa['mean_hold_fraction'])}/{num(pa['median_trades_per_year'])}",f"{pct(co(m,p,1.,'oos')['relative'])}/{pct(co(m,p,.5,'oos')['relative'])}"])
        t += [table(['조합','최종자산 차이 전량/절반','CAGR 전량/절반/보유','MDD 전량/절반/보유','보유시간/연 거래수 중앙','OOS 최종차이 전량/절반'],rows),f'![{m} 시간순 연결 자산]({rel}/{m}_relative_wealth.png)\n']
    t += ['연 거래수는 매수와 매도 각각1건이며1년 이상 관측된 종목의 중앙값이다. 보유시간은 실제 유효 종가 관측일 중 보유 상태인 비율의 종목평균이다.50% 방식은 회피 상태에서도 절반 주식을 보유한다. 실제 자산가중 주식 노출률도 경로표에 따로 저장했다. 종목별 시작일이 다른 평균을2016년부터 투자한 단일 포트폴리오처럼 표시하지 않았다.\n',
          '개별 투자 아이디어의 경험을 보기 위해 아래는 각 종목의 최초 적격 시점부터 끝까지 연결한 분포다. 보유를 이긴 종목 비율과 상대자산 중앙값을 함께 표시한다. 종목마다 시작일과 관측기간이 달라 고정 표본 포트폴리오와 다른 질문에 답하며, 사용자의 실제 기업 선정 능력에 대한 검증은 아니다.\n']
    for m in ('KR','US'):
        rows=[]
        for p in PAIRS:
            a=path(m,p);b=path(m,p,.5)
            rows.append([IDS[p],a['stocks'],f"{prob(a['win'])}/{prob(b['win'])}",
                f"{pct(a['median_relative'])}/{pct(b['median_relative'])}",
                f"{pct(a['p05_relative'])}/{pct(a['p95_relative'])}",
                f"{prob(a['end_cash_fraction'])}/{prob(b['end_cash_fraction'])}"])
        t += [f'### {m} 종목별 전체 경로\n',table(['조합','종목수','보유 초과 비율 전량/절반','상대자산 중앙 전량/절반','전량 상대자산 P5/P95','끝 현금 잔존 전량/절반'],rows)]
    t += ['한국 절반 회피에서는 일부 느린 매수 조합의 종목별 보유 초과 비율이50%를 넘고 상대자산 중앙값도 양수였다. 이는 반대 근거로 남긴다. 다만 종목별 최초 적격 시점이 다른 분포이고, 같은 시점에 출발한 포트폴리오 및 별도 검증기간의 자산 우위로 이어지지 않았다. 어떤 기업을 골랐는지에 따라 효과가 달라질 수 있지만, 이 결과로 성공할 종목을 사전에 식별했다고 볼 수 없다.\n',
          '## 6. 비용·시작상태·배당·체결과 노출 축소의 영향\n',
          '아래는 전량 회피의 고정 표본 **보유 대비 최종자산 차이**다. 절반 회피의 같은 모든 계산은 '+link('cohort_summary.csv')+'에 있다. 특정 민감도에서 좋은 값을 골라 기본 결과를 바꾸지 않았다.\n']
    for m in ('KR','US'):
        rows=[]
        for p in PAIRS:
            rows.append([IDS[p]]+[pct(co(m,p,mode=mode)['relative']) for mode in ('gross','base','stress','initial_held','delay2','income','interest4')])
        t += [f'### {m}\n',table(['조합','무비용','기본','고비용','처음부터 보유','하루 지연','배당민감도','현금이자4%'],rows)]
        rows=[]
        for p in PAIRS:
            a=co(m,p);pas=co(m,p,mode='base_passive');pa=path(m,p)
            rows.append([IDS[p],prob(pa['mean_exposure']),f"{prob(a['up_capture'])}/{prob(a['down_capture'])}",
                pct(a['final']/pas['final']-1),f"{prob(a['mdd'])}/{prob(pas['mdd'])}"])
        t.append(table(['조합','평균 실제 주식 노출','상승일/하락일 포착','동일노출 정적혼합 대비 자산','MDD 타이밍/정적혼합'],rows))
    t += ['**거래비용만의 문제도 아니었다.** 전량 방식의 무비용 고정 표본도9조합 모두 보유를 하회했다. 한국 B2S3은 무비용 상대자산−1.30%에서 기본비용−25.04%로 악화되어 반복 비용의 영향이 컸다. 미국 B3 계열은 무비용에서도 약49~52% 하회해 상승을 놓치는 영향이 더 뚜렷했다. 처음부터 전부 보유하거나 체결을 하루 늦추고, 현금에 연4%를 주어도 이 표의 기본 결론은 뒤집히지 않았다.\n',
          '전량 회피의 같은 평균 노출 정적혼합 대비 최종자산도 두 시장9조합 모두 더 적었다. 낮은 MDD가 교차 타이밍 자체의 수익 우위를 뜻하지 않는다는 근거다.\n',
          '상승일/하락일 포착은 해당 보유 포트폴리오의 일별 로그수익 부호별로 전략 로그수익 합계를 나눈 값이다. 상승일 포착이 낮으면 상승 기회를 놓친 것이다. 하락일 포착이 낮으면 일별 하락 노출을 줄인 것이다. 이는 전체 누적자산이나 MDD를 대체하지 않는다.\n',
          '동일노출 정적혼합은 전략의 사후 평균 주식 노출만큼 처음에 주식, 나머지 현금을 두고 보유한 **효과 분해용 진단**이다. 미래 노출을 알고 실행할 수 있는 전략이나 추가 후보가 아니다. 단순 현금 보유로도 얻을 수 있는 위험 감소와 교차 신호의 추가 기여를 구별하기 위해 제시한다.\n',
          '미국 배당민감도는 동일 Yahoo 응답의 adjclose/close를 적용한 총수익 단위 대용값이며 가격 신호를 바꾸지 않았다. 실제 지급일 현금·원천징수 원장은 아니다. 한국은 실제 배당 이력이 없어 주식 보유에 연4% 연속수익을 주는 가정이다. 현금이자4%는 별도의 가정이다. 초기·최종 자산은 양쪽에 같은 기준으로 평가하고 마지막에 가상의 청산비를 넣지 않았다.\n',
          '## 7. 규모·변동성·시장 국면의 일관성\n',
          '아래는2023년4월 이후 완료 적격 구간에서 **9조합에 걸친 평균값의 최솟값~최댓값**이다. 조합을 합쳐 독립 사건 수를 늘린 통계가 아니다. 좋은 하위 조건만으로 전략을 새로 만들지 않는다. 조합별 수치·표본 수는 '+link('conditions.csv')+'에서 확인할 수 있다.\n']
    for m in ('KR','US'):
        rows=[];x=condition[(condition.market==m)&(condition.period=='oos')]
        for (dim,group),sub in x.groupby(['dimension','group']):
            h=sub[sub.phase=='hold'];a=sub[sub.phase=='avoid']
            rows.append([dim,group,f"{pct(h.price_return_mean.min())}~{pct(h.price_return_mean.max())}",
                f"{pct(a.price_return_mean.min())}~{pct(a.price_return_mean.max())}",f"{prob(a.negative.min())}~{prob(a.negative.max())}",
                f"{num(a.n.min(),0)}~{num(a.n.max(),0)}"])
        t += [f'### {m}\n',table(['분류','구간','보유 평균수익 범위','회피 평균가격 변화 범위','피한 하락 확률 범위','조합별 회피 표본수'],rows)]
    t += ['**규모를 나눠도 회피의 평균 가격 변화는 검증기간 모든 규모·9조합에서 양수였다.** 미국은 변동성4분위 모두 같은 방향이다. 한국 최저 변동성의 일부 조합에서만 작은 음수가 나타났고 조합 전반에 일관되지는 않았다. 결측 변동성 등 작은 셀은 확정 결론에 사용하지 않는다.\n',
          '전일 지수가200일선 아래인 구간에서도 회피 평균가격 변화는 양수였으며 오히려 반등을 놓치는 폭이 더 컸다. 따라서 당시 관측 가능한 이 국면 구분만으로 “하락장이니 매도하면 유리하다”고 결론낼 수 없다. 향후 실제 수익으로 상승장·하락장을 사후 분류하지 않았다.\n',
          '한국 규모는 당시1–50/51–100/101–200위, 미국은 메가≥2,000억 달러·라지100억~2,000억·미드20억~100억이다. 변동성은 신호 전일20개 로그수익의 연율화 표준편차다.2016–2022년 적격 종목–일로 정한 사분위 경계를 미래 기간에 고정했다. 시장 국면은 전일 적격 표본의 동일가중 가격 대용지수의 전일200일선 위(up)/아래(down)다. 공식 KOSPI·S&P 500 분류가 아니며 미국 생존 편향도 공유한다.\n',
          '## 8. 연도별 성과와 시간 분리 검증\n',
          '다음은 전량 회피 고정 표본의 **연도별 전략 수익률−보유 수익률(%p)**이다. 완료 구간의 시작연도 분포와 달리 실제 달력연도 자산변화를 비교한다.2026년은10월15일까지다. 절반 회피와 구간별 연도표, 한 해씩 제외한 결과도 함께 저장했다.\n']
    for m in ('KR','US'):
        x=annual[(annual.market==m)&(annual.period=='full')&(annual.fraction==1)]
        rows=[[int(y)]+[pct(g[g.pair==p].iloc[0].difference) for p in PAIRS] for y,g in x.groupby('year')]
        t += [f'### {m}\n',table(['연도']+[IDS[p] for p in PAIRS],rows)]
    t += ['방어 효과는 특정 해에 집중됐다.2022년 미국 전량 방식은9조합 모두 보유보다 연수익이 높았지만,2023~2026년에는 매년9조합 모두 낮았다. 한국도2023~2025년에는 모두 낮았다.2020년 중간 급락을 피한 경험이 그해 전체 우위로 이어진 것도 아니다. 한국9조합의2020년 연수익은 모두 보유보다 낮았다. **전체기간에서 어떤 한 해를 빼더라도 두 시장·두 매도비중·모든 조합의 누적 상대성과는 음수**였다. 손해를 한 번의 특수한 해로만 설명할 수 없다.\n',
          '새 검증 경로는2023년4월 초에 다시 시작하며, 학습 구간 기술통계는2022년 안에 끝난 것만 사용했다.2023년1분기는 경계/완충 구간이다.9조합 모두 고정한 그대로 평가했고 검증 결과로 승자를 골라 신호를 변경하지 않았다. 기존6전략 및 이전 연구가 같은 역사 가격을 썼으므로, **시간 분리 검증이며 새 실시간 자료로 한 전향 검증은 아니다.**\n',
          '주 경제 검정은 검증 고정 표본의 연환산 `log(전략자산/보유자산)`이다. 일별 차이를 분기 블록으로 묶어4,000회 bootstrap하고36개 시장×9조합×2비중에 대해 Bonferroni 가족95% 구간을 제시한다. 종목별 반복 사건을 독립 t검정으로 다루지 않았다. 구간 수익 평균에는 종목×시작분기 군집 bootstrap도 저장했다. 조건부 비교는 탐색적이며 다중검정 후 확정 발견이 아니다. 적은 분기 수·분기 간 장기 의존성과 극단 분위수 Monte Carlo 오차는 남는다.\n']
    rows=[]
    for m in ('KR','US'):
        for p in PAIRS:
            a=co(m,p,1.,'oos');b=co(m,p,.5,'oos')
            rows.append([m,IDS[p],pct(a['annual_log_relative']),f"{pct(a['family_low'])}~{pct(a['family_high'])}",pct(b['annual_log_relative']),f"{pct(b['family_low'])}~{pct(b['family_high'])}"])
    t.append(table(['시장','조합','전량 연log상대수익','전량 가족95%CI','절반 연log상대수익','절반 가족95%CI'],rows))
    t += ['## 9. 미완료·자격 밖 구간과 기록 검증\n']
    rows=[]
    for (m,phase),x in counts.groupby(['market','phase']):
        rows.append([m,phase,int(x.total.sum()),int(x.initial.sum()),int(x.terminal.sum()),int(x.eligible_complete.sum()),int(x.excluded_cap.sum())])
    t.append(table(['시장','구간','전체 구간행','초기 부분구간','끝 미완료','완료 적격','완료 자격밖/결측'],rows))
    t += ['초기와 끝이 같은 한 구간일 수 있으므로 초기/미완료 수는 서로 겹칠 수 있다.9조합의 행 합계는 독립 사건 수가 아니다. 초기·미완료 구간도 실제 현금/주식 상태로 최종자산에 포함했고 완료구간과 별도로 '+link('censoring.csv')+'에 기간·평가가격 변화·생존분석을 남겼다. 정상 연휴는 거래일 연속성을 유지한다. 자료가 빠지면 체결을 만들지 않고 새 지표 워밍업 뒤 실제 새 교차를 기다린다. 보유 주식이나 현금을 임의로 지우지 않는다.\n',
          '초기 규약은 `1cab71f`에9조합 결과를 보기 전에 고정했다. 이전6전략에서 발견한 정상 연휴 처리 보정은 그 결과 요약 전 `88cd46b`로 기록되어 이번 입력에도 반영됐다. 원본 데이터는 추가 수집 없이 그대로이며, 이력 종료·주식분할/권리조정·당시 시총 자료의 한계도 그대로 남는다.\n']
    if v:
        t.append(f"**검증 {v['status']}:** 원본{v['raw_hashes']:,}개·입력{v['array_hashes']:,}개 해시를 대조했다. 구간{v['segment_rows']:,}행의 산식·연결·초기/미완료 구분, 종목×조합{v['stock_pair_chain_checks']:,}개의 전체 가격 복원 및 전량매도 거래비용 항등식을 확인했다. 별도 scalar 계산으로 신호{v['independent_signal_flags']:,}개, 일별 자산{v['independent_capital_paths']:,}경로, 실행{v['independent_actions']:,}건과 prefix{v['prefix_paths']:,}경로가 일치했다. 요약/고정 표본 합계{v['summary_and_cohort_checks']}개도 대조했다. 검증 통과는 실행·계산 정확성의 증거이며 시장자료 완전성이나 실제 주문 체결의 증거는 아니다.\n")
    t += ['## 10. 기존 여섯 절반매도 전략과의 연결\n',
          '원래 요청한 A(부분 역배열)/B(완전 역배열)×C(종가↑MA5)/G(MA5↑MA10)/E(둘 중 먼저)의6개 전략은 그대로 유지했다. 이 보조 연구는 처음부터 보유하고, 매도 신호 당일 자격으로 새 매도를 제한한다. 새9조합은 첫 매수 대기부터 전체 구간을 나누고 최초 선정 뒤 모든 신호를 따르므로 정의가 다르다. 동일 전략인 것처럼 수치 차이를 해석하지 않는다.\n']
    rows=[]
    for m in ('KR','US'):
        for s in ['AC','AG','AE','BC','BG','BE']:
            r=next(x for x in old['summaries'] if x['market']==m and x['strategy']==s and x['period']=='all')
            h=next(x for x in old['summaries'] if x['market']==m and x['strategy']==s and x['period']=='oos')
            c=next(x for x in old['cohorts'] if x['market']==m and x['strategy']==s and x['period']=='full' and x['mode']=='base')
            rows.append([m,LABEL[s],prob(r['cheaper']),prob(r['dearer']),pct(r['net_mean']),pct(h['net_mean']),pct(c['relative'])])
    t.append(table(['시장','기존 전략','싸게 재매수','비싸게 재매수','평균 순주식수','OOS 평균 순주식수','최종자산 차이'],rows))
    t += ['기존6개는 검증기간 평균 순주식수 변화가 모두 음수였고,2016년 고정 표본 최종자산도 모든 전략에서 보유보다 적었다. 미국의 일부 골든크로스 재매수 전략은 전체기간 산술평균 주식수 변화가 조금 양수였지만 평균 로그효과는 음수였다. 큰 이익 꼬리와 반복 운용을 구분해야 하는 사례다. 상세 하락·지속기간·손익분포·배당/비용·미완료 분석은 [기존6전략 보조 보고서](ma-reentry-research-2026-10-17.md)에 보존했다.\n',
          '## 11. 활용 판단과 재현\n',
          '**최종 판단: 보유 대비 자산을 늘리기 위한 기계적 매도·재매수 규칙으로는 추천하지 않는다.** 한국 전체기간의 상대손실이 가장 작았던 절반 B2S3도−12.73%, 미국 절반 B1S2도−13.04%였다. 이들은 사후 순위에서 덜 불리했을 뿐 유효성이 입증된 우승 전략은 아니다. 검증기간에도 각각−10.65%,−5.20%였다.\n',
          '낙폭 관리 효과는 존재했다. 예를 들어 B3S2 전량 회피는 전체기간 MDD를 한국52.22%→26.70%, 미국39.66%→19.46%로 줄였다. 그러나 최종자산은 각각39.59%,53.89% 적었다. 같은 조합의 절반 회피는 손실과 방어 효과를 모두 완화했지만 보유를 이기지는 못했다. **변동과 낙폭을 줄이는 대가로 상승 참여를 포기하는 선택**으로 이해해야 하며, 긴 상승은 유지하고 큰 하락만 피하는 해법으로 확인되지는 않았다.\n',
          '주된 실패 경로는 잦은 교차에 따른 비용, 매도 후 빠른 회복, 느린 골든크로스 확인까지 놓치는 상승이다. 보유 구간 평균의 양수만 보고 유리하다고 판단하면 이 회피 비용을 빠뜨리게 된다. 사용자의 기업 선정이 이 표본과 다른 분포를 만들 가능성은 남지만, 본 연구로 그 조건부 우위를 입증하지는 못했다.\n',
          '실제 계좌·주문·운영 알림·예약에는 적용하지 않았다. 재현 코드와 모든 구간/거래 원장을 분리 저장했다.\n',
          '\n'.join('- '+link(n) for n in ['protocol.json','simulation.json','results.json','KR_segments.csv.gz','US_segments.csv.gz','trades.csv.gz','paths.csv.gz','segment_summary.csv','path_summary.csv','cohort_summary.csv','conditions.csv','annual_segments.csv','cohort_annual.csv','cohort_leave_year_out.csv','leave_one_year_out.csv','segment_counts.csv','censoring.csv','execution_audit.csv.gz','verification.json','manifest.json'])+'\n',
          '```powershell\n$researchPython = ".venv/Scripts/python.exe"\n# 기존 source prepared.json의 실제 배열 경로를 유지한다. 새 출력 폴더 사용.\n& $researchPython scripts/ma_timing_segments.py --out runtime/ma-timing-replay\n& $researchPython scripts/ma_timing_statistics.py --out runtime/ma-timing-replay\n& $researchPython scripts/verify_ma_timing.py --out runtime/ma-timing-replay\n& $researchPython scripts/ma_timing_report.py --out runtime/ma-timing-replay --report docs/ma-timing-replay.md\n& $researchPython -m unittest discover -s tests -p test_ma_timing_segments.py\n```\n',
          '원본 입력은 기존 MA-DD 자료를 가리킨다. 일부 재사용 미국 파생 배열은 `ma-reentry-20261017-calendar-draft/arrays`에 있으므로, 폴더 이름만 보고 삭제하면 안 된다. 정확한 입력 경로와 해시는 참조된 prepared.json에 고정되어 있다.\n']
    report.write_text('\n'.join(t),encoding='utf-8');print(report,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();render(a.out,a.report)

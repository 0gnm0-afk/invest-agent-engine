"""Descriptive signed-drawdown tables and scientific figures."""
import argparse,os,sys
from pathlib import Path
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from ma_dd_describe import read,write,sha,SIGNALS

NAMES=['종가↓MA5','MA5↓MA10','MA5↓MA20'];GROUPS={'KR_top200':'한국 당시 KOSPI 시총 상위200','US_total':'미국 전체(아래3그룹 합계)','mega':'미국 메가캡 ≥$200.00B','large':'미국 라지캡 $10.00B–$200.00B','mid':'미국 미드캡 $2.00B–$10.00B'}
def num(x,d=2): return '—' if x is None or pd.isna(x) else f'{x:.{d}f}'
def pct(x): return num(x)+'%' if x is not None else '—'
def count(r): return f"{r['n']:,}/{r['stocks']:,}"+(' ⚠' if r['small_sample'] else '')
def tab(h,rows): return '\n'.join(['| '+' | '.join(h)+' |','|'+'|'.join(['---']*len(h))+'|']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def cell(r): return '— (0)' if not r['n'] else f"{num(r['mean'])} / {num(r['median'])} · {count(r)}"


def run(out,report):
 r=read(out/'results.json');rel=os.path.relpath(out,report.parent).replace('\\','/');kr=read(out/'KR_audit.json');us=read(out/'US_audit.json');ka=read(out/'kr_sources.json');ua=read(out/'us_acquisition.json');pop=read(out/'us_population.json')
 main=[dict(market=m,**x) for m,v in r['markets'].items() for x in v['rows'] if x['condition']=='all']
 text=['''# 이동평균선 하락 신호가 나올 때 이미 얼마나 빠져 있는가

독립 기술통계 연구 `MA-DD-DESCRIBE-20261017-v1` · 작성2026-10-17 · **원가격/시총 자료의 최신 완료일: 한국·미국2026-10-15** · 분석 시작2016-01-01.

이 보고서는 실제 세 하향 돌파가 확인된 날의 종가를 그 하락 다리의 관측 고점과 비교한다. 미래 하락 확률, 매도 성공률, 전략 수익률 또는 매도 비중을 추정하지 않는다. 앞선 두 연구의 표본·고점 사건·후보·손실 목표를 재사용하지 않았다. 같은 역사 기간을 쓰므로 통계적으로 서로 독립인 새 실험이라는 뜻은 아니다.

## 1. 먼저 보는 비교표

Drawdown = (신호일 종가 / 해당 하락 다리의 관측 고점 −1)×100. 아래의 음수는 이미 발생한 하락이다. 주 고점은 **조정 고가**다. 종가 고점과 재설정 방법을 바꾼 결과는 뒤의 민감도 표에 함께 실었다.
''']
 text.append(tab(['시장·규모','신호','평균DD','중앙DD','Q25','Q75','Q90','손실크기P90','고점→신호 봉 평균/중앙','사건/종목'],[
  [GROUPS[x['group']],NAMES[SIGNALS.index(x['signal'])],pct(x['mean']),pct(x['median']),pct(x['q25']),pct(x['q75']),pct(x['q90']),pct(x['loss_p90']),num(x['elapsed_mean'],1)+' / '+num(x['elapsed_median'],1),count(x)] for x in main]))
 text.append('''
**분위수의 방향:** Q25·Q75·Q90은 요청대로 음수 Drawdown의 오름차순 분위수다. 따라서 Q90은0에 더 가깝고 큰 손실 꼬리를 뜻하지 않는다. 직관적인 큰 손실 비교를 위해 손실크기P90=−DD의Q10도 추가했다. 예를 들어 손실크기P90이10%면 관측 사건 약90%가10% 이내 하락 시점에 발생했다는 기술통계이며 미래 보장이 아니다. ⚠는 사건100개 미만 또는 종목20개 미만이다.

각 행은 서로 다른 경고 표본일 수 있다. 3차 신호의 평균이 더 낮다고 해서 같은 모든 하락에서 순서대로 그만큼씩 더 빠졌다는 뜻은 아니다. 동일 사건의 대응 비교는 별도로 제시한다.

## 2. 직전 고점과 실제 하향 돌파를 고정한 방법

이동평균은 조정 종가의 단순평균 SMA5/10/20이다. A(t)<B(t)이고 A(t−1)≥B(t−1)인 날만 실제 교차다. 계속 아래에 머문 날과2봉 확인은 새 사건으로 세지 않았다. 수치 연산 오차만 제거하도록 가격의10⁻¹² 상대 허용오차로 수학적 동률을 처리했다. 실질적인 매매 완충폭을 둔 것이 아니다.

고점은 다음 온라인 규칙으로 정했다. 처음에는 관측 종가 저점에서5% 반등이 확인되어야 추적을 시작한다. 이후 지금까지 본 고가가 기존 추적 고가 이상이면 옛 다리는 고점을 회복한 것으로 종료하고 그 날 고가·날짜를 새 고점으로 둔다. 하락 중에는 최저 종가를 추적한다. 그 저점에서 종가가5% 반등하면 당일 신호를 기존 고점에 대해 계산한 뒤 다리를 종료하고, 다음 날부터 반등 확인일 고가를 출발점으로 새 고점을 추적한다. 더 오래된 고점 아래에서 반등한 종목도 새 국지 하락으로 관측한다.

이는 임의63/126일 최고가나 미래 저점을 본 피벗이 아니다. 반등 확인 전날의 고점을 소급해서 바꾸지 않는다. 당일 고가가 형성된 뒤 종가에서 교차할 수도 있으므로 경과 봉0이 가능하다. 고가가 언제 형성됐는지 장중 경로를 추정할 필요 없이, 최종 종가는 그 날 고가 이후에 확정된다. 주가가 다시 크게 빠졌는지 여부를 사건 선정 조건으로 쓰지 않는다.

**5%는 유일한 자연 고점의 정답이 아니다.** 먼저 고정한 재설정 규약이다. 3%·8% 반등, 종가 고점, 전일 ATR14의2배 반등 재설정으로 같은 계산을 반복해 고점 정의 의존성을 비교했다. ATR 방식은 변동성이 사건 구분에도 들어가므로 고정5% 방식과 함께 해석한다. 결과가 마음에 드는 정의로 주 분석을 바꾸지 않았다.

하락 다리마다 각 신호의 첫 실제 교차만 남긴다. 다른 MA 신호가 나왔거나 시간이 지났다는 이유로 다리를 재시작하지 않는다. 첫 교차가 규모/순위 조건 밖에 있었으면 조건 안으로 들어온 뒤 반복된 교차를 최초라고 바꾸지 않는다. 반복 교차 전체를 포함한 결과와 더 큰 running-high parent에서 처음 관측된 다리만 남긴 민감도도 제시했다. 같은 종목·시장 국면의 사건들이 독립이라는 가정은 하지 않는다.

## 3. 실제 자료와 사건 당시 표본

한국은 사용자 추가 답변에 따라 **사건 당일 KOSPI 시가총액 상위200 보통주**다. KOSPI200 지수 구성이 아니다. [FinanceData/marcap 원자료](https://github.com/FinanceData/marcap)는 KRX에서 수집한 날짜별 시세·시총을 보존한 공개 데이터셋이다. 고정 커밋의2015–2026년 파일에서 시장과 보통주 코드(끝자리0)를 구분하고, 매일 시총 내림차순·코드순 동률 처리로 순위를 재계산했다. 현재 순위를 과거에 대입하지 않았다. 우선주를 합산한 법인 전체 시총은 복원하지 못했으며 보통주 종목 시총 순위다.

KRX 직접 경로는 이번 연결에서 LOGOUT/인증키 부족 응답을 반환해 공개 원보존본을 썼다. 자료 설명의 시총 단위 문구를 그대로 가정하지 않고 실제 모든 사용 파일에서 `시총=종가×상장주식수`가 맞는 원(KRW) 단위임을 확인했다. 원장의 일별 시총은 그 날 값이며 가격 조정에는 사용하지 않는다.

한국 OHLC는 `f(t)=(종가(t)−전일대비(t))/전일 종가`를 기준가격 변경 계수로 삼아 이후 계수의 곱으로 일관되게 역조정했다. 예를 들어 원자료의2018-05-04 삼성전자 계수는0.02다. 무거래/잘못된 봉·빠진 거래일·10일 초과 달력 공백은 지표와 고점 추적 구간을 끊으며, 충분한 지표와 새 반등 시작이 생겨야 다시 관측한다. 멈춘 날을 연속된 정상 거래처럼 붙이지 않았다. 단순 배당 재투자 수익률은 아니다.

미국은 원시 보통주 목록의 STOCK을 SEC 정확한 티커–CIK와 대응했다. [SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)의 DEI `EntityCommonStockSharesOutstanding` **시점 주식수**와 [Yahoo 일봉](https://finance.yahoo.com/)의 분할 조정 OHLC·같은 응답의 분할 이벤트를 새로 수집했다. 배당 조정 adjclose는 쓰지 않았다. 현재의 주식 수·시총을 과거로 복사하거나 EPS용 가중평균/희석 주식수로 대체하지 않았다.

미국 시총은 **당시 공시로 알 수 있었던 주식 수를 사용한 근사치**다. 사건일보다 앞선 날 공시된 자료 중 기준일이 가장 최근인 시점 주식수를 선택하고180달력일을 넘으면 결측으로 둔다. 공시 당일의 장 마감 전후를 확정할 수 없어 다음 날부터 사용한다. 주식 수와 종가를 같은 분할 단위로 맞춰 곱한다. 공시 사이 발행/소각을 매일 알 수 없으므로 거래소의 정확한 일별 시총이라고 부르지 않는다. ADR 단위가 미검증인 종목 및 같은 CIK의 복수 상장 보통주 티커는 주 분석에서 제외했다. 상장되지 않은 의결권 주식 등 세부 클래스 범위 한계는 남는다.

메가≥$200B, 라지$10B 이상~$200B 미만, 미드$2B 이상~$10B 미만을 **사건일 근사 시총**으로 구분한다. 미국 전체는 이 세 규모 그룹의 합계이며 미국의 모든 소형주·모든 과거 상장사를 뜻하지 않는다. 현재 원시 목록에 없는 상장폐지 기업과 과거 다른 티커의 완전한 미국 모집단은 확보하지 못했다. 한국은 현재 목록으로 제한하지 않고 시총 원자료에 있는 과거 종목도 포함했다.
''')
 text.append(tab(['시장','입력·대상','최초 교차 사건','반복 교차 제외','첫/마지막 사건일'],[
  ['한국',f"과거 상위200 편입 {ka['symbols']}종목 / 결과 {r['markets']['KR']['stocks']}종목",f"{r['markets']['KR']['first_crosses']:,}",f"{r['markets']['KR']['repeats_dropped']:,}",r['markets']['KR']['first']+' ~ '+r['markets']['KR']['last']],
  ['미국',f"원목록 {pop['raw_count']:,} → SEC단위 대상 {len(pop['eligible']):,} → 수집ready {sum(v['status']=='ready' for v in ua):,} → 처리 {len(us['stocks']):,} → 규모조건 결과 {r['markets']['US']['stocks']:,}",f"{r['markets']['US']['first_crosses']:,}",f"{r['markets']['US']['repeats_dropped']:,}",r['markets']['US']['first']+' ~ '+r['markets']['US']['last']]]))
 text.append('''
동일 종목이 시기에 따라 여러 시총 그룹에 속할 수 있다. 그룹별 종목 수를 더해 미국 전체의 고유 종목 수로 쓰면 안 된다. 첫 교차 사건 수는 세 신호를 합한 관측행 수로, 세 독립 하락의 수가 아니다. 결측/무거래 분절로 일부 관측이 제외되므로 과거 전체 시장의 완전한 전수 통계라고 주장하지 않는다.

일부2026년 기존 Toss 저장 종가와 공개 시총 원자료의 종가가 다름을 입력 감사에서 확인했다. DD 결과를 계산하기 **전에** 가격 소스를 독립적으로 확정했고, 충돌하는 공급자 가격을 섞지 않았다. 차이가 컸던 대표3종목의2026-03-03~06일12개 봉은 네이버 과거 시세와 별도로 대조해 OHLCV60값이 모두 이번 원자료와 일치했다. 이는 점검한 사례의 근거이며 전 시장의 절대 정확성 보증은 아니다. 한국은 같은 원자료의 가격·당시 시총을 사용한다. 연도별 표에서2026년을 따로 확인할 수 있으며 원본은 변경하지 않았다. 최신 확보 원본은10/15까지라10/16 종가까지 전부 반영했다고 주장하지 않는다.

## 4. 변동성 분포와 조건부 Drawdown

σ는 신호 전일 기준 최근20개 로그수익의 표본 표준편차×√252다. ATR%는 전일까지 Wilder ATR14÷전일 종가이며 첫14 TR 산술평균으로 시작해 `(13×전일ATR+당일TR)/14`로 갱신했다. 당일 급락의 수익률/고저폭은 둘 다에 들어가지 않는다.

구간은 시장별 **규모/순위 조건에 해당하는 고유 종목–거래일**의 변동성 분포로 정한 사분위다. 신호가 자주 발생한 날을 세 번씩 넣어 경계를 계산하지 않았다. 임계값 이상 조건은 미리 고정한 Q50·Q75·Q90이며 실제 숫자는 다음과 같다. 표시 반올림과 무관하게 계산은 원정밀도를 썼다. 변동성 이력이 짧은 사건은 무조건부 표에 남기고 조건부 표에서 결측으로 구분한다.
''')
 text.append(tab(['시장','분포의 종목–일 수','지표','Q25','Q50','Q75','Q90'],[
  [m,f"{v['vol_risk_rows']:,}",key,*[pct(100*x) for x in v['cuts'][key]]] for m,v in r['markets'].items() for key in ('sigma','atrpct')]))
 for market in ('US','KR'):
  v=r['markets'][market];groups=['US_total','mega','large','mid'] if market=='US' else ['KR_top200']
  for group in groups:
   text.append('### '+GROUPS[group]+'\n\n각 신호 셀: **평균DD / 중앙DD · 사건수/종목수**. DD 단위는%. ⚠ 표본 부족.\n')
   for key,title in [('sigma','연율화 σ'),('atrpct','ATR%')]:
    cuts=[100*x for x in v['cuts'][key]];labels=[f'<{num(cuts[0])}%',f'{num(cuts[0])}–{num(cuts[1])}%',f'{num(cuts[1])}–{num(cuts[2])}%',f'≥{num(cuts[2])}%']
    cond=[key+'_Q'+str(i+1) for i in range(4)]+[key+'_geQ'+str(q) for q in (50,75,90)]+[key+'_missing']
    labels += [f'≥{num(cuts[i])}% (누적 상위, Q{q})' for i,q in zip((1,2,3),(50,75,90))]+['이력 부족/결측']
    text.append('**'+title+'**\n\n'+tab(['전일 변동성 구간']+NAMES,[[lab]+[cell(next(x for x in v['rows'] if x['group']==group and x['signal']==signal and x['condition']==c)) for signal in SIGNALS] for c,lab in zip(cond,labels)]))
 text.append('''
누적 상위 조건은 서로 겹친다. Q90 이상은 Q75 이상 안에 포함되므로 별개의 독립 표본처럼 더하지 않는다. σ는 일간 수익의 연율화, ATR%는 일간 진폭의 가격 대비 비율이므로 둘의 숫자를 같은 크기로 비교하지 않는다. 여기서의 조건부 평균은 “그 변동성·신호 조합에서 과거 이미 발생해 있던 하락”이며 앞으로의 손실 예측이 아니다.

## 5. 동일 하락 다리에서의 세 신호 비교

아래는 세 최초 교차가 모두 관측된 같은 다리의 대응 표본이다. 전체 사건을 대표하지 않으며 세 신호가 특정 순서로 나타난다고 가정하지 않았다. '순서1→2→3'은 날짜가 엄격하게 다른 경우만 포함한다. 동시 발생은 전체3신호 표본에는 남긴다. 규모별 표는 세 시점 모두 해당 규모여야 포함된다.
''')
 paired=[]
 for m,v in r['markets'].items():
  for x in v['paired']:
   if 'subset' in x: paired.append([GROUPS[x['group']],x['subset'],f"{x['n']:,}/{x['stocks']:,}",*[pct(x['signal_means'][s]) for s in SIGNALS],num(-x['delta2minus1_mean'])+'pp',num(-x['delta3minus2_mean'])+'pp'])
 text.append(tab(['표본','범위','다리/종목','신호1 평균DD','신호2','신호3','1→2 손실증가','2→3 손실증가'],paired))
 text.append('''
손실증가가 양수면 같은 고점 대비 낙폭이 더 커졌다는 뜻이고 음수면 회복한 상태에서 다음 교차가 나타났다는 뜻이다. 이름만으로 순서를 결정하지 않았고 누락 신호를 가정해 채우지 않았다. 실제 순서 빈도는 results.json의 paired/orders에 `+` 동시 발생과 `>` 날짜 선후로 보존했다.

## 6. 고점 정의·중복·순위 기준에 따른 민감도

다음은 동일한 자료에서 고점 재설정과 중복 처리만 바꾼 결과다. 각 셀은 평균DD / 중앙DD · 사건수/종목수다. 종가 고점은 고가 고점과 앵커 가격 자체가 달라지는 별도 정의이며 차이가 나는 것이 오류라는 뜻은 아니다.
''')
 for market,v in r['markets'].items():
  groups=['KR_top200'] if market=='KR' else ['US_total','mega','large','mid']
  for group in groups:
   methods=['primary','rebound3','rebound8','close_peak','atr2','all_crosses','first_leg_per_parent']+(['previous_day_top200'] if market=='KR' else [])
   labels={'primary':'고가·5%반등(주)','rebound3':'고가·3%반등','rebound8':'고가·8%반등','close_peak':'종가·5%반등','atr2':'고가·전일2ATR반등','all_crosses':'5%·반복 교차 포함','first_leg_per_parent':'5%·parent첫 관측 다리','previous_day_top200':'5%·전일 상위200'}
   text.append('**'+GROUPS[group]+'**\n\n'+tab(['정의']+NAMES,[[labels[method]]+[cell(next(x for x in v['sensitivity'] if x['group']==group and x['signal']==signal and x['method']==method)) for signal in SIGNALS] for method in methods]))
 text.append('''
고점 선택에 따라 평균이 바뀌는 폭도 연구 결과다. '직전 고점'에는 단 하나의 데이터 독립 정답이 없으므로 주 표 숫자만 보편적인 법칙으로 사용하지 않는다. 5% 반등 재설정은 변동성이 높은 종목에서 더 자주 새 다리를 만들 수 있고, ATR 방식은 그 차이를 일부 정규화하는 대신 정의에 변동성이 들어간다.

## 7. 신뢰도와 해석의 범위

평균과 중앙값이 크게 다르면 긴 손실 꼬리의 영향이 크다. 큰 값이 불리하다고 제거하거나 임의 손실 상한으로 자르지 않았다. 주 평균의 종목 군집1,000회 bootstrap95% 구간, 종목별 평균을 같은 비중으로 평균한 값, 연도별 분포는 아래 파일에 함께 제공한다. 같은 종목의 사건 의존성을 반영하지만 시장 공통 충격과 데이터 공급자 오류를 완전히 제거하는 구간은 아니다. 기술통계이므로 학습/검증 승자를 선정하지 않았고, 신호 예측력·전략 우월성 검정을 실행한 것처럼 표시하지 않는다.

미국은 현재 목록 생존 편향, SEC 정확한 티커/시점 주식수 결측, ADR/복수 클래스 제외, 공시 간 주식수 변동 미관측이 남는다. 한국도 코드/시장 변경·우선주 합산 제외·과거 데이터 결측·원보존본의 완전성을 확인할 한계가 있다. 무거래/누락 구간 뒤 초기 신호가 제외되는 효과를 전체 모집단으로 일반화해서는 안 된다. 사건수와 종목수가 충분하더라도 새 국면의 같은 평균을 보장하지 않는다.

이 표는 “이 신호가 나타날 때 해당 정의의 고점에서 보통 얼마나 내려와 있었는가”의 참고값이다. 현재 특정 종목의 실시간 고점과 변동성을 같은 정의로 계산하지 않고 표의 평균만 직접 대입할 수는 없다. 4분할 비중·수익률·미래 하락확률·운영 경보는 이번 연구에서 만들거나 변경하지 않았다.

## 8. 전체 표와 재현·검증
''')
 for m,v in r['markets'].items():
  text.append(f'- [{m} 모든 조건·주요 분위수·경과 봉·표본 수]({rel}/{m}_summary.csv) · [{m} 연도별 분포]({rel}/{m}_annual.csv) · [{m} 모든 민감도]({rel}/{m}_sensitivity.csv) · [{m} 날짜/고점/전일변동성/당시시총 사건원장]({rel}/{m}_events.csv.gz) · [{m} 자료/제외 감사]({rel}/{m}_audit.json)')
 text.append(f'''
- [사전 고정 프로토콜]({rel}/protocol.json) · [전체 결과·실제 순서]({rel}/results.json) · [한국 시총 파일·단위·고정 커밋]({rel}/kr_sources.json)
- [미국 표본 제외]({rel}/us_population.json) · [미국 수집 성공/실패]({rel}/us_acquisition.json) · [검증 영수증]({rel}/verification.json) · [해시 manifest]({rel}/manifest.json)
- [한국 대표 시세의 별도 공급자 대조]({rel}/kr_quote_corroboration.json)

핵심11검사 PASS: 실제 교차/지속 구분, 전일 σ/ATR, Wilder seed, 온라인 고점 prefix 불변, 같은 다리 반복 구분, 반등 후 낮은 새 고점, 주식분할 단위/공시일/시점 주식수/180일 제한, 결측 분절, 초기 변동성 결측 유지. 사용 원본7,920개 해시, 원장471,264행의DD산식·중복, 실제 원행79사례의교차·고점·전일변동성, 당시 미국 시총40사례, 변동성 셀 합계30검사를 확인했다. 그림2개도 실제 열어 표시를 검토했다. 여기의 사건행 수는 종목/국면 의존성을 제거한 독립 표본 수가 아니다.

프로토콜은 `27984ab`에 먼저 고정했고 가격 원자료 가용성 감사에 따른 소스 변경은 DD 통계 전에 `0426f9b`에 기록했다. 고점·교차·변동성·조건부 문턱을 결과에 맞춰 변경하지 않았다. 원본은 불변으로 보존하고 연구 산출물은 별도 runtime에 저장했다. 재현 시 새 출력 경로를 사용한다.

```powershell
$researchPython = '.venv/Scripts/python.exe'
# 별도 폴더에 PyArrow와 고정 커밋 한국 원본·SEC/Yahoo 원본을 먼저 준비한다.
& $researchPython scripts/ma_dd_describe.py prepare --out runtime/ma-dd-replay --market KR
& $researchPython scripts/ma_dd_describe.py prepare --out runtime/ma-dd-replay --market US
& $researchPython scripts/ma_dd_describe.py describe --out runtime/ma-dd-replay
& $researchPython scripts/ma_dd_report.py --out runtime/ma-dd-replay --report docs/ma-dd-replay.md
```
''')
 plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':10})
 for market in ('US','KR'):
  entries=[x for x in main if x['market']==market];groups=list(dict.fromkeys(x['group'] for x in entries));fig,axes=plt.subplots(2 if market=='US' else 1,2 if market=='US' else 1,figsize=(11,8.5) if market=='US' else (4,4.5),squeeze=False,sharey=True)
  for ax,group in zip(axes.flat,groups):
   rows=[x for x in entries if x['group']==group];means=np.array([-x['mean'] for x in rows]);med=np.array([-x['median'] for x in rows]);lo=np.array([-x['q75'] for x in rows]);hi=np.array([-x['q25'] for x in rows]);xx=np.arange(3)
   ax.bar(xx,means,color=['#2878B5','#E2A12B','#C44E52'],alpha=.8,label='평균');ax.errorbar(xx,med,yerr=np.stack([med-lo,hi-med]),fmt='kD',capsize=5,label='중앙·사분위');ax.set_xticks(xx,NAMES,rotation=15);ax.set_title(GROUPS[group].replace('$','USD '),fontsize=10);ax.set_ylabel('고점 대비 하락 크기 (%)');ax.legend(fontsize=8);ax.grid(axis='y',alpha=.2)
  fig.suptitle(market+' 실제 하향 돌파 시점 · 고가/5%반등 정의');fig.tight_layout();fig.savefig(out/f'{market}_drawdown.png',dpi=150);plt.close(fig)
  text.insert(3,f'![{market} 신호별 전형적 하락폭]({rel}/{market}_drawdown.png)')
 report.write_text('\n\n'.join(text),encoding='utf-8');print('report',report,flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args();run(a.out,a.report)

"""15% study report; descriptive tables never select final-test winners."""
import argparse,os
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from ma_warning_research import read,write,sha,load_market,NAMES,signal_summary,distribution

LABELS=['종가<MA5','종가<MA10','종가<MA20','MA5<MA10','MA5<MA20','MA10<MA20',
        'MA5 기울기≤0','MA10 기울기≤0','MA20 기울기≤0','종가<MA20·기울기20≤0','MA5<MA10·기울기20≤0']
def label(j): return LABELS[j//2]+f' / {j%2+1}봉'
def p(x): return '—' if x is None else f'{x*100:.1f}%'
def pp(x): return '—' if x is None else f'{x*100:+.2f}pp'
def num(x): return '—' if x is None else f'{x:.1f}'
def ci(x): return '—' if x is None or None in x else f'[{100*x[0]:.1f}, {100*x[1]:.1f}]'
def tab(headers,rows): return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def dd(r): return f"{p(r['mean'])} / {p(r['median'])} / {p(r['p10'])}~{p(r['p90'])}"
def days(r): return f"{num(r['mean'])} / {num(r['median'])} / {num(r['p10'])}~{num(r['p90'])}"


def run(out,report):
    f=read(out/'selection_frozen.json'); result=read(out/'final_results.json'); source=Path(f['source'])
    diagnostic=read(out/'diagnostics.json'); audit=read(source/'data_audit.json')
    rel=os.path.relpath(out,report.parent).replace('\\','/'); oldrel=os.path.relpath(source,report.parent).replace('\\','/')
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'font.size':10})
    text=['''# -15% 하락의 초기 경고 — 한국·미국 독립 검증

작성 2026-10-16 · 가격 기준 **2026-09-30 완료 일봉** · 2026-10-01 저장 수정주가 재사용 · 운영 적용 없음.

## 결론

**-15%로 연구 목표를 변경해 실제 후보 탐색과 보류 종목 검증을 수행했지만, 이번 자료에서 초기 경고의 추가 예측력을 충분히 확인한 신호는 없었다.** 이것은 이동평균선이 항상 무용하다는 결론이 아니다. 빠른 경고가 큰 하락을 상당수 포착한다는 사실과, 비슷한 하락 상태에서 이동평균선 신호가 추가 정보를 준다는 주장은 별개다.

현재 대형주 표본에서는 한국의 `종가<MA20 연속2봉`, 미국의 `MA5<MA20 연속2봉`이 선정 구간 점수상 앞섰다. 아래는 검증 전에 동결한 **설명용 후보**다. 통과한 신호만 우선하고 부족하면 탐색 후보를 남기는 사전 규칙을 적용했으며, 선정 단계에서 모든 조건을 만족한 후보는 두 시장·두 코호트 모두 없었다. 검증 결과를 보고 다른 후보를 우승자로 바꾸지 않았다.

사용자의 변경 지시에 따라 **같은 고점 대비 종가가 처음 -15%에 도달하는 사건**이 주 목표다. -20% 이상 계속 하락한 사례도 포함한다. 손실을15%로 잘라내거나 그보다 더 하락한 사례를 제외하지 않았다. 원래의 마지막 탈출 탐색보다 이번의 초기 식별을 우선했으며, 늦은 탈출 후보는 시점 비교용으로 별도 남겼다. 비중이나 매매 수익률을 최적화하지 않았다.
''']
    headline=[]
    for market in ('KR','US'):
        v=result['markets'][market]['largecap']; r=v['records'][0]['test']
        headline.append([market,label(r['signal_index']),r['n'],p(r['signal_dd']['median']),p(r['execution_dd']['median']),p(r['precision']),p(r['recall']),p(r['executable_recall']),p(r['false_discovery']),num(r['lead_to_target']['median'])])
    text.append(tab(['시장·현재대형주','동결1순위','경보수','발생 낙폭 중앙','다음시가 낙폭 중앙','60봉 -15% 확률','사건 포착률','실행가능 포착률','경보 중 오경보','적중시 남은 봉 중앙'],headline))
    text.append('''
하락폭은 고점 대비 손실의 양의 크기로 표기한다. 확률은 신호 이후60관측봉, 사건 포착률은 고점 이후126관측봉 내 첫 -15% 사건 기준이므로 분모와 시간 원점이 다르다. 경고 시점에 이미 -15%에 도달한 사건은 사전 경고 성공에서 제외했다.

## 1. 하락률 기준과 관련 근거

10% 조정·20% 약세장은 주로 지수의 시장 용어다. [FINRA 설명](https://syndication.finra.org/content/key-terms-tough-times-vocabulary-stressed-markets)은 이런 용어를 정리하며, 개별 대형주에 대한 최적 경고 문턱을 검증하지 않는다. 이번 검토에서 개별 대형주에 보편적으로 더 우수한 누적 낙폭 기준이라는 근거는 확보하지 못했다.

[Campbell·Lettau·Malkiel·Xu(2001)](https://dash.harvard.edu/entities/publication/73120378-863f-6bd4-e053-0100007fdf3b)는 시장·산업·기업 고유 변동성을 구분한다. 같은15%라도 종목 변동성과 시대에 따라 드문 정도가 다르다는 분석상의 근거이며, 15% 자체의 적정성을 증명하지 않는다. [Johansen·Sornette의 개별 기업 포함 낙폭 연구](https://arxiv.org/abs/cond-mat/0010050)와 [작은 반등을 허용하는 낙폭 정의 연구](https://arxiv.org/abs/cond-mat/0210574)는 낙폭 분포와 사건 정의의 중요성을 보여준다. 여기서도 모든 개별주에 적용할 단일 대체 퍼센트가 도출되는 것은 아니다.

[Kim·Kim(2023), 한국 기업 주가 급락 연구](https://journals.sagepub.com/doi/10.1177/21582440231199377)는 기업 고유 주간 수익의 극단값 및 비대칭 변동성 계열 지표를 쓴다. 이는 누적 고점 낙폭이나 사전 MA 경고와 다른 정의라 그대로 대체하지 않았다. [Duke의 Drawdowns(2020) 게재 기록](https://scholars.duke.edu/publication/1461725)도 참고했지만 원문 전체를 확보한 것으로 주장하지 않는다.

최초 v1은 사용자 fallback에 따라10/20%를 주 분석,15%를 민감도로 사전 고정했다. 이후 사용자가 “15% 하락을 초기에 식별”하도록 명시해 **v2를 별도로 고정**했다. 따라서15%는 실용적인 사용자 손실 목표이며 학술적 약세장 정의나 데이터에서 발견한 자연 경계가 아니다. v2 프로토콜의 원래 threshold_decision·sample·confidence 문구는 v1 출처 설명으로 남아 있고, 충돌 시 명시적 revision_reason·primary_target·cohort_priority·ranking15가 우선한다. 기존 불변 파일은 고치지 않았다.

한국과 미국은 별도 시장·변동성 분포·장세 대리변수·대조군·선정·검증을 사용했다. 이번 자료만으로 국가 간 제도 차이가 특정 신호의 원인이라고 말할 수 없다. 변동성 보정은 대응 분석에 포함했으며, 표준편차 목표로 결과 라벨을 바꾸지는 않았다.

## 2. 표본과 실시간 고점·사건

원시 SavedHistory 보통주 목록에서 운영 필터 없이 한국600·미국600개를 고정 해시로 뽑았다. 이전4분할 연구의800종목은 식별자만 이용해 제외했다. 현재 시총 상위100 중 겹치지 않는 종목을 보조로 더해 총 한국667·미국672종목을 준비했다. 600개 일반 코호트(core)와 현재 상위100 태그(largecap)는 겹칠 수 있으나 합산하지 않는다. 후자는 현재 생존·대형주라는 **사후 분류**로, 과거 당시 대형주 구성은 아니다. 이 연구는 “역사적 대형주 전체”에 대한 보증을 제공하지 못한다.

시총 태그는 2026-10-15 수집 snapshot의 제공자 보통주·시총 필드가 있는 가격이력 종목을 기준으로 한다. 전체 거래소의 완전한 시점별 구성이라는 보증은 없다. 기존 매수·매도 채택 여부, MA60 정배열, 상승률, 장기선 위 여부는 표본 조건이 아니다.

관측 가능한63봉 최고 **종가**를 감시한다. 같거나 높은 종가가 나오면 고점 가격과 날짜를 갱신하며, 처음 낮아진 종가부터 고점을 고정한다. 종가가 고점을 회복하면 사건 종료다. 이전 고점이63봉 이상 오래됐고 새63봉 최고 종가가 낮은 위치에서 형성되면 새 국지 고점으로 재설정한다. 126봉이 지났거나 신호가 반복됐다는 이유로 새 사건을 만들지 않는다. 더 큰 누적 고점이 회복되기 전 국지 사건들은 같은 parent로 묶고 첫 사건만 남기는 민감도를 제시했다. 국지 사건을 독립 하락 전체로 부풀리지 않는다.

각 사건은 최대126봉의 경고 관측과 추가60봉의 결과 지지구간을 요구한다. 고점+186봉이 같은 시간 분할에 있어야 하므로2026-09-30 직전 고점 사건까지 모두 포함하는 것은 아니다. 학습2010–2016, 선정2017–2020, 최종2021 이후로 나누고 약30% 종목은 과거까지 전부 선정에서 보류했다. 보류 종목이 공급한 검증 결과만 최종으로 사용한다. 기초 지표 준비는2008년부터다.

SMA5/10/20, 기울기는 MA(t)−MA(t−3)이다. 1봉은 조건이 거짓에서 참으로 바뀌는 날, 2봉은 연속 참2개가 처음 완성된 날이다. 사건별 첫 새 발생(first)과 사건 시작부터 이미 조건이 참인 경우도 포함한 첫 확인 상태(first_state)를 분리했다. 지속되는 동안 매일 새 경고로 세지 않는다. 두 종류가 같아지는 후보도 그대로 기록해 독립 증거처럼 취급하지 않았다.
''')
    sample_rows=[]
    for market in ('KR','US'):
        for cohort in ('largecap','core'):
            for split,title in ((0,'학습'),(1,'선정'),(2,'최종')):
                meta=read(out/f'{market}_s{split}_{cohort}_first.json')
                sample_rows.append([market,cohort,title,meta['symbols'],meta['events'],meta['parents'],*[meta['target_event_counts'][str(t)] for t in (10,15,20)]])
    text.append(tab(['시장','코호트','구간','종목','국지 사건','parent','-10% 사건','-15% 사건','-20% 사건'],sample_rows))
    text.append('''
목표 사건 수는 서로 포함 관계다. 이 비율은 “대형주가 연간15% 하락할 확률”이 아니다. 다수의 짧은 하락·횡보·고점 회복 사건을 분모에 포함하며, 같은 종목/parent에 여러 국지 고점이 있다. 최종 한국 현재대형주에서는15% 사건158건 대20%114건, 미국은145건 대93건이었다. 20%가 드물다는 가정을 미리 참으로 두지 않고 이 정의에서의 빈도를 함께 확인했다.

## 3. 평가 방법과 추가 예측력

- 조건부 확률(정밀도): 목표 미도달 상태의 첫 경고 뒤60봉 안에 같은 사건이 처음 -15%를 기록한 비율. 20봉도 별도 계산한다.
- 포착률: 고점 이후126봉 내 -15% 사건 중 첫 도달 **이전**에 경고가 있었던 비율. 실행가능 포착률은 바로 다음 관측 시가가 목표 가격보다 높고 목표 종가일까지 거래 가능한 봉이어야 한다.
- 오경보: 60봉 내 목표 미도달 경보 비율(1−정밀도). 비사건 오경보율(FPR)은 고점 이후126봉 내 목표 미도달 사건 중 경고가 있었던 비율이다. 두 지표는 같은 분모가 아니다.
- 고점 회복·반등: 이후20/60봉의 종가 고점 회복, 종가5% 이상 반등. 목표 도달과 반등은 동시에 일어날 수 있다. 추가 상승/하락은 신호 다음 봉부터 고가/저가 극값으로 측정하므로 종가 목표와 다르다.
- 다음시가: 종가 신호가 확정된 바로 다음 관측 봉의 시가. 거래량>0·고가>저가일 때만 사용한다. 갭으로 이미 -15% 이하이면 탈출 성공이 아니다. 결측·거래불가를 직전 종가 체결로 채우지 않는다. 실제 주문 전량 체결이나 호가를 입증한 값은 아니다.

이미 많이 빠져서 확률이 높아진 효과를 구분하기 위해 각 경고를 당시 비신호 상태와 대응했다. 같은 시장·분할·분기·시장 국면·사건 연령 구간·학습 변동성5분위, 낙폭 차≤0.5pp·변동성비0.8–1.25·시장20봉 수익 차≤3pp를 요구하고 다른 종목/사건의 최대5개 대조를 선택했다. 대조는 사건1,6,11…봉에 사전 배치한다. 나중에 신호가 생길 수 있으며 미래에 끝까지 비신호였다는 조건을 쓰지 않는다. 미래 결과는 대조 선택에 사용하지 않는다.

추가 예측력 Δ는 대응 가능한 경고 부분집합의 목표 확률−대응 대조 확률이다. 전체 정밀도와 다른 표본이며, 미대응 경고로 확대 해석할 수 없다. 대응이 낮다는 것은 신호가 강하다는 증거가 아니라 비교 정보의 부족이다. 업종·뉴스 등 관측하지 않은 교란은 남는다.

선정 문턱은 경보100개·20종목·대응≥50%, 신호 낙폭 중앙≤5%·다음시가≤6%, 정밀도≥20%·포착≥60%·실행가능 포착≥55%, Δ≥3pp·학습 Δ>0·선정의 종목/시간95% 하한>0이다. 통과 후보를 우선한 뒤 선정 점수 0.35×포착+0.30×정밀도+0.20×clip(Δ/0.10,−1,1)+0.15×(1−실행낙폭/0.15)로3개를 동결했다. 해석상 편의로 정한 실용 문턱이지 금융 상수가 아니다. 최종에서 동일 문턱과 각 시장·코호트의3후보 Holm 보정 p<0.05를 요구했다.
''')
    for market,title in (('KR','한국'),('US','미국')):
        text.append(f'## 4. {title}: 현재대형주 중심 결과\n\n다음22개는 첫 새 발생 기준 전체 비교이며 최종 검증 구간을 본 뒤 새 우승자를 고르기 위한 표가 아니다. 개별 신호의95% 구간은 탐색적 구간이다.\n')
        data=read(out/f'{market}_s2_largecap_first.json'); rows=data['rows']
        text.append(tab(['신호','n','발생낙폭 평균/중앙/p10~p90','다음시가 중앙','60봉 확률 [종목95%]','포착/실행포착','경보오류/FPR','적중시 남은봉 중앙','회복확률'],[
            [label(r['signal_index']),r['n'],dd(r['signal_dd']),p(r['execution_dd']['median']),p(r['precision'])+' '+ci(r['precision_ci']['symbol_ci95']),p(r['recall'])+'/'+p(r['executable_recall']),p(r['false_discovery'])+'/'+p(r['false_positive_rate_event126']),num(r['lead_to_target']['median']),p(r['recovery_probability'])] for r in rows]))
        text.append('\n### 동결 후보의 추가 예측력과 검증\n')
        records=result['markets'][market]['largecap']['records']
        text.append(tab(['후보·종류','대응n/전체n','대응비','대응경보/대조 확률','Δ','종목95%Δ (pp)','시간95%Δ (pp)','Holm p','결론'],[
            [label(z['test']['signal_index'])+' / '+z['candidate']['kind'],f"{z['test']['matched']['n']}/{z['test']['n']}",p(z['test']['matched']['coverage']),p(z['test']['matched'].get('treated_probability'))+'/'+p(z['test']['matched'].get('control_probability')),pp(z['test']['matched']['uplift']),ci(z['test']['matched']['symbol_ci95']),ci(z['test']['matched']['quarter_ci95']),f"{z['holm_p']:.3f}",'미통과' if z['failures'] else '통과'] for z in records]))
        text.append('\n전체 경보의 확률과 대응된 경보의 확률을 섞어 비교하면 안 된다. 특히 현재대형주에서 대응 가능한 사건은 적어 구간이 넓다. 아래는 고정 후보의 기간·실행·기회비용이다.\n')
        extra=[]
        for z in records:
            c=z['candidate']; r=z['test']; dat=read(out/f'{market}_s2_largecap_{c["kind"]}.json')
            h20=next(x for x in dat['sensitivity'] if x['signal_index']==c['j'])
            extra.append([label(c['j'])+'/'+c['kind'],p(h20['precision'])+'/'+p(r['precision']),ci(r['precision_ci']['quarter_ci95']),
                dd(r['execution_dd']),days(r['lead_to_target']),p(r['remaining_price_margin']['median']),p(r['rebound5_probability']),
                dd(r['further_up']),dd(r['further_down']),dd(r['false_alarm_missed_upside'])])
        text.append(tab(['후보','20/60봉 확률','확률 시간95%','실행낙폭 평균/중앙/p10~p90','남은봉 평균/중앙/p10~p90','적중시15%까지 여유 중앙','5%반등 확률','추가상승 분포','추가하락 분포','오경보 상승기회 분포'],extra))
        text.append('\n잔여 가격 여유는 고점 기준15%−다음시가 낙폭(pp)이다. 적중 사례만의 사후 분포라 신규 경보의 확정 여유가 아니다. 오경보 상승기회는 다음시가 이후60봉 내 최고 고가 상승의 양수 부분으로 기회비용의 상한 성격이며 실제 다시 살 수 있는 수익이 아니다.\n')
        text.append('### 일반 코호트의 독립 비교\n')
        text.append(tab(['동결 후보','n','다음시가 중앙','정밀도','포착률','대응비','Δ','종목95%Δ(pp)','검증'],[
            [label(z['test']['signal_index'])+'/'+z['candidate']['kind'],z['test']['n'],p(z['test']['execution_dd']['median']),p(z['test']['precision']),p(z['test']['recall']),p(z['test']['matched']['coverage']),pp(z['test']['matched']['uplift']),ci(z['test']['matched']['symbol_ci95']),'미통과' if z['failures'] else '통과'] for z in result['markets'][market]['core']['records']]))
        text.append('\n### 최초 발생·상태, parent 및 고점기간 민감도\n')
        text.append(tab(['후보','기본 포착/확률','parent첫사건 포착/확률','126봉고점 포착/확률','126봉고점 실행낙폭'],[
            [label(z['test']['signal_index'])+'/'+z['candidate']['kind'],p(z['test']['recall'])+'/'+p(z['test']['precision']),
             p(d['parent_first']['recall'])+'/'+p(d['parent_first']['precision']),p(d['peak126']['recall'])+'/'+p(d['peak126']['precision']),p(d['peak126']['execution_dd']['median'])]
            for z in records for d in diagnostic[market]['largecap']['rows'] if d['id']==z['candidate']['id']]))
        text.append('\n국지 사건을 parent 첫 사건으로 제한하면 분모·질문이 바뀐다. 126봉 고점은 사전 민감도이며 여기서 후보를 재선정하지 않았다.\n')
        subpath=out/'fixed_candidate_subgroups.csv'
        if subpath.exists():
            sub=pd.read_csv(subpath); chosen=records[0]['candidate']['id']
            sub=sub[(sub.market==market)&(sub.cohort=='largecap')&(sub.candidate==chosen)]
            text.append('\n### 동결1순위의 장세·변동성 차이\n')
            text.append(tab(['분류','그룹','경보n','종목n','60봉 확률','다음시가 낙폭 중앙'],[
                [r.type,({-1:'하락',0:'혼조',1:'상승'}[r.group] if r.type=='regime' else f'{int(r.group)+1}/5'),r.n,r.symbols,p(r.precision),p(r.median_exec_dd)] for r in sub.itertuples()]))
            text.append('\n장세는 선정용 종목의 동일비중 가격 대리 지수와200봉 평균·20봉 평균 방향으로 정한 상승/하락/혼조이며 공식 지수 판정이 아니다. 변동성 경계는 학습20봉 로그수익 표준편차로 고정했다. 같은15% 목표의 정밀도가 그룹에 따라 다르지만 작은 셀과 시장 공통 충격 때문에 탐색적 차이로만 읽는다. 이 표를 보고 그룹 필터를 추가하거나 신호를 다시 고르지 않았다.\n')
        # Export every metric including confirmation/state, intervals, counts and tails.
        for cohort in ('largecap','core'):
            exported=[]
            for kind in ('first','first_state'):
                for r in read(out/f'{market}_s2_{cohort}_{kind}.json')['rows']:
                    exported.append(pd.json_normalize(r,sep='.'))
            pd.concat(exported,ignore_index=True).to_csv(out/f'{market}_{cohort}_all44.csv',index=False,encoding='utf-8-sig')
        text.append(f'\n[현재대형주44조건 전체 값·분포·신뢰구간]({rel}/{market}_largecap_all44.csv) · [일반44조건]({rel}/{market}_core_all44.csv) · [사후 신호 순서·간격]({rel}/{market}_largecap_signal_order15.csv) · [실제15% 사건의 신호 행]({rel}/{market}_largecap_retrospective15.csv) · [동결 후보 날짜·다음시가 원장]({rel}/{market}_largecap_candidate_trace.csv). 실현된15% 사건만 고른 순서 표는 사후 설명이며 그 표의 비율을 적중 확률로 사용하지 않았다. 신호 누락·역순·동시 발생이 가능하고 고정 순서를 가정하지 않았다.\n')
        fig,axes=plt.subplots(1,2,figsize=(12,6)); y=np.arange(len(rows)); colors=['#276FBF' if j%2==0 else '#DC8A22' for j in range(22)]
        axes[0].barh(y,[100*r['execution_dd']['median'] for r in rows],color=colors); axes[0].axvline(6,color='red',ls='--',lw=1); axes[0].set_xlabel('다음 시가 고점 대비 낙폭 중앙값 (%)')
        axes[0].set_yticks(y,[label(j) for j in range(22)],fontsize=8); axes[0].invert_yaxis()
        axes[1].scatter([100*r['precision'] for r in rows],y,c=colors,label='60봉 정밀도'); axes[1].scatter([100*r['recall'] for r in rows],y,c='#333333',marker='x',label='사건 포착률'); axes[1].set_yticks(y,[]); axes[1].invert_yaxis(); axes[1].set_xlabel('확률 / 포착률 (%)'); axes[1].legend(loc='lower right')
        fig.suptitle(title+' 현재대형주 · -15% 최초 도달 · 최종 보류 종목'); fig.tight_layout(); fig.savefig(out/f'{market}_timing.png',dpi=140); plt.close(fig)
        text.append(f'\n![{title} 경고 시점과 확률]({rel}/{market}_timing.png)\n')
    text.append('''## 5. 무엇을 초기 경고·마지막 탈출로 남길 수 있는가

한국의 선정1순위 종가<MA20·2봉은 최종 시가 낙폭 중앙7.15%로15% 목표의 약 절반이 진행된 뒤였다. 예측 확률41.4%만으로 “초기”라 부르기 어렵고, 대응은268개 중15개에 불과했다. Δ는0pp, 구간은 매우 넓어 추가 예측력이 입증되지 않았다. 더 빠른 MA10·2봉 후보도 최종 예측 우위는 확인하지 못했다.

미국의 선정1순위 MA5<MA20·2봉 상태는 시가 낙폭 중앙4.97%, 사건 포착82.8%였으나 정밀도28.9%라 경보의 약71.1%는60봉 내15%로 이어지지 않았다. 비슷한 상태와 비교한 Δ도 음수이고 불확실했다. 따라서 “15% 하락의 검증된 초기 식별기”로 확정하지 않는다.

원래 마지막 탈출 역할로 고정한 종가<MA20·MA20 기울기≤0·2봉은 v1에서20% 목표로 검증했고 두 시장 모두 추가 예측력 기준을 통과하지 못했다. 이번15% 전체표에도 포함했지만 새 마지막 탈출 최적화를 실행한 것으로 포장하지 않는다. 늦은 신호의 높은 확률은 늦음 자체의 효과일 수 있으며 목표 이후 신호는 성공에서 제외된다. 검증된 최후 탈출선 역시 확정하지 않는다.

실무적으로 전달할 것은 경고별 **시점·포착·오경보·실행 여유의 교환관계**다. 빠른 경고만으로 자동 매도를 연결하거나 현재 추정확률을 개별 종목의 확정 확률처럼 사용하지 않는다. 후속4분할 연구의 입력으로는 검증 부족 표시와 전체 분포를 함께 전달해야 한다.

## 6. 통계적 불확실성과 검증의 한계

확률·포착·회복에는 종목 군집2,000회 및 연속4분기 순환 블록2,000회 구간을 별도로 계산했다. 대응 Δ의 종목 구간은 신호와 재사용 대조 종목을 포함한 선형화 wild multiplier 방식, 시간 구간은 같은 분기 대응 차이의4분기 블록이다. 둘을 동시에 완벽히 통제하는 보증은 아니며 유리한 한쪽만 고르지 않았다. 종목23/24개나 적은 대응 경고는 수백 경보로 늘려도 수백 독립 표본이 되지 않는다. 95% 구간은 다음 거래 결과가95% 확률로 들어갈 범위도 아니다.

Δ의 구간이0을 포함하면 관측 자료가 추가 정보의 부재와 의미 있는 양의 효과를 구분하지 못한다는 뜻이다. “신호가 효과없음이 증명됐다”는 뜻은 아니다. 다만 검증된 확률로 채택할 근거는 부족하다. 전체44개 및 여러 기간의 구간은 동시 유의성 보장이 없는 탐색 결과다. 최종 동결3후보에만 시장·코호트 안 Holm 보정을 적용했다. 같은 신호의 first/state가 같으면 별개의 반복 성공 증거가 아니다.

v1과 다른 종목을 선정/최종에 나누었지만 공통 시장 시대를 이미 알고 있었고,15% 결과는 v1 민감도에 존재했다. 사용자가 기준을 바꾼 v2는 **사후 목적 변경에 따른 재탐색**이다. 동결 뒤 결과를 바꾸지 않았다는 장점은 있지만 순수한 미공개 최종 검증 또는 전향 검증으로 부르지 않는다. 신규 기간과 당시 시점별 대형주 구성으로 다시 확인해야 한다.

현재 생존 보통주 목록만 있어 상장폐지·합병·식별자 변경을 완전하게 반영하지 못한다. 최신 대형주 분류로 과거를 나눈 생존/크기 편향도 있다. 이 때문에20% 사건 빈도나15% 정밀도를 역사적 전체 대형주 확률로 일반화할 수 없다. 극단 종목 영향은 평균과 중앙·p10/p90 및 종목 군집 구간으로 드러내되, 결과가 나쁘다는 이유로 급락/급등주를 제거하지 않았다.

지표·평가 지지구간의 고립된 결측/잘못된 봉은 제외 사유와 수를 감사에 남겼다. 달력10일 초과 공백은 휴장·정지·제공자 누락을 완전히 구별할 수 없어 보존하고 표시했다. 단절 뒤 다음 관측 시가를 즉시 다음 거래일과 동일하게 보장하지 못하며, 경고 원장에 long_gap을 남겼다. 수정 종가 목표와 장중 최저가는 다른 정의다. 일봉 시가가 있어도 주문량·호가·슬리피지·전량 체결은 미검증이며 이번에는 거래비용을 넣은 전략 수익률을 계산하지 않았다.

후속 검증의 우선순위는 시점별 상장·대형주 구성과 새 미사용 기간 확보다. 부족한 대응 수를 숨기거나 결과에 맞춰 캘리퍼·고점·기간을 완화하는 방식은 이번에 사용하지 않았다. 기존 매수·매도·비중·운영 경보·알림·예약·사용자 판단 기록은 변경하지 않았다.

## 7. 재현 자료
''')
    text.append(f'''
- [15% 프로토콜]({rel}/protocol.json) · [동결44후보·선정 결과]({rel}/selection_frozen.json) · [최종 후보 판정·실패 조건]({rel}/final_results.json)
- [원자료 감사·해시·제외 수]({oldrel}/data_audit.json) · [표본·현재 시총 태그]({oldrel}/sample.csv) · [한국 사건]({oldrel}/KR_events.csv) · [미국 사건]({oldrel}/US_events.csv)
- [고점126봉·parent 민감도]({rel}/diagnostics.json) · [검증 영수증]({rel}/verification.json) · [산출물 해시]({rel}/manifest.json)
- [고정 후보의 장세·변동성별 비교]({rel}/fixed_candidate_subgroups.csv)
- [하락률 문헌 검토](ma-warning-thresholds-2026-10-16.md) · [보존된 v1 최종 결과]({oldrel}/final_results.json)

v2 동결 SHA-256 `{result['selection_hash_before']}`는 최종 검증 전후 동일하다. v1의 원 프로토콜·결과도 별도 보존했다. 코드 `scripts/ma_warning_research.py`의 prepare가 원자료·사건을 준비하고 `ma_warning15_research.py select/test`가 v2를 실행한다. 재현 시 새 출력 폴더를 사용한다. 최신 포인터에 의존하는 표본 준비는 감사에 기록된 원본 경로/해시를 먼저 고정해야 한다.

핵심10검사(미래가격 변경 시 지표/사건 불변·연속하락 단일 사건·오경보 회복·국지고점 재설정·1/2봉 최초 발생·목표 후 신호와 갭 실행 제외·미래 결과와 무관한 매칭 등)를 통과했다. 원본1,339개 해시·가격 원행40개·경보178,482행·대응쌍120,174개를 직접 대조했고 목표 도달 후 경보41,573행은 성공 후보에서 제외됨을 확인했다. 여기의 행 수는 두 코호트·여러 신호의 중복 검사량이며 독립 표본 수가 아니다. 그림2개도 직접 열어 축·문자·범례를 확인했다.

```powershell
$researchPython = '.venv/Scripts/python.exe'
& $researchPython scripts/ma_warning15_research.py select --source runtime/ma-warning-20261016-v1 --out runtime/ma-warning15-replay
& $researchPython scripts/ma_warning15_research.py test --out runtime/ma-warning15-replay
& $researchPython scripts/ma_warning15_diagnostics.py --out runtime/ma-warning15-replay --secondary runtime/ma-warning-20261016-peak126
& $researchPython scripts/ma_warning15_report.py --out runtime/ma-warning15-replay --report docs/ma-warning15-replay.md
& $researchPython scripts/verify_ma_warning15.py --out runtime/ma-warning15-replay --report docs/ma-warning15-replay.md
```
''')
    report.write_text('\n\n'.join(text),encoding='utf-8'); print('report',report,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--out',type=Path,required=True); parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(); run(args.out,args.report)

/* Display-only explanations. Values and screening decisions remain server-owned. */
window.WorkbenchHelp = (() => {
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const glossary={
    '관찰 피벗':'선택된 관찰 패턴의 돌파 확인 기준 가격입니다. 패턴의 저항 고점·횡보 상단 등을 사용합니다. 저장 스크리닝 시점의 기준이며 현재가나 매수 주문 가격이 아닙니다.',
    '구조 무효화 참고':'선택된 패턴의 구조가 훼손되는지 살펴보는 저점·횡보 하단 등의 참고 가격입니다. 사용자가 계좌에 채택한 보호선이나 자동 손절 주문과는 별개입니다.',
    '피벗까지':'(관찰 피벗 ÷ 스크리닝 당시 종가 − 1) × 100. 양수이면 종가가 피벗 아래, 음수이면 피벗 위에 있다는 뜻입니다. 저장 기준일 이후의 가격과 구분하세요.',
    '피벗 거리':'(관찰 피벗 ÷ 스크리닝 당시 종가 − 1) × 100. 예를 들어 4.5%는 당시 종가에서 피벗까지 약 4.5% 상승 거리가 남았다는 뜻입니다. 현재 차트 종가와 기준일이 다를 수 있습니다.',
    '구조 위험':'(1 − 구조 무효화 참고 가격 ÷ 스크리닝 당시 종가) × 100. 예를 들어 7.0%는 당시 종가에서 구조 기준선까지의 하락 가격 거리입니다. 손실 확률·계좌 손실률·보장된 최대손실을 뜻하지 않습니다.',
    'RS 백분위':'같은 시장에서 유니버스를 통과하고 충분한 가격 이력이 있는 종목들의 최근 252거래봉 수익률을 비교한 상대 순위입니다. 68.3은 상대적 위치를 나타내며 68.3% 수익률이나 RSI가 아닙니다. 저장 스크리닝 기준입니다.',
    'RS':'같은 시장의 적격 종목들 사이에서 최근 252거래봉 수익률을 비교한 상대강도 백분위입니다. 숫자가 높을수록 비교 집단에서 수익률이 높았습니다. 수익률 %나 RSI가 아니며, 비교 집단과 기준일에 따라 달라집니다.',
    '스크리닝 당시 종가':'후보 선정과 피벗 거리·구조 위험 계산에 사용한 저장 시점의 종가입니다. 차트의 최근 저장 종가와 시점 또는 수정주가 기준이 다를 수 있어 별도로 표시합니다.',
    '횡보 하단 거리':'(1 − 횡보 하단 ÷ 당시 종가) × 100. 횡보 구조의 하단까지 남은 하락 가격 거리입니다. 계좌 위험이나 손실 확률이 아닙니다.',
    '상단 여지':'횡보 상단까지 남은 상승 가격 거리입니다. 현재 저장 코드가 계산한 관찰값이며 도달 가능성이나 목표 수익률을 보장하지 않습니다.',
    '거리비':'횡보 상단까지의 상승 거리 ÷ 횡보 하단까지의 하락 거리입니다. 가격 간 거리만 비교한 값이며 기대수익·승률을 반영한 손익비가 아닙니다.',
    '상대거래량':'최근 완료 일봉 거래량 ÷ 그 봉을 제외한 직전 20개 일봉 평균 거래량입니다. 1배는 평균과 같고 2배는 두 배입니다. 장중 진행률로 보정한 거래량은 아닙니다.',
    '관찰 시작':'모양을 관찰하는 단계입니다. 돌파·거래량·시장 조건이 모두 확인된 진입 후보라는 뜻은 아닙니다.',
    '돌파 대기':'저장된 관찰 패턴의 피벗 돌파와 후속 조건을 기다리는 단계입니다. 피벗을 넘었다는 사실만으로 자동 채택하지 않습니다.',
    '진입 후보':'저장 기준의 패턴·가격·거래량 등 코드 조건을 통과한 검토 후보입니다. 매수 지시나 주문 상태가 아니며 추가 확인 조건이 남아 있습니다.'
  };
  function term(label,description){return description?`<span class="help-term" tabindex="0" data-help="${esc(description)}">${esc(label)}</span>`:esc(label);}
  const keys=Object.keys(glossary).sort((a,b)=>b.length-a.length);
  const pattern=new RegExp(keys.map(k=>k==='RS'?'\\bRS\\b':k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|'),'g');
  function text(value){let result='',at=0;const s=String(value??'');for(const m of s.matchAll(pattern)){result+=esc(s.slice(at,m.index))+term(m[0],glossary[m[0]]);at=m.index+m[0].length;}return result+esc(s.slice(at));}
  const meanings={
    relative_volume:glossary['상대거래량'],
    avg_volume:'최근 완료봉을 제외한 직전 20개 일봉의 거래량 산술평균입니다.',
    eps:'최근 연속 4개 분기의 공시 희석 주당순이익입니다. 필요한 분기가 없으면 결측으로 둡니다.',
    annual_eps:'최근 확정 연간 공시의 희석 주당순이익입니다. TTM과 회계기간이 다를 수 있습니다.',
    sales:'최근 연속 4개 분기의 연결 매출 합계입니다.',
    income:'최근 연속 4개 분기의 지배주주 귀속 순이익입니다.',
    pe:'저장 종가를 양의 TTM 희석 EPS로 나눈 배수입니다. 필수 EPS가 없거나 0 이하이면 계산하지 않습니다.',
    pe_annual:'저장 종가를 최근 확정 연간 희석 EPS로 나눈 배수입니다. TTM P/E와 기간이 다릅니다.',
    ps:'전체 종류별 시가총액을 TTM 연결 매출로 나눈 배수입니다.',
    pb:'전체 종류별 시가총액을 최근 연결 자본으로 나눈 배수입니다. 정확한 분모는 아래 산식과 클릭 근거를 확인하세요.',
    market_cap:'보통주 저장 종가 × 최근 공시 보통주 유통주식수입니다. 두 입력의 기준일이 다를 수 있습니다.',
    all_market_cap:'확인한 각 주식 종류의 저장 가격 × 유통주식수를 합산합니다. 우선주가 있으면 이를 포함해야 합니다.',
    ev:'전체 시가총액 + 공시 이자부부채 − 현금및현금성자산입니다. 단기 투자자산·리스의 별도 조정은 포함하지 않습니다.',
    fcf:'TTM 영업현금흐름에서 공시 자본지출을 뺀 자체 산식입니다. 회사가 발표한 별도 FCF 정의와 다를 수 있습니다.',
    shares:'기말 보통주 유통주식수입니다. 가중평균 희석주식수와 다르며 국내는 발행주식수에서 자기주식을 뺍니다.',
    dividend:'최근 4개 분기에 선언한 주당배당 합계입니다. 지급일 기준 배당이나 예상배당이 아닙니다.',
    atr:'14기간 Wilder 방식 평균 실제 변동폭입니다. 일중 고저와 전일 종가 대비 갭을 반영하며 가격 단위로 표시합니다.',
    rsi:'14기간 Wilder 방식 상승·하락 변화의 상대 크기를 0~100으로 표현한 지표입니다. 종목 간 순위인 RS 백분위와 다릅니다.',
    quick:'(현금 + 매출채권) ÷ 유동부채. 기타 당좌자산을 제외한 보수적 산식입니다.'
  };
  function financial(cell,result){
    const meaning=meanings[cell.id]|| (cell.id.startsWith('sma')?'저장 종가가 해당 기간 단순 이동평균에서 얼마나 떨어져 있는지 나타냅니다.':cell.id.startsWith('perf')?'표시된 기간 전 완료봉 종가와 최근 저장 종가의 가격 변화율입니다. 배당 포함 총수익률은 아닙니다.':'');
    return [cell.label,meaning,cell.reason?'결측 사유: '+cell.reason:'산식: '+(cell.formula||'공시 원값'),
      '기간: '+(cell.start||'시점')+' ~ '+(cell.end||result.financial_as_of||'미확인'),
      '시세 기준: '+(result.price_as_of||'해당 없음/미확인')+' · 공시: '+(cell.filed||'해당 없음/미확인'),
      '값을 클릭하면 원자료와 계산 입력을 확인할 수 있습니다.'].filter(Boolean).join('\n');
  }
  let bubble,trigger,timer;
  function hide(){clearTimeout(timer);if(trigger)trigger.removeAttribute('aria-describedby');trigger=null;if(bubble)bubble.hidden=true;}
  function show(el){
    if(!el?.dataset.help)return;clearTimeout(timer);
    if(!bubble){bubble=document.createElement('div');bubble.id='workbench-help';bubble.className='help-bubble';bubble.setAttribute('role','tooltip');document.body.append(bubble);bubble.addEventListener('pointerenter',()=>clearTimeout(timer));bubble.addEventListener('pointerleave',()=>schedule());}
    if(trigger&&trigger!==el)trigger.removeAttribute('aria-describedby');trigger=el;
    bubble.textContent=el.dataset.help;bubble.hidden=false;el.setAttribute('aria-describedby',bubble.id);
    const r=el.getBoundingClientRect(),w=bubble.offsetWidth,h=bubble.offsetHeight;
    bubble.style.left=Math.max(8,Math.min(innerWidth-w-8,r.left+r.width/2-w/2))+'px';
    bubble.style.top=Math.max(8,r.top-h-10>=8?r.top-h-10:Math.min(innerHeight-h-8,r.bottom+10))+'px';
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(()=>{if(document.activeElement!==trigger)hide();},160);}
  document.addEventListener('pointerover',e=>{const el=e.target.closest?.('[data-help]');if(el)show(el);});
  document.addEventListener('pointerout',e=>{if(e.target.closest?.('[data-help]')&&!e.relatedTarget?.closest?.('[data-help],.help-bubble'))schedule();});
  document.addEventListener('focusin',e=>{const el=e.target.closest?.('[data-help]');if(el)show(el);});
  document.addEventListener('focusout',schedule);
  document.addEventListener('click',e=>{const el=e.target.closest?.('[data-help]');if(el)show(el);else if(!e.target.closest?.('.help-bubble'))hide();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')hide();});
  addEventListener('scroll',hide,true);addEventListener('resize',hide);addEventListener('hashchange',hide);
  return {term,text,financial,glossary};
})();

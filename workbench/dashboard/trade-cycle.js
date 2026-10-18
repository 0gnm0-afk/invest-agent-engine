'use strict';
window.WorkbenchTradeCycle = (() => {
 const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const money = (x,c) => WorkbenchFormat.money(x,c);
 const number = x => x == null ? '미확인' : Number(x).toLocaleString('ko-KR',{maximumFractionDigits:4});
 const paths = ['bullish','neutral','bearish'];
 const pathLabels = {bullish:'강세',neutral:'중립',bearish:'약세'};
 const phaseLabels = {draft:'초안',waiting:'진입 대기',buying:'매수 진행',holding:'보유',selling:'매도 진행',closed:'사이클 종료',stopped:'손절 종료',open:'진행 중'};
 const actionLabels = {buy:'매수',sell:'매도',stop:'손절',mark:'마지막 평가'};
 const permutations = [[20,30,50],[20,50,30],[30,20,50],[30,50,20],[50,20,30],[50,30,20]];
 let generation = 0, timer = null, chart = null;
 const clone = x => JSON.parse(JSON.stringify(x));
 const ratioText = a => (a||[]).map(v => Number(v) <= 1 ? Math.round(Number(v)*100) : Number(v)).join(' / ');
 const value = x => x == null || x === '' ? '' : String(x);
 const numeric = x => { const v=String(x??'').trim().replaceAll(',',''); if(!v)return null; if(!/^\d+(?:\.\d+)?$/.test(v))throw Error('가격과 금액은 0 이상의 숫자로 입력하세요.');return v; };
 const kstDate = x => /^\d{4}-\d{2}-\d{2}$/.test(String(x))?String(x):new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(x));
 const kstStamp = x => x?new Date(x).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})+' KST':'미확인';
 const percent = x => x == null ? '미정' : number(x)+'%';
 const currentPlan = cycle => cycle?.draft?.plan || cycle?.adopted?.plan || null;
 const cycleId = cycle => cycle?.cycle_id || cycle?.id;
 const priceStep = () => ({confirmation_price:null,assumed_fill_price:null,condition:''});
 function incompletePlan(context) {
  return {title:context.name||context.stock_key,currency:context.currency,budget:context.budget==null?null:String(context.budget),account_alias:context.account_alias||null,initial_holding:context.initial_holding||{quantity:0,average_price:'0'},buy_steps:[priceStep(),priceStep(),priceStep()],sell_steps:[priceStep(),priceStep(),priceStep()],stop:priceStep(),scenarios:Object.fromEntries(paths.map(k=>[k,{label:pathLabels[k],events:[]}]))};
 }
 async function rpc(op,args={}) {
  const h=await fetch('/health',{cache:'no-store'});if(!h.ok)throw Error('대시보드 연결을 확인하세요.');
  const {token}=await h.json();
  const r=await fetch('/api/analysis-rpc',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token},body:JSON.stringify({op:'cycle_'+op,arguments:args})});
  const data=await r.json();if(!r.ok)throw Error(data.error||'매매 사이클 요청을 완료하지 못했습니다.');return data;
 }
 const unwrap = data => data.cycle || data;
 function phase(cycle) {return phaseLabels[cycle?.state?.phase||cycle?.status]||cycle?.state?.phase||'초안';}
 function metrics(state,currency) {
  if(!state)return '<p class="cycle-muted">계산에 필요한 가정이 아직 정해지지 않았습니다.</p>';
  return `<div class="cycle-metrics">${[['보유 수량',number(state.quantity)+'주'],['평균 매입가',money(state.average_price,currency)],['현금',money(state.cash,currency)],['실현손익',money(state.realized_pnl,currency)],['평가손익',money(state.unrealized_pnl,currency)],['합산손익',money(state.total_pnl,currency)]].map(([a,b])=>`<div><small>${a}</small><strong>${esc(b)}</strong></div>`).join('')}</div>`;
 }
 function valuationHTML(cycle,currency){
  const last=cycle.fills?.at(-1),valuation=cycle.valuation;
  const basis=valuation?.source==='saved_close'?'저장 완료 종가 · '+value(valuation.date):last?'마지막 입력 체결가 · '+kstStamp(last.at):'초기 입력 평균 매입가';
  return `<p class="cycle-muted">평가손익 기준 ${money(cycle.state?.mark_price,currency)} ${esc(currency)} · ${esc(basis)}</p>`;
 }
 function outcomeCard(outcome,currency,label) {
  return `<article class="cycle-outcome"><h4>${esc(label)}</h4>${outcome?`<p class="cycle-total ${Number(outcome.total_pnl)<0?'loss':'gain'}">${money(outcome.total_pnl,currency)} <small>${esc(currency)}</small></p><p>수익률 ${percent(outcome.return_pct)} · ${esc(phaseLabels[outcome.phase]||outcome.phase||'')}</p>${metrics(outcome,currency)}<p class="cycle-muted">평가 가격 ${money(outcome.mark_price,currency)} · 기말 자산 ${money(outcome.equity,currency)}</p>${ledgerHTML(outcome.ledger,currency)}`:'<p class="cycle-muted">경로 가정 미정</p>'}</article>`;
 }
 function ledgerHTML(ledger,currency){
  if(!ledger?.length)return '';
  const reasons={buy_cancelled_after_selling_started:'매도 시작 후 매수 취소',already_recorded:'실제 체결 반영됨',cycle_closed:'사이클 종료',duplicate_step:'이미 지난 단계'};
  return `<details><summary>경로의 단계별 수량과 현금</summary><div class="cycle-comparison"><table><thead><tr><th>단계</th><th>구분</th><th>체결 수량</th><th>체결가</th><th>잔여 수량</th><th>현금</th><th>평균 매입가</th><th>합산손익</th></tr></thead><tbody>${ledger.map(row=>`<tr><th>${actionLabels[row.event?.kind]||''} ${row.event?.step||''}</th><td>${({recorded:'실제',assumed:'가정',marked:'평가',skipped:'건너뜀'})[row.status]||esc(row.status)}${row.reason?'<br>'+esc(reasons[row.reason]||row.reason):''}</td><td>${number(row.quantity)}</td><td>${money(row.price??row.event?.price,currency)}</td><td>${number(row.state?.quantity)}</td><td>${money(row.state?.cash,currency)}</td><td>${money(row.state?.average_price,currency)}</td><td>${money(row.state?.total_pnl,currency)}</td></tr>`).join('')}</tbody></table></div></details>`;
 }
 function outcomeNumber(combo,path) {const x=combo?.outcomes?.[path]?.total_pnl;return x==null?-Infinity:Number(x);}
 function orderedCombinations(result,sort='balanced') {
  const score=c=>sort==='upside'?outcomeNumber(c,'bullish'):sort==='defensive'?outcomeNumber(c,'bearish'):Math.min(...paths.map(p=>outcomeNumber(c,p)));
  return [...(result?.combinations||[])].sort((a,b)=>score(b)-score(a)||outcomeNumber(b,'neutral')-outcomeNumber(a,'neutral')||String(a.id).localeCompare(String(b.id)));
 }
 function representativeIds(result) {
  return [...new Set(Object.values(result?.representatives||{}).map(x=>typeof x==='object'?x?.id:x).filter(Boolean))];
 }
 function chartMarkers(fills) {
  return (fills||[]).filter(f=>f.at&&Number(f.quantity)>0).map(f=>({time:kstDate(f.at),position:f.kind==='buy'?'belowBar':'aboveBar',color:f.kind==='buy'?'#66cfac':'#ed927f',shape:f.kind==='buy'?'arrowUp':'arrowDown',text:`실제 ${actionLabels[f.kind]||f.kind}${f.step||''} ${f.quantity}주`})).sort((a,b)=>a.time.localeCompare(b.time));
 }
 function mountChart(host,context,cycle,plan) {
  if(chart){WorkbenchCharts.remove(chart);chart=null;}
  const bars=context?.chart?.bars||context?.bars||[];
  const actualMarkers=chartMarkers(cycle?.fills).filter(m=>bars.some(b=>(b.date||b.time)===m.time));
  chart=WorkbenchCharts.create(host,[{type:'candles',label:'저장 가격',currency:plan?.currency||context.currency,data:bars.map(b=>({time:b.date||b.time,open:b.open,high:b.high,low:b.low,close:b.close}))}],{height:290,lastBars:100,onReady(c,refs){
   const ref=refs[0].ref;
   if(actualMarkers.length&&LightweightCharts.createSeriesMarkers)LightweightCharts.createSeriesMarkers(ref,actualMarkers);
   for(const kind of ['buy','sell'])for(const [i,step] of (plan?.[kind+'_steps']||[]).entries()){
    if(cycle?.state?.phase==='closed'||cycle?.state?.[kind+'_completed']?.includes(i+1)||kind==='buy'&&cycle?.state?.cancelled_buy_steps?.includes(i+1))continue;
    const p=Number(step.confirmation_price);if(step.confirmation_price!=null&&p>0)ref.createPriceLine({price:p,color:kind==='buy'?'#6cb8ee':'#d5b877',lineStyle:LightweightCharts.LineStyle.Dashed,lineWidth:1,axisLabelVisible:true,title:`가정 ${actionLabels[kind]} ${i+1}`});
   }
   const stop=plan?.stop?.confirmation_price;if(cycle?.state?.phase!=='closed'&&stop!=null&&Number(stop)>0)ref.createPriceLine({price:Number(stop),color:'#ed927f',lineStyle:LightweightCharts.LineStyle.Dashed,lineWidth:1,axisLabelVisible:true,title:'가정 손절'});
  }});
 }
 function field(label,path,content,type='price',extra='') {
  const shown=type==='price'&&content!=null?value(content):value(content);
  return `<label>${esc(label)}<input data-plan="${esc(path)}" ${type==='integer'?'type="number" min="0" step="1"':'type="text"'} ${type==='price'?'inputmode="decimal"':''} value="${esc(shown)}" ${extra}></label>`;
 }
 function stepEditor(plan,kind) {
  return `<div class="cycle-step-group"><h3>${actionLabels[kind]} 단계 <small>최대 3단계</small></h3>${(plan[kind+'_steps']||[]).map((s,i)=>`<div class="cycle-step"><strong>${i+1}차</strong>${field('확인 가격',`${kind}_steps.${i}.confirmation_price`,s.confirmation_price)}${field('가정 체결가',`${kind}_steps.${i}.assumed_fill_price`,s.assumed_fill_price)}<label class="cycle-condition">확인할 조건<input data-plan="${kind}_steps.${i}.condition" value="${esc(s.condition)}" placeholder="예: 완료 일봉 종가가 기준 이상"></label>${triggerEditor(`${kind}_steps.${i}`,s.trigger)}</div>`).join('')}<div class="cycle-inline"><button type="button" data-step="${kind}:add" ${(plan[kind+'_steps']||[]).length>=3?'disabled':''}>단계 추가</button><button type="button" data-step="${kind}:remove" ${(plan[kind+'_steps']||[]).length<=(kind==='buy'?0:1)?'disabled':''}>마지막 단계 제거</button></div></div>`;
 }
 function triggerEditor(path,trigger){
  const kinds={manual:'직접 판단',price:'확인 가격',trailing:'트레일링',ma5:'5일 이동평균',ma10:'10일 이동평균'},comparators={gte:'이상',gt:'초과',lte:'이하',lt:'미만',cross_above:'상향 돌파',cross_below:'하향 이탈'},kind=trigger?.kind||'manual';
  return `<div class="cycle-trigger"><label>완료 종가 확인 기준<select data-trigger="${path}:kind">${Object.entries(kinds).map(([k,v])=>`<option value="${k}" ${kind===k?'selected':''}>${v}</option>`).join('')}</select></label>${kind!=='manual'?`<label>비교 조건<select data-trigger="${path}:comparator">${Object.entries(comparators).map(([k,v])=>`<option value="${k}" ${(trigger?.comparator||trigger?.operator)===k?'selected':''}>${v}</option>`).join('')}</select></label>`:'<small>정성 조건은 숫자로 자동 판정하지 않습니다.</small>'}</div>`;
 }
 function pathEditor(plan,path) {
  const scenario=plan.scenarios?.[path]||{events:[]};
  const options=(event)=>['buy','sell','stop','mark'].map(k=>`<option value="${k}" ${event.kind===k?'selected':''}>${actionLabels[k]}</option>`).join('');
  return `<section class="cycle-path"><h3>${pathLabels[path]} 경로</h3><label>경로 설명<input data-plan="scenarios.${path}.label" value="${esc(scenario.label||pathLabels[path])}"></label><label>이 경로의 가정<input data-plan="scenarios.${path}.assumption" value="${esc(scenario.assumption||'')}"></label><p class="cycle-muted">위에서 아래 순서로 실행하는 가정입니다. 가격을 비우면 해당 단계의 가정 체결가를 사용합니다.</p><div>${(scenario.events||[]).map((e,i)=>`<div class="cycle-event"><span>${i+1}</span><label>행동<select data-event="${path}:${i}:kind">${options(e)}</select></label>${['buy','sell'].includes(e.kind)?`<label>단계<select data-event="${path}:${i}:step">${(plan[e.kind+'_steps']||[]).map((_,s)=>`<option value="${s+1}" ${Number(e.step)===s+1?'selected':''}>${s+1}차</option>`).join('')}</select></label>`:'<span></span>'}<label>${e.kind==='mark'?'평가 가격':'경로 체결가'}<input inputmode="decimal" data-event="${path}:${i}:price" value="${esc(value(e.price))}" placeholder="${e.kind==='mark'?'필수':'단계값 사용'}"></label><button type="button" data-remove-event="${path}:${i}" aria-label="${pathLabels[path]} ${i+1}번째 가정 삭제">×</button></div>`).join('')||'<p class="cycle-muted">아직 경로가 없습니다. Agent 초안을 생성하거나 가정을 추가하세요.</p>'}</div><button type="button" data-add-event="${path}">가정 추가</button></section>`;
 }
 function editorHTML(plan) {
  return `<div class="cycle-editor">${plan.rationale||plan.unresolved?.length?`<details class="cycle-rationale"><summary>Agent 가정의 근거와 미정 사항</summary><p>${esc(plan.rationale||'')}</p>${(plan.unresolved||[]).map(s=>`<p>· ${esc(s)}</p>`).join('')}</details>`:''}<div class="cycle-section-heading"><div><h2>분할 시나리오 계산기</h2><p>가격 조건과 체결 가정을 구분하고, 6개 매수 순열 × 6개 매도 순열을 세 경로로 비교합니다. 입력을 바꾸면 결과가 자동 갱신됩니다.</p></div></div><div class="cycle-assumptions">${field('투자 예산 ('+plan.currency+')','budget',plan.budget)}${field('시작 보유 수량 (실제 확인)','initial_holding.quantity',plan.initial_holding?.quantity,'integer')}${field('시작 평균 매입가 ('+plan.currency+')','initial_holding.average_price',plan.initial_holding?.average_price)}</div><p class="cycle-muted">예산은 시작 현금입니다. 매수는 예산 기준, 매도는 첫 매도 시점 수량 기준으로 나누며 정수 주식과 잔여 현금을 계산합니다.</p><div class="cycle-step-columns">${stepEditor(plan,'buy')}${stepEditor(plan,'sell')}</div><div class="cycle-stop"><h3>무효화 · 손절</h3>${field('확인 가격','stop.confirmation_price',plan.stop?.confirmation_price)}${field('가정 체결가','stop.assumed_fill_price',plan.stop?.assumed_fill_price)}<label class="cycle-condition">조건<input data-plan="stop.condition" value="${esc(plan.stop?.condition)}" placeholder="판단이 무효화되는 조건"></label>${triggerEditor('stop',plan.stop?.trigger)}</div><div class="cycle-path-grid">${paths.map(p=>pathEditor(plan,p)).join('')}</div><div class="cycle-actions"><button type="button" class="primary" data-action="calculate">이 초안 저장</button><span data-dirty class="cycle-muted"></span></div></div>`;
 }
 function progressHTML(cycle,currency) {
  const adopted=cycle.adopted;if(!adopted)return '';
  const state=cycle.state,plan=adopted.plan||cycle.original_plan,selected=cycle.adopted.combination_id;
  return `<section class="panel cycle-progress"><div class="panel-body"><div class="cycle-section-heading"><div><div class="cycle-eyebrow">ACTUAL · 사용자 입력 체결</div><h2>현재 진행 · ${esc(phase(cycle))}</h2></div><span class="cycle-badge">확정 v${esc(adopted.version)} · ${esc(selected)}</span></div>${metrics(state,currency)}${valuationHTML(cycle,currency)}<p class="cycle-muted">${cycle.fills?.length?'실제 입력된 거래 '+cycle.fills.length+'건을 반영했습니다.':'아직 입력된 실제 거래가 없습니다.'} 계획·계좌 잔고만으로 체결을 만들지 않습니다.</p><div class="cycle-stage-strip">${['buy','sell'].flatMap(k=>(plan?.[k+'_steps']||[]).map((s,i)=>{const fs=(cycle.fills||[]).filter(f=>f.kind===k&&f.step===i+1);return `<div class="${fs.length?'has-fill':''}"><strong>${actionLabels[k]} ${i+1}</strong><span>${money(s.confirmation_price,currency)}</span><small>${fs.length?'실제 '+number(fs.reduce((t,f)=>t+Number(f.quantity),0))+'주':k==='buy'&&state?.cancelled_buy_steps?.includes(i+1)?'매도 전환으로 취소':state?.phase==='closed'?'사이클 종료':'조건 대기'}</small></div>`;})).join('')}</div>${(cycle.next_steps||[]).length?`<div class="cycle-next"><h3>다음 단계</h3>${cycle.next_steps.map(s=>`<p><strong>${actionLabels[s.kind]} ${s.step?s.step+'차':'잔여 전량'}</strong> · ${s.ratio==null?'전량':s.ratio+'%'} · 가정 ${number(s.planned_quantity)}주 × ${money(s.assumed_fill_price,currency)} ${esc(currency)}<br><span class="cycle-muted">${esc(s.condition)} · ${s.quantity_basis==='initial_budget'?'시작 예산 기준':s.quantity_basis==='first_sell_quantity'?'첫 매도 시점 수량 기준':'남은 수량 기준'}</span></p>`).join('')}</div>`:''}<details><summary>원래 확정한 계획과 현재 전망 비교</summary><p class="cycle-muted">원래 계획은 확정 당시 가정, 현재 전망은 실제 거래를 반영한 남은 경로입니다. 수익률은 확률 예측이 아닙니다.</p><div class="cycle-comparison"><table><thead><tr><th>경로</th><th>원래 합산손익</th><th>현재 전망 합산손익</th><th>현재 전망 잔여 수량</th><th>현재 전망 현금</th></tr></thead><tbody>${paths.map(k=>{const o=cycle.original_outcomes?.[k],c=cycle.current_outcomes?.[k];return `<tr><th>${pathLabels[k]}</th><td>${money(o?.total_pnl,currency)}</td><td>${money(c?.total_pnl,currency)}</td><td>${number(c?.quantity)}</td><td>${money(c?.cash,currency)}</td></tr>`;}).join('')}</tbody></table></div></details><details class="cycle-fill-panel"><summary>실제 거래 입력 · 매수 / 매도 / 손절</summary><form data-fill-form><div class="cycle-fill-grid"><label>거래<select name="kind"><option value="buy">매수</option><option value="sell">매도</option><option value="stop">손절</option></select></label><label>계획 단계<select name="step">${(plan?.buy_steps||[]).map((_,i)=>`<option value="${i+1}">${i+1}차</option>`).join('')}</select></label><label>체결 수량<input name="quantity" type="number" min="1" step="1" required></label><label>실제 체결가 (${esc(currency)})<input name="price" inputmode="decimal" required></label><label>체결 시각 (KST)<input name="at" type="datetime-local" required></label><label>메모<input name="note" maxlength="2000"></label></div><p class="cycle-muted">거래소·계좌에서 확인한 실제 거래만 입력하세요. 이 버튼은 주문을 전송하지 않습니다.</p><button class="primary" type="submit">실제 거래 기록</button></form></details><div class="cycle-fill-history">${(cycle.fills||[]).map(f=>`<p><span class="cycle-badge actual">실제</span> ${esc(kstStamp(f.at))} · ${actionLabels[f.kind]||esc(f.kind)} ${f.step?f.step+'차':''} · ${number(f.quantity)}주 × ${money(f.price,currency)} ${esc(currency)}${f.note?' · '+esc(f.note):''}</p>`).join('')}</div>${['closed','stopped'].includes(state?.phase)?'<button type="button" data-action="reentry">같은 종목으로 새 사이클 시작</button>':''}</div></section>`;
 }
 function comparisonHTML(result,selectedId,currency,sort,showAll) {
  if(!result)return '<p class="cycle-muted">초안을 저장하고 계산하면 대표 조합과 전체 36조합이 표시됩니다.</p>';
  const selected=(result.combinations||[]).find(c=>c.id===selectedId),reps=representativeIds(result),ordered=orderedCombinations(result,sort);
  const shown=showAll?ordered:ordered.filter(c=>reps.includes(c.id)||c.id===selectedId).slice(0,5);
  return `${result.needs_input?.length?`<div class="cycle-warning">미정 입력 ${result.needs_input.length}개 · ${result.needs_input.slice(0,10).map(esc).join(' / ')}</div>`:''}<div class="cycle-section-heading"><div><h2>36조합 · 3개 경로</h2><p>${esc(result.evaluated_result_count??(result.needs_input?.length?0:108))} / ${esc(result.result_count??108)}개 경로 계산 완료 · 전체 경로를 함께 비교하세요.</p></div><label>정렬<select data-sort><option value="balanced" ${sort==='balanced'?'selected':''}>최저 경로 손익 우선</option><option value="upside" ${sort==='upside'?'selected':''}>강세 손익 우선</option><option value="defensive" ${sort==='defensive'?'selected':''}>약세 손익 우선</option></select></label></div><div class="cycle-permutations">${['buy','sell'].map(k=>`<fieldset><legend>${actionLabels[k]} 비중 순서 (%)</legend>${permutations.map(r=>`<button type="button" data-ratio="${k}:${r.join(',')}" class="${ratioText(selected?.[k+'_ratios'])===ratioText(r)?'selected':''}">${ratioText(r)}</button>`).join('')}</fieldset>`).join('')}</div><div class="cycle-comparison"><table><thead><tr><th>선택</th><th>매수 (%)</th><th>매도 (%)</th><th>강세 손익</th><th>중립 손익</th><th>약세 손익</th><th>비교</th></tr></thead><tbody>${shown.map(c=>`<tr class="${c.id===selectedId?'selected':''}"><td><input type="radio" name="cycle-combination" value="${esc(c.id)}" ${c.id===selectedId?'checked':''} aria-label="매수 ${ratioText(c.buy_ratios)}, 매도 ${ratioText(c.sell_ratios)} 선택"></td><td>${ratioText(c.buy_ratios)}</td><td>${ratioText(c.sell_ratios)}</td>${paths.map(p=>`<td class="${Number(c.outcomes?.[p]?.total_pnl)<0?'loss':'gain'}">${money(c.outcomes?.[p]?.total_pnl,currency)}</td>`).join('')}<td>${c.pareto?'<span class="cycle-badge">비교 후보</span>':''}${c.duplicate_of?'<small>동일 계산 결과</small>':''}</td></tr>`).join('')}</tbody></table></div><button type="button" data-action="all">${showAll?'대표 조합만 보기':'전체 36조합 펼치기'}</button><p class="cycle-muted">비교 후보는 다른 조합이 세 경로 모두에서 앞서지 않는 조합입니다. 단일 최적 비중이나 매수·매도 지시를 뜻하지 않습니다.</p>${(result.assumptions||[]).map(s=>`<p class="cycle-muted">${esc(s)}</p>`).join('')}${selected?`<h3>선택 조합 · 매수 ${ratioText(selected.buy_ratios)}% / 매도 ${ratioText(selected.sell_ratios)}%</h3><div class="cycle-outcomes">${paths.map(p=>outcomeCard(selected.outcomes?.[p],currency,pathLabels[p])).join('')}</div>`:''}`;
 }
 async function mount(host,{page,args=[]}={}) {
  clearTimeout(timer);const version=++generation,hash=location.hash;
  const valid=()=>version===generation&&location.hash===hash&&host.isConnected;
  host.classList.add('cycle-page');
  const id=new URLSearchParams(hash.split('?')[1]||'').get('id')||args[0];
  if(page==='cycles'||!id)return mountList(host,valid);
  host.innerHTML='<div class="loading">매매 사이클을 불러오는 중…</div>';
  let response=await rpc('read',{cycle_id:id});if(!valid())return;
  let cycle=unwrap(response),job=response.job||cycle.job,context=await rpc('context',{stock_key:cycle.stock_key,account_alias:cycle.draft?.plan?.account_alias||cycle.adopted?.plan?.account_alias});if(!valid())return;
  let plan=clone(currentPlan(cycle)||incompletePlan(context)),result=null,selectedId=cycle.adopted?.combination_id||null,dirty=false,sort='balanced',showAll=false,busy=false,previewTimer,previewSequence=0;
  const status=(message,error=false)=>{const n=host.querySelector('[data-status]');if(n){n.textContent=message;n.classList.toggle('cycle-warning',error);}};
  function markDirty(){dirty=true;const label=host.querySelector('[data-dirty]');if(label)label.textContent='수정됨 · 자동 계산 후 초안을 저장해야 확정할 수 있습니다.';const approve=host.querySelector('[data-action="approve"]');if(approve)approve.disabled=true;schedulePreview();}
  function schedulePreview(){clearTimeout(previewTimer);const seq=++previewSequence;previewTimer=setTimeout(async()=>{if(!valid()||busy)return;try{const preview=normalizePlan(plan),computed=await rpc('calculate',{plan:preview,cycle_id:id});if(!valid()||seq!==previewSequence||busy)return;result=computed;selectedId=result.combinations?.some(c=>c.id===selectedId)?selectedId:result.representatives?.balanced||representativeIds(result)[0]||result.combinations?.[0]?.id;renderResults();renderApproval();const n=host.querySelector('[data-dirty]');if(n)n.textContent='입력값으로 자동 계산했습니다 · 초안 저장 후 확정 가능';}catch(e){if(valid()&&seq===previewSequence){const n=host.querySelector('[data-dirty]');if(n)n.textContent='입력을 마치면 자동 계산합니다 · '+e.message;}}},500);}
  async function action(fn){if(busy)return;busy=true;clearTimeout(previewTimer);previewSequence++;host.querySelectorAll('button,input,select,textarea').forEach(b=>b.disabled=true);try{await fn();}catch(e){status(e.message,true);}finally{busy=false;if(valid())render();}}
  function renderResults(){
   const box=host.querySelector('[data-results]');box.innerHTML=comparisonHTML(result,selectedId,plan.currency,sort,showAll);
   box.querySelector('[data-sort]')?.addEventListener('change',e=>{sort=e.target.value;renderResults();});
   box.querySelector('[data-action="all"]')?.addEventListener('click',()=>{showAll=!showAll;renderResults();});
   box.querySelectorAll('[name="cycle-combination"]').forEach(n=>n.onchange=()=>{selectedId=n.value;renderResults();renderApproval();});
   box.querySelectorAll('[data-ratio]').forEach(b=>b.onclick=()=>{const [kind,raw]=b.dataset.ratio.split(':'),other=kind==='buy'?'sell':'buy',selected=result.combinations.find(c=>c.id===selectedId)||result.combinations[0];selectedId=result.combinations.find(c=>ratioText(c[kind+'_ratios'])===ratioText(raw.split(',').map(Number))&&ratioText(c[other+'_ratios'])===ratioText(selected[other+'_ratios']))?.id||selectedId;renderResults();renderApproval();});
  }
  function renderApproval(){
   const el=host.querySelector('[data-approval]');if(!el)return;
   const selected=result?.combinations?.find(c=>c.id===selectedId),complete=selected&&paths.every(p=>selected.outcomes?.[p])&&!result.needs_input?.length&&selected.adoptable!==false;
   el.innerHTML=`<div class="cycle-approval"><h3>${cycle.adopted?'남은 단계를 이 초안과 조합으로 변경':'이 초안과 조합으로 진행'}</h3>${cycle.adopted?`<p>현재 확정 v${esc(cycle.adopted.version)} · ${esc(cycle.adopted.combination_id)}. 최초 계획과 실제 거래를 보존하고, 미실행 단계만 변경합니다.</p>`:''}<p>초안 v${esc(cycle.draft?.version??cycle.draft_version??'—')} · 매수 ${ratioText(selected?.buy_ratios)||'미선택'}% / 매도 ${ratioText(selected?.sell_ratios)||'미선택'}% · 예산 ${money(plan.budget,plan.currency)} ${esc(plan.currency)}</p>${selected?.adoptable===false?`<p class="cycle-warning">${result.needs_input?.length?'미정인 가격·조건·경로 입력을 보완하면 이 조합을 확정할 수 있습니다.':'이 조합은 이미 완료한 단계와 맞지 않아 확정할 수 없습니다.'} ${(selected.adoption_blockers||[]).map(esc).join(' / ')}</p>`:''}<label><input type="checkbox" data-confirm> 위 가격·경로·수량 가정과 선택 조합을 확인했습니다.</label><button type="button" class="primary" data-action="approve" disabled>이 버전 사용자 확정</button><p class="cycle-muted">확정 후에도 실제 거래는 직접 입력합니다.</p></div>`;
   const check=el.querySelector('[data-confirm]'),button=el.querySelector('[data-action="approve"]');
   check.onchange=()=>button.disabled=!check.checked||!complete||dirty||busy;
   button.onclick=()=>action(async()=>{if(dirty||!check.checked||!complete)throw Error('저장된 계산과 확인 항목을 먼저 확인하세요.');response=await rpc('approve',{cycle_id:id,draft_version:cycle.draft?.version??cycle.draft_version,combination_id:selectedId,expected_revision:cycle.revision});cycle=unwrap(response);status('선택한 초안을 확정했습니다. 실제 거래를 입력하면 진행 상황에 반영됩니다.');});
  }
  function bindEditor(){
   host.querySelectorAll('[data-plan]').forEach(n=>n.oninput=()=>{const keys=n.dataset.plan.split('.'),last=keys.pop();let obj=plan;keys.forEach(k=>{if(obj[k]==null)obj[k]={};obj=obj[k];});obj[last]=n.value;markDirty();});
   host.querySelectorAll('[data-trigger]').forEach(n=>n.onchange=()=>{const [path,key]=n.dataset.trigger.split(':');let step=plan;path.split('.').forEach(k=>{if(step[k]==null)step[k]={confirmation_price:null,assumed_fill_price:null,condition:''};step=step[k];});if(key==='kind')step.trigger=n.value==='manual'?{kind:'manual',evaluation:'daily_close'}:{kind:n.value,evaluation:'daily_close',comparator:'gte'};else step.trigger={kind:step.trigger.kind,evaluation:'daily_close',comparator:n.value};markDirty();render();});
   host.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>{const[k,a]=b.dataset.step.split(':'),steps=plan[k+'_steps'];if(a==='add'&&steps.length<3)steps.push(priceStep());else if(a==='remove'&&steps.length>(k==='buy'?0:1))steps.pop();markDirty();render();});
   host.querySelectorAll('[data-event]').forEach(n=>n.onchange=()=>{const[p,i,k]=n.dataset.event.split(':'),event=plan.scenarios[p].events[Number(i)];if(k==='price'){if(n.value.trim())event.price=n.value;else delete event.price;}else if(k==='kind'){event.kind=n.value;if(['buy','sell'].includes(n.value))event.step=1;else delete event.step;}else event[k]=Number(n.value);markDirty();if(k==='kind')render();});
   host.querySelectorAll('[data-event][inputmode]').forEach(n=>n.oninput=()=>n.onchange());
   host.querySelectorAll('[data-add-event]').forEach(b=>b.onclick=()=>{const p=b.dataset.addEvent;plan.scenarios[p]??={label:pathLabels[p],events:[]};plan.scenarios[p].events.push({kind:'mark',price:null});markDirty();render();});
   host.querySelectorAll('[data-remove-event]').forEach(b=>b.onclick=()=>{const[p,i]=b.dataset.removeEvent.split(':');plan.scenarios[p].events.splice(Number(i),1);markDirty();render();});
   host.querySelector('[data-action="calculate"]').onclick=()=>action(async()=>{const normalized=normalizePlan(plan);response=await rpc('save_draft',{cycle_id:id,expected_revision:cycle.revision,plan:normalized});cycle=unwrap(response);plan=clone(currentPlan(cycle));dirty=false;result=await rpc('calculate',{plan,cycle_id:id});selectedId=result.combinations?.some(c=>c.id===selectedId)?selectedId:result.representatives?.balanced||representativeIds(result)[0]||result.combinations?.[0]?.id;status(result.needs_input?.length?'초안을 저장했습니다. 미정 입력을 채우면 비교와 확정을 마칠 수 있습니다.':'초안을 저장하고 36조합 × 3경로를 계산했습니다.');});
  }
  function render(){
   if(!valid())return;const previousStatus=host.querySelector('[data-status]')?.textContent||'',previousError=host.querySelector('[data-status]')?.classList.contains('cycle-warning');
   const draftVersion=cycle.draft?.version??cycle.draft_version;
   host.innerHTML=`<a class="back" href="#cycles">← 매매 사이클 목록</a><header class="cycle-heading"><div><div class="cycle-eyebrow">TRADE CYCLE</div><h1>${esc(context.name||cycle.stock_key)} <small>${esc(cycle.stock_key)}</small></h1><p>${esc(plan.account_alias||'계좌 미지정')} · ${esc(phase(cycle))} · 초안 v${esc(draftVersion||'—')}</p></div><a class="cycle-stock-link" href="#stock/${encodeURIComponent(cycle.stock_key.split(':')[0])}/${encodeURIComponent(cycle.stock_key.split(':').slice(1).join(':'))}">종목 분석과 차트 →</a></header><p data-status role="status" aria-live="polite" class="${previousError?'cycle-warning':''}">${esc(previousStatus)}</p><section class="panel"><div class="panel-body"><div class="cycle-section-heading"><h2>가격과 사이클</h2><span class="cycle-muted">가격 기준 ${esc(context.chart?.bars?.at(-1)?.date||context.as_of||'미확인')}</span></div><div data-cycle-chart class="cycle-chart" aria-label="저장 가격, 실제 체결, 앞으로의 가정 가격"></div><div class="cycle-chart-legend"><span>● 실제 체결 화살표</span><span>┄ 매수 / 매도 / 손절 가정선</span><span>휴장일 입력은 거래 기록에서 확인</span></div></div></section>${progressHTML(cycle,plan.currency)}<section class="panel"><div class="panel-body"><div data-job></div>${editorHTML(plan)}<div data-results class="cycle-results"></div><div data-approval></div></div></section>`;
   mountChart(host.querySelector('[data-cycle-chart]'),context,cycle,cycle.adopted?.plan||plan);
   const jobHost=host.querySelector('[data-job]');
   if(job&&['queued','running'].includes(job.state||job.status))jobHost.innerHTML='<div class="cycle-job" role="status">Agent가 저장된 분석과 가격으로 초안을 작성하고 있습니다. 결과가 저장되면 여기에 표시됩니다. <small>아직 사용자 확정 전입니다.</small></div>';
   else if(job&&(job.state||job.status)==='failed')jobHost.innerHTML=`<div class="cycle-warning">Agent 초안 생성이 완료되지 않았습니다. ${esc(job.error||job.message||'잠시 후 다시 시도하세요.')} ${response.links?.codex_url?`<a href="${esc(response.links.codex_url)}">Codex에서 이어서 작성</a>`:''}</div>`;
   jobHost.innerHTML+='<button type="button" data-action="agent">Agent 초안 다시 생성</button>';
   jobHost.querySelector('[data-action="agent"]').onclick=()=>action(async()=>{if(dirty){response=await rpc('save_draft',{cycle_id:id,expected_revision:cycle.revision,plan:normalizePlan(plan)});cycle=unwrap(response);dirty=false;}response=await rpc('request_draft',{cycle_id:id,expected_revision:cycle.revision,stock_key:cycle.stock_key,account_alias:plan.account_alias,budget:numeric(plan.budget)});cycle=unwrap(response);job=response.job||cycle.job;status('Agent 초안 생성을 요청했습니다. 생성 상태와 저장 결과를 확인 중입니다.');schedulePoll();});
   bindEditor();renderResults();renderApproval();if(dirty)host.querySelector('[data-dirty]').textContent='수정됨 · 자동 계산 후 초안을 저장해야 확정할 수 있습니다.';
   const form=host.querySelector('[data-fill-form]');if(form){
    const kind=form.elements.kind,step=form.elements.step;form.hidden=cycle.state?.phase==='closed';
    for(const option of kind.options)option.disabled=!(cycle.next_steps||[]).some(s=>s.kind===option.value);
    kind.value=cycle.next_steps?.[0]?.kind||'buy';
    kind.onchange=()=>{const next=cycle.next_steps?.find(s=>s.kind===kind.value);step.disabled=kind.value==='stop';step.innerHTML=next?.step?`<option value="${next.step}">${next.step}차</option>`:'';form.elements.quantity.placeholder=next?.planned_quantity!=null?'계획 '+number(next.planned_quantity)+'주':'';form.elements.price.placeholder=next?.assumed_fill_price!=null?'가정 '+money(next.assumed_fill_price,plan.currency):'';};kind.onchange();
    form.onsubmit=e=>{e.preventDefault();const f=new FormData(form);action(async()=>{const fill={kind:f.get('kind'),quantity:Number(f.get('quantity')),price:numeric(f.get('price')),at:new Date(String(f.get('at'))+'+09:00').toISOString(),note:f.get('note')||''};if(fill.kind!=='stop')fill.step=Number(f.get('step'));response=await rpc('fill',{cycle_id:id,expected_revision:cycle.revision,fill});cycle=unwrap(response);result=await rpc('calculate',{plan,cycle_id:id});status('실제 거래를 기록했습니다. 잔여 수량과 현재 전망을 갱신했습니다.');});};
   }
   host.querySelector('[data-action="reentry"]')?.addEventListener('click',()=>{location.hash='#cycles?stock='+encodeURIComponent(cycle.stock_key)+'&reentry_from='+encodeURIComponent(id);});
  }
  function schedulePoll(){clearTimeout(timer);if(!valid())return;timer=setTimeout(async()=>{if(!valid())return;try{const next=await rpc('read',{cycle_id:id});if(!valid())return;const nc=unwrap(next),nj=next.job||nc.job;if(!dirty&&!busy&&!host.contains(document.activeElement?.closest?.('input,select,textarea'))&&(nc.revision!==cycle.revision||JSON.stringify(nj)!==JSON.stringify(job))){response=next;cycle=nc;job=nj;plan=clone(currentPlan(cycle)||plan);result=await rpc('calculate',{plan,cycle_id:id});selectedId=selectedId||result.representatives?.balanced||representativeIds(result)[0]||result.combinations?.[0]?.id;render();}else if(dirty&&nc.revision!==cycle.revision)status('새 Agent 초안이 저장됐습니다. 현재 수정 내용을 먼저 저장하거나 목록에서 다시 열어주세요.');}catch(e){status('초안 상태 확인 실패: '+e.message,true);}schedulePoll();},2500);}
  result=await rpc('calculate',{plan,cycle_id:id});if(!valid())return;selectedId=selectedId||result.representatives?.balanced||representativeIds(result)[0]||result.combinations?.[0]?.id;render();schedulePoll();
 }
 function normalizePlan(plan) {
  const next=clone(plan);next.budget=numeric(next.budget);next.initial_holding||={quantity:0,average_price:'0'};next.initial_holding.quantity=next.initial_holding.quantity==null||String(next.initial_holding.quantity).trim()===''?null:Number(next.initial_holding.quantity);next.initial_holding.average_price=numeric(next.initial_holding.average_price);
  if(next.initial_holding.quantity!=null&&(!Number.isInteger(next.initial_holding.quantity)||next.initial_holding.quantity<0))throw Error('시작 보유 수량은 0 이상의 정수로 입력하세요.');
  for(const step of [...(next.buy_steps||[]),...(next.sell_steps||[]),next.stop].filter(Boolean)){step.confirmation_price=numeric(step.confirmation_price);step.assumed_fill_price=numeric(step.assumed_fill_price);}
  for(const path of paths)for(const event of next.scenarios?.[path]?.events||[]){if(event.price!=null&&String(event.price).trim())event.price=numeric(event.price);else delete event.price;}
  return next;
 }
 async function mountList(host,valid) {
  const query=new URLSearchParams(location.hash.split('?')[1]||''),preselected=query.get('stock'),reentry=query.get('reentry_from');
  let context=null,busy=false,searchSequence=0,searchTimer=null;
  host.innerHTML='<header class="cycle-heading"><div><div class="cycle-eyebrow">TRADE CYCLES</div><h1>매매 사이클</h1><p>가정을 비교하고, 실제 거래를 기록하며 다음 판단을 준비합니다.</p></div></header><section class="panel"><div class="panel-body"><h2>새 사이클</h2><div class="cycle-start"><label>종목 선택<input data-stock-search type="search" placeholder="종목명 또는 티커" autocomplete="off"></label><div data-stock-results class="cycle-search-results"></div><div data-context></div></div><p data-status role="status" aria-live="polite">저장된 종목을 선택하세요.</p></div></section><section><h2>저장된 사이클</h2><div data-cycles class="cycle-cards">불러오는 중…</div></section>';
  const status=t=>{if(valid())host.querySelector('[data-status]').textContent=t;};
  async function select(stockKey){context=await rpc('context',{stock_key:stockKey});if(!valid())return;host.querySelector('[data-stock-search]').value=(context.name||stockKey)+' · '+stockKey;host.querySelector('[data-stock-results]').replaceChildren();renderContext();status('저장 현금을 기본값으로 가져왔습니다. 사용할 계좌와 투자 예산을 확인하세요.');}
  function renderContext(){
   const box=host.querySelector('[data-context]');box.innerHTML=`<div class="cycle-start-fields"><label>계좌<select data-account><option value="">기본 계좌</option>${(context.accounts||[]).map(a=>`<option value="${esc(a.alias)}" ${a.alias===context.account_alias?'selected':''}>${esc(a.alias)} · 저장 현금 ${money(typeof a.cash==='object'?a.cash?.amount:a.cash,context.currency)} ${esc(context.currency)}</option>`).join('')}</select></label><label>투자 예산 (${esc(context.currency)})<input data-budget inputmode="decimal" value="${esc(value(context.budget))}" placeholder="사용할 현금"></label></div><p class="cycle-muted">현금 기준 ${esc(context.as_of||'미확인')} · 예산은 수정할 수 있습니다. 저장 현금이 미확인이면 직접 입력하세요.</p>${reentry?'<p>이전 종료 사이클을 보존하고 별도 사이클을 만듭니다.</p>':''}<button class="primary" type="button" data-start>Agent 초안 생성</button><p class="cycle-muted">저장된 분석·논의·차트 조건으로 가격 가정과 세 경로를 만듭니다. 초안 생성 후 조합을 비교하고 직접 확정하세요.</p>`;
   box.querySelector('[data-account]').onchange=async e=>{try{context=await rpc('context',{stock_key:context.stock_key,account_alias:e.target.value||null});if(valid())renderContext();}catch(e){status(e.message);}};
   box.querySelector('[data-start]').onclick=async()=>{if(busy)return;busy=true;box.querySelector('[data-start]').disabled=true;try{const budget=numeric(box.querySelector('[data-budget]').value);if(budget==null||Number(budget)<=0)throw Error('투자 예산을 입력하세요.');const data=await rpc('request_draft',{stock_key:context.stock_key,account_alias:box.querySelector('[data-account]').value||null,budget,...(reentry?{reentry_from:reentry}:{})});if(!valid())return;const id=cycleId(unwrap(data));if(!id)throw Error('생성된 사이클을 확인하지 못했습니다.');location.hash='#cycle?id='+encodeURIComponent(id);}catch(e){status(e.message);}finally{busy=false;if(valid())box.querySelector('[data-start]').disabled=false;}};
  }
  const input=host.querySelector('[data-stock-search]');input.oninput=()=>{clearTimeout(searchTimer);const sequence=++searchSequence,text=input.value.trim();if(!text){host.querySelector('[data-stock-results]').replaceChildren();return;}searchTimer=setTimeout(async()=>{try{const r=await fetch('/api/stock-search?q='+encodeURIComponent(text)),d=await r.json();if(!valid()||sequence!==searchSequence)return;const results=host.querySelector('[data-stock-results]');results.innerHTML=(d.items||[]).slice(0,10).map(s=>`<button type="button" data-stock="${esc(s.market+':'+s.symbol)}"><strong>${esc(s.name||s.symbol)}</strong><span>${esc(s.market)} · ${esc(s.symbol)}</span></button>`).join('')||'<p>일치하는 종목이 없습니다.</p>';results.querySelectorAll('[data-stock]').forEach(b=>b.onclick=()=>select(b.dataset.stock).catch(e=>status(e.message)));}catch(e){status('종목 검색 실패: '+e.message);}},180);};
  const data=await rpc('list');if(!valid())return;
  host.querySelector('[data-cycles]').innerHTML=(data.cycles||[]).map(c=>`<a class="cycle-card" href="#cycle?id=${encodeURIComponent(cycleId(c))}"><div><span class="cycle-badge">${esc(phase(c))}</span><h3>${esc(c.title||c.stock_key)}</h3><p>${esc(c.stock_key)} · ${esc(c.account_alias||c.draft?.plan?.account_alias||'계좌 미지정')}</p></div><small>${esc(c.updated_at||c.created_at||'')} →</small></a>`).join('')||'<div class="empty">아직 저장된 사이클이 없습니다. 종목을 선택해 첫 초안을 만들어보세요.</div>';
  if(preselected)await select(preselected);
 }
 function mountStock({market,symbol}){
  const nav=document.querySelector('.stock-section-nav');if(!nav||nav.querySelector('[data-cycle-link]'))return;
  const link=document.createElement('a');link.dataset.cycleLink='true';link.className='cycle-stock-entry';link.href='#cycles?stock='+encodeURIComponent(market+':'+symbol);link.textContent='매매 사이클';nav.append(link);
 }
 return {mount,mountStock,normalizePlan,orderedCombinations,representativeIds,chartMarkers,comparisonHTML,progressHTML,editorHTML,incompletePlan,mountChart};
})();

'use strict';
window.WorkbenchScenarios = (() => {
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const names={holding:'보유 시나리오',buy:'매수 감시',lower:'하단',upper:'상단',entry:'진입',stop:'무효화',target:'목표',zone:'매수 구간',clear:'접근 범위 밖',approach:'접근',reached:'도달',breakout:'돌파',breakdown:'이탈',pending:'판정 대기',paused:'중단',active:'감시 중',monitoring:'감시 중',warning:'경계 경고',deferred:'판정 보류',held:'실제 보유 확인',both:'상·하단 / 구간'};
 const issues={account_stale_or_incomplete:'계좌 확인이 오래되었거나 불완전합니다.',holding_unconfirmed:'현재 계좌의 실제 보유 확인이 필요합니다.',drawing_missing:'연결한 작도가 없어 판정을 보류합니다.',completed_price_stale_or_missing:'최신 완료 거래일 종가가 없습니다.',price_sessions_incomplete:'가격 이력에 공백이 있습니다.',atr_history_missing:'ATR14 계산 이력이 부족합니다.',market_identity_or_basis_changed:'종목 또는 가격 기준이 변경되었습니다.'};
 const label=x=>names[x]||x||'미정';
 const money=(v,c)=>v==null?'미확인':WorkbenchFormat.money(v,c);
 const rpc=(op,args={})=>WorkbenchAnalysis.rpc(op,args);
 const monitoringPlans=plans=>(plans||[]).filter(r=>r.monitoring_only===true).filter((r,i,a)=>a.findIndex(x=>x.plan_id===r.plan_id)===i);
 function trendHTML(m,currency){
  const rows=(m.evaluations||[]).filter(e=>e.revision===m.revision&&!e.superseded&&/^\d{4}-\d{2}-\d{2}$/.test(e.date)).sort((a,b)=>a.date.localeCompare(b.date));
  if(rows.length<2)return '';
  const keys=['close',...new Set(rows.flatMap(e=>(e.lines||[]).map(l=>l.name)))];
  const value=(row,key)=>key==='close'?row.close:row.lines?.find(l=>l.name===key)?.value;
  const finite=x=>typeof x==='number'&&Number.isFinite(x);
  const prices=rows.flatMap(e=>keys.map(k=>value(e,k))).filter(finite);
  if(!prices.length)return '';
  const low=Math.min(...prices),high=Math.max(...prices),pad=Math.max((high-low)*.12,Math.abs(high)*.003,0.01),min=low-pad,max=high+pad;
  const first=Date.parse(rows[0].date+'T00:00:00Z'),last=Date.parse(rows.at(-1).date+'T00:00:00Z');
  if(!Number.isFinite(first)||!Number.isFinite(last)||first===last)return '';
  const x=e=>70+(Date.parse(e.date+'T00:00:00Z')-first)/(last-first)*540,y=v=>140-(v-min)/(max-min)*120;
  const colors=['#6db8f6','#f5b85b','#cb97eb','#7ed0ac'];
  const paths=keys.map((key,i)=>{
   let previous=null,path='';
   const dots=rows.map(e=>{const v=value(e,key),day=Date.parse(e.date+'T00:00:00Z');if(!finite(v)){previous=null;return '';}
    path+=(previous!==null&&day-previous===86400000?'L':'M')+x(e).toFixed(2)+' '+y(v).toFixed(2)+' ';previous=day;
    return `<circle cx="${x(e).toFixed(2)}" cy="${y(v).toFixed(2)}" r="3" fill="${colors[i%colors.length]}"><title>${esc(e.date)} · ${esc(key==='close'?'종가':label(key))} ${money(v,currency)} ${esc(currency)}</title></circle>`;
   }).join('');
   return `<path d="${path}" fill="none" stroke="${colors[i%colors.length]}" stroke-width="${i===0?2.5:1.8}" ${i?'stroke-dasharray="5 3"':''}/>${dots}`;
  }).join('');
  return `<figure class="scenario-trend"><figcaption>완료 종가와 날짜별 시나리오 경계 · 현재 감시 v${esc(m.revision)}</figcaption><svg viewBox="0 0 640 180" role="img" aria-label="${esc(rows[0].date)}부터 ${esc(rows.at(-1).date)}까지 실제 판정한 종가와 시나리오 기준선"><title>실제 저장 판정 ${rows.length}일 · 이전 버전 제외 · 판정이 없는 날짜는 선 연결 없음</title><path d="M70 20H610M70 140H610" stroke="#344455" stroke-width="1"/>${paths}<g fill="#aebccc" font-size="11"><text x="64" y="24" text-anchor="end">${money(max,currency)}</text><text x="64" y="144" text-anchor="end">${money(min,currency)}</text><text x="70" y="165">${esc(rows[0].date)}</text><text x="610" y="165" text-anchor="end">${esc(rows.at(-1).date)}</text></g></svg><div class="scenario-trend-legend">${keys.map((key,i)=>`<span style="color:${colors[i%colors.length]}">${esc(key==='close'?'종가':label(key))} ${money(value(rows.at(-1),key),currency)}</span>`).join('')}</div><small>저장된 판정만 표시 · 이전 감시 버전 제외 · 판정이 없는 날짜는 연결하지 않습니다.</small></figure>`;
 }
 function monitorHTML(m){
  const v=m.latest||{},currency=m.currency||m.identity?.currency||(m.stock_key?.startsWith('KR:')?'KRW':'USD');
  const route=m.detail_url||'#stock/'+String(m.stock_key||'').split(':').map(encodeURIComponent).join('/')+(m.case_id?'?analysis='+encodeURIComponent(m.case_id):'');
  const lines=Array.isArray(v.lines)?v.lines:Object.entries(v.lines||{}).map(([name,value])=>({name,value}));
  return `<article class="scenario-card" data-monitor="${esc(m.id)}"><div class="row"><h3><a href="${esc(route)}">${esc(m.name||m.identity?.name||m.stock_key)}</a> · ${esc(m.title||label(m.purpose))}</h3><span class="scenario-badge">${m.test_record?'검증용 · ':''}${m.active?label(m.status):'감시 중단'}</span></div>
   <p>${m.issue?'<strong class="error-text">판정 보류 · '+esc(issues[m.issue]||m.issue)+'</strong>':`완료 거래일 ${esc(v.date||'미판정')} · 종가 <strong>${money(v.close,currency)}</strong> ${esc(currency)}`} · ATR14 ${money(v.atr,currency)} · 접근폭 ${esc(m.tolerance_atr)} × ATR14${v.tolerance!=null?' = '+money(v.tolerance,currency):''}</p>
   ${v.superseded?'<p class="error-text">아래 수치는 변경 전 기준의 과거 판정입니다. 수정된 기준은 다음 완료 종가 판정 대기 중입니다.</p>':m.issue&&v.date?'<p>아래 기준선은 마지막 정상 판정 당시 값입니다. 현재 판정은 보류되었습니다.</p>':''}<div class="scenario-levels">${lines.map(l=>`<span><strong>${esc(label(l.name))}</strong> ${money(l.value,currency)} ${v.states?.[l.name]?`<em>${esc(label(v.states[l.name]))}</em>`:''}</span>`).join('')||'<span>아직 종가 판정 기록이 없습니다. 저장 작도는 차트에서 미리 확인할 수 있습니다.</span>'}${v.states?.zone?`<span class="scenario-zone-state"><strong>매수 구간 상태</strong> <em>${v.states.zone==='reached'?'구간 내부 도달':esc(label(v.states.zone))}</em></span>`:''}</div>
   ${(m.evaluations||[]).length?`<details><summary>과거 종가 판정 차트</summary>${trendHTML(m,currency)}</details>`:""}<p class="chart-caption">작도 ${esc(m.drawing_id)} · 경계 ${esc(label(m.boundary))} · 저장 v${esc(m.workspace_revision)} · ${esc(m.timeframe)} · ${esc(m.price_basis||m.identity?.price_basis||'기준 확인 중')} · 계획 ${esc(m.plan_record_id)}<br>저장한 작도 수정은 활성 참조에 반영됩니다. 당시 확정 계획과 과거 입력은 보존됩니다.</p>
   ${m.confirmed_response?`<p>확정 대응: ${esc(m.confirmed_response)}</p>`:'<p>대응 기준 미정</p>'}
   <form data-monitor-settings><label>접근 허용폭 (ATR14 배수)<input name="tolerance_atr" aria-label="접근 허용폭" type="number" min="0" max="10" step="0.05" value="${esc(m.tolerance_atr)}" required></label><button>허용폭 저장</button><button type="button" data-toggle>${m.active?'감시 중단':'감시 재개'}</button><button type="button" data-monitor-history>변경 이력</button></form>
   ${m.purpose==='buy'&&m.conversion?.status==='pending'?'<div class="scenario-conversion"><strong>계좌에서 실제 보유가 확인되었습니다.</strong><p>이 매수 감시를 보유 감시로 전환하시겠습니까? 주문이나 체결 기록을 만들지 않습니다.</p><button data-convert="yes">보유 감시로 전환</button><button data-convert="no">매수 감시 유지</button></div>':''}<div data-history hidden></div><p data-result role="status"></p></article>`;
 }
 function eventsHTML(events){return events.length?`<details><summary>시나리오 사건 기록 (${events.length})</summary>${events.slice(0,100).map(e=>`<p><strong>${esc(e.date)} · ${esc(e.stock_key||e.symbol)} · ${esc(label(e.boundary))+' '+esc(label(e.state||e.kind))}</strong><br>종가 ${money(e.close,e.currency)} · ${e.zone_lower!=null?'구간 '+money(e.zone_lower,e.currency)+' ~ '+money(e.zone_upper,e.currency):'기준 '+money(e.threshold,e.currency)} ${esc(e.currency)}<br>${esc(e.confirmed_response||'대응 기준 미정')} ${e.message?'<pre class="analysis-document">'+esc(e.message)+'</pre>':''}</p>`).join('')}</details>`:'';}
 async function renderList(host,stockKey,riskPositions=null){
  const data=await rpc('monitor_list',stockKey?{stock_key:stockKey}:{});if(!host.isConnected)return;
  const signature=JSON.stringify(data);if(host.dataset.snapshot===signature)return data;host.dataset.snapshot=signature;
  const rows=data.monitors||[];
  host.querySelectorAll('.chart').forEach(p=>{if(p._scenarioChart){WorkbenchCharts.remove(p._scenarioChart);p._scenarioChart=null;}});
  host.innerHTML=['holding','buy'].map(p=>`<section class="panel scenario-list"><div class="panel-body"><h2>${p==='holding'?'보유 시나리오 · 가격위험':'매수 감시'}</h2><p>${p==='holding'?'기존 트레일링·5일선과 별도로 시나리오 경계를 확인합니다.':'원하는 매수 구간을 기다리는 목록입니다. 보유 시나리오와 별개로 유지합니다.'} 공개 데모는 고정된 합성 자료로 판정하며 예약 갱신은 실행하지 않습니다.</p>${rows.filter(m=>m.purpose===p).map(monitorHTML).join('')||'<p class="empty">등록된 감시가 없습니다. 종목 차트 옆 버튼으로 확정 시나리오를 연결하세요.</p>'}</div></section>`).join('')+eventsHTML(data.events||[]);
  host.querySelectorAll('[data-monitor]').forEach(card=>{
   const m=rows.find(x=>x.id===card.dataset.monitor),result=card.querySelector('[data-result]');
   async function mutate(op,args){try{result.textContent='저장 중…';await rpc(op,args);await renderList(host,stockKey,riskPositions);}catch(e){result.textContent=e.message;}}
   card.querySelector('form').onsubmit=e=>{e.preventDefault();mutate('monitor_update',{id:m.id,revision:m.revision,tolerance_atr:Number(new FormData(e.target).get('tolerance_atr'))});};
   card.querySelector('[data-toggle]').onclick=()=>mutate('monitor_update',{id:m.id,revision:m.revision,active:!m.active});
   card.querySelectorAll('[data-convert]').forEach(b=>b.onclick=()=>mutate('monitor_conversion',{id:m.id,revision:m.revision,accept:b.dataset.convert==='yes'}));
   card.querySelector('[data-monitor-history]').onclick=()=>{const h=card.querySelector('[data-history]');h.hidden=!h.hidden;h.innerHTML='<pre class="analysis-document">'+esc(JSON.stringify(m.history||m.revisions||[],null,2))+'</pre>';};
  });
  if(riskPositions&&window.ScenarioPreview)await previewRisk(host,rows,riskPositions);
  return data;
 }
 async function previewRisk(host,rows,positions){
  const cache=new Map(),root=host.parentElement;
  const load=key=>{if(!cache.has(key))cache.set(key,fetch('/api/chart/'+encodeURIComponent(key)+'?timeframe=1d').then(r=>{if(!r.ok)throw Error('저장 가격 조회 실패');return r.json();}));return cache.get(key);};
  const matches=(p,m)=>p.market===m.identity.market&&(p.quote_symbol||p.symbol)===(m.stock_key.split(':')[1])&&(!m.account_alias||m.account_alias===p.account);
  // Cards live under their account's existing chart; remove old cards before a settings refresh.
  root.querySelectorAll('[data-integrated-scenario]').forEach(e=>e.remove());
  for(const [i,p] of positions.entries()){
   const monitors=rows.filter(m=>m.purpose==='holding'&&matches(p,m));if(!monitors.length)continue;
   const plot=root.querySelector('#trailing-chart-'+i),panel=plot.parentElement.parentElement;
   const block=document.createElement('div');block.dataset.integratedScenario='';panel.append(block);
   for(const m of monitors){const card=host.querySelector('[data-monitor="'+m.id+'"]');if(card)block.append(card);}
   try{const stock=await load(monitors[0].stock_key);if(!root.isConnected)return;
    const usable=monitors.filter(m=>m.identity.price_basis===stock.price_basis&&m.identity.currency===stock.currency);
    ScenarioPreview.mount(plot,stock.bars||[],usable,{currency:p.currency,trailing:p.active?p.line:null,holdingLegend:root.querySelector('#trailing-legend-'+i)});
    block.insertAdjacentHTML('afterbegin','<p>위 차트: 최근 한 달 종가 + 저장된 보유 시나리오 · 미래 점선은 작도 미리보기이며 종가 판정 결과가 아닙니다.</p>');
   }catch(e){block.insertAdjacentHTML('afterbegin','<p>'+esc(e.message)+'</p>');}
  }
  for(const m of rows){const card=host.querySelector('[data-monitor="'+m.id+'"]');if(!card)continue;
   const plot=document.createElement('div');plot.className='chart';plot.setAttribute('aria-label',m.name+' 최근 한 달 종가와 시나리오 미리보기');card.querySelector('.row').after(plot);
   try{const stock=await load(m.stock_key);if(!root.isConnected)return;
    if(stock.currency!==m.identity.currency||m.identity.price_basis!==stock.price_basis)throw Error('가격 기준 불일치 · 미리보기 보류');
    const preview=ScenarioPreview.mount(plot,stock.bars||[],[m],{currency:m.currency});
    plot.insertAdjacentHTML('afterend','<p class="chart-caption">최근 한 달 저장 종가 · '+esc(stock.bars?.at(-1)?.date||'자료 없음')+' 기준. '+(preview.series.length?'저장 작도 점선은 미래 미리보기이며 판정 결과가 아닙니다.':'표시 가능한 활성 작도가 없습니다.')+' 미래 날짜는 휴일을 반영하지 않은 평일 좌표입니다.</p>');
   }catch(e){plot.textContent=e.message;}
  }
  const holding=host.querySelector('.scenario-list');if(holding&&!holding.querySelector('[data-monitor]'))holding.remove();
 }
 async function mountRisk(host,positions=[]){const box=document.createElement('div');box.id='scenario-risk-lists';host.append(box);try{await renderList(box,undefined,positions);}catch(e){box.textContent='시나리오 감시 조회 실패 · '+e.message;}}
 async function mountStock({market,symbol,currency,controller,isCurrent}){
  const stockKey=market+':'+symbol,price=document.querySelector('#stock-price');
  const toolbar=document.createElement('div');toolbar.className='scenario-chart-actions';toolbar.innerHTML='<button data-purpose="holding">가격위험으로 보내기</button><button data-purpose="buy">매수감시 추가</button><span>저장 작도 → 시나리오 확인 → 종가 감시</span>';
  price.querySelector('.stock-chart-toolbar').after(toolbar);
  const panel=document.createElement('section');panel.className='panel scenario-editor';panel.hidden=true;price.after(panel);
  const list=document.createElement('div');list.id='stock-scenario-list';panel.after(list);
  let generation=0,busy=false;
  const valid=()=>isCurrent()&&panel.isConnected;
  async function refresh(){if(!valid())return;try{await renderList(list,stockKey);}catch(e){if(valid())list.textContent='시나리오 감시 조회 실패 · '+e.message;}}
  async function open(nextPurpose){
   const purpose=nextPurpose;panel.hidden=false;const seq=++generation;
   panel.innerHTML='<div class="panel-body">저장된 시나리오와 작도를 읽는 중…</div>';
   try{
    const caseId=await WorkbenchAnalysis.ensureCase(),frame=controller.context().timeframe;
    const [packet,collab]=await Promise.all([fetch('/api/chart-state/'+encodeURIComponent(stockKey)+'?timeframe='+frame).then(async r=>{if(!r.ok)throw Error('작도 읽기 실패');return r.json();}),rpc('collaboration_read',{analysis_key:caseId})]);
    if(!valid()||seq!==generation)return;
    const drawings=packet.state.drawings.filter(d=>['box','channel','riskreward'].includes(d.kind)),adopted=collab.active_plans||[];
    panel.innerHTML=`<div class="panel-body"><div class="row"><h2>${purpose==='holding'?'가격위험에 보유 시나리오 연결':'매수 감시 추가'}</h2><button data-close>닫기</button></div><p>서버에 저장된 작도 v${packet.revision}만 연결합니다. 박스·평행 채널·손익비를 선택하세요. 기울어진 선은 종가의 거래일 값으로 판정합니다.</p>
     <form data-draft class="scenario-form"><label>계획 변경 이력<select name="prior_plan"><option value="">새 시나리오</option>${monitoringPlans(collab.plans).map(r=>`<option value="${esc(r.id)}">${esc(r.plan.title)} · v${r.plan_version} 이어 수정</option>`).join('')}</select></label><label>저장 작도<select name="drawing_id" required>${drawings.map((d,i)=>`<option value="${esc(d.id)}">${i+1}. ${esc(DrawingTools.labels[d.kind])} · ${esc(d.id)} · ${esc(d.timeframe||frame)}</option>`).join('')}</select></label><label>감시 경계<select name="boundary" required></select></label><label>시나리오 제목<input name="title" maxlength="200" required value="${esc(symbol+' '+(purpose==='holding'?'보유 시나리오':'매수 대기 시나리오'))}"></label><label>확정할 대응·먼저 확인할 조건 (미정이면 비워두세요)<textarea name="response" maxlength="4000" placeholder="예: 경계 이탈 시 사업 근거를 다시 확인하고 논의한다."></textarea></label><label>접근 허용폭 (ATR14 배수)<input name="tolerance_atr" type="number" min="0" max="10" step="0.05" value="0.5" required></label><p>완료 종가와 선택 경계 거리 ≤ 허용폭. 실제 도달·돌파·이탈은 접근과 구분됩니다. 손익비는 보유 시 무효화·목표, 매수 시 진입을 연결합니다.</p><button ${drawings.length?'':'disabled'} class="primary">시나리오 초안 저장 후 검토</button></form>
     ${drawings.length?'':'<p>먼저 차트에 박스·평행 채널·손익비를 그리고 저장하세요. AI 제안은 채택한 뒤 사용할 수 있습니다.</p>'}
     <details><summary>이미 확정한 계획으로 연결</summary>${adopted.map(a=>`<button data-existing="${esc(a.record.id)}">${esc(a.record.plan.title)} · v${a.record.plan_version}</button>`).join('')||'<p>확정 계획이 없습니다.</p>'}<p>위에서 선택한 저장 작도와 허용폭으로 등록합니다. 계획과 연결한 작도가 일치해야 합니다.</p></details><div data-review></div><p data-status role="status"></p></div>`;
    const form=panel.querySelector('form'),status=t=>{if(valid())panel.querySelector('[data-status]').textContent=t;};
    panel.querySelector('[data-close]').onclick=()=>{panel.hidden=true;generation++;};
    form.elements.prior_plan.onchange=()=>{const prior=monitoringPlans(collab.plans).find(r=>r.id===form.elements.prior_plan.value);if(prior){form.elements.title.value=prior.plan.title;form.elements.response.value=prior.plan.response||prior.plan.conditions?.find(c=>c.statement)?.statement||'';}};
    const selected=controller.context().selected_drawing;if(drawings.some(d=>d.id===selected))form.elements.drawing_id.value=selected;
    function updateBoundary(){const drawing=drawings.find(d=>d.id===form.elements.drawing_id.value),risk=drawing?.kind==='riskreward';const options=risk?(purpose==='buy'?['entry']:['stop','target','both']):['lower','upper','both'];form.elements.boundary.innerHTML=options.map(value=>`<option value="${value}">${esc(label(value))}</option>`).join('');form.elements.boundary.value=risk?(purpose==='buy'?'entry':'both'):(purpose==='buy'?'lower':'both');}
    form.elements.drawing_id.onchange=updateBoundary;updateBoundary();
    function selection(){const f=new FormData(form);return {drawing_id:f.get('drawing_id'),boundary:f.get('boundary'),tolerance_atr:Number(f.get('tolerance_atr'))};}
    async function register(recordId,choice){
     await rpc('monitor_register',{analysis_key:caseId,plan_record_id:recordId,workspace_revision:packet.revision,purpose,...choice});
     status('감시 등록 저장됨 · 다음 일일 갱신의 완료 종가부터 판정합니다.');await refresh();
    }
    panel.querySelectorAll('[data-existing]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await register(b.dataset.existing,selection());}catch(e){status(e.message);}finally{b.disabled=false;}});
    form.onsubmit=async e=>{
     e.preventDefault();if(busy)return;busy=true;const submit=form.querySelector('button');submit.disabled=true;
     try{
      const f=new FormData(form),choice=selection(),v=await rpc('collaboration_read',{analysis_key:caseId});
      const prior=monitoringPlans(collab.plans).find(r=>r.id===f.get('prior_plan'));
      const saved=await rpc('scenario_draft',{...(prior?{plan_id:prior.plan_id,expected_plan_version:prior.plan_version}:{}),analysis_key:caseId,expected_revision:v.revision,drawing_id:choice.drawing_id,workspace_revision:packet.revision,title:f.get('title'),purpose,response:f.get('response')||null});
      const record=saved.record||saved.plan;
      if(!saved.verified||!record?.id)throw Error('초안 저장 확인 실패');
      const reread=await rpc('collaboration_read',{analysis_key:caseId});
      const review=await rpc('plan_review',{analysis_key:caseId,plan_record_id:record.id,expected_revision:reread.revision});
      if(!valid()||seq!==generation)return;
      const reviewHost=panel.querySelector('[data-review]');
      reviewHost.innerHTML=`<h3>정확한 시나리오 버전 확인</h3>${window.WorkbenchCollaboration?WorkbenchCollaboration.planHTML(review.plan):'<pre class="analysis-document">'+esc(JSON.stringify(review.plan.plan,null,2))+'</pre>'}<p>감시 경계 ${esc(label(choice.boundary))} · 접근폭 ${choice.tolerance_atr} × ATR14 · ${purpose==='holding'?'보유 가격위험':'매수 감시'}. 아래 버튼은 이 계획 버전을 확정하고 선택한 저장 작도를 감시에 연결합니다. 실제 체결은 아닙니다.</p><button data-confirm class="primary">이 버전 확정 후 ${purpose==='holding'?'가격위험':'매수감시'}에 등록</button>`;
      status('초안 저장 확인 · '+record.id+' · 아직 미확정');
      reviewHost.querySelector('button').onclick=async e=>{
       e.target.disabled=true;try{
        const decided=await rpc('plan_decide',{contract_version:reread.contract_version,analysis_key:caseId,expected_revision:review.revision,confirmation:review.confirmation,action:'adopt',reason:'종목 상세에서 저장 작도·시나리오·감시 목적 확인'});
        if(!decided.verified)throw Error('계획 확정 재조회 필요');
        await register(record.id,choice);reviewHost.innerHTML='<p>계획 확정과 감시 등록을 저장했습니다.</p>';
       }catch(err){status(err.message+' · 확정/등록 상태를 다시 읽은 뒤 재시도하세요.');}
      };
     }catch(err){status(err.message);}finally{busy=false;submit.disabled=false;}
    };
   }catch(e){if(valid())panel.innerHTML='<div class="panel-body"><p role="alert">'+esc(e.message)+'</p><p>자료를 확인한 뒤 차트 옆 연결 버튼을 다시 누르세요.</p></div>';}
  }
  toolbar.querySelectorAll('button').forEach(b=>b.onclick=()=>open(b.dataset.purpose));
  const saved=()=>{if(valid())refresh();else window.removeEventListener('workbench-drawing-saved',saved);};window.addEventListener('workbench-drawing-saved',saved);
  await refresh();
  async function tick(){if(!valid())return;if(!list.contains(document.activeElement))await refresh();if(valid())setTimeout(tick,15000);}setTimeout(tick,15000);
 }
 return {mountStock,mountRisk,monitorHTML,eventsHTML,trendHTML,monitoringPlans};
})();

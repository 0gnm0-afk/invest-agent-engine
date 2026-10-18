window.WorkbenchAnalysis=(()=>{
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let generation=0, currentCase=null, createCurrent=null;
 const continuing=d=>!d.case.test_record&&d.items.length>0;
 const launchUrl=d=>continuing(d)?d.links.resume_codex_url||d.links.codex_url:d.links.codex_url;
 let collaborationLoading=null;
 async function loadCollaboration(){
  if(window.WorkbenchCollaboration)return true;
  if(!collaborationLoading)collaborationLoading=(async()=>{
   const h=await fetch('/health');const health=await h.json();
   if(health.collaboration_protocol!==1)return false;
   await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='/collaboration.js';script.onload=resolve;script.onerror=()=>reject(Error('후속 협업 화면을 불러오지 못했습니다.'));document.head.append(script);});return true;
  })();
  return collaborationLoading;
 }
 async function rpc(op,args){const h=await fetch('/health');const {token}=await h.json();const r=await fetch('/api/analysis-rpc',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token},body:JSON.stringify({op,arguments:args})});const v=await r.json();if(!r.ok)throw Error(v.error||'분석 연결 실패');return v;}
 function requested(){return new URLSearchParams(location.hash.split('?')[1]||'').get('analysis');}
 async function initial({market,symbol}={}){let id=requested();if(!id&&market&&symbol){const v=await rpc('list',{stock_key:market+':'+symbol});id=v.cases.filter(c=>!c.test_record).sort((a,b)=>(b.updated_at||b.created_at).localeCompare(a.updated_at||a.created_at))[0]?.id;}return id?rpc('detail',{analysis_key:id}):null;}
 async function mount({market,symbol,currency,controller,isCurrent,initialDetail}){
  const version=++generation,key=market+':'+symbol;
  const valid=()=>version===generation&&isCurrent();
  const container=document.createElement('section');container.className='panel analysis-panel';container.id='analysis-panel';
  document.querySelector('#stock-price').before(container);
  container.innerHTML=`<div class="panel-body"><div class="row"><strong>종목 분석 기록</strong><select id="analysis-select" aria-label="분석건 선택"><option value="">새 분석건</option></select><input id="analysis-title" aria-label="새 분석건 이름" placeholder="분석 이름 / 시나리오"><button id="analysis-start" class="primary">정성분석 하기</button><button id="analysis-new">새 분석 만들기</button><button id="analysis-copy" disabled>분석 연결 복사</button><a id="analysis-open" hidden>Codex 입력 다시 열기</a></div><p id="analysis-state" role="status">정성분석 스킬 연결됨 · 새 Codex 입력을 전송하면 분석을 시작합니다. 저장한 리포트는 아래에 표시됩니다. 작도 없이 시작할 수 있습니다.</p><details><summary>검증용 연결</summary><label><input type="checkbox" id="analysis-test">연결 검증용 기록 (실제 투자 분석 아님)</label></details><div id="analysis-proposals"></div></div>`;
  const q=s=>container.querySelector(s);let detail=initialDetail,current=detail?.case.id,signature='',polling=false;
  currentCase=current||null;
  const status=t=>{if(valid())q('#analysis-state').textContent=t;};
  const startHint=test=>test?'연결 검증용 · 새 Codex 입력을 전송하면 자료 연결만 확인합니다.':'정성분석 스킬 연결됨 · 새 Codex 입력을 전송하면 분석을 시작합니다. 저장한 리포트는 아래에 표시됩니다.';
  async function list(){const v=await rpc('list',{stock_key:key});if(!valid())return;q('#analysis-select').innerHTML='<option value="">새 분석건</option>'+v.cases.map(c=>`<option value="${c.id}">${esc(c.title)}${c.test_record?' [검증용]':''} · ${esc((c.updated_at||c.created_at).slice(0,10))} · 리포트 ${c.report_count||0}</option>`).join('');q('#analysis-select').value=current||'';}
  function routeKey(){currentCase=current||null;history.replaceState(null,'','#stock/'+encodeURIComponent(market)+'/'+encodeURIComponent(symbol)+(current?'?analysis='+current:''));}
  function render(){
   if(!detail||!valid())return;
   if(window.WorkbenchCollaboration)WorkbenchCollaboration.show(container,current,rpc,detail.links);
   q('#analysis-test').checked=Boolean(detail.case.test_record);q('#analysis-test').disabled=true;q('#analysis-start').textContent=detail.case.test_record?'연결 검증 하기':continuing(detail)?'기존 기록에서 분석 이어가기':'정성분석 하기';
   q('#analysis-copy').disabled=false;const link=q('#analysis-open');link.hidden=false;link.href=launchUrl(detail);
   const decisions=new Set(detail.items.filter(i=>i.kind==='decision').map(i=>i.proposal_id));
   const proposals=detail.items.filter(i=>i.kind==='proposal'&&!decisions.has(i.id));
   WorkbenchWorkspace.current()?.setProposals(proposals.flatMap(p=>p.drawings.map((d,i)=>({...d,id:'preview_'+p.id+'_'+i,ai:true,color:'#b08dff'}))));
   const sig=JSON.stringify(detail.items);if(sig===signature)return;const editing=document.activeElement;if(editing?.matches('input,textarea,select')&&editing.closest('#analysis-proposals,#qualitative-report'))return;signature=sig;
   q('#analysis-proposals').innerHTML=proposals.map(p=>`<details class="analysis-proposal"><summary>AI 제안 · 기준 작도 v${p.base_revision} · ${esc(p.rationale)}</summary><p>보라색 점선은 AI 검토 가정입니다. 채택 시 원본을 보존하고 별도 작도로 추가합니다.</p><form data-proposal="${p.id}">${p.drawings.map((d,i)=>`<fieldset><legend>${esc(DrawingTools.labels[d.kind])}</legend>${d.points.map((point,j)=>`<label>점 ${j+1} 날짜 <input type="date" name="${i}-${j}-time" value="${point.time}" required></label><label>가격 (${esc(currency)}) <input type="text" inputmode="decimal" name="${i}-${j}-price" value="${WorkbenchFormat.money(point.price,currency)}" required></label>`).join('')}</fieldset>`).join('')}<label>수정·채택 이유 <input name="reason" maxlength="4000"></label><button type="submit">수정안 채택</button><button type="button" data-discard="${p.id}">폐기</button><p class="proposal-result" role="status"></p></form></details>`).join('');
   q('#analysis-proposals').querySelectorAll('form').forEach(form=>{
    const p=proposals.find(x=>x.id===form.dataset.proposal);
    async function decide(action){
     try{if(WorkbenchWorkspace.current()&&!await WorkbenchWorkspace.current().save())throw Error('현재 작도를 먼저 저장하세요.');const f=new FormData(form);const drawings=p.drawings.map((d,i)=>({...d,points:d.points.map((v,j)=>({time:f.get(`${i}-${j}-time`),price:f.get(`${i}-${j}-price`)===WorkbenchFormat.money(v.price,currency)?v.price:Number(String(f.get(`${i}-${j}-price`)).replaceAll(',',''))}))}));await rpc('decide',{analysis_key:current,proposal_id:p.id,action,reason:f.get('reason'),...(action==='adopt'?{drawings}:{})});await WorkbenchWorkspace.current()?.reload();signature='';await poll();}
     catch(e){form.querySelector('.proposal-result').textContent=e.message.includes('stale')?'기준 작도가 바뀌었습니다. 최신 자료를 다시 읽고 새 제안을 제출해야 합니다.':e.message;}
    }
    form.onsubmit=e=>{e.preventDefault();decide('adopt');};form.querySelector('[data-discard]').onclick=()=>decide('discard');
   });
   const reports=document.querySelector('#qualitative-report');
   const records=detail.items.filter(i=>['report','review'].includes(i.kind)).slice().reverse();
   reports.innerHTML=`<section class="panel"><div class="panel-body"><h2>정성 리포트 · 검토 기록</h2><p>${records.length?'저장된 리포트·검토 기록입니다.':detail.case.test_record?'아직 저장된 연결 검증 기록이 없습니다.':'아직 저장된 리포트가 없습니다. Codex에서 분석 후 저장하면 여기에 표시됩니다.'} ${detail.case.test_record?'[연결 검증용 분석건]':''}</p>${records.map((r,i)=>`<details ${i===0?'open':''}><summary>${esc(r.title)} · ${r.kind==='report'?'리포트':'검토'} · ${esc(r.author)} · ${esc(r.created_at)} ${r.test_record?'[검증용]':''}</summary>${window.WorkbenchReportReader?WorkbenchReportReader.record(r):`<pre class="analysis-document">${esc(r.body)}</pre>`}</details>`).join('')}<div id="analysis-replay"></div><details><summary>사용자 검토 기록 남기기</summary><form id="analysis-user-note"><label>검토 내용<textarea name="body" required maxlength="12000"></textarea></label><label>동의 <input name="agreement"></label><label>반례 <input name="counterexample"></label><label>수정 이유 <input name="change_reason"></label><label>채택 여부 <input name="adoption"></label><button>검토 기록 저장</button></form></details><details><summary>실제 보유와 분석 연결</summary><p>저장 잔고에서 확인되는 보유만 연결합니다. 매수 당시 시나리오는 현재 편집과 별도로 고정됩니다. 계획 수량은 체결이 아닙니다.</p><form id="analysis-holding"><label>계좌 별칭 <input name="account_alias" required></label><label>매수 당시 시나리오 입력 ID <input name="adopted_snapshot_id" placeholder="sn_…" required></label><label>매수 논리 <textarea name="thesis" required></textarea></label><label>무효화 조건 <textarea name="invalidation" required></textarea></label><label>실제 보유·당시 시나리오 확인 근거 <textarea name="confirmation" required></textarea></label><label><input type="checkbox" name="confirmed" required>실제 보유와 이 분석의 연결을 확인합니다.</label><button>보유 연결 기록</button></form></details><details><summary>입력·검토 이력</summary>${detail.snapshots.map(s=>`<div>${s.captured_at} · ${s.id} <button data-replay="${s.id}">당시 자료 보기</button></div>`).join('')}${detail.items.filter(i=>!['report','review','proposal'].includes(i.kind)).map(i=>`<pre>${esc(JSON.stringify(i.kind==='decision'?{kind:i.kind,action:i.action,reason:i.reason,result_revision:i.result_revision}:i,null,2))}</pre>`).join('')}</details></div></section>`;
   reports.querySelectorAll('[data-replay]').forEach(b=>b.onclick=async()=>{try{const s=await rpc('read',{analysis_key:current,snapshot_id:b.dataset.replay,section:'summary'});reports.querySelector('#analysis-replay').innerHTML='<h3>과거 고정 입력 (현재 차트와 구분)</h3><pre class="analysis-document">'+esc(JSON.stringify(s,null,2))+'</pre>';}catch(e){status(e.message);}});
   reports.querySelector('#analysis-user-note').onsubmit=async e=>{e.preventDefault();try{await sync();const s=await rpc('read',{analysis_key:current});const f=new FormData(e.target);await rpc('record',{analysis_key:current,snapshot_id:s.snapshot_id,kind:'review',title:'사용자 검토',body:f.get('body'),author:'user',user_response:Object.fromEntries(['agreement','counterexample','change_reason','adoption'].map(k=>[k,f.get(k)]))});signature='';await poll();status('사용자 검토 저장됨');}catch(err){status(err.message);}};
   reports.querySelector('#analysis-holding').onsubmit=async e=>{e.preventDefault();try{await sync();const s=await rpc('read',{analysis_key:current});const f=new FormData(e.target);await rpc('holding',{analysis_key:current,snapshot_id:s.snapshot_id,...Object.fromEntries(['account_alias','adopted_snapshot_id','thesis','invalidation','confirmation'].map(k=>[k,f.get(k)])),confirmed_by_user:f.has('confirmed')});signature='';await poll();status('관측 보유 연결 기록됨 · 체결/위험 기준 변경 없음');}catch(err){status(err.message);}};
  }
  async function sync(){if(!current)return;if(!controller.ready())throw Error('봉 주기를 전환 중입니다. 차트가 준비되면 다시 연결하세요.');if(WorkbenchWorkspace.current()&&!await WorkbenchWorkspace.current().save())throw Error('작도 저장 실패 · 전달하지 않았습니다.');detail=await rpc('context',{analysis_key:current,context:controller.context()});}
  async function poll(){if(!valid()||!current||polling)return;polling=true;const id=current;try{const value=await rpc('detail',{analysis_key:id});if(valid()&&id===current){detail=value;render();}}catch(e){status(e.message);}finally{polling=false;}}
  q('#analysis-start').onclick=async()=>{q('#analysis-start').disabled=true;try{if(!controller.ready())throw Error('차트를 준비 중입니다. 잠시 후 다시 연결하세요.');
   if(WorkbenchWorkspace.current()&&!await WorkbenchWorkspace.current().save())throw Error('작도 저장 실패 · 분석 연결을 만들지 않았습니다.');
   if(!current){detail=await rpc('create',{stock_key:key,title:q('#analysis-title').value||key,context:controller.context(),test_record:q('#analysis-test').checked});current=detail.case.id;routeKey();await list();}
   else await sync();
   render();status(startHint(detail.case.test_record));
   q('#analysis-open').click();
  }catch(e){status(e.message);}finally{if(valid())q('#analysis-start').disabled=false;}};
  q('#analysis-copy').onclick=async()=>{try{await sync();await navigator.clipboard.writeText(continuing(detail)?detail.links.resume_prompt:detail.links.prompt||detail.links.copy_text);status('기존 보고서·논의 이어가기 문맥 복사됨 · Codex 대화에 붙여 넣어 전송하세요.');}catch(e){status(e.message);}};
  q('#analysis-open').onclick=async e=>{if(e.isTrusted){e.preventDefault();try{await sync();render();location.href=launchUrl(detail);}catch(err){status(err.message);}}};
  q('#analysis-select').onchange=async()=>{try{await sync();current=q('#analysis-select').value||null;routeKey();signature='';WorkbenchWorkspace.current()?.setProposals([]);if(current){detail=await rpc('detail',{analysis_key:current});await controller.restore(detail.case.context);render();status(startHint(detail.case.test_record));}else{detail=null;window.WorkbenchCollaboration?.clear();document.querySelector('#qualitative-report').innerHTML='<section class="panel"><div class="panel-body"><h2>정성 리포트</h2><p>새 분석건 · 아직 저장된 리포트가 없습니다. Codex에서 분석 후 저장하면 여기에 표시됩니다.</p></div></section>';q('#analysis-open').hidden=true;q('#analysis-copy').disabled=true;q('#analysis-test').disabled=false;q('#analysis-test').checked=false;q('#analysis-start').textContent='정성분석 하기';q('#analysis-proposals').replaceChildren();status(startHint(false));}}catch(e){status(e.message);}};
  q('#analysis-test').onchange=()=>{q('#analysis-start').textContent=q('#analysis-test').checked?'연결 검증 하기':'정성분석 하기';status(startHint(q('#analysis-test').checked));};
  loadCollaboration().then(()=>{if(valid()&&detail)render();}).catch(e=>status(e.message));
  await list();if(detail){routeKey();render();status('저장된 분석과 논의를 불러왔습니다. 기존 기록에서 이어가거나 새 분석을 만들 수 있습니다.');}
  createCurrent=async()=>{if(!valid())throw Error('종목 화면이 바뀌었습니다.');if(!current){if(!controller.ready())throw Error('차트 준비 후 다시 시도하세요.');detail=await rpc('create',{stock_key:key,title:q('#analysis-title').value||key,context:controller.context(),test_record:q('#analysis-test').checked});current=detail.case.id;routeKey();await list();render();}else{detail=await rpc('context',{analysis_key:current,context:controller.context()});}return current;};
  q('#analysis-new').onclick=()=>{q('#analysis-select').value='';q('#analysis-select').onchange();};
  async function tick(){if(!valid())return;await poll();if(valid())setTimeout(tick,2000);}setTimeout(tick,2000);
  // Context follows actual chart gestures, but does not trigger financial collection.
  window.addEventListener('workbench-context',()=>{if(valid()&&current)sync().catch(e=>status(e.message));});
 }
 async function account(alias,host){try{const result=await rpc('holding_links',{account_alias:alias});if(!host.isConnected)return;const section=document.createElement('section');section.className='panel';section.innerHTML='<div class="panel-body"><h2>보유 논리 · 분석 연결</h2>'+ (result.links.map(i=>`<p><a href="${esc(i.links.dashboard_url)}">${esc(i.stock_key)} · ${esc(i.case_id)}${i.test_record?' [연결 검증용]':''}</a><br>매수 논리: ${esc(i.thesis)}<br>무효화 조건: ${esc(i.invalidation)}<br>당시 입력 ${esc(i.adopted_snapshot_id)} · 관측 잔고 ${esc(i.as_of)}</p>`).join('')||'<p>사용자가 확인한 분석 연결 없음. 계획을 실제 체결로 간주하지 않습니다.</p>')+'</div>';host.append(section);}catch{}}
 return {mount,initial,account,rpc,currentKey:()=>currentCase,ensureCase:()=>createCurrent?createCurrent():Promise.reject(Error("분석 화면 준비 중"))};
})();

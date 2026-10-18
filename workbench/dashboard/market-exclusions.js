'use strict';
window.WorkbenchExclusions={mount(host,onResult){
 if(!host||host.dataset.mounted)return;host.dataset.mounted='true';
 let latest=null,selected=new Set(),submitting=false,timer=null,checking=null,readError='',note='',offered=null,opened=null,stopped=false;
 const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
 const busy=()=>submitting||latest?.busy;
 function button(parent,id,text,action,disabled=false){const b=el('button',text);b.id=id;b.type='button';b.disabled=disabled;b.onclick=action;parent.append(b);return b;}
 function render(){
  host.replaceChildren();const items=latest?.issues||[],job=latest?.job;
  const activeJob=job&&!['completed','superseded'].includes(job.state);
  host.hidden=!items.length&&!activeJob&&!note&&!readError;if(host.hidden)return;
  const heading=el('h2',(items.length?'NH 보완 실패':'자료 재처리 상태')+(items.length?' · '+items.length+'종목':''));host.append(heading);
  if(items.length){
   const sessions=Object.entries(latest.sessions||{}).map(([market,date])=>market+' '+date).join(' · ');
   if(sessions)host.append(el('p','기준 완료일 · '+sessions));
   host.append(el('p','실패 종목을 선택해 Toss → NH 순서로 각각 한 번 재수집하세요. 둘 다 시도한 뒤에도 남은 종목은 제외 승인할 수 있습니다. 같은 기준일·종목의 재수집 이력은 저장됩니다.'));
   const wrap=el('div',undefined,'scope-table-wrap'),table=el('table'),head=el('tr');
   for(const label of ['선택','종목','실패한 자료','Toss 상태','NH 보완 결과'])head.append(el('th',label));
   const thead=el('thead');thead.append(head);table.append(thead);const body=el('tbody');
   for(const item of items){
    const tr=el('tr'),cell=el('td'),box=el('input');box.type='checkbox';box.checked=selected.has(item.id);
    box.disabled=busy()||(!item.exclude_allowed&&!item.retry?.toss&&!item.retry?.nh);box.setAttribute('aria-label',item.symbol+' 처리 선택');box.dataset.issueId=item.id;
    box.onchange=()=>{box.checked?selected.add(item.id):selected.delete(item.id);render();};cell.append(box);tr.append(cell);
    const stock=el('td');stock.append(el('strong',item.name||item.symbol),el('small',item.market+' · '+item.symbol));tr.append(stock);
    tr.append(el('td',item.field),el('td',item.toss_reason));const reason=el('td',item.nh_reason);
    if(item.retry)reason.append(el('small','재수집 완료: '+(item.retry.attempted.map(p=>p==='toss'?'Toss':'NH').join(' · ')||'없음')));
    if(item.block_reason)reason.append(el('small',item.block_reason));tr.append(reason);body.append(tr);
   }
   table.append(body);wrap.append(table);host.append(wrap);
   const actions=el('div',undefined,'scope-actions');
   button(actions,'scope-select-all','전체 선택',()=>{selected=new Set(items.filter(r=>r.exclude_allowed||r.retry?.toss||r.retry?.nh).map(r=>r.id));render();},busy());
   const chosen=items.filter(r=>selected.has(r.id)),excludable=chosen.filter(r=>r.exclude_allowed);
   const applyingSaved=job?.kind==='recollect'&&job.retry_available;
   for(const provider of ['toss','nh']){const eligible=chosen.filter(r=>r.retry?.[provider]);button(actions,'scope-recollect-'+provider,(provider==='toss'?'Toss':'NH')+' 재수집 ('+eligible.length+')',()=>post({action:'recollect',provider,review_id:latest.review_id,issue_ids:eligible.map(r=>r.id)}),busy()||applyingSaved||!eligible.length||eligible.length>500);}
   button(actions,'scope-approve',submitting?'요청 저장 중…':'선택 '+excludable.length+'종목 제외 승인',()=>post({action:'approve',review_id:latest.review_id,issue_ids:excludable.map(r=>r.id)}),busy()||applyingSaved||!excludable.length||excludable.length>500);
   if(selected.size>500)actions.append(el('span','한 번에 500종목까지 선택할 수 있습니다.'));host.append(actions);
  }
  if(job&&job.state!=='superseded'){
   const recollect=job.kind==='recollect',provider=job.provider==='toss'?'Toss':'NH';
   const text=recollect?({approved:provider+' 재수집 요청을 저장했습니다.',applying:provider+' 재수집·검증·결과 반영 중입니다.',computed:'재수집 검증을 마쳤습니다. 화면을 준비 중입니다.',completed:provider+' 재수집을 마쳤습니다. 남은 실패 종목을 확인하세요.',failed:provider+' 재수집 또는 결과 반영에 실패했습니다. 기존 자료는 유지됩니다.',interrupted:'이전 재수집 처리가 중단됐습니다. 상태 확인·저장 결과 반영을 재개하세요.'}[job.state]||'재수집 상태 확인 중입니다.'): {approved:'제외 승인이 저장됐습니다. 반영을 준비하고 있습니다.',applying:'제외 승인이 저장됐습니다. 기존 자료로 재계산하고 화면을 준비 중입니다.',computed:'계산을 마쳤습니다. 화면을 준비 중입니다.',completed:'선택한 '+job.count+'종목의 제외와 결과 반영을 완료했습니다.',failed:'제외 승인은 저장됐지만 결과 반영에 실패했습니다. 다시 적용할 수 있습니다.',interrupted:'이전 적용이 중단됐습니다. 저장된 승인으로 다시 적용할 수 있습니다.'}[job.state]||'제외 처리 상태를 확인 중입니다.';
   const p=el('p',text,'scope-job');p.setAttribute('role','status');host.append(p);
   const actions=el('div',undefined,'scope-actions');
   if(job.retry_available)button(actions,'scope-retry',recollect?'재수집 상태 확인·저장 결과 반영':'저장된 제외 다시 적용',()=>post({action:'retry',job_id:job.id}),busy());
   if(job.state==='completed'&&latest.view_ready&&opened!==job.id)button(actions,'scope-open','반영 결과 열기',()=>open(job.id),submitting);
   host.append(actions);
  }
  if(note||readError){const p=el('p',readError||note,'scope-message');p.setAttribute('role','status');host.append(p);button(host,'scope-refresh','상태 다시 확인',refresh,submitting);}
 }
 async function open(id){try{if(await onResult()){opened=id;note='';}}catch{note='반영 결과를 열지 못했습니다. 결과 열기를 다시 눌러주세요.';}render();}
 async function accept(value){
  if(latest?.review_id!==value.review_id)selected.clear();latest=value;
  if(!value.issues?.length&&(!value.job||['completed','superseded'].includes(value.job.state)))note='';render();
  const job=value.job;
  if(job?.state==='completed'&&value.view_ready&&offered!==job.id&&(!location.hash||location.hash==='#home')){offered=job.id;await open(job.id);}
 }
 function schedule(){clearTimeout(timer);if(!stopped)timer=setTimeout(refresh,latest?.busy?3000:10000);}
 async function refresh(){
  if(stopped)return;if(checking)return checking;
  checking=(async()=>{try{const r=await fetch('/api/market-exclusions');if(!r.ok)throw Error();const value=await r.json();readError='';await accept(value);}
   catch{readError='NH 실패 목록을 확인하지 못했습니다. 현재 자료는 유지됩니다.';render();}})();
  try{await checking;}finally{checking=null;schedule();}
 }
 async function post(body){
  if(busy())return;submitting=true;note='';clearTimeout(timer);render();
  try{
   if(checking)await checking;
   const health=await fetch('/health');if(!health.ok)throw Error();const {token}=await health.json();
   const r=await fetch('/api/market-exclusions',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token},body:JSON.stringify(body)});
   const value=await r.json();
   if(r.status===409){note='자료가 바뀌었거나 재수집 순서·횟수 또는 다른 갱신을 확인해야 합니다. 최신 목록을 다시 확인하세요.';await refresh();}
   else if(!r.ok){note=value.error==='empty_market_scope'?'모든 종목을 제외할 수는 없습니다. 선택을 확인하세요.':'요청을 저장하지 못했습니다. 상태를 다시 확인하세요.';}
   else await accept(value);
  }catch{note='요청 결과를 확인하지 못했습니다. 상태를 다시 확인하세요.';}
  finally{submitting=false;render();await refresh();}
 }
 refresh();return {refresh,stop(){stopped=true;clearTimeout(timer);},get selected(){return [...selected];}};
}};

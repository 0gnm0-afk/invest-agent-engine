'use strict';
window.WorkbenchRisk = (() => {
 const esc = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const money = (v,c) => WorkbenchFormat.money(v,c);
 // Display grouping requested by the user. This never supplies investable cash
 // or changes the Python risk calculation's reconciliation requirements.
 const numeric = x => x == null || x === '' || !Number.isFinite(Number(x)) ? null : Number(x);
 const isEquity = p => {
  if(p.instrument_class_hint==='CMA_note_from_broker_label')return false;
  const type=String(p.asset_type||p.product_type||p.security_type||'').toLowerCase();
  return !type || ['stock','equity','etf','adr','common_stock','commonshare','depositary_receipt'].includes(type);
 };
 function composition(bundle,alias=null) {
  const currency=bundle?.valuation?.base_currency||'KRW',rates=bundle?.valuation?.fx_to_base||{},rows=[];
  const add=(account,kind,label,amount,c,source)=>{amount=numeric(amount);const rate=c===currency?1:numeric(rates[c]);rows.push({account,kind,label,amount,currency:c,value:amount!=null&&rate>0?amount*rate:null,source});};
  const accounts=(bundle?.accounts||[]).filter(a=>!alias||a.alias===alias);
  for(const a of accounts){
   for(const p of a.positions||[]){
    const stock=isEquity(p),q=numeric(p.quantity),price=numeric(p.price);
    const observed=p.currency==='KRW'?numeric(p.observed_evaluation_krw):null;
    const amount=stock?(p.data_unavailable_reason?null:q!=null&&price!=null?q*price:null):numeric(p.native_value??p.market_value)??observed??(q!=null&&price!=null?q*price:null);
    add(a.alias,stock?'stock':'cash',p.name||p.symbol,amount,p.currency,'저장 평가');
   }
   const cash=a.cash||{},d=a.observed_deposits||{};
   for(const [c,v] of Object.entries(cash))add(a.alias,'cash',c+' 예수금',v,c,'저장 잔액');
   // D+1/D+2 and KRW representations of foreign cash are not extra balances.
   if(!Object.hasOwn(cash,'KRW')&&d.KR&&Object.hasOwn(d.KR,'dca'))add(a.alias,'cash','원화 예수금',d.KR.dca,'KRW','관측 예수금');
   if(!Object.hasOwn(cash,'USD')&&d.US&&Object.hasOwn(d.US,'fc_dca'))add(a.alias,'cash','외화 예수금',d.US.fc_dca,'USD','관측 예수금');
  }
  const groups=['stock','cash'].map(kind=>{const items=rows.filter(r=>r.kind===kind),known=items.filter(r=>r.value!=null);return {kind,items,subtotal:known.length?known.reduce((n,r)=>n+r.value,0):!items.length&&accounts.length&&accounts.every(a=>(kind==='stock'?a.holdings_complete:a.cash_complete)===true)?0:null,missing:items.filter(r=>r.value==null).length};});
  const total=groups.every(g=>g.subtotal!=null)?groups.reduce((n,g)=>n+g.subtotal,0):null;
  const partial=!accounts.length||accounts.some(a=>a.holdings_complete!==true||a.cash_complete!==true)||groups.some(g=>g.missing);
  return {currency,rows,groups,total,partial,canChart:total>0&&groups.every(g=>g.subtotal>=0&&!g.missing)};
 }
 function compositionHTML(bundle,alias) {
  const c=composition(bundle,alias),names={stock:'주식',cash:'현금성 자산'};
  const share=g=>c.canChart?(g.subtotal/c.total*100).toFixed(1)+'%':'비중 보류';
  return `<div class="account-composition"><div class="composition-heading"><h3>계좌 자산 구성</h3><span>${c.partial?'금액이 있는 저장 항목 기준':'저장 항목 기준'} · ${esc(c.currency)}</span></div>
   <div class="risk-stats">${c.groups.map(g=>`<div class="risk-stat"><span class="composition-key ${g.kind}">${names[g.kind]}</span><strong>${money(g.subtotal,c.currency)} <small>${esc(c.currency)}</small></strong><span>${share(g)} · ${g.items.filter(r=>r.kind==='stock'||r.amount!==0).length}개 항목${g.missing?' · 금액 미확인 '+g.missing+'개':''}</span></div>`).join('')}<div class="risk-stat"><span>표시 합계</span><strong>${money(c.total,c.currency)} <small>${esc(c.currency)}</small></strong><span>저장 평가액·예수금 기준 참고 합계</span></div></div>
   ${c.canChart?`<div class="composition-bar" role="img" aria-label="표시 합계 중 주식 ${share(c.groups[0])}, 현금성 자산 ${share(c.groups[1])}">${c.groups.map(g=>`<span class="${g.kind}" style="width:${g.subtotal/c.total*100}%"></span>`).join('')}</div>`:'<p>비중을 표시할 금액이 부족합니다.</p>'}
   <p>주식 외 자산은 현금성 자산으로 묶어 표시합니다. 표시 합계는 매수 가능액이나 부채를 차감한 순자산이 아닙니다.</p>
   <details class="composition-details"><summary>자산 구성 상세 · 금액과 분류 근거</summary><div class="scroll"><table><thead><tr><th>계좌 · 자산</th><th>분류</th><th>저장 금액</th><th>${esc(c.currency)} 환산</th></tr></thead><tbody>${c.rows.map(r=>`<tr><td>${esc(r.account)} · ${esc(r.label)}</td><td>${names[r.kind]}</td><td>${money(r.amount,r.currency)} ${esc(r.currency)}</td><td>${money(r.value,c.currency)}</td></tr>`).join('')||'<tr><td colspan="4">저장된 자산 항목이 없습니다.</td></tr>'}</tbody></table></div><p>환율은 저장 기준을 사용합니다. D+1·D+2 예수금과 외화 예수금의 원화 표시를 중복 합산하지 않습니다. 현금성 분류는 사용자 지정 표시이며, 예수금·CMA 중복 여부와 부채 대조는 미확인입니다.</p></details></div>`;
 }
 function mount(host,opts={}) {host.innerHTML=compositionHTML(opts.accountBundle,opts.accountAlias)+(opts.accountHTML||'')+'<p><a href="#risk">가격 위험 경고 보기 →</a></p>';}
 return {mount,isEquity,composition,compositionHTML};
})();

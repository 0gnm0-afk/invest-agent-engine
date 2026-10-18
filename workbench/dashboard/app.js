'use strict';
const $ = (s) => document.querySelector(s);
const esc = (x) => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (x, digits = 2) => x == null || x === '' ? '미확인' : Number(x).toLocaleString('ko-KR', {maximumFractionDigits:digits});
const money = WorkbenchFormat.money;
const pct = x => x == null ? '결측' : num(x * 100) + '%';
const stamp = x => x ? new Date(x).toLocaleString('ko-KR', {timeZone:'Asia/Seoul',hour12:false}) + ' KST' : '미확인';
const phase = {watch:'관찰 시작', ready:'돌파 대기', entry:'진입 조건 충족'};
const state = {received:'조회 성공',partial:'일부 결측',failed:'조회 실패',not_requested:'저장값 사용',unavailable:'미확인'};
const tag = (text, cls = '') => `<span class="tag ${cls}">${esc(text)}</span>`;
const stockLink = (market, symbol, name, screenRoute='') => `<a href="#stock/${encodeURIComponent(market)}/${encodeURIComponent(symbol)}${screenRoute?'?screen='+encodeURIComponent(screenRoute):''}"><span class="stock-name">${esc(name || symbol)}</span><span class="stock-code">${esc(market)} · ${esc(symbol)}</span></a>`;
const block = (title, body, link = '') => `<section class="panel"><div class="panel-head"><h2>${title}</h2>${link}</div>${body}</section>`;
const explain = x => ({protection_uses_saved_completed_bars_check_each_price_date:'보호 기준선은 과거 저장 봉으로 계산했습니다. 종목별 봉 기준일과 판정일을 확인하세요.',KR_US_scope_not_all_assets:'국내·미국 주식 범위이며 전체 금융자산이 아닙니다.',cash_and_liabilities_not_reconciled:'현금·부채 대조 미완료',working_orders_not_reconciled:'미체결·예약은 이번 조회 범위에 포함하지 않습니다.',adopted_protection_not_applicable_to_this_snapshot:'채택 보호선과 현재 잔고의 대응을 확인하지 못했습니다.',risk_calculation_unavailable:'위험 계산에 필요한 자료가 부족합니다.'})[x] || (x.includes('cash_inventory_incomplete') || x.includes('gross_cash_balance_unavailable') ? '계좌 현금 구성·총액 미확인' : x);
const metric = (label, value) => `<div class="metric"><small>${WorkbenchHelp.text(label)}</small><strong>${esc(value)}</strong></div>`;
let DATA, routeVersion = 0, lastRouteHash = location.hash;
function heading(title, eyebrow, subtitle) { return `<div class="heading"><div><div class="eyebrow">${eyebrow}</div><h1>${esc(title)}</h1><p class="sub">${esc(subtitle)}</p></div><div class="date-box">자료 기준 · 완료 일봉<strong>KR ${esc(DATA.market.session_dates?.KR || '미확인')}<br>US ${esc(DATA.market.session_dates?.US || '미확인')}</strong></div></div>`; }
// Global stock lookup stays mounted while the page route changes.
function mountStockSearch() {
 const host = document.getElementById('stock-search');
 if (!host || host.dataset.mounted) return;
 host.dataset.mounted = 'true';
 const input = host.querySelector('input'), clear = host.querySelector('button');
 const panel = host.querySelector('.stock-search-panel'), list = host.querySelector('[role="listbox"]');
 const status = host.querySelector('[role="status"]'), note = host.querySelector('.stock-search-note');
 let items = [], active = -1, sequence = 0, timer, controller, composing = false;
 const query = () => input.value.trim();
 function cancel() { clearTimeout(timer); controller?.abort(); sequence++; }
 function close() {
  cancel(); panel.hidden = true; input.setAttribute('aria-expanded', 'false');
  input.removeAttribute('aria-activedescendant'); active = -1; items = []; list.replaceChildren();
 }
 function show(message) {
  panel.hidden = false; input.setAttribute('aria-expanded', 'true');
  status.textContent = message;
 }
 function select(index) {
  const item = items[index]; if (!item) return;
  close(); input.value = ''; clear.hidden = true; input.blur();
  location.hash = '#stock/' + encodeURIComponent(item.market) + '/' + encodeURIComponent(item.symbol);
 }
 function highlight(value, needle) {
  const text = String(value ?? ''), start = text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase());
  return start < 0 ? esc(text) : esc(text.slice(0, start)) + '<mark>' + esc(text.slice(start, start + needle.length)) + '</mark>' + esc(text.slice(start + needle.length));
 }
 function activate(index, scroll = false) {
  active = index;
  Array.from(list.children).forEach((row, i) => row.setAttribute('aria-selected', String(i === active)));
  const row = list.children[active];
  if (row) { input.setAttribute('aria-activedescendant', row.id); if (scroll) row.scrollIntoView({block:'nearest'}); }
  else input.removeAttribute('aria-activedescendant');
 }
 async function search(value, request) {
  controller = new AbortController();
  try {
   const response = await fetch('/api/stock-search?q=' + encodeURIComponent(value), {signal:controller.signal});
   if (!response.ok) throw Error('stock_search_failed');
   const result = await response.json();
   if (request !== sequence || value !== query() || document.activeElement !== input) return;
   items = result.items; list.replaceChildren();
   items.forEach((item, i) => {
    const row = document.createElement('div');
    row.id = 'stock-search-option-' + i; row.className = 'stock-search-option';
    row.setAttribute('role', 'option'); row.setAttribute('aria-selected', 'false');
    row.innerHTML = '<span class="stock-search-identity"><strong>' + highlight(item.name || item.symbol, value) + '</strong><span>' + highlight(item.symbol.replace(/\.(KS|KQ)$/, ''), value) + '</span></span><span class="stock-search-market">' + esc(item.exchange || item.market) + '<small>' + (item.market === 'KR' ? '한국' : '미국') + '</small></span>';
    row.addEventListener('pointerdown', event => event.preventDefault());
    row.addEventListener('click', () => select(i));
    row.addEventListener('pointermove', () => activate(i));
    list.append(row);
   });
   show(items.length ? '검색 결과 ' + result.total + '개' : '일치하는 종목이 없습니다. 종목명이나 티커를 확인해 주세요.');
   status.classList.toggle('stock-search-sr', items.length > 0);
   note.textContent = items.length ? '저장된 종목 · ' + items.length + (result.total > items.length ? ' / ' + result.total + '개 · 더 입력하면 좁혀집니다' : '개') + ' · ↑↓ 선택 · Enter 열기' : '저장된 한국·미국 종목에서 검색합니다';
   activate(items.length ? 0 : -1);
  } catch (error) {
   if (request !== sequence || error.name === 'AbortError') return;
   items = []; list.replaceChildren(); input.removeAttribute('aria-activedescendant');
   status.classList.remove('stock-search-sr');
   show('검색을 불러오지 못했습니다. 잠시 후 다시 입력해 주세요.');
   note.textContent = '연결이 끊겼다면 대시보드를 다시 열어 주세요';
  }
 }
 function schedule() {
  cancel(); items = []; active = -1; list.replaceChildren(); input.removeAttribute('aria-activedescendant');
  clear.hidden = !input.value;
  if (!query()) { close(); return; }
  status.classList.remove('stock-search-sr'); show('검색 중…'); note.textContent = '저장된 한국·미국 종목에서 검색합니다';
  const request = sequence, value = query();
  timer = setTimeout(() => search(value, request), 150);
 }
 input.disabled = false;
 input.addEventListener('input', () => { clear.hidden = !input.value; if (!composing) schedule(); });
 input.addEventListener('compositionstart', () => { composing = true; close(); });
 input.addEventListener('compositionend', () => { composing = false; schedule(); });
 input.addEventListener('focus', () => { if (query()) schedule(); });
 input.addEventListener('keydown', event => {
  if (composing || event.isComposing || event.keyCode === 229) return;
  if (event.key === 'Escape') { event.preventDefault(); close(); return; }
  if (event.key === 'Tab') { close(); return; }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
   event.preventDefault();
   if (panel.hidden) { if (query()) schedule(); return; }
   if (items.length) activate((active + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length, true);
  }
  if (event.key === 'Enter') { event.preventDefault(); if (!panel.hidden && active >= 0) select(active); }
 });
 clear.addEventListener('click', () => { input.value = ''; clear.hidden = true; close(); input.focus(); });
 host.addEventListener('focusout', event => { if (!host.contains(event.relatedTarget)) close(); });
 document.addEventListener('pointerdown', event => { if (!host.contains(event.target)) close(); });
 window.addEventListener('hashchange', close);
}
function marketStatus() {
 const m=DATA.market;
 const data=({succeeded:'확보 완료',complete:'확보 완료',partial:'일부 결측',failed:'수집 실패',unavailable:'미준비'})[m.quality?.state]||'미확인';
 const execution=m.state==='failed'?'실패':({completed:'완료',completed_with_exceptions:'완료 · 자료 예외',failed:'실패'})[m.processing_state]||({partial:'일부 미완료',succeeded:'완료',unavailable:'미준비'})[m.state]||'미확인';
 return `자료 ${data} · 실행 ${execution}`;
}
function qualityNotice() {
  const m = DATA.market, q = m.quality || {};
  const gaps=Object.entries({price_history:'가격 이력 주의',market_cap:'시총 미확보',classification:'업종 분류 미확인'}).filter(([key])=>m.quality_gaps?.[key]>0).map(([key,label])=>label+' '+num(m.quality_gaps[key],0)+'종목').join(' · ');
  return `<div class="notice ${m.state === 'unavailable' ? 'error' : ''}"><strong>${esc(marketStatus())}</strong><span>수신 ${num(q.received,0)} / ${num(q.requested,0)} · 실패 ${num(q.failed,0)} · 재계산 ${stamp(m.finished_at)}${gaps?'<br>'+esc(gaps)+' · 해당 종목의 계산·분류가 제한됩니다.':''}<br>수집 ${stamp(m.data_times?.fetched_at)} → ${stamp(m.data_times?.collected_until)}${m.errors?.length ? '<br>' + esc(m.errors.join(' / ')) : ''}${DATA.daily_update?.saved_only?'<br>시장 최신화 생략 · '+(DATA.daily_update.display_stale?'이전 검증 가능 스크리닝 표시 · ':'')+'최근 시장 실행 상태 '+esc(({partial:'완료 · 일부 자료 결측',succeeded:'완료',failed:'실패',running:'진행 중',interrupted:'중단',unavailable:'미준비'})[DATA.daily_update.latest_state]||'미확인')+' · 장기 차트 기준일과 구분하세요.':''}</span></div>`;
}
function stats() {
 const e=DATA.market.entries, fields=[['관찰 시작',e.filter(x=>x.phase==='watch').length,'추세와 조정 구조를 관찰'],['돌파 대기',e.filter(x=>x.phase==='ready').length,'피벗 돌파·유지 확인'],['진입 조건 충족',DATA.market.entry_candidate_count,'현재 저장 결과 · 0개도 그대로 표시'],['연결 계좌',DATA.accounts.accounts.length,`합성 데모 · ${state[DATA.accounts.query_state] || DATA.accounts.query_state}`]];
 return `<div class="stats">${fields.map(([l,v,d],i)=>`<div class="stat ${i===2?'emphasis':''}"><div class="label">${l}</div><div class="value">${DATA.market.candidate_count == null && i < 3 ? '—' : num(v,0)}<small style="font-size:13px;margin-left:6px">${i===3?'개':'종목'}</small></div><div class="detail">${d}</div></div>`).join('')}</div>`;
}
function accountCards() {
 const a=DATA.accounts;
 return `<div class="panel-body"><p class="sub">${tag(state[a.query_state] || a.query_state,a.query_state==='failed'?'bad':'neutral')} ${a.source_mode==='historical_saved'?tag(a.stale?'과거 저장값':'저장된 실조회',a.stale?'warn':'neutral'):tag('읽기 전용')} ${a.stale?tag('24시간 경과','warn'):''}</p><p class="chart-caption">잔고 기준 ${stamp(a.as_of)}</p>${a.accounts.map((x,i)=>`<a class="account-card" href="#account/${encodeURIComponent(x.alias)}"><div class="row"><div><span class="account-title">합성 계좌 ${i+1}</span><div class="account-id">${esc(x.alias)}</div></div><span class="account-count">${x.holdings_complete?x.positions.filter(WorkbenchRisk.isEquity).length:'—'}<small>보유 주식 →</small></span></div><div class="account-summary">${x.holdings_complete?'국내·미국 잔고 확인':'잔고 일부 미확인'} · 현금성 자산 ${x.cash_complete?'확인':'대조 필요'}</div></a>`).join('') || '<div class="empty">표시할 계좌가 없습니다.</div>'}${a.reason?`<p class="danger-text">조회 오류: ${esc(a.reason)}</p>`:''}<p class="chart-caption">계좌를 선택하면 보유 내역과 관측 예수금을 확인할 수 있습니다.</p></div>`;
}
function screeningTable(entries) {
 if (!entries.length) return '<div class="empty"><strong>조건에 맞는 후보가 없습니다.</strong>저장 결과의 0개 상태를 그대로 표시합니다.</div>';
 return `<div class="scroll"><table><thead><tr><th>종목</th><th>시가총액</th><th>단계 / 경로</th><th>선별 근거</th><th>기준선까지</th><th>다음 확인</th></tr></thead><tbody>${entries.map(e=>`<tr><td>${stockLink(e.market,e.symbol,e.name,e.entry_signal?.route)}</td><td class="number">${esc(capText(e))}</td><td>${tag(phase[e.phase] || e.phase,e.phase==='ready'?'warn':'')}<span class="stock-code">${esc((e.origins||[]).map(r=>({pdf_momentum:'추세, 조정, 돌파',sperandeo:'스페란데오',base_watch:'바닥·눌림 횡보',downtrend_approach:'하락 추세선 근접'}[r]||r)).join(' · '))}</span></td><td>${esc(e.entry_signal?.label || e.reasons?.join(' · '))}${e.route_signals?.downtrend_approach?`<br><a href="#stock/${encodeURIComponent(e.market)}/${encodeURIComponent(e.symbol)}?screen=downtrend_approach">탐지한 하락 추세선 보기 →</a>`:''}</td><td class="number">${pct(e.entry_signal?.pivot_gap_pct)}${e.entry_signal?.trendline?`<span class="stock-code">${num(e.entry_signal.trendline.gap_atr,2)} ATR</span>`:''}</td><td class="cell-note">${esc(e.entry_signal?.pending?.[0] || '조건 유지 확인')}</td></tr>`).join('')}</tbody></table></div>`;
}
function sectorDetail(market, code) {
 const s=DATA.market.sectors.find(s=>s.market===market&&s.group_code===code);if(!s) return missing();const c=s.current||{};const classification=DATA.market.focus?.items.find(x=>x.market===market&&x.code===code)?.classification;
 $('#main').innerHTML='<a class="back" href="#sectors">← 섹터 관찰</a>'+heading(s.group_name,'SECTOR DETAIL',market+' · '+(c.label||'미확인')+' · '+(c.summary||''))+qualityNotice()+block('섹터 가격 추이',`<div class="panel-body"><div class="metrics">${metric('최근 상대강도',pct(c.rs_now))}${metric('거래활동',num(c.activity_now)+'배')}${metric('상승 참여',pct(c.breadth))}${metric('유효 / 확인 구성',num(c.used,0)+' / '+num(c.matched,0))}</div><p class="chart-caption">${classificationNote(classification)}</p><div id="sector-price" class="chart" role="img" aria-label="섹터 동일비중 가격지수"></div><p class="chart-caption">126세션 고정 구성·시작 100. 현행 분류의 과거 재구성으로 당시 구성 백테스트가 아닙니다.</p><div id="sector-rs" class="chart" role="img" aria-label="섹터 최근 상대강도 63세션 추이"></div><p class="chart-caption">RS 비율(%) · KR 대상 전체 동일비중 / US SPY 프록시 대비 · 날짜별 구성 변경 가능</p><ul class="bullet">${(c.issues||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`)+`<div class="details-grid">${block('주도 종목',leaderTable(s.leaders||[],market))}${block('약세 기여 종목',leaderTable(s.weak_contributors||[],market))}</div>`+block('상태 변화',`<div class="panel-body"><ul class="bullet">${(s.transitions||[]).slice(-8).map(t=>`<li>${esc(t.date)} · ${esc(t.from)} → ${esc(t.to)}</li>`).join('')||'<li>기록된 전환 없음</li>'}</ul></div>`);
 WorkbenchCharts.create($('#sector-price'),[{label:'동일비중 지수',data:WorkbenchCharts.lineData(s.horizons?.['126']?.index||[],'value')}]);WorkbenchCharts.create($('#sector-rs'),[{label:'최근 RS (%)',data:WorkbenchCharts.lineData(s.history||[],'rs_now',100)}]);
}
function leaderTable(rows,market){return rows.length?`<div class="scroll"><table><thead><tr><th>종목</th><th>최근 수익률</th><th>추정 거래 비중</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${stockLink(market,r.symbol,r.name)}</td><td>${pct(r.return_recent)}</td><td>${pct(r.proxy_share)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">검증된 구성 자료 없음</div>';}
// Screening explanation only: source signals and their stored values stay unchanged.
const ScreeningGuide = (() => {
 const routes={pdf_momentum:'추세, 조정, 돌파',sperandeo:'스페란데오',base_watch:'바닥·눌림 횡보',downtrend_approach:'하락 추세선 근접'};
 const finite=v=>typeof v==='number'&&Number.isFinite(v);
 const labels={watch:'관찰 시작',ready:'돌파 대기',entry:'진입 조건 충족'};
 const templates={
  pullback_base:{title:'상승 후 눌림 횡보',summary:'먼저 오른 가격이 내려온 뒤, 일정한 범위에서 쉬는 모습입니다.',steps:['선행 상승','눌림','하단 지지 반복'],points:[[0,85],[.07,74],[.13,80],[.2,52],[.27,61],[.34,20],[.4,30],[.48,62],[.56,49],[.64,66],[.72,47],[.8,63],[.88,51],[1,55]],zone:[.48,43,1,68]},
  bottom_base:{title:'하락 후 바닥 횡보',summary:'먼저 하락한 가격이 더 밀리지 않고, 하단을 여러 번 확인하는 모습입니다.',steps:['선행 하락','하락 진정','하단 지지 반복'],points:[[0,15],[.08,30],[.15,22],[.22,48],[.3,39],[.39,68],[.47,54],[.55,70],[.63,51],[.71,67],[.8,53],[.89,68],[1,57]],zone:[.43,49,1,72]},
  range_breakout:{title:'조정 범위 형성',summary:'상승 뒤 조정 폭과 거래량이 줄어들며, 조정 구간의 고점 돌파를 준비하는 모습입니다.',steps:['선행 상승','조정 안정화','피벗 돌파 확인'],points:[[0,87],[.08,76],[.16,81],[.24,54],[.31,63],[.4,29],[.48,62],[.55,35],[.63,54],[.71,37],[.79,48],[.88,35],[1,39]],zone:[.4,29,1,64]},
  vcp:{title:'변동성 축소',summary:'큰 조정 다음에 더 작은 조정이 이어지며, 가격의 흔들림이 좁아지는 모습입니다.',steps:['선행 상승','변동폭 축소','피벗 돌파 확인'],points:[[0,90],[.09,74],[.18,80],[.3,26],[.41,76],[.53,27],[.63,56],[.74,27],[.82,43],[.91,29],[1,34]],zone:[.3,26,1,78]},
  cup_handle:{title:'컵 손잡이',summary:'둥글게 조정한 가격이 고점 부근으로 회복한 뒤, 짧게 눌리는 손잡이를 만드는 모습입니다.',steps:['컵 형성','오른쪽 회복','손잡이 → 돌파 확인'],points:[[0,84],[.13,25],[.2,34],[.28,57],[.36,72],[.44,78],[.53,74],[.6,59],[.67,34],[.74,26],[.8,44],[.87,50],[.94,39],[1,34]],zone:[.75,25,1,52]},
  sperandeo:{title:'하락 뒤 1-2-3 전환',summary:'하락선을 넘은 뒤 되밀려도 이전 저점을 지키고, 반등 고점을 다시 넘는지 봅니다.',steps:['S2 하락선 돌파','S3 저점 재시험','S4 반등 고점 돌파'],points:[[0,14],[.1,45],[.21,31],[.32,65],[.41,53],[.52,88],[.66,41],[.78,74],[.88,58],[1,46]],zone:[.65,40,1,77]},
  downtrend_approach:{title:'하락 추세선 근접',summary:'낮아지는 고점들에 맞춘 하락 저항선에 종가가 가까워진 모습입니다. 아직 돌파 전입니다.',steps:['낮아지는 고점','하락 저항선 형성','돌파 전 근접'],points:[[0,16],[.12,50],[.29,36],[.44,75],[.58,56],[.68,89],[.83,73],[.91,93],[1,90]]}
 };
 function options(entry){
  const found=Object.entries(entry?.route_signals||{}).filter(([,s])=>s&&typeof s==='object');
  if(!found.length&&entry?.entry_signal)found.push([entry.entry_signal.route||entry.origins?.[0]||'unknown',entry.entry_signal]);
  return found;
 }
 function template(sig){return templates[sig.route==='sperandeo'?'sperandeo':sig.route==='downtrend_approach'?'downtrend_approach':sig.kind];}
 function shape(sig){
  const t=template(sig);if(!t)return '<p class="sg-empty">이 유형의 설명 도해는 준비되지 않았습니다. 아래 저장 근거를 확인하세요.</p>';
  const x=v=>28+v*570,y=v=>28+v*1.62;
  let points=t.points.map(p=>[...p]);
  if(sig.phase==='entry')points=points.concat([[1.07,14],[1.14,21]]);
  const path=points.map((p,i)=>(i?'L':'M')+x(p[0])+','+y(p[1])).join(' '),end=points.at(-1);
  let guide='';
  if(t.zone){const [a,b,c,d]=t.zone;guide=`<rect class="sg-zone" x="${x(a)}" y="${y(b)}" width="${x(c)-x(a)}" height="${y(d)-y(b)}"/><path class="sg-resistance" d="M${x(a)},${y(b)} H${x(c)}"/>`;}
  if(sig.route==='sperandeo')guide+=`<path class="sg-trend" d="M${x(0)},${y(14)} L${x(.8)},${y(79)}"/>`;
  if(sig.route==='downtrend_approach')guide+=`<path class="sg-trend" d="M${x(0)},${y(16)} L${x(1.06)},${y(89)}"/>${[[0,16],[.29,36],[.58,56],[.83,73]].map(p=>`<circle class="sg-touch" cx="${x(p[0])}" cy="${y(p[1])}" r="5"/>`).join('')}`;
  const dot=[x(end[0]),y(end[1])];
  return `<svg class="sg-shape" viewBox="0 0 710 232" role="img" aria-label="${esc(t.title)} 설명용 가격 흐름. 실제 종목의 과거 차트가 아닙니다."><text x="10" y="16" class="sg-svg-label">가격 흐름</text>${guide}<path class="sg-price" d="${path}"/><circle class="sg-now" cx="${dot[0]}" cy="${dot[1]}" r="6"/><text class="sg-svg-label" text-anchor="end" x="${Math.min(dot[0]+5,685)}" y="${Math.max(dot[1]-17,16)}">${sig.phase==='entry'?'돌파 후 유지':sig.route==='downtrend_approach'?'종가 · 돌파 전':'현재 관찰 단계'}</text><path class="sg-axis" d="M28 205 H682"/><text class="sg-svg-label" text-anchor="end" x="681" y="225">시간 →</text></svg>`;
 }
 function levels(sig,currency){
  const c=sig.latest_close,p=sig.pivot,lo=sig.structure_stop;
  const base=sig.route==='base_watch',down=sig.route==='downtrend_approach';
  const topLabel=base?'횡보 상단':down?'하락 저항선 · 피벗':'돌파 기준 · 피벗';
  const lowLabel=base?'횡보 하단':sig.route==='sperandeo'?'재시험 저점':'구조 저점';
  if(![c,p,lo].every(finite)||Math.max(c,p,lo)===Math.min(c,p,lo))return `<div class="sg-empty">위치 도해에 필요한 저장 가격이 부족합니다.<br>판정 종가 ${money(c,currency)} · 기준선 ${money(p,currency)} · 참고 저점 ${money(lo,currency)}</div>`;
  const max=Math.max(c,p,lo),min=Math.min(c,p,lo),y=v=>40+(max-v)/(max-min)*208;
  const rows=[[p,topLabel,'sg-upper'],[c,'판정 종가','sg-current'],[lo,lowLabel,'sg-lower']].sort((a,b)=>b[0]-a[0]);
  const gap=sig.pivot_gap_pct,risk=sig.structure_risk_pct,above=finite(gap)&&gap<0;
  const target=base?'상단':down?'저항선':'피벗';
  const gapText=finite(gap)?`${above?'↓':'↑'} ${target}${above?' 위':'까지'} ${pct(Math.abs(gap))}`:`${target} 거리 미확인`;
  const riskText=finite(risk)?`↓ 구조 위험 ${pct(risk)}`:'구조 위험 미확인';
  function arrow(x,from,to,cls){
   const a=y(from),b=y(to),dir=Math.sign(b-a),head=Math.min(5,Math.abs(b-a)/3);
   if(!dir)return '';
   return `<path class="sg-distance-arrow ${cls}" d="M${x} ${a} V${b} M${x-4} ${b-dir*head} L${x} ${b} L${x+4} ${b-dir*head}"/>`;
  }
  return `<svg class="sg-level-chart" viewBox="0 0 370 290" role="img" aria-label="${esc(topLabel)} ${money(p,currency)}, 판정 종가 ${money(c,currency)}, ${esc(lowLabel)} ${money(lo,currency)} ${esc(currency)}. ${esc(gapText)}. ${esc(riskText)}는 판정 종가에서 참고 저점까지의 하락 거리입니다."><rect class="sg-zone" x="18" y="${Math.min(y(p),y(lo))}" width="76" height="${Math.abs(y(p)-y(lo))}"/>${arrow(78,c,p,'sg-upper')}${arrow(38,c,lo,'sg-lower')}${rows.map(([value,label,cls],i)=>{const labelY=40+i*104;return `<path class="sg-level-line ${cls}" d="M18 ${y(value)} H94 L124 ${labelY} H137"/><circle class="${cls}" cx="56" cy="${y(value)}" r="${cls==='sg-current'?6:3}"/><text class="sg-svg-label" x="147" y="${labelY-6}">${esc(label)}</text><text class="sg-svg-value" x="147" y="${labelY+17}">${money(value,currency)} <tspan class="sg-svg-label">${esc(currency||'')}</tspan></text>`;}).join('')}<text class="sg-svg-distance sg-distance-up" x="147" y="91">${esc(gapText)}</text><text class="sg-svg-note" x="147" y="109">${above?'판정 종가에서 기준선으로':'판정 종가 → '+target}</text><text class="sg-svg-distance sg-distance-down" x="147" y="195">${esc(riskText)}</text><text class="sg-svg-note" x="147" y="213">판정 종가 → ${esc(lowLabel)}</text></svg>`;
 }
 function relativeStrength(entry){
  const rs=entry.analysis?.rs_percentile,population=entry.analysis?.rs_population;
  if(!finite(rs)||rs<0||rs>100)return '<div class="sg-rs"><span>최근 1년 상대강도</span><strong>비교 자료 미확인</strong></div>';
  const top=100-rs;
  const rank=rs===100?'최상위권':rs===0?'최하위권':rs>=50?(top<.1?'상위 0.1% 미만':`상위 약 ${num(top,1)}%`):(rs<.1?'하위 0.1% 미만':`하위 약 ${num(rs,1)}%`);
  const pool=finite(population)?`같은 시장 비교 대상 ${num(population,0)}종목`:'같은 시장의 비교 대상';
  return `<div class="sg-rs"><div class="sg-rs-heading"><span>최근 1년 상대강도</span><strong>${rank}</strong></div><div class="sg-rs-scale" role="img" aria-label="상대강도 ${rank}. 높은 쪽일수록 비교 종목보다 1년 수익률이 강합니다."><span style="width:${rs}%"></span><i style="left:${rs}%"></i></div><div class="sg-rs-ends"><span>낮음</span><span>높음</span></div><small>${pool}의 252거래일 수익률 비교 · RS ${num(rs,1)}</small></div>`;
 }
 function explanation(sig){
  const m=sig.metrics||{},base=sig.route==='base_watch';
  if(base)return `${m.base_bars?m.base_bars+'봉 동안 ':''}횡보하고, 떨어진 시점에 하단을 반복 확인한 후보입니다. 판정 당시 하단을 지키고 있습니다.`;
  if(sig.route==='sperandeo')return sig.phase==='entry'?'재시험 뒤 반등 고점을 돌파하고 저장된 진입 조건을 충족했습니다. 다음 완료봉에서도 돌파가 유지되는지 확인합니다.':'하락선 돌파 뒤의 저점 재시험이 확인됐습니다. 위쪽 반등 고점을 종가로 넘는 S4는 아직 기다리는 단계입니다.';
  if(sig.route==='downtrend_approach')return `하락 저항선 아래 ${pct(sig.pivot_gap_pct)} (${num(sig.trendline?.gap_atr,2)} ATR)에 접근했습니다. ATR은 최근 하루 변동폭의 기준입니다. 이 유형의 피벗은 판정일의 하락 저항선 값입니다.`;
  if(sig.phase==='entry')return '패턴의 피벗을 돌파한 뒤, 저장된 거래량·시장·이격·구조 위험 조건을 통과한 상태입니다.';
  if(m.preliminary)return '상승 후 조정이 안정되는 초기 모습입니다. 완성된 패턴으로 판정된 것은 아니며, 추가 수축과 패턴 완성을 확인합니다.';
  return sig.phase==='ready'?'조정 패턴이 형성되고 피벗 가까이 왔습니다. 피벗의 종가 돌파와 거래량 확인이 남아 있습니다.':'조정 패턴을 관찰하고 있습니다. 아래 남은 조건을 확인한 뒤 돌파 대기·진입 여부를 판단합니다.';
 }
 function distances(sig){
  const base=sig.route==='base_watch',down=sig.route==='downtrend_approach',gap=sig.pivot_gap_pct,risk=sig.structure_risk_pct;
  const above=finite(gap)&&gap<0;
  const upLabel=above?(base?'상단 위 이격':'피벗 위 이격'):(base?'상단까지 상승 거리':down?'하락 저항선까지 상승 거리':'피벗까지 상승 거리');
  const upValue=finite(gap)?pct(Math.abs(gap)):'미확인';
  const rows=[[upLabel,upValue,above?'기준 가격 위에 있는 정도':'판정 종가 → 위쪽 기준 가격'],[base?'하단까지 하락 거리':'참고 저점까지 하락 거리',pct(risk),'기존 ‘구조 위험’ 수치 · 손실 한도 아님']];
  if(base)rows.push(['상·하단 거리비',finite(sig.range_reward_risk)?num(sig.range_reward_risk,2)+'배':'미확인','상단 여지 ÷ 하단 거리 · 기대수익 아님']);
  return `<div class="sg-distances">${rows.map(([l,v,n])=>`<div><span>${l}</span><strong>${v}</strong><small>${n}</small></div>`).join('')}</div>`;
 }
 function shell(){return `<section id="stock-evidence">${block('스크리닝 해설 · 선별 근거와 다음 확인','<div class="panel-body" id="screening-guide"></div>')}</section>`;}
 function mount(entry,stock,currency){
  const host=document.getElementById('screening-guide');if(!host)return;
  const available=options(entry);
  if(!available.length){host.innerHTML='<p>현재 스크리너 후보가 아닙니다. 직접 검색·계좌·섹터 연결로 열린 종목이며, 표시할 선별 도해가 없습니다.</p>';return;}
  const requested=new URLSearchParams(location.hash.split('?')[1]||'').get('screen');
  let selected=available.some(([r])=>r===requested)?requested:available.find(([,s])=>s.kind===entry.entry_signal?.kind&&s.phase===entry.entry_signal?.phase)?.[0]||available[0][0];
  host.innerHTML=`${available.length>1?`<div class="sg-routes" role="group" aria-label="선별 유형">${available.map(([r])=>`<button type="button" data-guide-route="${esc(r)}">${esc(routes[r]||r)}</button>`).join('')}</div>`:''}<div id="screening-guide-content" aria-live="polite"></div><div id="screening-detected-line"></div><details class="sg-original"><summary>저장된 원문·전체 계산 근거</summary><ul class="bullet">${(entry.reasons||[]).map(r=>`<li>${WorkbenchHelp.text(WorkbenchFormat.priceText(r,currency))}</li>`).join('')}</ul><p class="chart-caption">원문 페이지 ${esc(entry.analysis?.source_pages?.join(', ')||'해당 없음')}</p><pre>${esc(JSON.stringify(WorkbenchFormat.analysis(entry.analysis,currency),null,2))}</pre></details>`;
  function draw(){
   const raw=available.find(([r])=>r===selected)[1];
   // Older entry events may only carry the close in their analysis features.
   const stored=stock.bars?.find(b=>b.date===entry.as_of)?.close;
   const sig={...raw,route:selected,latest_close:finite(raw.latest_close)?raw.latest_close:finite(entry.analysis?.features?.close)?entry.analysis.features.close:stored};
   const t=template(sig),base=selected==='base_watch';
   const pending=sig.pending?.length?sig.pending:sig.phase==='entry'?['다음 완료봉에서도 피벗 위 가격·거래량·구조 유지 확인']:['현재 저장된 후속 확인 조건 없음'];
   const warnings=[...new Set([...(sig.warnings||[]),...(entry.reasons||[]).filter(r=>r==='재무·기관·질적 조건 미검증')])];
   document.getElementById('screening-guide-content').innerHTML=`<div class="sg-heading"><div><span class="sg-route-name">${esc(routes[selected]||selected)}</span><h3>${esc(sig.label||t?.title||'저장 선별 근거')}</h3></div>${tag(labels[sig.phase]||sig.phase||'단계 미확인',sig.phase==='ready'?'warn':'')}</div><p class="sg-intro">${esc(t?.summary||'저장된 선별 조건을 확인합니다.')}</p><div class="sg-visuals"><figure class="sg-pattern"><figcaption>이 유형의 모양 <span>설명용 · 실제 주가 재현 아님</span></figcaption>${shape(sig)}${t?`<ol class="sg-steps">${t.steps.map((s,i)=>`<li><b>${i+1}</b>${esc(s)}</li>`).join('')}</ol>`:''}</figure><figure class="sg-position"><figcaption>이 종목의 위치 <span>${esc(entry.as_of||'기준일 미확인')} 저장값</span></figcaption>${levels(sig,currency)}</figure></div><p class="sg-reading">${esc(explanation(sig))}</p>${distances(sig)}<div class="sg-context">${relativeStrength(entry)}${finite(sig.metrics?.volume_dry_ratio)?`<span>조정 거래량 <strong>${num(sig.metrics.volume_dry_ratio,2)}배</strong><small>최근 5봉 평균 / 직전 50봉 평균</small></span>`:''}${finite(sig.volume_ratio)?`<span>돌파 거래량 <strong>${num(sig.volume_ratio,2)}배</strong><small>돌파 당일 / 직전 50봉 평균</small></span>`:''}</div><div class="sg-followup"><h4>다음에 볼 것</h4><ul>${pending.map(r=>`<li>${WorkbenchHelp.text(WorkbenchFormat.priceText(r,currency))}</li>`).join('')}</ul>${warnings.length?`<h4>함께 볼 주의</h4><ul class="sg-warnings">${warnings.map(r=>`<li>${WorkbenchHelp.text(WorkbenchFormat.priceText(r,currency))}</li>`).join('')}</ul>`:''}${base?'<p class="chart-caption">이 경로는 관찰 후보만 표시합니다. 상단은 목표수익, 하단은 보장된 손절 가격이 아닙니다.</p>':''}</div>`;
   host.querySelectorAll('[data-guide-route]').forEach(b=>{b.classList.toggle('selected',b.dataset.guideRoute===selected);b.setAttribute('aria-pressed',String(b.dataset.guideRoute===selected));});
   const detected=document.getElementById('screening-detected-line');detected.hidden=selected!=='downtrend_approach';
   if(selected==='downtrend_approach')window.dispatchEvent(new Event('resize'));
  }
  host.querySelectorAll('[data-guide-route]').forEach(b=>b.onclick=()=>{selected=b.dataset.guideRoute;draw();});draw();
 }
 return {routes,shell,mount,shape,levels,relativeStrength};
})();

async function stockDetail(market, symbol, version) {
 $('#main').innerHTML='<div class="loading">저장 가격 자료를 여는 중…</div>';
 const response=await fetch('/api/stock/'+encodeURIComponent(market+':'+symbol));if(!response.ok)throw Error('price_request_failed');const s=await response.json();if(version!==routeVersion)return;
 const currency=s.currency || ({KR:'KRW',US:'USD'})[market];
 const e=DATA.market.entries.find(e=>e.market===market&&e.symbol===symbol);const sig=e?.entry_signal;const bars=s.bars||[], last=bars.at(-1);
 $('#main').innerHTML=`<div class="stock-detail"><header class="stock-heading"><div class="stock-identity"><a class="back" href="#home">← 작업대</a><div class="stock-title"><h1>${esc(symbol.replace(/\.(KS|KQ)$/,''))}</h1><h2>${esc(s.name||symbol)}</h2></div><p class="sub">${esc(market)} / ${esc(symbol)} · ${esc(e?phase[e.phase]:'현재 스크리너 후보 아님')}</p></div><div class="stock-quote"><strong id="stock-quote-price">${money(last?.close,currency)}</strong><span>${esc(currency)} · <span id="stock-quote-date">스크리닝 가격 ${esc(last?.date||'미확인')}</span></span></div></header><nav class="stock-section-nav" aria-label="종목 상세 영역"><button data-section="stock-price" class="selected">차트</button><button data-section="financials">재무정보</button><button data-section="stock-evidence">스크리닝 해설</button><button data-section="qualitative-report">정성 리포트</button><span>저장 자료 · ${esc(currency)}</span></nav><details class="stock-data-status"><summary>${esc(marketStatus())} · 기준일과 수집 내역</summary>${qualityNotice()}${e?.display_cap?`<p class="chart-caption">저장 시가총액 ${esc(capText(e))} · 기준 ${stamp(e.display_cap.as_of)} · 가격×발행주식수 파생값</p>`:''}</details><section class="stock-chart-panel" id="stock-price" aria-label="가격과 거래량"><div class="stock-chart-toolbar"><button id="stock-tools-toggle" aria-expanded="false" aria-controls="chart-workspace"><span aria-hidden="true">✎</span> 작도 · SMA</button><div class="chart-toolbar" id="timeframe-controls" aria-label="봉 주기"><button data-frame="1d" class="selected">일봉</button><button data-frame="1w">주봉</button><button data-frame="1mo">월봉</button></div><div class="chart-toolbar" id="history-range-controls" aria-label="차트 기간"><button data-range="3">3개월</button><button data-range="6" class="selected">6개월</button><button data-range="12">1년</button><button data-range="60">5년</button><button data-range="all">전체 이력</button></div><button id="stock-chart-expand" aria-pressed="false"><span aria-hidden="true">⛶</span> 차트 확대</button></div><div id="chart-workspace" hidden></div><div class="stock-plot-wrap"><div id="price-chart" class="chart large" role="img" aria-label="저장 OHLC 가격과 거래량 차트"></div><div id="price-legend" class="chart-legend stock-price-legend">차트 위에서 날짜별 수치를 확인하세요.</div></div><div class="stock-chart-foot"><p id="history-price" class="chart-caption"></p><details class="history-info"><summary>가격 이력 · 품질 · 기준일</summary><p id="history-status" role="status" class="chart-caption">장기 가격 이력 준비 중…</p><details id="history-quality" class="chart-caption" hidden><summary>자료 품질 상세</summary><p></p></details><p class="chart-caption">스크리닝 원본 ${bars.length}개 일봉 · 수집 ${stamp(s.collected_at)} · 거래량은 공급자 조정 기준. 피벗·무효화 가격은 저장 관찰값이며 주문 기준이 아닙니다.</p></details><button id="history-retry" class="history-retry" hidden>장기 이력 재시도</button></div><div class="metrics stock-reference-metrics">${metric('스크리닝 당시 종가',money(last?.close,currency)+' '+(currency||''))}${metric(sig?.trendline?'판정일 하락선':'관찰 피벗',money(sig?.pivot,currency))}${metric('피벗까지',pct(sig?.pivot_gap_pct))}${metric('구조 무효화 참고',money(sig?.structure_stop,currency))}</div></section>`+WorkbenchFinancials.shell()+`${ScreeningGuide.shell()}<div class="placeholder-grid" id="qualitative-report">${block('정성 리포트',`<div class="empty">${tag('미준비','neutral')}<strong>연결된 검증 리포트가 없습니다.</strong>사업·촉매·위험·반증 근거<br>공시·IR 인용과 사용자 판단을 분리해 연결합니다.</div>`)}</div></div>`;
 document.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>{document.getElementById(b.dataset.section)?.scrollIntoView({behavior:'smooth',block:'start'});document.querySelectorAll('[data-section]').forEach(x=>x.classList.toggle('selected',x===b));});
 $('#stock-tools-toggle').onclick=()=>{const host=$('#chart-workspace');host.hidden=!host.hidden;$('#stock-tools-toggle').setAttribute('aria-expanded',String(!host.hidden));};
 $('#stock-chart-expand').onclick=()=>{const panel=$('#stock-price'),expanded=panel.classList.toggle('expanded');$('#stock-chart-expand').setAttribute('aria-pressed',String(expanded));$('#stock-chart-expand').innerHTML=expanded?'⛶ 기본 크기':'⛶ 차트 확대';};
 ScreeningGuide.mount(e,s,currency);
 WorkbenchFinancials.mount({market,symbol,isCurrent:()=>version===routeVersion});
 const downtrend=e?.route_signals?.downtrend_approach;
 if(downtrend){
  const line=downtrend.trendline;
  const host=document.createElement('section');host.id='downtrend-evidence';host.className='block';
  host.innerHTML=`<h3>실제 탐지 추세선 · 저장 일봉</h3><div><p>판정일 ${esc(e.as_of)} · 종가 ${money(downtrend.latest_close,currency)} · 연장선 ${money(line.value_latest,currency)} ${esc(currency)} · 거리 ${num(line.gap_atr,2)} ATR / ${pct(downtrend.pivot_gap_pct)}</p><p class="sub">${line.algorithm?.startsWith('trendln')?'고점 회귀선':'저장 고점 연결선'} ${esc(line.anchor_a_date)} (${money(line.anchor_a_price,currency)}) → ${esc(line.anchor_b_date)} (${money(line.anchor_b_price,currency)}) · 확정 ${esc(line.confirmed_at)} · ${line.trend_bars}거래일 조정</p><div id="downtrend-chart" aria-label="자동 탐지 하락 추세선과 종가" style="height:310px"></div><p class="chart-caption">주황 실선: ${line.algorithm?.startsWith('trendln')?'고점 3개 이상 회귀':'저장 고점 연결'} · 점선: 판정일까지 연장 · 일봉/선형 가격축 · 사용자 작도와 별도인 자동 탐지 근거입니다. ${esc(downtrend.warnings.join(' '))}</p></div>`;
  $('#screening-detected-line').append(host);
  const history=bars.filter(b=>b.date>=line.anchor_a_date&&b.date<=e.as_of);
  const bi=history.findIndex(b=>b.date===line.anchor_b_date);
  if(bi>=0)WorkbenchCharts.create($('#downtrend-chart'),[
   {type:'candles',label:'가격',currency,data:history.map(b=>({time:b.date,open:b.open,high:b.high,low:b.low,close:b.close}))},
   {label:'하락 추세선',currency,color:'#e5aa53',data:[{time:line.anchor_a_date,value:line.anchor_a_price},{time:line.anchor_b_date,value:line.anchor_b_price}]},
   {label:'점선 연장',currency,color:'#e5aa53',options:{lineStyle:2},data:history.slice(bi).map((b,i)=>({time:b.date,value:line.anchor_b_price+line.slope_per_bar*i}))}
  ],{height:310});
 }
 const analysis=await WorkbenchAnalysis.initial({market,symbol});
 if(analysis&&analysis.case.stock_key!==market+':'+symbol)throw Error('분석건 종목이 일치하지 않습니다.');
 const controller=await WorkbenchStockChart.mount({stock:s,market,symbol,currency,isCurrent:()=>version===routeVersion,context:analysis?.case.context});
 if(version===routeVersion)await WorkbenchAnalysis.mount({market,symbol,currency,controller,isCurrent:()=>version===routeVersion,initialDetail:analysis});
 if(version===routeVersion&&window.WorkbenchTradeCycle)WorkbenchTradeCycle.mountStock({market,symbol});
 if(version===routeVersion&&window.WorkbenchScenarios)await WorkbenchScenarios.mountStock({market,symbol,currency,controller,isCurrent:()=>version===routeVersion});
}
function accountWorkspace(title='나의 계좌',withAccounts=true) {return `<div id="risk-account" data-title="${esc(title)}">${block(title,withAccounts?accountCards():'<div class="panel-body">저장된 계획을 확인합니다.</div>')}</div>`;}
function accounts() {$('#main').innerHTML=heading('나의 계좌','READ-ONLY ACCOUNTS','생성한 국내·미국 계좌 예시입니다. 실제 잔고와 연결되지 않습니다.')+accountWorkspace('계좌 정보와 계획')+`<div class="notice"><span>조회 시도 ${stamp(DATA.accounts.attempted_at)} · 데모 기준일은 고정입니다. 브라우저 새로고침 후에도 저장한 기록은 유지됩니다.</span></div>`;}
function accountDetail(alias){
 const a=DATA.accounts.accounts.find(a=>a.alias===alias);if(!a)return missing();
 $('#main').innerHTML='<a class="back" href="#accounts">← 나의 계좌</a>'+heading('계좌 상세','ACCOUNT / READ ONLY',alias+' · 잔고 기준 '+stamp(DATA.accounts.as_of))+accountWorkspace('계좌 자산 구성',false)+block('보유 주식',`<div class="scroll"><table><thead><tr><th>종목</th><th>수량</th><th>관측 가격</th></tr></thead><tbody>${a.positions.filter(WorkbenchRisk.isEquity).map(p=>`<tr><td>${stockLink(p.market,p.quote_symbol||p.symbol,p.name)}</td><td>${num(p.quantity)}</td><td>${money(p.price,p.currency)} ${esc(p.currency)}</td></tr>`).join('')}</tbody></table></div>`);
}
async function route(){const version=++routeVersion;if(!await WorkbenchWorkspace.closeActive()){history.replaceState(null,'',lastRouteHash||'#home');return;}if(version!==routeVersion)return;lastRouteHash=location.hash;WorkbenchFinancials.close();WorkbenchCharts.clear();const [page,...args]=(location.hash.slice(1).split('?')[0]||'home').split('/').map(decodeURIComponent);document.body.classList.toggle('stock-page',page==='stock');$('#main').classList.toggle('cycle-page',page==='cycles'||page==='cycle');document.querySelectorAll('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===page||page==='cycle'&&a.dataset.nav==='cycles'||page==='stock'&&a.dataset.nav==='screen'||page==='account'&&a.dataset.nav==='accounts'||(page==='sector'||page==='focus')&&a.dataset.nav==='sectors'));$('#breadcrumb').textContent=({home:'작업대',screen:'스크리닝',sectors:'섹터 관찰',sector:'섹터 상세',focus:'활동 변화 상세',risk:'가격 위험 경고',cycles:'매매 사이클',cycle:'매매 사이클',accounts:'나의 계좌',account:'계좌 상세',stock:'종목 상세'})[page]||'작업대';try{if(page==='home')home();else if(page==='screen')screen(...args);else if(page==='sectors')sectors();else if(page==='sector')sectorDetail(...args);else if(page==='focus')focusDetail(...args);else if(page==='risk')await WorkbenchTrailing.mount($('#main'));else if(page==='cycles'||page==='cycle')await WorkbenchTradeCycle.mount($('#main'),{page,args,isCurrent:()=>version===routeVersion});else if(page==='accounts')accounts();else if(page==='account'){accountDetail(...args);WorkbenchAnalysis.account(args[0],$('#main'));}else if(page==='stock')await stockDetail(...args,version);else missing();if(['home','accounts','account'].includes(page)&&window.WorkbenchRisk){const host=$('#risk-account');if(host)WorkbenchRisk.mount(host,{accountAlias:page==='account'?args[0]:null,title:host.dataset.title,accountHTML:page==='account'?'':accountCards(),showAccountLink:page==='home',accountBundle:DATA.accounts});}if(page==='stock'&&new URLSearchParams(location.hash.split('?')[1]||'').get('screen')&&$('#stock-evidence'))$('#stock-evidence').scrollIntoView({block:'start'});else window.scrollTo(0,0);}catch(e){$('#main').innerHTML='<div class="empty"><strong>화면 자료를 표시하지 못했습니다.</strong>'+esc(e.message)+'<br><a href="#home">작업대로</a></div>';console.error(e);}}
const pp = x => x == null ? '결측' : (x > 0 ? '+' : '') + num(x * 100) + '%p';
const signedPct = x => x == null ? '결측' : (x > 0 ? '+' : '') + pct(x);
function focusLink(s) { return '#focus/'+encodeURIComponent(s.market)+'/'+encodeURIComponent(s.code); }
function focusCard(s) {
 const c=s.current;return `<a class="focus-card ${c.state==='unavailable'?'muted-card':''}" href="${focusLink(s)}"><div class="row"><span class="eyebrow">${esc(s.market)} · ${s.level==='industry'?'세부업종':'대분류'}</span>${tag(c.label,c.state==='building'?'':c.state==='price_weak'?'bad':'neutral')}</div><h3>${esc(s.name)} <span>↗</span></h3><p class="sub">${esc(s.parent||'')} · ${esc(c.date||'기준일 미확인')}</p><div class="focus-numbers"><div><small>거래활동 증가율</small><strong>${signedPct(c.growth)}</strong></div><div><small>증가율의 가속</small><strong>${pp(c.acceleration)}</strong></div></div><p class="focus-evidence">평소 대비 ${num(c.activity_now)}배 · 증가 동참 ${pct(c.participation)}<br>최근 가격 ${signedPct(c.return_recent)} · ${num(c.used,0)}종목 · 활동 계산 ${num(c.activity_used,0)}종목</p><p class="sub">${classificationNote(s.classification)}</p>${c.issues?.length?`<p class="danger-text">${esc(c.issues.join(' · '))}</p>`:''}</a>`;
}
function focusGuide() {
 const c=DATA.market.focus?.settings||{window:5,baseline:20,min_change:.1,min_acceleration:.05,min_participation:.5,min_persistence:3,min_members:5,min_coverage:.8,price_floor:-.005};
 return `<details class="focus-guide"><summary>어떻게 읽나요? · 계산과 상단 노출 기준</summary><div class="guide-grid"><div><strong>① 평소보다 활발한가</strong><p>종목별 하루 거래량 ÷ 그 전 ${c.baseline}일 평균. 업종 대표값은 종목 비율의 중앙값입니다. 1배가 평소 수준입니다.</p></div><div><strong>② 증가 속도가 커졌는가</strong><p>최근 ${c.window}일 평균 ÷ 직전 ${c.window}일 평균 − 1이 증가율입니다. 이 증가율에서 한 구간 전 증가율을 뺀 값이 가속(%p)입니다.</p></div><div><strong>③ 함께 움직이는가</strong><p>거래활동이 늘어난 종목 비율과 지속 일수를 확인합니다. 가격이 하락하면서 거래가 늘면 별도 주의 상태로 표시합니다.</p></div></div><p>상단 기준: 증가율 ${pct(c.min_change)} 이상 · 가속 ${pp(c.min_acceleration)} 이상 · 증가 동참 ${pct(c.min_participation)} 이상 · 최근 ${c.window}일 중 ${c.min_persistence}일 이상 직전 구간 수준 상회 · 시장 내 추정 거래 비중 증가 · 가격 변화 ${pct(c.price_floor)} 이상. 최소 ${c.min_members}종목, 시세·분류 유효 비율 ${pct(c.min_coverage)} 이상입니다.</p><p>세부업종을 먼저, 각 분류 안에서는 가속 → 증가율 순으로 최대 3개를 보여줍니다. 거래 규모·시가총액은 섹터 순위에 쓰지 않습니다. 예: 증가율이 5%→20%면 가속은 +15%p입니다. 평소 아래에서도 개선 중이면 표시할 수 있습니다.</p><p class="sub">이 기준은 수정 가능한 관찰 가정이며 조기 포착·수익성을 검증한 전략이 아닙니다. 거래량과 종가×거래량은 실제 순유입이 아닙니다. 현재 분류로 과거 가격을 묶으므로 당시 구성의 백테스트가 아닙니다.</p></details>`;
}
const classificationDay = value => value ? new Date(value).toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'}) : '';
function classificationNote(c) {
 if(!c)return '';
 if(c.user_defined&&!c.observed_from)return `사용자 지정 기타 ${num(c.user_defined,0)}종목 · 공식 업종 미확인`;
 return `${c.user_defined?'사용자 지정 기타 '+num(c.user_defined,0)+'종목 포함 · ':''}분류 확인 ${esc(classificationDay(c.observed_from)||'미확인')}${classificationDay(c.observed_until)!==classificationDay(c.observed_from)?' ~ '+esc(classificationDay(c.observed_until)||''):''}${c.source_as_of?' · 분류 기준 '+esc(c.source_as_of):''}${c.refresh_due?' · 갱신 필요 '+num(c.refresh_due,0)+'종목 (기존 분류 사용)':''}`;
}
function focusCoverage(market) {
 const m=DATA.market.focus?.markets?.[market];if(!m)return '계산 자료 미준비';
 return `${market} 분류 ${num(m.matched,0)}/${num(m.target,0)}종목 · ${pct(m.classification_coverage)}${market==='KR'?' · 합성 예시 분류':''} · ${classificationNote(m.classification)}`;
}
function focusPanel() {
 return block('관심이 늘어나는 곳',`<div class="panel-body"><div class="chart-toolbar"><select id="focus-home-market" aria-label="관심 업종 시장"><option value="KR">한국</option><option value="US">미국</option></select><span class="sub">거래 규모보다 변화의 속도 · 시장별 최대 3개</span></div><div id="focus-home-results"></div><p class="chart-caption">한국은 합성 예시 분류, 미국은 확인된 섹터·세부업종을 관찰합니다. 분류 기준일 또는 근거 확인일이 7일을 넘으면 갱신 필요로 표시하며 기존 분류를 사용합니다.</p>${focusGuide()}</div>`,'<a href="#sectors">전체 섹터·업종 보기 →</a>');
}
function bindHomeFocus() {
 const f=DATA.market.focus,sel=$('#focus-home-market');
 sel.value=f?.markets?.KR?.selected.length?'KR':f?.markets?.US?.selected.length?'US':'KR';
 const render=()=>{const m=sel.value,info=f?.markets?.[m],rows=(f?.items||[]).filter(s=>s.market===m&&info?.selected.includes(s.code));rows.sort((a,b)=>info.selected.indexOf(a.code)-info.selected.indexOf(b.code));$('#focus-home-results').innerHTML=(rows.length?`<div class="focus-card-row">${rows.map(focusCard).join('')}</div>`:`<div class="focus-empty"><strong>${info?.classification_coverage<.8?'분류 자료 부족으로 판단을 보류합니다.':!info?'계산 자료 미준비':'지금 기준을 충족한 섹터가 없습니다.'}</strong><p>전체 목록에서 변화와 미충족 근거를 볼 수 있습니다.</p></div>`)+`<p class="chart-caption">${focusCoverage(m)} · ${info?.classification_coverage<.8?'판정 보류':'조건 충족 '+num(info?.qualifying,0)+'개'}</p>`;};sel.onchange=render;render();
}
function capText(e) {const c=e.display_cap;return c?.value==null?'시총 미확인':money(c.value,c.currency)+' '+c.currency;}
function screenPreview(market) {
 const groups=(DATA.market.screen_groups||[]).filter(g=>g.market===market);
 return `<div class="screen-previews">${groups.map(g=>`<section class="screen-preview"><div class="row"><h3>${esc(ScreeningGuide.routes[g.route]||g.label)}</h3><a class="text-link" href="#screen/${market}/${g.route}">전체 ${num(g.count,0)}개 →</a></div>${g.top.map(symbol=>{const e=DATA.market.entries.find(e=>e.market===market&&e.symbol===symbol);const sig=e.route_signals?.[g.route]||e.entry_signal;return `<div class="preview-stock"><div class="row">${stockLink(e.market,e.symbol,e.name,g.route)}${tag(phase[sig?.phase||e.phase]||'관찰')}</div><div class="preview-cap">${esc(capText(e))}</div><p class="sub">${esc(sig?.label||'저장 근거 확인')}</p></div>`;}).join('')||'<p class="sub">시총이 확인된 후보 없음</p>'}<p class="chart-caption">시총 내림차순 · 결측 ${num(g.missing_cap,0)}개는 전체 목록에서 확인</p></section>`).join('')}</div>`;
}
function home() {
 $('#main').innerHTML=heading('오늘의 투자 작업대','CHANGES BEFORE SIZE.','평소와 달라지는 거래활동부터 살펴보세요.')+`<details class="compact-quality"><summary>${esc(marketStatus())} · 기준일과 수집 상세</summary>${qualityNotice()}</details>`+accountWorkspace()+focusPanel()+block('유형별 스크리닝',`<div class="panel-body"><div class="chart-toolbar"><label for="preview-market">시장</label><select id="preview-market"><option value="KR">한국 · KRW</option><option value="US">미국 · USD</option></select><span class="sub">유형별 시총 상위 3개 · 전체 고유 후보 ${num(DATA.market.candidate_count,0)}개 · 진입 조건 충족 ${num(DATA.market.entry_candidate_count,0)}개</span></div><div id="screen-preview"></div><p class="chart-caption">저장 시총 기준입니다. 유형 간 중복 종목은 각 유형에 남으며, 시총 순서는 매수 우선순위가 아닙니다.</p></div>`,'<a href="#screen">전체 후보 보기 →</a>');
 bindHomeFocus();const select=$('#preview-market');select.onchange=()=>$('#screen-preview').innerHTML=screenPreview(select.value);select.onchange();
}
function screen(initialMarket='',initialRoute='') {
 $('#main').innerHTML=heading('스크리닝','SCREENING','시총은 시장별로 정렬합니다. 원래의 후보·선별 근거는 보존합니다.')+qualityNotice()+block('저장된 전체 후보',`<div class="filters"><input id="search" aria-label="종목 검색" placeholder="종목명 또는 코드 검색"><select id="market-filter" aria-label="시장"><option value="">전체 시장</option><option>KR</option><option>US</option></select><select id="route-filter" aria-label="스크리닝 유형"><option value="">전체 유형</option><option value="pdf_momentum">추세, 조정, 돌파</option><option value="sperandeo">스페란데오 S3·S4</option><option value="base_watch">바닥·눌림 횡보</option><option value="downtrend_approach">하락 추세선 근접</option></select><select id="phase-filter" aria-label="관찰 단계"><option value="">전체 단계</option><option value="watch">관찰 시작</option><option value="ready">돌파 대기</option><option value="entry">진입 조건 충족</option></select><span id="screen-count"></span></div><div id="screen-table"></div><p class="mini-note">하락 추세선 근접: trendln · 최근 60거래일의 확정 고점 3개 이상 회귀 · 10거래일 이상 추세 · 최근 접촉 20거래일 이내 · 종가가 선 아래 ${num(DATA.market.downtrend_settings?.approach_atr??.5,2)} ATR14 이내 · 과거 종가 돌파선 제외. 관찰 후보이며 매수 신호가 아닙니다.<br>시장별 시총 내림차순 · 결측은 각 시장 끝에 표시 · 통화 간 환산 순위 없음. 시총 기준일은 종목 상세에서 확인합니다.</p>`);
 $('#market-filter').value=['KR','US'].includes(initialMarket)?initialMarket:'';$('#route-filter').value=initialRoute;
 const render=()=>{const q=$('#search').value.toLowerCase(),m=$('#market-filter').value,r=$('#route-filter').value,p=$('#phase-filter').value;const rows=DATA.market.entries.filter(e=>(!m||e.market===m)&&(!r||e.origins?.includes(r))&&(!p||(r?e.route_signals?.[r]?.phase:e.phase)===p)&&((e.name||'')+' '+e.symbol).toLowerCase().includes(q));rows.sort((a,b)=>a.market.localeCompare(b.market)||(a.display_cap?.value==null)-(b.display_cap?.value==null)||(b.display_cap?.value||0)-(a.display_cap?.value||0)||a.symbol.localeCompare(b.symbol));$('#screen-count').textContent=rows.length+'종목';$('#screen-table').innerHTML=screeningTable(r?rows.map(e=>({...e,phase:e.route_signals?.[r]?.phase||e.phase,entry_signal:e.route_signals?.[r]||e.entry_signal})):rows);};['#search','#market-filter','#route-filter','#phase-filter'].forEach(id=>$(id).oninput=render);render();
}
function sectors() {
 $('#main').innerHTML=heading('섹터·업종 관찰','ACTIVITY DELTA','평소 대비 활동의 증가율과 가속을 비교합니다.')+qualityNotice()+focusGuide()+block('전체 관찰 목록',`<div class="filters"><select id="focus-market" aria-label="섹터 시장"><option value="">전체 시장</option><option>KR</option><option>US</option></select><select id="focus-level" aria-label="분류 수준"><option value="">대분류·세부업종</option><option value="industry">세부업종</option><option value="sector">대분류</option></select><select id="focus-state" aria-label="관찰 상태"><option value="">전체 상태</option><option value="building">관심 증가 가속</option><option value="price_weak">활동 증가·가격 약세</option><option value="slowing">증가 속도 둔화</option><option value="watch">조건 일부 미충족</option><option value="unavailable">자료 확인 필요</option></select><input id="focus-search" aria-label="업종 검색" placeholder="섹터·업종 이름 검색"><span id="focus-count"></span></div><p class="mini-note">${focusCoverage('KR')}<br>${focusCoverage('US')}</p><div id="focus-list" class="sector-tiles focus-list"></div>`);
 const render=()=>{const rows=(DATA.market.focus?.items||[]).filter(s=>(!$('#focus-market').value||s.market===$('#focus-market').value)&&(!$('#focus-level').value||s.level===$('#focus-level').value)&&(!$('#focus-state').value||s.current.state===$('#focus-state').value)&&(s.name+' '+(s.parent||'')).toLowerCase().includes($('#focus-search').value.toLowerCase()));rows.sort((a,b)=>(a.current.state==='unavailable')-(b.current.state==='unavailable')||(b.current.acceleration??-1e100)-(a.current.acceleration??-1e100)||a.name.localeCompare(b.name));$('#focus-count').textContent=rows.length+'개';$('#focus-list').innerHTML=rows.map(focusCard).join('')||'<div class="empty">해당하는 저장 관찰 결과 없음</div>';};['#focus-market','#focus-level','#focus-state','#focus-search'].forEach(id=>$(id).oninput=render);render();
}
function focusDetail(market,code) {
 const s=DATA.market.focus?.items.find(s=>s.market===market&&s.code===code);if(!s)return missing();const c=s.current;
 $('#main').innerHTML='<a class="back" href="#sectors">← 전체 섹터·업종</a>'+heading(s.name,'ACTIVITY DETAIL',market+' · '+(s.parent||'대분류')+' · '+c.label)+qualityNotice()+block('관찰 근거',`<div class="panel-body"><div class="metrics">${metric('거래활동 증가율',signedPct(c.growth))}${metric('증가율의 가속',pp(c.acceleration))}${metric('평소 대비 활동',num(c.activity_now)+'배')}${metric('활동 증가 동참',pct(c.participation))}</div><p>이전 ${num(c.activity_earlier)}배 → 직전 ${num(c.activity_previous)}배 → 최근 ${num(c.activity_now)}배<br>이전 증가율 ${signedPct(c.previous_growth)} → 최근 증가율 ${signedPct(c.growth)} · 최근 가격 ${signedPct(c.return_recent)}</p><p class="sub">직전 수준을 넘은 날 ${num(c.persistent_days,0)}일 · 시장 내 추정 거래 비중 변화 ${signedPct(c.share_change)} · 상위3종목 거래 비중 ${pct(c.top3_share)}<br>${classificationNote(s.classification)}<br>시세 유효 ${num(c.used,0)} / 분류 ${num(c.matched,0)} · 활동 계산 ${num(c.activity_used,0)}종목 · 최근 구간 ${esc(c.current_start)} ~ ${esc(c.date)}</p>${c.issues?.length?`<div class="notice error">${esc(c.issues.join(' · '))}</div>`:''}${Object.keys(c.activity_exclusions||{}).length?`<details><summary>거래활동 계산 제외 ${Object.keys(c.activity_exclusions).length}종목 · 나머지 유효 자료로 계산</summary><p>비교 구간 내 동일한 구성으로 계산합니다. 설정된 최소 종목 수와 유효 비율을 충족할 때만 판정합니다. 기준은 아래 계산 설명에서 확인할 수 있습니다.</p><ul>${Object.entries(c.activity_exclusions).map(([symbol,reason])=>`<li>${esc(symbol)} · ${reason==='comparison_activity_zero'?'비교 구간 거래활동 0으로 증가율 계산 불가':'평균 거래량 0으로 상대 거래량 계산 불가'}</li>`).join('')}</ul></details>`:''}<div class="focus-checks">${(c.checks||[]).map(k=>`<span>${tag(k.passed===true?'충족':k.passed===false?'미충족':'결측',k.passed===true?'':'neutral')} ${esc(k.label)}</span>`).join('')}</div><div id="focus-activity" class="chart" role="img" aria-label="평소 대비 거래활동 추이"></div><p class="chart-caption">평소 대비 거래활동(배) · 1배 기준 · 날짜별 유효 구성이 바뀔 수 있습니다.</p><div id="focus-delta" class="chart" role="img" aria-label="거래활동 증가율과 가속 추이"></div><p class="chart-caption">증가율(%)과 가속(%p)을 구분해 읽으세요. 실제 순유입 금액을 뜻하지 않습니다.</p>${focusGuide()}</div>`)+block('활동 증가에 기여한 종목',`<div class="scroll"><table><thead><tr><th>종목</th><th>활동 증가율</th><th>평소 대비</th><th>최근 가격</th></tr></thead><tbody>${s.leaders.map(r=>`<tr><td>${stockLink(market,r.symbol,r.name)}</td><td>${signedPct(r.growth)}</td><td>${num(r.activity)}배</td><td>${signedPct(r.return_recent)}</td></tr>`).join('')||'<tr><td colspan="4">유효 종목 자료 없음</td></tr>'}</tbody></table></div>`)+(s.level==='sector'?block('기존 섹터 지표',`<div class="panel-body"><a class="text-link" href="#sector/${encodeURIComponent(market)}/${encodeURIComponent(code)}">가격·상대강도·상태 변화 상세 →</a></div>`):'');
 const usable=(s.history||[]).map(r=>r.state==='unavailable'?{date:r.date}:r);
 WorkbenchCharts.create($('#focus-activity'),[{label:'거래활동 (배)',data:WorkbenchCharts.lineData(usable,'activity_now')},{label:'평소 1배',data:usable.map(r=>({time:r.date,...(r.activity_now==null?{}:{value:1})})),color:'#9da8a1'}]);
 WorkbenchCharts.create($('#focus-delta'),[{label:'증가율 (%)',data:WorkbenchCharts.lineData(usable,'growth',100)},{label:'가속 (%p)',data:WorkbenchCharts.lineData(usable,'acceleration',100),color:'#c39345'}]);
}

fetch('/api/home').then(r=>{if(!r.ok)throw Error('dashboard_load_failed');return r.json();}).then(d=>{DATA=d;mountStockSearch();window.addEventListener('hashchange',route);route();pollDaily();WorkbenchExclusions.mount($('#market-exclusions'),async()=>{if(!await WorkbenchWorkspace.closeActive())return false;const r=await fetch('/api/home');if(!r.ok)throw Error('home_unavailable');DATA=await r.json();await route();return true;});}).catch(e=>{$('#main').textContent='저장 자료 불러오기 실패: '+e.message;});

$('.skip').onclick = event => { event.preventDefault(); $('#main').focus(); };

async function pollDaily() {
 if(pollDaily.controller)return pollDaily.controller;
 const host=$('#daily-update');
 let latest=null,timer=null,checking=null,submitting=false,applying=false,stopped=false;
 let publishedKey='',offeredKey='',rendered='',statusNote='',statusFailed=false;
 const stages={starting:'저장 자료 준비',accounts:'계좌 잔고 갱신',calendar:'완료 거래일 확인',inputs:'입력 확인',collection:'종가 자료 수집',adr_metadata:'ADR 시총·업종 보강',nh_fallback:'실패 종목 보완',quality:'자료 검사',stock_screen:'스크리닝 계산',sector_screen:'섹터 계산',priority_review:'후보 선별',charts:'차트 저장',sector_states:'섹터 상태 계산',report:'보고서 저장',finished:'화면 준비',waiting:'다른 실행을 기다리는 중'};
 const errors={basis_document_changed_review_required:'실행 당시 Toss 공식 문서 변경이 감지되어 시장 갱신을 중단했습니다. 문서 변경 검토가 반영된 뒤 다시 시도할 수 있습니다.',basis_document_refresh_unavailable:'Toss 공식 기준 문서를 확인하지 못해 시장 갱신을 중단했습니다. 연결 상태를 확인한 뒤 다시 시도해주세요.',basis_document_not_current:'Toss 가격·거래량 기준 문서의 최신 확인이 필요합니다.',market_authentication_http_403_ip_not_allowed:'Toss가 현재 접속 IP를 허용하지 않았습니다(403). Toss Open API 설정에서 허용 IP를 확인해주세요.',market_http_403_ip_not_allowed:'Toss가 현재 접속 IP를 허용하지 않았습니다(403). Toss Open API 설정에서 허용 IP를 확인해주세요.',market_authentication_http_403:'Toss 인증 거절(403). API 접근 확인이 필요합니다.',market_http_403:'Toss가 시장 자료 접근을 거절했습니다(403). Toss Open API 설정에서 허용 IP와 API 접근을 확인해주세요.',market_listing_unavailable:'거래 대상 종목 목록을 받지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해주세요.',previous_launch_interrupted:'이전 실행이 끝나기 전에 중단됐습니다.',market_worker_failed:'시장 자료 수집 작업이 오류로 종료됐습니다. 아래 버튼으로 다시 시도할 수 있습니다.',daily_update_unavailable:'현재 서버에서 갱신을 시작할 수 없습니다. 바탕화면의 투자 대시보드를 다시 열어주세요.',dashboard_preparation_failed:'시장 처리 후 화면 자료를 준비하지 못했습니다. 아래 버튼으로 다시 시도할 수 있습니다.'};
 const busy=u=>!!u&&(!u.view_ready||u.state==='running');
 const key=u=>JSON.stringify([u.attempt_id||u.started_at||u.day,u.revision,u.finished_at,u.display_updated]);
 function schedule(){
  clearTimeout(timer);
  if(!stopped&&!submitting)timer=setTimeout(check,busy(latest)?3000:10000);
 }
 function button(parent,id,label,action,disabled=false){
  const b=document.createElement('button');b.id=id;b.type='button';b.textContent=label;b.disabled=disabled;b.onclick=action;parent.append(b);
 }
 function render(){
  const u=latest,signature=JSON.stringify([u,submitting,applying,publishedKey,statusNote,statusFailed]);
  if(signature===rendered)return;rendered=signature;
  if(u&&!u.enabled&&!statusNote){host.hidden=true;return;}
  const open=host.querySelector?.('details')?.open;
  host.hidden=false;
  const running=u?.state==='running',preparing=!!u&&!u.view_ready&&!running,locked=busy(u),bad=u&&(['failed','interrupted'].includes(u.state)||['failed','partial'].includes(u.account_state));
  host.classList.toggle('error',!!bad||statusFailed);
  if(u?.enabled){
   const label=u.observe_only&&!running?'저장 결과 · 새 수집 결과 자동 반영':running?'오늘의 종가·계좌 갱신 중':({succeeded:'오늘의 갱신 완료',partial:'오늘의 갱신 완료 · 일부 자료 결측',failed:'오늘의 갱신에 오류가 있습니다',interrupted:'오늘의 갱신이 중단됐습니다'})[u.state]||'오늘의 저장 결과';
   const reason=u.observe_only?'저장 자료 조회':u.manual_retry?'수동 재시도':u.reused?'오늘 기록 재사용':'하루 첫 실행';
   const ipDenied=['market_authentication_http_403_ip_not_allowed','market_http_403_ip_not_allowed'].includes(u.error);
   const display=u.display_updated?'이후 저장 결과 표시':u.display_stale?'이전 저장 자료 표시':'';
   const p=u.progress;
   const accountError=u.account_error ? (/IGW40043|IGW40044/.test(u.account_error)?'NH 인증 복구 후에도 토큰이 거절됐습니다.':/http_400/.test(u.account_error)?'NH가 계좌 요청을 거절했습니다.':'계좌 갱신을 완료하지 못했습니다.')+' ('+u.account_error+')' : '';
   host.innerHTML='<details '+((open??(running||bad))?'open':'')+'><summary><strong>'+esc(label)+'</strong> · '+reason+(u.account_attempted?' · 계좌 '+({received:'갱신 완료',partial:'일부 조회 실패',failed:'조회 실패'}[u.account_state]||'확인 중'):'')+(ipDenied?' · Toss 허용 IP 거절403':u.error==='market_authentication_http_403'?' · Toss 인증403':'')+(display?' · '+display:'')+' · 상세</summary><span>'+esc(u.day)+' · 목표 종가 KR '+esc(u.targets?.KR||'확인 중')+' / US '+esc(u.targets?.US||'확인 중')+'<br>'+(preparing?'저장 화면 준비 중 · 시장 처리는 종료됨':running?esc(stages[u.stage]||'갱신 중')+(p?.requested?' · '+num(p.completed,0)+' / '+num(p.requested,0)+'종목':''):(u.manual_retry?'수동 재시도 처리':u.reused?'오늘 실행 기록 재사용 · 수집·스크리닝 재실행 없음':'하루 첫 실행 처리'))+(u.finished_at?' · '+stamp(u.finished_at):'')+(u.error?'<br>'+esc(errors[u.error]||'갱신 중 오류가 발생했습니다. 아래 재시도 버튼과 실제 자료 기준일을 확인하세요.'):'')+(accountError?'<br>'+esc(accountError):'')+(u.display_updated?'<br>갱신 오류 이후에 저장된 결과를 표시합니다. 위 오류는 당시 실행 기록이며 아래 실제 자료 기준일을 확인하세요.':u.display_stale?'<br>갱신 전 저장 결과를 표시합니다. 아래 실제 자료 기준일을 확인하세요.':'')+(bad||u.state==='partial'?'<br>같은 날에는 자동으로 재시도하지 않습니다.':'')+'</span></details>';
  }else host.innerHTML='<strong>갱신 상태 확인 필요</strong>';
  const actions=document.createElement('div');actions.className='daily-actions';
  if(u?.enabled&&!u.observe_only&&(u.retry_available||locked||submitting))button(actions,'daily-retry',submitting?'재시도 요청 중…':running?'갱신 진행 중…':preparing?'화면 준비 중…':'오늘의 갱신 재시도',retry,locked||submitting||statusFailed);
  if(u?.enabled&&u.view_ready&&!running&&key(u)!==publishedKey)button(actions,'daily-result',applying?'결과 여는 중…':'갱신 결과 열기',()=>apply(key(latest)),applying||submitting);
  if(statusNote){const note=document.createElement('span');note.className='daily-message';note.setAttribute?.('role','status');note.textContent=statusNote;actions.append(note);}
  if(statusFailed)button(actions,'daily-status-retry','상태 다시 확인',check,submitting);
  host.append(actions);
 }
 async function apply(expected){
  if(applying||busy(latest)||!latest?.view_ready||key(latest)!==expected)return;
  applying=true;offeredKey=expected;render();
  try{
   const r=await fetch('/api/home');if(!r.ok)throw Error();
   const next=await r.json();
   if(busy(latest)||key(latest)!==expected||!await WorkbenchWorkspace.closeActive())return;
   DATA=next;publishedKey=expected;await route();
  }catch{statusNote='갱신 결과를 열지 못했습니다. 현재 화면은 유지됩니다. 결과 열기를 다시 눌러주세요.';}
  finally{applying=false;render();}
 }
 async function accept(u){
  latest=u;render();
  if(u.enabled&&u.view_ready&&!busy(u)&&key(u)!==publishedKey&&key(u)!==offeredKey&&WorkbenchWorkspace.current?.()?.canAutoRefresh?.()!==false&&!document.activeElement?.matches?.('input,textarea,select,[contenteditable=true]'))await apply(key(u));
 }
 async function check(){
  if(stopped||submitting)return;
  if(checking)return checking;
  clearTimeout(timer);
  checking=(async()=>{
   try{
    const response=await fetch('/api/daily-update');if(!response.ok)throw Error();
    const u=await response.json();
    if(statusFailed){if(statusNote==='갱신 상태를 확인하지 못했습니다. 현재 화면과 마지막 확인 상태를 유지합니다.')statusNote='';statusFailed=false;}
    await accept(u);
   }catch{
    statusFailed=true;statusNote='갱신 상태를 확인하지 못했습니다. 현재 화면과 마지막 확인 상태를 유지합니다.';render();
   }
  })();
  try{await checking;}finally{checking=null;schedule();}
 }
 async function retry(){
  if(submitting||busy(latest)||!latest?.retry_available||statusFailed)return;
  const attempt=latest.attempt_id||latest.started_at;
  submitting=true;statusNote='';clearTimeout(timer);render();
  let refresh=false;
  try{
   // Let an already-running status read finish before accepting the new attempt.
   if(checking)await checking;
   clearTimeout(timer);
   const health=await fetch('/health');if(!health.ok)throw Error('daily_update_unavailable');
   const {token}=await health.json();if(!token)throw Error('daily_update_unavailable');
   const response=await fetch('/api/daily-update',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token},body:JSON.stringify({retry:true,attempt_id:attempt})});
   const u=await response.json();
   if(response.status===409){statusNote='다른 창에서 갱신 상태가 바뀌었습니다. 최신 상태를 확인합니다.';refresh=true;}
   else if(!response.ok)throw Error(u.error||'daily_update_unavailable');
   else{statusFailed=false;await accept(u);}
  }catch(e){
   statusNote=errors[e.message==='daily_retry_unavailable'?'daily_update_unavailable':e.message]||'재시도 요청 결과를 확인하지 못했습니다. 상태를 확인한 뒤 다시 시도하세요.';
   statusFailed=true;refresh=true;
  }finally{
   submitting=false;render();
   if(refresh)await check();else schedule();
  }
 }
 pollDaily.controller={refresh:check,stop(){stopped=true;clearTimeout(timer);}};
 await check();
 return pollDaily.controller;
}

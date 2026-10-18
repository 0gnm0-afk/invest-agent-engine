/* Official financial snapshot UI. No browser-side financial calculations. */
window.WorkbenchFinancials = (() => {
  let active=null;
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // Older running servers may not serve the new help asset until restarted.
  window.WorkbenchHelp ||= {text:esc,term:esc,financial:cell=>cell.formula||cell.reason||cell.label};
  const n=(x,d=2)=>Number(x).toLocaleString('ko-KR',{maximumFractionDigits:d});
  function moneyScale(currency,amount){
    if(currency==='KRW')return {scale:1e8,unit:'억원'};
    const absolute=Math.abs(amount||0);
    return absolute>=1e9?{scale:1e9,unit:'B USD'}:absolute>=1e6?{scale:1e6,unit:'M USD'}:absolute>=1e3?{scale:1e3,unit:'K USD'}:{scale:1,unit:currency};
  }
  function value(cell,currency,scaled=false){
    if(cell?.display_text)return cell.display_text;
    if(cell?.status==='not_applicable')return '업종 비적용';
    if(['unavailable','missing'].includes(cell?.status)&&cell?.value==null)return '자료 미확보';
    if(cell?.value==null)return '—';
    if(cell.kind==='percent')return n(cell.value*100)+'%';
    if(cell.kind==='multiple')return n(cell.value)+'배';
    if(cell.unit?.startsWith(currency)){
      const {scale,unit}=scaled?moneyScale(currency,cell.value):{scale:1,unit:''};
      return WorkbenchFormat.money(cell.value/scale,currency)+(unit?' '+unit:'');
    }
    return n(cell.value,cell.unit==='shares'?0:2);
  }
  const periodMetrics=[['sales','매출 / 영업수익'],['operating','영업이익'],['income','지배주주 순이익'],['eps','EPS']];
  const estimateIds=new Set(['forward_pe','forwardpe','peg','target_price','price_target','recommendation','eps_next_y','eps_next_q','eps_next_5y','eps_this_y','eps_surprise','sales_surprise','earnings_surprise']);
  const growthIds=key=>['growth_1','growth_4','ttm_growth','cagr3','cagr5'].map(suffix=>key+'_'+suffix);
  const summaryGroups=[
    ['실적 · 가치',['sales','operating','income','eps','annual_eps','latest_quarter_eps','cfo','fcf','market_cap','all_market_cap','ev','book','cash_share','pe','pe_annual']],
    ['가치 · 수익 · 재무',['ps','pb','pc','pfcf','evsales','evebitda','gross_margin','operating_margin','net_margin','roa','roe','current','quick','debt_equity','long_debt_equity']],
    ['매출 · 영업이익 성장',[...growthIds('sales'),...growthIds('operating')]],
    ['순이익 · EPS 성장',[...growthIds('income'),...growthIds('eps')]],
    ['시세',['price','previous_close','sma20','sma50','sma200','perf1','perf5','perf21','perf63','perf126','perf252','perf756','perf1260','perf2520','high52','low52']],
    ['주식 · 배당 · 시세 · 별도 자료',['shares','dividend','yield','payout','volume','avg_volume','relative_volume','atr','rsi','float','insider','institution','short','beta','roic']]
  ];
  function actualSummary(summary=[]){
    return summary.filter(cell=>!estimateIds.has(String(cell.id).toLowerCase())&&!['estimate','forecast','consensus'].includes(cell.basis)&&!/(예상|전망|컨센서스|서프라이즈|목표\s*주가|forward|surprise|next\s*[qy]|next\s*\d*\s*y)/i.test(cell.label||''));
  }
  function columnsFor(summary){
    const remaining=new Map(summary.map(cell=>[cell.id,cell]));
    const columns=summaryGroups.map(([title,ids])=>({title,cells:ids.flatMap(id=>{const cell=remaining.get(id);remaining.delete(id);return cell?[cell]:[];})}));
    for(const cell of remaining.values())columns.reduce((a,b)=>a.cells.length<=b.cells.length?a:b).cells.push(cell);
    return columns;
  }
  function epsBasisName(basis){return basis==='reported'?'공시 EPS':basis==='basic'?'기본 EPS':'희석 EPS';}
  function epsName(result,frame){return epsBasisName(result.eps_basis?.[frame]);}
  function periodCell(row,key){return key==='eps'?(Object.hasOwn(row.values||{},'display_eps')?row.values.display_eps:row.values?.eps):row.values?.[key];}
  function valueClass(cell){return cell?.status==='not_applicable'?'not-applicable':cell?.status==='comparison_warning'?'comparison-note':cell?.value==null&&!cell?.display_text?'missing':'';}
  function metadata(cell){return [cell?.account_label?'표시 기준: '+cell.account_label:'',cell?.account?'공시 계정: '+cell.account:'',cell?.revenue_note?'매출 기준: '+cell.revenue_note:cell?.revenue_basis?'매출 기준: '+cell.revenue_basis:'',cell?.eps_basis?'EPS 기준: '+epsBasisName(cell.eps_basis):'',cell?.report_year?'자료 사업연도: '+cell.report_year:'',cell?.report_column?'공시 비교열: '+({thstrm:'당기',frmtrm:'전기',lwfr:'전전기',bfefrmtrm:'전전기',thstrm_amount:'당기',frmtrm_amount:'전기',bfefrmtrm_amount:'전전기'}[cell.report_column]||cell.report_column):'',cell?.period_name?'공시 기간: '+cell.period_name:'',cell?.comparison_note?'비교 기준: '+cell.comparison_note:''].filter(Boolean);}
  const comparisonMark=cell=>cell?.status==='comparison_warning'?'<small class="financial-comparison-mark">기준 확인</small>':'';
  function help(cell,result){
    if(!cell)return '공시 자료 미확보';
    const dynamic=/(^eps|^annual_eps|^latest_quarter_eps|^pe$|^pe_annual$|^sales$|^dividend$|^yield$|^payout$)/.test(cell.id||'')||cell.status||cell.eps_basis||cell.revenue_basis||cell.revenue_note;
    const base=dynamic?[cell.label,cell.reason?'자료 사유: '+cell.reason:'산식: '+(cell.formula||'공시 원값'),'기간: '+(cell.start||'시점')+' ~ '+(cell.end||result.financial_as_of||'미확인'),'공시: '+(cell.filed||'미확인'),'값을 클릭하면 원자료와 계산 입력을 확인할 수 있습니다.'].filter(Boolean).join('\n'):WorkbenchHelp.financial({...cell,id:cell.id||''},result);
    return [base,...metadata(cell),...qualityMetadata(cell)].filter(Boolean).join('\n');
  }
  function qualityMetadata(cell){
    const causes=items=>(items||[]).flatMap(x=>[x.reason,...causes(x.causes)]).filter(Boolean);
    return [cell?.dividend_basis?'배당 기준: '+({paid:'지급',declared:'선언'}[cell.dividend_basis]||cell.dividend_basis):'',
      cell?.period_note||'',cell?.period_coverage==='unknown'?'실제 공시기간 미확인':'',
      cell?.revision_history?.length?'과거 공시값 '+cell.revision_history.length+'건의 변경 이력 보존':'',
      ...[...new Set(causes(cell?.missing_inputs))].map(x=>'필수 입력 결측: '+x)].filter(Boolean);
  }
  function shell(){return '<section class="panel financial-panel" id="financials" aria-label="재무정보"><div class="panel-head"><div><h2>재무정보</h2><p class="chart-caption">버튼을 누를 때 공식 자료 수집·계산 · 이후 저장 결과 재사용</p></div><div class="financial-actions"><button id="financial-load">재무요약 만들기</button><button id="financial-refresh" hidden>재무요약 다시 만들기</button></div></div><div class="panel-body"><p id="financial-status" role="status">저장된 재무자료 확인 중…</p><div id="financial-content"></div></div></section>';}
  function clearCharts(ctx){ctx.charts.splice(0).forEach(c=>WorkbenchCharts.remove(c));}
  function close(){if(active){active.closed=true;clearTimeout(active.timer);clearCharts(active);active=null;}}
  async function mount({market,symbol,isCurrent}){
    close();const el=document.querySelector('#financials');if(!el)return;
    const ctx={el,key:market+':'+symbol,charts:[],frame:'annual',closed:false,timer:null,isCurrent};active=ctx;
    const live=()=>!ctx.closed&&isCurrent()&&el.isConnected;
    const endpoint='/api/financials/'+encodeURIComponent(ctx.key);
    async function load(){
      try{const response=await fetch(endpoint);if(!response.ok)throw Error('저장 재무자료를 읽지 못했습니다.');const payload=await response.json();if(!live())return;render(ctx,payload);if(payload.state==='running')ctx.timer=setTimeout(load,1500);}
      catch(error){if(live())el.querySelector('#financial-status').textContent=error.message;}
    }
    async function request(refresh){
      const buttons=el.querySelectorAll('.financial-actions button');buttons.forEach(b=>b.disabled=true);
      el.querySelector('#financial-status').textContent='공식 자료 요청 중… 종목을 이동해도 수집은 계속됩니다.';
      try{
        const health=await (await fetch('/health')).json();
        const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':health.token},body:JSON.stringify({refresh})});
        if(!response.ok)throw Error('재무자료 요청 실패 ('+response.status+')');
        const payload=await response.json();if(!live())return;render(ctx,payload);if(payload.state==='running')ctx.timer=setTimeout(load,1500);
      }catch(error){if(live())el.querySelector('#financial-status').textContent=error.message;}
      finally{if(live())buttons.forEach(b=>b.disabled=ctx.state==='running');}
    }
    el.querySelector('#financial-load').onclick=()=>request(false);el.querySelector('#financial-refresh').onclick=()=>request(true);
    await load();
  }
  function render(ctx,payload){
    const {el}=ctx, r=payload.result, running=payload.state==='running';ctx.state=payload.state;
    el.querySelector('#financial-load').hidden=!!r;el.querySelector('#financial-refresh').hidden=!r;
    el.querySelectorAll('.financial-actions button').forEach(b=>b.disabled=running);
    const status=el.querySelector('#financial-status');
    const errors={SEC_USER_AGENT_REQUIRED:'SEC_USER_AGENT 설정 필요: secrets/financial.json에 연락 가능한 이메일을 포함한 User-Agent를 설정하세요.',DART_API_KEY_REQUIRED:'DART_API_KEY 설정 필요: opendart.fss.or.kr에서 인증키를 발급받아 환경변수 또는 secrets/financial.json에 저장하세요.'};
    const error=payload.attempt?.error;
    status.textContent=running?'공식 자료 수집 중 · '+(payload.attempt.stage||'요청 준비'):
      error?'최근 갱신 '+(payload.state==='interrupted'?'중단':'실패')+' · '+(errors[error]||error)+(r?' · 아래는 이전 성공 자료입니다.':''):
      r?'저장 결과 · 수집 '+new Date(r.collected_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+(r.supplemented_at?' · 자료 보완 '+new Date(r.supplemented_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'')+(r.recalculated_at?' · 재계산 '+new Date(r.recalculated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'')+' · 재무기간 '+r.financial_as_of+' · 시세 '+(r.price_as_of||'결측'):
      '이 종목의 저장된 재무자료가 없습니다. 만들기 버튼을 누르면 공식 공시를 요청합니다.';
    status.className=error?'financial-status error':'financial-status';
    if(!r){el.querySelector('#financial-content').innerHTML='<div class="empty">SEC / DART 공식 실적에서 요약표와 매출·이익·EPS 추이를 준비합니다.<br>전 종목을 자동으로 수집하지 않습니다.</div>';return;}
    if(ctx.snapshot===r.snapshot_id)return;
    ctx.snapshot=r.snapshot_id;ctx.result=r;clearCharts(ctx);
    const summary=actualSummary(r.summary),columns=columnsFor(summary);
    const comparisonNotes=[...new Set(summary.filter(cell=>cell.status==='comparison_warning').map(cell=>cell.comparison_note).filter(Boolean))];
    el.querySelector('#financial-content').innerHTML=`
      <div class="financial-meta"><span class="tag">${esc(r.standard)}</span><span>${esc(r.currency)} · 공시 실적 · 기말 주식수 / 기간 실적 구분</span><a href="${'/api/financials/'+encodeURIComponent(ctx.key)}" target="_blank" rel="noreferrer">동일 계산 결과 JSON ↗</a></div>
      <div class="financial-summary">${columns.map(column=>`<div class="financial-group"><h3>${esc(column.title)}</h3><table><tbody>${column.cells.map(cell=>`<tr><th scope="row">${WorkbenchHelp.term(cell.label,help(cell,r))}</th><td><button class="financial-value ${valueClass(cell)}" data-metric="${esc(cell.id)}" data-help="${esc(help(cell,r))}">${esc(value(cell,r.currency,cell.kind==='money'&&!cell.unit?.includes('/share')&&!['price','previous_close','high52','low52','atr'].includes(cell.id)))}${comparisonMark(cell)}</button></td></tr>`).join('')}</tbody></table></div>`).join('')}</div>
      ${comparisonNotes.length?'<div class="financial-coverage-note">'+comparisonNotes.map(note=>'<p>'+esc(note)+'</p>').join('')+'</div>':''}
      <details class="financial-evidence" id="financial-evidence"><summary>값을 누르면 계산 근거·기준일·결측 사유를 확인할 수 있습니다</summary><div id="financial-evidence-body"></div></details>
      <div class="financial-chart-head"><div><h3>공시 실적과 성장률</h3><p class="chart-caption">연간 최근 3년 · 분기 최근 8개 · 전년 동기 대비 · ${esc(r.provider)}</p></div><div class="chart-toolbar" aria-label="실적 기간"><button data-financial-frame="annual" aria-pressed="true">연간</button><button data-financial-frame="quarterly" aria-pressed="false">분기</button></div></div>
      <details class="financial-periods" open><summary>기간별 실적 · 전년 대비 성장률 · 공시 출처</summary><div id="financial-period-table" class="scroll"></div></details>
      <div class="financial-charts">${['sales','earnings','eps'].map(key=>`<article class="financial-chart-card"><h3 id="financial-title-${key}">${key==='sales'?'매출 / 영업수익':key==='earnings'?'영업이익 · 지배주주 순이익':'공시 EPS'}</h3><div id="financial-latest-${key}" class="financial-latest"></div><div id="financial-chart-${key}" class="financial-chart" role="img" aria-label="${key==='sales'?'매출':key==='earnings'?'영업이익과 순이익':'EPS'} 재무 그래프"></div><p id="financial-legend-${key}" class="chart-caption"></p></article>`).join('')}</div>
      ${r.guidance?.length?'<div class="financial-guidance"><h3>회사 공식 가이던스 · 실적과 별도</h3>'+r.guidance.map(g=>`<p>${esc(g.period)} · ${esc(g.standard)} · 발표 ${esc(g.published)}<br>${esc(g.text)} <a href="${esc(g.url)}" target="_blank" rel="noreferrer">공식 발표 ↗</a></p>`).join('')+'</div>':''}
      <details class="financial-limits"><summary>자료 범위와 한계</summary><ul>${[...(r.coverage_metadata?.notes||[]),...(r.limitations||[])].map(x=>`<li>${esc(x)}</li>`).join('')}<li>가치배수는 저장 시세와 최근 공시 실적의 조합이며 실시간 지표가 아닙니다.</li><li>성장률은 같은 실적·EPS 기준의 전년 기간과 비교합니다. 음수·0 기저는 흑자전환·적자축소 등의 상태로 표시합니다.</li><li>매출은 업종별 공시 계정에 따라 영업수익 등을 사용하며, 채택 계정과 산식은 값을 눌러 확인할 수 있습니다.</li><li>기관·내부자·공매도 자료 미연결 항목은 사유와 함께 결측으로 표시합니다.</li></ul></details>`;
    el.querySelectorAll('[data-metric]').forEach(b=>b.onclick=()=>showEvidence(ctx,summary.find(cell=>cell.id===b.dataset.metric)));
    el.querySelectorAll('[data-financial-frame]').forEach(b=>b.onclick=()=>{ctx.frame=b.dataset.financialFrame;renderCharts(ctx);});renderCharts(ctx);
  }
  function sources(ctx,cell){
    const links=[];
    if(cell?.receipt)links.push({url:'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+cell.receipt,id:'DART 공시 '+cell.receipt});
    if(cell?.accession&&ctx.result.identity?.cik){const cik=String(Number(ctx.result.identity.cik));links.push({url:'https://www.sec.gov/Archives/edgar/data/'+cik+'/'+cell.accession.replaceAll('-','')+'/'+cell.accession+'-index.html',id:'SEC '+cell.accession});}
    for(const id of cell?.sources||[]){const source=(ctx.result.sources||[]).find(x=>x.id===id);if(source)links.push(source);}
    return links.map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(s.id)} ↗</a>`).join(' · ')||'저장 가격 자료 / JSON 원자료 참조';
  }
  function showEvidence(ctx,cell){
    if(!cell)return;
    const d=ctx.el.querySelector('#financial-evidence');d.open=true;
    const detail=[...metadata(cell),...qualityMetadata(cell)];
    ctx.el.querySelector('#financial-evidence-body').innerHTML=`<strong>${esc(cell.label||cell.tag||'공시 값')} · ${esc(value(cell,ctx.result.currency))} ${esc(cell.unit)}</strong><p>${esc(cell.reason||cell.formula||'공시 원값')}</p>${detail.length?'<p>'+detail.map(esc).join(' · ')+'</p>':''}<p>기간 ${esc(cell.start||'시점')} ~ ${esc(cell.end||'미확인')} · 공시 ${esc(cell.filed||'해당 없음/미확인')}</p><p>${sources(ctx,cell)}</p>${cell.inputs?'<ul>'+cell.inputs.map(x=>`<li>${esc(x.formula||x.account_label||x.tag||'계산 입력')} · ${esc(value(x,ctx.result.currency))} ${esc(x.unit)} · ${esc(x.end)}${metadata(x).length?' · '+metadata(x).map(esc).join(' · '):''}</li>`).join('')+'</ul>':''}`;
    if(cell.revision_history?.length)ctx.el.querySelector('#financial-evidence-body').innerHTML+='<ul>'+cell.revision_history.map(x=>`<li>과거 공시 ${esc(x.filed)} · ${esc(value({...cell,value:x.value},ctx.result.currency))} ${esc(cell.unit)} · ${esc(x.explained_by_split_or_rounding?'분할·공시 정밀도 범위에서 일치':'변경 원인 확인 필요')} · ${sources(ctx,{...x,sources:[]})}</li>`).join('')+'</ul>';
  }
  function renderPeriods(ctx,rows){
    const r=ctx.result;ctx.evidenceCells=[];
    const salesTitle=periodCell(rows.at(-1)||{},'sales')?.account_label||'매출 / 영업수익';
    function button(cell,label,scaled=false,extraClass=''){
      const fact=cell?{...cell,label}:null, index=ctx.evidenceCells.push(fact)-1;
      return `<button class="financial-value ${extraClass} ${valueClass(cell)}" data-period-cell="${index}" data-help="${esc(help(fact,r))}">${esc(value(cell,r.currency,scaled))}${comparisonMark(cell)}</button>`;
    }
    ctx.el.querySelector('#financial-period-table').innerHTML=`<table class="financial-performance-table"><caption>${ctx.frame==='annual'?'최근 3개 회계연도':'최근 8개 분기'} · ${esc(r.standard)} · ${esc(epsName(r,ctx.frame))} · 금액 ${r.currency==='KRW'?'억원':'USD (K·M·B는 천·백만·십억)'}</caption><thead><tr><th scope="col">공시 실적</th>${rows.map(row=>`<th scope="col">${esc(row.label)}<small>${esc(row.date)}</small></th>`).join('')}</tr></thead><tbody>${periodMetrics.map(([key,title])=>`<tr><th scope="row">${esc(key==='eps'?epsName(r,ctx.frame)+' ('+r.currency+'/주)':key==='sales'?salesTitle:title)}</th>${rows.map(row=>{
      const cell=periodCell(row,key),growth=row.growth?.[key];
      return `<td>${button(cell,row.label+' · '+(key==='sales'?cell?.account_label||salesTitle:title),key!=='eps')}${key==='sales'&&(cell?.account||cell?.account_label!==salesTitle)?'<small class="financial-account-label">'+esc(cell?.account||cell?.account_label)+'</small>':''}<div class="financial-growth"><span>전년 대비 </span>${button(growth,row.label+' · '+title+' 전년 동기 대비',false,'financial-growth-value')}</div></td>`;
    }).join('')}</tr>`).join('')}<tr class="financial-shares-row"><th scope="row">기말 보통주 유통주식수</th>${rows.map(row=>`<td>${button(row.values?.shares,row.label+' · 기말 보통주 유통주식수')}</td>`).join('')}</tr><tr class="financial-source-row"><th scope="row">공시 근거</th>${rows.map(row=>`<td>${sources(ctx,periodCell(row,'sales')||periodCell(row,'income'))}</td>`).join('')}</tr></tbody></table>${rows.length?'':'<p class="empty">표시할 공시 기간이 없습니다.</p>'}`;
    ctx.el.querySelectorAll('[data-period-cell]').forEach(b=>b.onclick=()=>showEvidence(ctx,ctx.evidenceCells[Number(b.dataset.periodCell)]));
  }
  function renderCharts(ctx){
    clearCharts(ctx);const r=ctx.result,rows=(r[ctx.frame]||[]).slice(ctx.frame==='annual'?-3:-8);
    const salesTitle=periodCell(rows.at(-1)||{},'sales')?.account_label||'매출 / 영업수익';
    ctx.el.querySelectorAll('[data-financial-frame]').forEach(b=>{b.classList.toggle('selected',b.dataset.financialFrame===ctx.frame);b.setAttribute('aria-pressed',String(b.dataset.financialFrame===ctx.frame));});
    ctx.el.querySelector('#financial-title-eps').textContent=(r.standard||'공시')+' · '+epsName(r,ctx.frame);
    ctx.el.querySelector('#financial-title-sales').textContent=salesTitle;
    for(const chartKey of ['sales','earnings','eps']){
      const keys=chartKey==='earnings'?['operating','income']:[chartKey];
      const amounts=rows.flatMap(row=>keys.map(key=>Math.abs(periodCell(row,key)?.value||0)));
      const {scale,unit}=chartKey==='eps'?{scale:1,unit:r.currency+'/주'}:moneyScale(r.currency,Math.max(0,...amounts));
      const formatter=x=>WorkbenchFormat.money(x,r.currency),legend=ctx.el.querySelector('#financial-legend-'+chartKey);
      const labelFor=key=>key==='eps'?epsName(r,ctx.frame):key==='sales'?salesTitle:periodMetrics.find(([id])=>id===key)[1];
      function rowLabel(row){return keys.map(key=>{const cell=periodCell(row||{},key);return (keys.length>1?labelFor(key)+' ':'')+(cell?.value==null?'—':formatter(cell.value/scale))+' '+unit;}).join(' / ');}
      ctx.el.querySelector('#financial-latest-'+chartKey).textContent=rowLabel(rows.at(-1));
      const missing=rows.filter(row=>keys.some(key=>periodCell(row,key)?.value==null)).length;
      legend.textContent=rows.length+'개 기간 · '+unit+(missing?' · 결측 '+missing+'개':'')+' · '+(rows.at(-1)?.date||'')+(chartKey==='earnings'?' · 파랑: 영업이익 / 초록: 순이익':'');
      const series=keys.map(key=>({type:chartKey==='earnings'?'line':'volume',label:labelFor(key),color:key==='operating'?'#6db8f6':'#31b884',data:rows.map(row=>{const cell=periodCell(row,key);return {time:row.date,...(cell?.value==null?{}:{value:cell.value/scale,...(chartKey==='earnings'?{}:{color:cell.value<0?'#ed6572':'#31b884'})})};}),options:{title:'',lastValueVisible:false,priceLineVisible:false,priceFormat:{type:'custom',minMove:r.currency==='KRW'?1:.01,formatter}}}));
      const chart=WorkbenchCharts.create(ctx.el.querySelector('#financial-chart-'+chartKey),series,{height:250,onReady(chart){
        chart.timeScale().applyOptions({tickMarkFormatter:time=>ctx.frame==='annual'?String(time).slice(0,4):String(time).slice(2,7)});
        chart.subscribeCrosshairMove(p=>{if(!p.time)return;const row=rows.find(x=>x.date===String(p.time));legend.textContent=(row?.label||String(p.time))+' · '+rowLabel(row);});
        chart.subscribeClick(p=>{const row=rows.find(x=>x.date===String(p.time));if(row){const key=keys.find(key=>periodCell(row,key)?.value!=null)||keys[0],cell=periodCell(row,key);if(cell)showEvidence(ctx,{...cell,label:row.label+' · '+labelFor(key)});}});
      }});if(chart)ctx.charts.push(chart);
    }
    renderPeriods(ctx,rows);
  }
  return {shell,mount,close};
})();

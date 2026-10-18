/* Official financial-focus contract: all financial calculations come from Python. */
window.WorkbenchFinancials=(()=>{
  let active;
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n=x=>Number(x).toLocaleString('ko-KR',{maximumFractionDigits:2});
  const colors={improved:'#31c58b',worsened:'#ed6572',neutral:'#a8b3c3'};
  const sourceName=c=>({synthetic:'합성 예시값',company_reported:'회사 공시값',opendart_indicator:'OpenDART 제공 지표',calculated:'계산값'}[c?.source_type]||'원자료');
  function compactMoney(x,currency){
    if(x==null||!Number.isFinite(Number(x)))return '자료 없음';
    const units=currency==='KRW'?[[1e12,'조'],[1e8,'억'],[1e4,'만']]:[[1e12,'T'],[1e9,'B'],[1e6,'M'],[1e3,'K']];
    const unit=units.find(([scale])=>Math.abs(x)>=scale);
    return unit?n(x/unit[0])+unit[1]:(['KRW','USD'].includes(currency)?WorkbenchFormat.money(x,currency):n(x))+(currency==='KRW'?'원':'');
  }
  function exact(c,currency){
    if(c?.value==null)return value(c,currency);
    if(c.unit==='shares')return Number(c.value).toLocaleString('ko-KR',{maximumFractionDigits:10})+'주';
    if(c.unit?.startsWith(currency))return Number(c.value).toLocaleString('ko-KR',{minimumFractionDigits:currency==='USD'?2:0,maximumFractionDigits:20})+' '+c.unit;
    return value(c,currency);
  }
  const dividendBasis=c=>({paid:'지급 기준',declared:'선언 기준',annual_common_cash:'사업연도 보통주 현금배당'}[c?.dividend_basis]||'공시 기준');
  function value(c,currency){
    if(c?.value==null&&(c?.eps_basis||String(c?.id||'').includes('eps'))&&(!c?.display_text||/미확보/.test(c?.reason||'')))return '값 없음';
    if(c?.display_text)return c.display_text;
    if(c?.value==null)return '자료 없음';
    if(c.unit==='shares')return compactMoney(c.value,'USD')+'주';
    if(c.unit==='percentage_point')return n(c.value*100)+'%p';
    if(c.kind==='percent')return n(c.value*100)+'%';
    if(c.unit==='multiple'||c.kind==='multiple')return n(c.value)+'배';
    if(c.dividend_basis&&currency==='USD'&&Math.abs(c.value)<1000)return Number(c.value).toLocaleString('ko-KR',{minimumFractionDigits:2,maximumFractionDigits:4});
    if(c.unit?.startsWith(currency))return compactMoney(c.value,currency);
    if(/^[A-Z]{3}(?:\/share)?$/.test(c.unit||'')&&!c.unit.startsWith(currency))return n(c.value)+' '+c.unit;
    return n(c.value);
  }
  function shell(){return '<section class="panel financial-panel" id="financials" aria-label="재무정보"><div class="panel-head"><div><h2>재무정보</h2><p class="chart-caption">실적·성장·건전성 · 공개 데모는 합성 예시</p></div><div class="financial-actions"><button id="financial-load">재무요약 만들기</button><button id="financial-refresh" hidden>재무요약 다시 만들기</button></div></div><div class="panel-body"><p id="financial-status" role="status"></p><div id="financial-content"></div></div></section>';}
  function clear(ctx){ctx.charts.splice(0).forEach(c=>WorkbenchCharts.remove(c));}
  function close(){if(active){active.closed=true;clearTimeout(active.timer);clear(active);active=null;}}
  async function mount({market,symbol,isCurrent}){
    close();const el=document.querySelector('#financials');if(!el)return;
    const ctx={el,key:market+':'+symbol,frame:'annual',comparison:'growth',charts:[],cells:[],isCurrent};active=ctx;
    const live=()=>!ctx.closed&&isCurrent()&&el.isConnected,endpoint='/api/financials/'+encodeURIComponent(ctx.key);
    async function load(refresh){
      try{
        let options;
        if(typeof refresh==='boolean'){
          const health=await(await fetch('/health')).json();
          options={method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':health.token},body:JSON.stringify({refresh})};
        }
        const response=await fetch(endpoint,options);if(!response.ok)throw Error('재무자료 요청 실패 ('+response.status+')');
        const packet=await response.json();if(!live())return;
        render(ctx,packet);if(packet.state==='running')ctx.timer=setTimeout(()=>load(),1500);
      }catch(e){if(live()){el.querySelector('#financial-status').textContent=e.message;el.querySelectorAll('.financial-actions button').forEach(b=>b.disabled=false);}}
    }
    el.querySelector('#financial-load').onclick=()=>load(false);el.querySelector('#financial-refresh').onclick=()=>load(true);
    await load();
  }
  function links(ctx,c){
    const all=[];
    if(c?.receipt)all.push({id:'DART 공시 '+c.receipt,url:'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+encodeURIComponent(c.receipt)});
    if(c?.accession&&ctx.result.identity?.cik)all.push({id:'SEC '+c.accession,url:'https://www.sec.gov/Archives/edgar/data/'+Number(ctx.result.identity.cik)+'/'+c.accession.replaceAll('-','')+'/'+c.accession+'-index.html'});
    for(const id of c?.sources||[]){const s=ctx.result.sources?.find(x=>x.id===id);if(s)all.push(s);}
    return all.filter(s=>/^https?:\/\//.test(s.url||'')).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(s.id)} ↗</a>`).join(' · ')||'원자료 미확보';
  }
  function evidence(ctx,cell){
    if(!cell)return;
    function detail(c,depth=0){
      const text=[c.label||c.tag,sourceName(c),c.reason,c.formula,c.definition,c.explanation,c.account,c.revenue_note,c.dividend_basis&&dividendBasis(c),c.dividend_basis_note,c.repurchase_basis&&({cash_paid:'매입 현금 지급액',reported_repurchase_cost:'공시 매입원가(수수료·세금 포함 여부는 원문 기준)'}[c.repurchase_basis]),c.comparison_note,c.period_note,c.eps_basis&&'EPS 종류: '+c.eps_basis,c.revision_basis&&'정정 비교 기준: '+c.revision_basis].filter(Boolean).map(esc).join(' · ');
      return `<div class="financial-input"><p><strong>${esc(exact(c,ctx.result.currency))}</strong> · ${text}</p>${c.change?'<p>비교기간 대비 변화 '+esc(value(c.change,ctx.result.currency))+' · '+esc(c.change.reason||c.change.compared_to||'')+'</p>':''}<p>기간 ${esc(c.start||'기말')} ~ ${esc(c.end||'미확인')} · 공시 ${esc(c.filed||'미확인')} · ${links(ctx,c)}</p>${c.revision_history?.length?'<p>과거 공시 변경 '+c.revision_history.length+'건 · '+esc(JSON.stringify(c.revision_history))+'</p>':''}${depth<3?(c.inputs||[]).map(x=>detail(x,depth+1)).join(''):''}${depth===0&&c.latest_disclosure?'<h4>별도 보존한 개별 최신 공시값</h4>'+detail(c.latest_disclosure,1):''}${depth===0&&c.change?.previous?'<h4>변화 비교 원값</h4>'+detail(c.change.previous,1):''}</div>`;
    }
    ctx.el.querySelector('#financial-evidence').open=true;ctx.el.querySelector('#financial-evidence-body').innerHTML=detail(cell);
  }
  function button(ctx,c,label){
    const id=ctx.cells.push(c?{...c,label:label||c.label}:null)-1;
    return `<button class="financial-value ${esc(c?.trend||'neutral')}" data-focus-cell="${id}" title="${esc([exact(c,ctx.result.currency),sourceName(c),c?.dividend_basis&&dividendBasis(c),c?.formula,c?.reason].filter(Boolean).join(' · '))}">${esc(value(c,ctx.result.currency))}</button>`;
  }
  function render(ctx,packet){
    const r=packet.result,el=ctx.el;
    el.querySelector('#financial-load').hidden=!!r;el.querySelector('#financial-refresh').hidden=!r;
    el.querySelectorAll('.financial-actions button').forEach(b=>b.disabled=packet.state==='running');
    el.querySelector('#financial-status').textContent=packet.state==='running'?'공식 자료 수집 중 · '+(packet.attempt?.stage||''):
      [packet.attempt?.error&&(packet.attempt.error==='UNSUPPORTED_FINANCIAL_FORMAT'?'새로운 공시 형식입니다. 코드 수정이 필요합니다.':'최근 갱신 실패: '+packet.attempt.error),r?'자료 수집 '+new Date(r.collected_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})+' · 재무기간 '+r.financial_as_of:'저장된 재무자료가 없습니다.',r?.recalculated_at&&'재계산 '+new Date(r.recalculated_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})].filter(Boolean).join(' · ');
    if(r?.synthetic){el.querySelectorAll('.financial-actions button').forEach(b=>b.hidden=true);el.querySelector('#financial-status').textContent='합성 예시 · 실제 공시 아님 · 재무기간 '+r.financial_as_of;}
    if(!r){el.querySelector('#financial-content').innerHTML='<p class="empty">만들기 버튼으로 선택 종목의 공식 자료를 수집합니다.</p>';return;}
    if(r.eligibility?.status==='unsupported'){el.querySelector('#financial-status').textContent='사업회사 재무정보 대상 제외';el.querySelector('#financial-content').innerHTML='<p class="empty">'+esc(r.eligibility.reason)+'</p>';return;}
    if(r.calculation_version!=='financial-focus-8'||packet.result_stale){el.querySelector('#financial-content').innerHTML='<p class="empty" role="status">이전 형식의 저장 결과입니다. 최신 재무 항목을 표시하려면 위의 ‘재무요약 다시 만들기’를 눌러 주세요. 과거 결과는 보존됩니다.</p>';return;}
    if(ctx.snapshot===r.snapshot_id)return;ctx.snapshot=r.snapshot_id;ctx.result=r;paint(ctx);
  }

  const palette=['#438dff','#258d75','#d99734','#9560db','#4ab7b8'];
  const missing={value:null,reason:'공식 공시 자료 미확보'};
  function cell(row,path){return path.split('.').reduce((v,k)=>v?.[k],row)||missing;}
  function series(label,path,color,type='bar'){return {label,path,color,type};}
  const plotWidth=rows=>Math.max(720,rows.length*94+160);
  function tableCell(row,path,comparison){
    if(path.startsWith('values.')){
      const key=path.slice(7),group=comparison==='qoq'?'value_movements_qoq':'value_movements';
      return row[group]?.[key]||row[comparison==='qoq'?'performance_qoq':'performance']?.[key]||cell(row,path);
    }
    if(path.endsWith('.change')){const parent=cell(row,path.slice(0,-7));return {...cell(row,path),trend:parent.trend};}
    return cell(row,path);
  }
  function table(ctx,rows,items,cls='',comparison='growth'){
    return `<div class="scroll" tabindex="0" aria-label="기간별 수치 가로 스크롤"><table class="financial-performance-table ${cls}" ${cls?`style="width:${plotWidth(rows)}px;min-width:${plotWidth(rows)}px"`:""}><thead><tr><th>항목</th>${rows.map(row=>`<th>${esc(cls?row.date.slice(2,7):row.label)}<small>${esc(row.date)}</small></th>`).join('')}</tr></thead><tbody>${items.map(m=>`<tr><th><i style="background:${m.color||'transparent'}"></i>${esc(m.label)}</th>${rows.map(row=>`<td>${button(ctx,tableCell(row,m.path,comparison),m.label)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function plot(rows,items,currency){
    if(!items.some(m=>rows.some(row=>cell(row,m.path).value!=null)))return '<p class="empty">이 기간에 표시할 공시 자료가 없습니다.</p>';
    const width=plotWidth(rows),height=310,left=160,right=width,top=20,bottom=275,step=(right-left)/Math.max(1,rows.length);
    const bars=items.filter(s=>s.type==='bar'),lines=items.filter(s=>s.type==='line');
    const extent=list=>{const vals=list.flatMap(s=>rows.map(r=>cell(r,s.path).value)).filter(x=>x!=null&&Number.isFinite(x));return [Math.min(0,...vals),Math.max(0,...vals)];};
    let [lo,hi]=extent(bars.length?bars:lines);if(lo===hi)hi=lo+1;
    const y=x=>bottom-(x-lo)/(hi-lo)*(bottom-top-20),zero=y(0);
    const parts=[`<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" style="width:${width}px;min-width:${width}px" preserveAspectRatio="xMinYMin meet" role="img" aria-label="기간별 공시 수치 막대와 비율 선 차트">`];
    for(let j=0;j<=4;j++){const v=lo+(hi-lo)*j/4,yy=y(v);parts.push(`<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" class="financial-grid"/><text x="${left-10}" y="${yy+4}" text-anchor="end">${esc(compactMoney(v,currency))}</text>`);}
    rows.forEach((row,i)=>{const x=left+step*i+step/2,bw=Math.min(30,step*.65/Math.max(bars.length,1));bars.forEach((m,j)=>{const c=cell(row,m.path);if(c.value!=null)parts.push(`<rect x="${x+(j-bars.length/2)*bw}" y="${Math.min(zero,y(c.value))}" width="${bw-3}" height="${Math.max(1,Math.abs(zero-y(c.value)))}" rx="2" fill="${c.value<0?colors.worsened:m.color}"/>`);});parts.push(`<text x="${x}" y="300" text-anchor="middle">${esc(row.date.slice(2,7))}</text>`);});
    lines.forEach(m=>{const vals=rows.map(r=>cell(r,m.path).value).filter(x=>x!=null);let min=Math.min(...vals),max=Math.max(...vals);if(min===max){min-=Math.max(1,Math.abs(min)*.2);max+=Math.max(1,Math.abs(max)*.2);}
      const ly=v=>55+(max-v)/(max-min)*130;let prev=null;
      rows.forEach((row,i)=>{const c=cell(row,m.path),x=left+step*i+step/2;if(c.value==null){prev=null;return;}const yy=ly(c.value);if(prev)parts.push(`<line x1="${prev.x}" y1="${prev.y}" x2="${x}" y2="${yy}" stroke="${m.color}" stroke-width="2"/>`);parts.push(`<circle cx="${x}" cy="${yy}" r="3.5" fill="#17191f" stroke="${m.color}" stroke-width="2"/>`);prev={x,y:yy};});});
    rows.forEach((row,i)=>parts.push(`<rect class="financial-hit" data-period="${i}" x="${left+step*i}" y="${top}" width="${step}" height="${bottom-top}" fill="transparent" tabindex="0" role="button" aria-label="${esc(row.label+' '+items.map(m=>m.label+' '+value(cell(row,m.path),currency)).join(' · '))}"/>`));
    parts.push('</svg>');return parts.join('');
  }
  function sharesPlot(rows){
    const width=plotWidth(rows),height=260,left=160,right=width-36,top=30,bottom=220,step=(right-left)/Math.max(1,rows.length);
    const values=rows.map(r=>cell(r,'values.shares').value).filter(v=>v!=null&&v>=0);
    if(!values.length)return '<p class="empty">SEC 기말 주식수 자료가 없습니다.</p>';
    const max=Math.max(...values),scale=max>=1e9?1e9:max>=1e6?1e6:max>=1e3?1e3:1,unit=scale===1e9?'B (십억 주)':scale===1e6?'M (백만 주)':scale===1e3?'K (천 주)':'주';
    const high=(max||1)*1.12,y=v=>bottom-v/high*(bottom-top),parts=[`<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" style="width:${width}px;min-width:${width}px;height:${height}px" role="img" aria-label="SEC 기말 유통주식수 연간 및 최신 분기 막대차트"><text x="${left}" y="16">Shares Outstanding · ${unit}</text>`];
    for(let i=0;i<=4;i++){const v=high*i/4,yy=y(v);parts.push(`<line x1="${left}" x2="${right}" y1="${yy}" y2="${yy}" class="financial-grid"/><text x="${right+5}" y="${yy+4}">${esc(n(v/scale))}</text>`);}
    rows.forEach((r,i)=>{const c=cell(r,'values.shares'),x=left+i*step+4,bw=step-8,v=c.value;
      if(v!=null&&v>=0)parts.push(`<rect x="${x}" y="${y(v)}" width="${bw}" height="${Math.max(1,bottom-y(v))}" fill="${r.shares_mrq?'none':'#20b7c4'}" stroke="#6dd4dc" stroke-width="${r.shares_mrq?2:0}"/><text class="financial-shares-label" x="${x+bw/2}" y="${Math.min(bottom-5,y(v)+17)}" text-anchor="middle">${esc((v/scale).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}))}</text>`);
      parts.push(`<text x="${x+bw/2}" y="244" text-anchor="middle">${r.shares_mrq?'MRQ':esc(r.date.slice(0,4)+(r.shares_quarter?'-'+r.date.slice(5,7):''))}</text><rect class="financial-hit" data-period="${i}" x="${left+i*step}" y="${top}" width="${step}" height="${bottom-top}" fill="transparent" tabindex="0" role="button" aria-label="${esc(r.label+' '+exact(c,'USD'))}"/>`);
    });
    return parts.join('')+'</svg>';
  }
  function paint(ctx){
    clear(ctx);ctx.cells=[];ctx.views||={};const r=ctx.result,rows=r[ctx.frame]||[],last=rows.at(-1),mode=ctx.frame==='annual'?'growth':ctx.comparison;
    const perf=mode==='qoq'?'performance_qoq':'performance',metrics=r.performance_metrics||[];
    const groups=[
      {id:'profit',title:'수익성',subtitle:'매출·순이익 성장률',items:[series('매출','values.sales',palette[0]),series(r.performance_metrics?.find(m=>m.id==='income')?.label||'지배주주 순이익','values.income',palette[1]),series('순이익률','metrics.net_margin',palette[2],'line')],extra:[series('매출 성장률',mode+'.sales'),series('순이익 성장률',mode+'.income')]},
      {id:'growth',title:'성장성',subtitle:'영업이익 성장률',items:[series('영업이익','values.operating',palette[3]),series('영업이익률','metrics.operating_margin',palette[4],'line')],extra:[series('영업이익 성장률',mode+'.operating')]},
      {id:'stability',title:'안정성',subtitle:'부채·유동비율',items:[],extra:[]},
      ...metrics.filter(m=>m.id.includes('eps')).map(m=>({id:m.id,title:m.label,subtitle:'공시 주당순이익과 성장률',items:[series(m.label,'values.'+m.id,palette[0]),series('성장률',mode+'.'+m.id,palette[2],'line')],extra:[]})),
      ...(r.provider==='SEC'?[{id:'shares',title:'유통주식수',subtitle:'SEC 기말 보통주 · Shares Outstanding',items:[series('기말 유통주식수','values.shares','#20b7c4')],extra:[]}]:[]),
      ...(r.provider==='SEC'?[{id:'returns',title:'주주환원',subtitle:'실제 지급·매입액과 순이익 대비 비율',items:[series('현금배당 총액','values.cash_dividends',palette[0]),series('자사주 매입액','values.buybacks',palette[1]),series('합계','values.shareholder_return',palette[3]),series('총주주환원율','metrics.shareholder_payout',palette[2],'line')],extra:[]}]:[]),
      {id:'dividend',title:'배당',subtitle:'공시 주당배당금',items:[series('주당배당금','values.dividend',palette[0])],extra:[]}
    ];
    const stability=ctx.stability||'debt_ratio';groups[2].items=stability==='current_ratio'?[series('유동자산','values.current_assets',palette[4]),series('유동부채','values.current_liabilities',palette[1]),series('유동비율','metrics.current_ratio',palette[2],'line')]:[series('총자본','values.total_equity',palette[4]),series('총부채','values.liabilities',palette[1]),series('부채비율','metrics.debt_ratio',palette[2],'line')];
    const rendered=groups.map(g=>{
      const view=ctx.views[g.id]||{frame:ctx.frame,years:ctx.frame==='annual'?5:3};let rr=r[view.frame]||[],fallback=false;
      if(g.id==='dividend'&&view.frame==='quarterly'&&!rr.some(row=>cell(row,'values.dividend').value!=null)){rr=r.annual||[];fallback=true;}
      if(view.frame==='annual'){g.items=g.items.map(m=>({...m,path:m.path.replace('qoq.','growth.')}));g.extra=g.extra.map(m=>({...m,path:m.path.replace('qoq.','growth.')}));}
      rr=rr.slice(-(view.years*(view.frame==='annual'||fallback?1:4)));
      if(g.id==='shares'){
        rr=rr.map(row=>({...row,shares_quarter:view.frame==='quarterly'}));
        const mrq=r.quarterly?.at(-1);
        if(view.frame==='annual'&&mrq&&(!rr.length||mrq.date>rr.at(-1).date))rr=[...rr,{...mrq,label:'MRQ · '+mrq.date,shares_mrq:true}];
      }
      g.rows=rr;
      const items=[...g.items,...g.extra],last=rr.at(-1);
      return `<section class="financial-toss-section" data-chart-section="${g.id}"><header><h3>${esc(g.title)} <span>${esc(g.subtitle)}</span></h3><p class="financial-story">${last?esc(last.label+' · '+g.items.map(m=>m.label+' '+value(cell(last,m.path),r.currency)).join(' · ')):'공시 자료 없음'}</p></header><div class="financial-selectors">${g.id==='stability'?`<select data-stability aria-label="안정성 지표">${[['debt_ratio','부채비율'],['current_ratio','유동비율']].map(([id,label])=>`<option value="${id}" ${stability===id?'selected':''}>${label}</option>`).join('')}</select>`:''}<select data-chart-years="${g.id}" aria-label="${esc(g.title)} 기간">${[1,3,5].map(y=>`<option value="${y}" ${view.years===y?'selected':''}>${y}년</option>`).join('')}</select><select data-chart-frame="${g.id}" aria-label="${esc(g.title)} 주기"><option value="annual" ${view.frame==='annual'?'selected':''}>연간</option><option value="quarterly" ${view.frame==='quarterly'?'selected':''}>분기</option></select></div>${fallback?'<p class="chart-caption">분기 공시값이 없어 연간 배당 내역을 표시합니다.</p>':''}${g.id==='shares'?'<p class="chart-caption">기말 보통주 유통주식수 · MRQ는 최신 공시 분기(테두리 막대) · 가중평균주식수 제외 · 확보된 공식 분할 조정 반영 · 감소 초록/증가 빨강</p>':''}${g.id==='returns'?'<p class="chart-caption">보통주 현금배당 지급액 + 공시 자사주 매입액 · 계획/승인 한도·별도 직원 세금 원천징수 제외 · 동일 기간 순이익 대비 · 적자/순이익 0은 비율 산출 불가</p>':''}${g.id==='dividend'?'<p class="chart-caption">사업기간별 주당배당금 · 지급/선언 기준은 각 값에서 확인 · 예상치 제외</p>':''}<div class="financial-toss-chart">${g.id==='shares'?sharesPlot(rr):plot(rr,g.items,r.currency)}</div><div class="financial-series-key">${g.items.map(m=>`<span><i style="background:${m.color}"></i>${esc(m.label)}${m.type==='line'?' (선 · 별도 비율 척도)':''}</span>`).join('')}</div><div class="financial-chart-table">${table(ctx,rr,items,'financial-short-table',view.frame==='annual'||fallback?'growth':mode)}</div></section>`;
    }).join('');
    const all=[...metrics.flatMap(m=>[series(m.label,perf+'.'+m.id),series(m.label+' '+(mode==='growth'?'YoY':'QoQ'),mode+'.'+m.id)]),...['net_margin','operating_margin','debt_ratio','current_ratio'].flatMap(id=>{const label=last?.metrics?.[id]?.label||({net_margin:'순이익률',current_ratio:'유동비율'}[id])||id;return [series(label,'metrics.'+id),series(label+' 전년 대비 변화','metrics.'+id+'.change')];}),series('주당배당금','values.dividend'),...(r.provider==='SEC'?[series('기말 유통주식수','values.shares'),series('현금배당 총액','values.cash_dividends'),series('자사주 매입액','values.buybacks'),series('주주환원 합계','values.shareholder_return'),series('총주주환원율','metrics.shareholder_payout')]:[])];
    ctx.el.querySelector('#financial-content').innerHTML=`<div class="financial-meta"><span>${esc(r.standard)} · ${esc(r.currency)} · ${r.synthetic?'합성 예시 · 실제 공시 아님':'공시 실적만 표시'}</span></div>${r.security_basis?.notice?'<p class="chart-caption">'+esc(r.security_basis.notice)+'</p>':''}${r.format_issues?.length?'<p class="financial-format-notice" role="status">새로운 공시 형식입니다. 코드 수정이 필요합니다. 검증된 항목만 표시합니다.</p><details><summary>지원하지 않는 공시 형식</summary><ul>'+r.format_issues.map(x=>'<li>'+esc(x.message)+' · '+esc(x.source_id)+'</li>').join('')+'</ul></details>':''}${r.enrichment_errors?.length?'<p class="chart-caption">일부 공시 보완 수집에 실패했습니다. 재시도 후에도 계속되면 수집 오류를 확인하세요.</p>':''}${r.data_quality?.history_limited?'<p class="chart-caption">요청한 전체 기간을 확보하지 못해 검증된 기간만 표시합니다.</p>':''}${r.data_quality?.state==='partial'?'<p class="chart-caption">일부 공시 항목 미확보 · 각 값에서 사유를 확인하세요.</p>':''}<div class="financial-chart-head"><h3>재무</h3><div class="chart-toolbar"><button data-focus-frame="annual" aria-pressed="${ctx.frame==='annual'}">연간</button><button data-focus-frame="quarterly" aria-pressed="${ctx.frame==='quarterly'}">분기</button>${ctx.frame==='quarterly'?`<button data-focus-comparison="growth" aria-pressed="${mode==='growth'}">YoY 전년 대비</button><button data-focus-comparison="qoq" aria-pressed="${mode==='qoq'}">QoQ 전분기 대비</button>`:''}</div></div><p class="chart-caption">연간 5개 · 분기 12개 공시 확보 범위 내 표시 · 금액 원/만/억/조, USD K/M/B/T · 각 기간에 마우스를 올려 수치를 확인하세요. 막대: 음수(적자)는 빨강, 양수는 항목별 색상 · 표의 증감: 초록 개선 · 빨강 악화 · 회색 동일/비교 불가. 실적은 선택한 YoY/QoQ, 건전성 비율은 전년 대비입니다.</p><div class="financial-focus-health">${['debt_ratio','current_ratio','operating_margin'].map(id=>{const c=last?.metrics?.[id];return `<article><h4>${esc(c?.label||({current_ratio:'유동비율'}[id])||id)}</h4>${button(ctx,c)}<p>${esc(c?.definition||'공시 원값 미확보')}</p></article>`;}).join('')}</div>${(last?.warnings||[]).map(w=>`<p class="financial-equity-warning">${esc(w.message)} ${button(ctx,w.evidence)}</p>`).join('')}${rendered}<details class="financial-evidence" id="financial-evidence"><summary>공시 원값·계산 근거·결측 사유</summary><div id="financial-evidence-body"></div></details><details><summary>자료 범위와 한계</summary><ul>${(r.limitations||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details><details class="financial-periods financial-final-table" open><summary>기간별 수치·성장률·건전성</summary>${table(ctx,rows,all,'',mode)}</details><div class="financial-hover" role="tooltip" hidden></div>`;
    ctx.el.querySelectorAll('[data-chart-section]').forEach(section=>{
      const chart=section.querySelector('.financial-toss-chart'),scroll=section.querySelector('.financial-chart-table .scroll');
      chart.scrollLeft=chart.scrollWidth;scroll.scrollLeft=chart.scrollLeft;
      chart.onscroll=()=>{scroll.scrollLeft=chart.scrollLeft;};scroll.onscroll=()=>{chart.scrollLeft=scroll.scrollLeft;};
    });
    const tooltip=ctx.el.querySelector('.financial-hover');
    function show(html,event,target){tooltip.innerHTML=html;tooltip.hidden=false;const rect=target.getBoundingClientRect();const x=event?.clientX??rect.left,y=event?.clientY??rect.top;tooltip.style.left=Math.max(8,Math.min(x+16,window.innerWidth-320))+'px';tooltip.style.top=Math.max(8,Math.min(y+16,window.innerHeight-tooltip.offsetHeight-12))+'px';}
    const hide=()=>{tooltip.hidden=true;};
    ctx.el.querySelectorAll('[data-focus-frame]').forEach(b=>b.onclick=()=>{ctx.frame=b.dataset.focusFrame;ctx.views={};paint(ctx);});
    ctx.el.querySelectorAll('[data-focus-comparison]').forEach(b=>b.onclick=()=>{ctx.comparison=b.dataset.focusComparison;paint(ctx);});
    ctx.el.querySelectorAll('[data-chart-frame],[data-chart-years]').forEach(b=>b.onchange=()=>{const id=b.dataset.chartFrame||b.dataset.chartYears;ctx.views[id]||={frame:ctx.frame,years:ctx.frame==='annual'?5:3};ctx.views[id][b.dataset.chartFrame?'frame':'years']=b.dataset.chartFrame?b.value:Number(b.value);paint(ctx);});
    ctx.el.querySelector('[data-stability]').onchange=e=>{ctx.stability=e.target.value;paint(ctx);};
    ctx.el.querySelectorAll('[data-focus-cell]').forEach(b=>{const c=ctx.cells[Number(b.dataset.focusCell)];b.onclick=()=>evidence(ctx,c);const hover=e=>show(`<strong>${esc(c?.label||'공시 수치')}</strong><b>${esc(value(c,r.currency))}</b><small>${esc(exact(c,r.currency))}</small><small>${esc(c?.dividend_basis?dividendBasis(c):c?.reason||c?.definition||sourceName(c))}</small>`,e,b);b.onpointerenter=hover;b.onpointermove=hover;b.onfocus=hover;b.onpointerleave=hide;b.onblur=hide;});
    groups.forEach(g=>{const section=ctx.el.querySelector(`[data-chart-section="${g.id}"]`);section.querySelectorAll('[data-period]').forEach(hit=>{const row=g.rows[Number(hit.dataset.period)],items=[...g.items,...g.extra];const hover=e=>show(`<strong>${esc(row.label)} · ${esc(row.date)}</strong>${items.map(m=>`<div><i style="background:${m.color||'#a8b3c3'}"></i>${esc(m.label)} <b>${esc(value(cell(row,m.path),r.currency))}</b><small>${esc(exact(cell(row,m.path),r.currency))}</small></div>`).join('')}`,e,hit);hit.onpointerenter=hover;hit.onpointermove=hover;hit.onfocus=hover;hit.onpointerleave=hide;hit.onblur=hide;hit.onclick=()=>evidence(ctx,cell(row,g.items[0].path));});});
  }
  return {shell,mount,close};
})();


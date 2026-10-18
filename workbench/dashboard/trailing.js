'use strict';
window.WorkbenchTrailing = (() => {
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const labels={price_sessions_incomplete:'완료 거래일 이력에 공백 또는 비거래일이 있습니다.',account_stale_or_incomplete:'잔고가 24시간을 넘었거나 보유 확인이 불완전합니다.',holding_unconfirmed:'보유 수량·평단 확인이 필요합니다.',market_identity_unconfirmed:'종목·통화 대응 확인이 필요합니다.',market_identity_or_basis_changed:'종목 식별자 또는 가격 기준이 바뀌었습니다.',completed_price_stale_or_missing:'최신 완료 거래일의 가격이 없습니다.',atr_history_missing:'ATR 계산 이력이 부족합니다.',processed_price_history_changed:'이미 처리한 가격 이력이 변경되었거나 연결 봉이 없습니다.',price_date_regressed:'가격 기준일이 과거입니다.'};
 const yellow='#f5b85b';
 const trailingPrice=p=>p.status!=='closed'&&p.active&&p.line!=null&&Number.isFinite(Number(p.line))?Number(p.line):null;
 function series(p){
  const line=trailingPrice(p);
  return [{label:'종가',currency:p.currency,color:'#6db8f6',options:{title:'',autoscaleInfoProvider:original=>{
   const info=original();if(info&&line!=null)info.priceRange={minValue:Math.min(info.priceRange.minValue,line),maxValue:Math.max(info.priceRange.maxValue,line)};return info;
  }},data:(p.prices||[]).map(b=>({time:b.date,value:b.close}))}];
 }
 function mountChart(plot,legend,p){
  const line=trailingPrice(p),latest=p.prices?.at(-1)?.close;
  function show(close,date){legend.innerHTML=`<span style="color:#6db8f6">종가 <strong>${WorkbenchFormat.money(close,p.currency)}</strong>${date?' · '+esc(date):''}</span><span style="color:${yellow}">최종 트레일링 <strong>${WorkbenchFormat.money(line,p.currency)}</strong></span>`;}
  show(latest,p.prices?.at(-1)?.date);
  return WorkbenchCharts.create(plot,series(p),{lastBars:126,onReady(chart,refs){
   const price=refs[0].ref;
   if(line!=null)price.createPriceLine({price:line,color:yellow,lineWidth:2,lineStyle:LightweightCharts.LineStyle.Dotted,axisLabelVisible:true,title:''});
   chart.subscribeCrosshairMove(point=>{
    const value=point.time?point.seriesData.get(price):null;
    show(value?.value??latest,point.time||p.prices?.at(-1)?.date);
   });
  }});
 }
 async function mount(host){
  host.innerHTML='<h1>가격 위험 경고</h1><p>저장된 완료 일봉 판정을 불러오는 중…</p>';
  const response=await fetch('/api/trailing');if(!response.ok)throw Error('가격 경고를 불러오지 못했습니다.');const data=await response.json();
  if(!host.isConnected||!location.hash.startsWith('#risk'))return;
  const positions=data.positions.filter(p=>p.status!=='closed');
  host.innerHTML='<h1>가격 위험 경고</h1><p>완료 종가 기준 · 트레일링 = 감시 최고가의 95% · 트레일링/5일선/10일선 위쪽 0.5 ATR 접근을 각각 알립니다.</p>'+(!positions.length?'<div class="empty">아직 감시 가능한 저장 보유 자료가 없습니다.</div>':'')+positions.map((p,i)=>`<section class="panel"><div class="panel-body"><h2>${esc(p.name)} <small>${esc(p.symbol)} · ${esc(p.account)}</small></h2><p>${p.issue?'판정 보류 — '+esc(labels[p.issue]||p.issue):p.active?(p.status==='warning'?'기준선 0.5 ATR 접근·이탈 상태':'감시 중'):'수익 상태 진입 대기'} · 가격 ${esc(p.last_date||p.prices?.at(-1)?.date||'미확인')} · 잔고 ${esc(p.observed_account_as_of||p.account_as_of||'미확인')}</p><div class="stock-plot-wrap"><div id="trailing-chart-${i}" class="chart" aria-label="종가와 최종 트레일링 노란 수평 점선"></div><div id="trailing-legend-${i}" class="stock-price-legend trailing-price-legend" aria-label="종가와 최종 트레일링 값"></div></div></div></section>`).join('')+`<section class="panel"><div class="panel-body"><h2>발생한 경고</h2>${data.events.length?data.events.slice(0,100).map(e=>`<p><strong>${esc(e.date)} · ${esc(e.symbol)} · ${e.policy==='high95-approach-v2'?({trailing:'트레일링',ma5:'MA5',ma10:'MA10'}[e.kind]+' 0.5 ATR 접근·이탈'):(e.kind==='ma5'?'MA5 접촉·이탈':'트레일링 접촉·이탈')}</strong><br>${esc(e.reason)} · 종가 ${WorkbenchFormat.money(e.close,e.currency)} / 기준 ${WorkbenchFormat.money(e.threshold,e.currency)} ${esc(e.currency)}${e.alert_threshold!=null?' / 알림 기준 '+WorkbenchFormat.money(e.alert_threshold,e.currency):''} · ${esc(e.account)}</p>`).join(''):'<p>기록된 경고가 없습니다. 자료 보류는 정상 판정을 뜻하지 않습니다.</p>'}</div></section>`;
  positions.forEach((p,i)=>{const plot=host.querySelector('#trailing-chart-'+i);plot._scenarioChart=mountChart(plot,host.querySelector('#trailing-legend-'+i),p);});
  if(window.WorkbenchScenarios)await WorkbenchScenarios.mountRisk(host,positions);
 }
 function stockLines(data,{market,symbol,currency}){
  return (data.positions||[]).filter(p=>p.status!=='closed'&&p.market===market&&((p.quote_symbol||p.symbol)===symbol||(!p.quote_symbol&&market==='KR'&&/^[0-9]{6}\.K[QS]$/.test(symbol)&&p.symbol===symbol.slice(0,6)))&&p.currency===currency&&Number(p.quantity)>0).flatMap(p=>{
   const detail=`${p.account} · 잔고 ${p.account_as_of||'미확인'} · 가격 ${p.last_date||p.prices?.at(-1)?.date||'미확인'}${p.issue?' · 판정 보류: '+(labels[p.issue]||p.issue):''}`;
   const values=[],cost=Number(p.average_cost),line=trailingPrice(p);
   if(Number.isFinite(cost)&&cost>0)values.push({price:cost,color:'#75c9ce',label:'장부 평단',detail});
   if(line!=null)values.push({price:line,color:yellow,label:'최종 트레일링',detail});
   return values;
  });
 }
 function attachStock({series,host,market,symbol,currency,isCurrent}){
  const legend=document.createElement('div');legend.className='holding-price-legend';host.append(legend);
  let dead=false,timer,refs=[],signature='',levels=[];
  series.applyOptions({autoscaleInfoProvider:original=>{
   const info=original();if(info&&levels.length)info.priceRange={minValue:Math.min(info.priceRange.minValue,...levels),maxValue:Math.max(info.priceRange.maxValue,...levels)};return info;
  }});
  const clear=()=>{refs.forEach(ref=>series.removePriceLine(ref));refs=[];levels=[];};
  async function refresh(){
   if(dead||!isCurrent())return;
   try{
    const response=await fetch('/api/trailing',{cache:'no-store'});if(!response.ok)throw Error();
    const data=await response.json();if(dead||!isCurrent())return;
    const lines=stockLines(data,{market,symbol,currency}),next=JSON.stringify(lines);
    if(next!==signature){
     clear();levels=lines.map(line=>line.price);legend.replaceChildren();
     for(const line of lines){
      refs.push(series.createPriceLine({price:line.price,color:line.color,lineWidth:2,lineStyle:LightweightCharts.LineStyle.Dotted,axisLabelVisible:true,title:''}));
      const span=document.createElement('span');span.style.color=line.color;
      span.textContent=`${line.label} ${WorkbenchFormat.money(line.price,currency)} · ${line.detail}`;legend.append(span);
     }
     signature=next;
    }
   }catch{if(!dead&&isCurrent()){clear();signature='';legend.textContent='보유 기준선 조회 실패 · 다시 확인 중';}}
   finally{if(!dead&&isCurrent())timer=setTimeout(refresh,15000);}
  }
  refresh();
  return ()=>{dead=true;clearTimeout(timer);clear();legend.remove();};
 }
 return {mount,series,mountChart,stockLines,attachStock};
})();

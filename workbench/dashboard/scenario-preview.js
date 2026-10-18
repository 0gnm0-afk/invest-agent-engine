/* Saved drawing preview. No evaluation, market fetch, or writes. */
window.ScenarioPreview=(()=>{
 const labels={lower:'하단',upper:'상단',entry:'진입',stop:'무효화',target:'목표',line:'기준선'};
 const day=d=>d.toISOString().slice(0,10);
 function nativeBars(m,bars){
  const rows=new Map((m.geometry_bars||[]).map(b=>[b.date,{...b}]));
  for(const b of bars){const d=new Date(b.date+'T00:00:00Z');if(m.timeframe==='1w')d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7);if(m.timeframe==='1mo')d.setUTCDate(1);
   const key=day(d),old=rows.get(key);rows.set(key,{date:key,last_session:old?.last_session>b.date?old.last_session:b.date});}
  return [...rows.values()].sort((a,b)=>a.date.localeCompare(b.date));
 }
 function project(m,bars,dates){
  const d=m.drawing;if(!m.active||!d||m.issue==='saved_drawing_deleted_or_type_changed'||m.issue==='market_identity_or_basis_changed')return [];
  const slots=DrawingTools.timeline(nativeBars(m,bars),m.timeframe),p=d.points,x=p.map(v=>DrawingTools.index(v.time,slots,m.timeframe));
  if(x.some(v=>v==null))return [];
  const times=(d.kind==='riskreward'?p:p.slice(0,2)).map(v=>v.time).sort(),ext=DrawingTools.extensionBounds(d,slots,m.timeframe);
  const start=d.kind==='horizontal'?dates[0]:d.extend?ext.left:times[0],end=d.kind==='horizontal'?dates.at(-1):d.extend?ext.right:times.at(-1);
  const slope=x[1]===x[0]?null:(p[1]?.price-p[0].price)/(x[1]-x[0]);
  const samples=dates.filter(t=>t>=start&&t<=end).map(time=>{
   let values={};const at=DrawingTools.index(time,slots,m.timeframe);if(at==null)return {time,values};
   if(d.kind==='box')values={lower:Math.min(...p.map(v=>v.price)),upper:Math.max(...p.map(v=>v.price))};
   else if(d.kind==='riskreward')values={entry:p[0].price,stop:p[1].price,target:p[2].price};
   else if(d.kind==='horizontal')values={line:p[0].price};
   else if(slope!=null){const base=p[0].price+slope*(at-x[0]);if(d.kind==='channel'){const other=base+p[2].price-(p[0].price+slope*(x[2]-x[0]));values={lower:Math.min(base,other),upper:Math.max(base,other)};}else values={line:base};}
   return {time,values};
  });
  const keys=[...new Set(samples.flatMap(s=>Object.keys(s.values)))];
  return keys.map(key=>({label:(m.title||m.name||m.stock_key)+' · '+labels[key]+(m.boundary==='both'||m.boundary===key?' (감시)':''),currency:m.currency||m.identity.currency,color:key==='stop'?'#ee6677':key==='target'?'#36c595':d.color||'#cb97eb',options:{title:'',lineWidth:2,lineStyle:2,lastValueVisible:false},data:samples.filter(s=>Number.isFinite(s.values[key])).map(s=>({time:s.time,value:s.values[key]}))}));
 }
 function model(bars,monitors){
  if(!bars.length)return {dates:[],series:[],from:0,to:0};
  const last=bars.at(-1).date,until=new Date(last+'T00:00:00Z');until.setUTCDate(until.getUTCDate()+30);
  let end=day(until);for(const m of monitors)if(m.active){for(const p of m.drawing?.points||[])if(p.time>end)end=p.time;if(m.drawing?.extend&&m.drawing.extension?.right>end)end=m.drawing.extension.right;}
  const max=new Date(last+'T00:00:00Z');max.setUTCFullYear(max.getUTCFullYear()+5);if(end>day(max))end=day(max);
  const dates=bars.map(b=>b.date),cursor=new Date(last+'T00:00:00Z');while(day(cursor)<end){cursor.setUTCDate(cursor.getUTCDate()+1);if(![0,6].includes(cursor.getUTCDay()))dates.push(day(cursor));}
  const cutoff=new Date(last+'T00:00:00Z');cutoff.setUTCMonth(cutoff.getUTCMonth()-1);
  const from=Math.max(0,bars.findIndex(b=>b.date>=day(cutoff)));
  const groups=monitors.map(m=>({monitor:m,lines:project(m,bars,dates.slice(from))}));
  return {dates,groups,series:groups.flatMap(g=>g.lines),from,to:dates.length+2};
 }
 function mount(plot,bars,monitors,{currency,trailing=null,holdingLegend=null}={}){
  if(plot._scenarioChart)WorkbenchCharts.remove(plot._scenarioChart);
  const preview=model(bars,monitors);
  const close={label:'종가',currency,color:'#6db8f6',options:{title:'',autoscaleInfoProvider:original=>{const info=original();if(info&&trailing!=null)info.priceRange={minValue:Math.min(info.priceRange.minValue,trailing),maxValue:Math.max(info.priceRange.maxValue,trailing)};return info;}},data:preview.dates.map((time,i)=>i<bars.length?{time,value:bars[i].close}:{time})};
  let price;
  const chart=WorkbenchCharts.create(plot,[close,...preview.series],{onReady(c,refs){price=refs[0].ref;if(trailing!=null)price.createPriceLine({price:trailing,color:'#f5b85b',lineStyle:2,lineWidth:2,title:''});if(holdingLegend)c.subscribeCrosshairMove(p=>{const value=p.time?p.seriesData.get(price)?.value:null;holdingLegend.textContent='종가 '+WorkbenchFormat.money(p.time?value:bars.at(-1)?.close,currency)+' · '+(p.time||bars.at(-1)?.date||'')+' · 최종 트레일링 '+WorkbenchFormat.money(trailing,currency);});}});
  if(chart)chart.timeScale().setVisibleLogicalRange({from:preview.from,to:preview.to});
  plot._scenarioChart=chart;
  plot.parentElement.querySelector('[data-preview-legend]')?.remove();
  const legend=document.createElement('p');legend.dataset.previewLegend='';legend.className='chart-caption';
  legend.textContent=['종가',...preview.series.map(s=>s.label)].join(' · ');plot.after(legend);
  if(chart){
   const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');plot.style.position='relative';
   Object.assign(svg.style,{position:'absolute',inset:'0',width:'100%',height:'100%',pointerEvents:'none'});svg.setAttribute('aria-hidden','true');plot.append(svg);
   const polygons=preview.groups.flatMap(g=>{
    const pairs=g.monitor.drawing?.kind==='riskreward'?[[0,1,'#ee6677'],[0,2,'#36c595']]:g.lines.length===2?[[0,1,g.monitor.drawing.color]]:[];
    return pairs.filter(([a,b])=>g.lines[a]&&g.lines[b]).map(([a,b,color])=>{const polygon=document.createElementNS(ns,'polygon');polygon.setAttribute('fill',color||'#218775');polygon.setAttribute('fill-opacity','.10');polygon.setAttribute('stroke',color||'#218775');polygon.setAttribute('stroke-width','1');svg.append(polygon);return {polygon,points:[...g.lines[a].data,...g.lines[b].data.slice().reverse()]};});
   });
   function paint(){if(!plot.isConnected||plot._scenarioChart!==chart){svg.remove();return;}svg.style.width=chart.timeScale().width()+'px';svg.style.height=chart.panes()[0].getHeight()+'px';svg.style.overflow='hidden';for(const item of polygons){const pts=item.points.map(p=>[chart.timeScale().timeToCoordinate(p.time),price.priceToCoordinate(p.value)]).filter(p=>p.every(Number.isFinite));item.polygon.setAttribute('points',pts.map(p=>p.join(',')).join(' '));}requestAnimationFrame(paint);}requestAnimationFrame(paint);
  }
  return preview;
 }
 return {model,project,mount};
})();

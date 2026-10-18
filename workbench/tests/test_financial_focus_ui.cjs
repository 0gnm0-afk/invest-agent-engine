const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Element{
 constructor(dataset={}){this.dataset=dataset;this.style={};this.offsetHeight=160;this.innerHTML='';this.textContent='';this.isConnected=true;this.children=new Map();}
 getBoundingClientRect(){return {left:30,top:30};}
 querySelector(s){if(!this.children.has(s))this.children.set(s,new Element());return this.children.get(s);}
 querySelectorAll(s){
  if(s==='.financial-actions button')return [this.querySelector('#financial-load'),this.querySelector('#financial-refresh')];
  const m=s.match(/^\[data-focus-(frame|comparison|cell)\]$/);if(!m)return [];
  const content=this.querySelector('#financial-content').innerHTML,key='focus'+m[1][0].toUpperCase()+m[1].slice(1);this.lists||=new Map();
  if(!this.lists.has(s+content))this.lists.set(s+content,[...content.matchAll(new RegExp(`data-focus-${m[1]}="([^"]+)"`,'g'))].map(x=>new Element({[key]:x[1]})));
  return this.lists.get(s+content);
 }
}
function harness(result,packet={state:'complete',result}){
 const root=new Element(),charts=[],ctx={innerWidth:1200,innerHeight:800,console,setTimeout,clearTimeout,document:{querySelector:()=>root},fetch:async()=>({ok:true,json:async()=>packet}),WorkbenchCharts:{remove(){},create(el,series,options){if(!series.some(s=>s.data.some(p=>p.value!=null)))return;const chart={series,panes(){return [{setHeight(){}},{setHeight(){}}];},applyOptions(o){this.options=o;},timeScale(){return {applyOptions(){}};},subscribeClick(fn){this.click=fn;},subscribeCrosshairMove(fn){this.move=fn;}};charts.push(chart);options.onReady(chart);return chart;}}};
 ctx.window=ctx;vm.createContext(ctx);for(const name of ['format.js','financial-focus.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dashboard',name),'utf8'),ctx);
 return {root,charts,mount:()=>ctx.WorkbenchFinancials.mount({market:result.provider==='DART'?'KR':'US',symbol:'TEST',isCurrent:()=>true})};
}
function sample(provider='SEC',eps=['basic_eps','eps']){
 const currency=provider==='SEC'?'USD':'KRW',cell=(value,kind='money',extra={})=>({value,kind,unit:kind==='percent'?'ratio':currency,source_type:'calculated',formula:'검증 산식',trend:'improved',...extra});
 const keys=['sales','operating','income',...(provider==='SEC'?eps:[])],performance_metrics=keys.map(id=>({id,label:({basic_eps:'기본 EPS',eps:'희석 EPS',sales:'매출액',operating:'영업이익',income:'순이익'})[id]}));
 const row=date=>({date,label:date,performance:Object.fromEntries(keys.map(k=>[k,cell(1234.5)])),performance_qoq:Object.fromEntries(keys.map(k=>[k,cell(1234.5)])),growth:Object.fromEntries(keys.map(k=>[k,cell(.25,'percent')])),qoq:Object.fromEntries(keys.map(k=>[k,cell(null,'percent',{display_text:'적자축소'})])),metrics:{operating_margin:cell(.2,'percent',{label:'영업이익률'}),debt_ratio:cell(.5,'percent',{label:'부채비율',definition:'총부채 ÷ 자본총계'}),icr:cell(null,'multiple',{label:'ICR',reason:'이자비용 미확보'})},values:{...Object.fromEntries(keys.map(k=>[k,cell(1234.5)])),dividend:cell(.004,'money',{dividend_basis:'declared',unit:currency+'/share'}),total_equity:cell(10)},warnings:[]});
 return {calculation_version:'financial-focus-8',snapshot_id:provider+eps,collected_at:'2026-10-06T00:00:00Z',financial_as_of:'2025-12-31',provider,currency,standard:'공시',performance_metrics,annual:[2021,2022,2023,2024,2025].map(y=>row(y+'-12-31')),quarterly:[2023,2024,2025].flatMap(y=>['03-31','06-30','09-30','12-31'].map(d=>row(y+'-'+d))),limitations:[],sources:[]};
}
(async()=>{
 const unsupported=sample();unsupported.calculation_version='financial-focus-8';unsupported.format_issues=[{message:'새로운 공시 형식입니다. 코드 수정이 필요합니다.',source_id:'<script>unsafe</script>'}];
 const uh=harness(unsupported);await uh.mount();const uhtml=uh.root.querySelector('#financial-content').innerHTML;
 assert.match(uhtml,/새로운 공시 형식입니다/);assert.match(uhtml,/코드 수정이 필요합니다/);assert.match(uhtml,/&lt;script&gt;/);assert.doesNotMatch(uhtml,/<script>unsafe/);
 const fh=harness(sample(),{state:'failed',attempt:{error:'UNSUPPORTED_FINANCIAL_FORMAT'},result:null});await fh.mount();assert.match(fh.root.querySelector('#financial-status').textContent,/새로운 공시 형식입니다/);
 const nh=harness(sample(),{state:'failed',attempt:{error:'NETWORK_ERROR:filing'},result:null});await nh.mount();assert.doesNotMatch(nh.root.querySelector('#financial-status').textContent,/새로운 공시/);

 for(const provider of ['DART','SEC']){
  const r=sample(provider),before=JSON.stringify(r),h=harness(r);await h.mount();
  const html=h.root.querySelector('#financial-content').innerHTML;
  for(const label of ['수익성','성장성','안정성','순이익률','유동비율','주당배당금'])assert.ok(html.includes(label),label);
  assert.ok(html.indexOf('financial-final-table')>html.indexOf('data-chart-section="dividend"'));
  assert.match(html,/<svg/);assert.match(html,/data-period="4"/);assert.doesNotMatch(html,/pane:1|좌표|예상 매출/);
  assert.match(html,provider==='SEC'?/>1.23K<\/button>/:/>1,235원<\/button>/);
  const b=h.root.querySelectorAll('[data-focus-cell]')[0];b.onpointerenter({clientX:300,clientY:200});
  const tip=h.root.querySelector('.financial-hover');assert.equal(tip.hidden,false);assert.match(tip.innerHTML,/공시 수치|부채비율|50%/);b.onpointerleave();assert.equal(tip.hidden,true);
  h.root.querySelectorAll('[data-focus-frame]').find(x=>x.dataset.focusFrame==='quarterly').onclick();assert.match(h.root.querySelector('#financial-content').innerHTML,/data-period="11"/);
  h.root.querySelectorAll('[data-focus-comparison]').find(x=>x.dataset.focusComparison==='qoq').onclick();assert.match(h.root.querySelector('#financial-content').innerHTML,/적자축소/);
  h.root.querySelector('[data-stability]').onchange({target:{value:'current_ratio'}});assert.match(h.root.querySelector('#financial-content').innerHTML,/유동자산/);
  assert.equal(JSON.stringify(r),before);
 }
 const payout=sample();payout.calculation_version='financial-focus-8';
 for(const row of payout.annual){row.values.cash_dividends={value:200,unit:'USD'};row.values.buybacks={value:300,unit:'USD'};row.values.shareholder_return={value:500,unit:'USD'};row.metrics.shareholder_payout={value:.5,unit:'ratio',kind:'percent'};}
 const ph=harness(payout);await ph.mount();const phtml=ph.root.querySelector('#financial-content').innerHTML;
 assert.doesNotMatch(phtml,/ICR|이자보상배율|이자비용/);
 for(const label of ['현금배당 총액','자사주 매입액','주주환원 합계','총주주환원율'])assert.ok(phtml.includes(label),label);
 assert.match(phtml,/data-chart-section="returns"/);assert.match(phtml,/>500.00<\/button>/);assert.match(phtml,/>50%<\/button>/);
 const nonUS=harness(sample('DART'));await nonUS.mount();assert.doesNotMatch(nonUS.root.querySelector('#financial-content').innerHTML,/ICR|이자보상배율|data-chart-section="returns"/);
 const gaps=sample();gaps.annual[2].values.basic_eps={value:null,id:'basic_eps',unit:'USD/share',reason:'해당 기간 직접 공시 EPS 미확보'};
 gaps.annual[2].growth.basic_eps={value:null,id:'basic_eps_yoy',display_text:'비교불가',reason:'필수 공시 원값 미확보'};
 const gh=harness(gaps);await gh.mount();const gapHtml=gh.root.querySelector('#financial-content').innerHTML.split('data-chart-section="basic_eps"')[1].split('</section>')[0];
 assert.match(gapHtml,/값 없음/);assert.equal((gapHtml.match(/<rect x=/g)||[]).length,4);assert.equal((gapHtml.match(/<circle /g)||[]).length,4);assert.equal((gapHtml.match(/stroke-width="2"\/>/g)||[]).length,6);
 const colored=sample();colored.calculation_version='financial-focus-8';
 for(const row of colored.annual){
  row.value_movements={sales:{...row.values.sales,value:88,trend:'worsened'},liabilities:{value:999,unit:'USD',trend:'improved'},dividend:{...row.values.dividend,trend:'worsened'}};
  row.metrics.debt_ratio={...row.metrics.debt_ratio,trend:'worsened',change:{value:.12,unit:'percentage_point'}};
 }
 const ch=harness(colored);await ch.mount();const coloredHtml=ch.root.querySelector('#financial-content').innerHTML;
 assert.match(coloredHtml,/financial-value worsened[^>]*>88.00<\/button>/);
 assert.match(coloredHtml,/financial-value improved[^>]*>999.00<\/button>/);
 assert.match(coloredHtml,/financial-value worsened[^>]*>12%p<\/button>/);
 assert.match(coloredHtml,/width="720" height="310" style="width:720px;min-width:720px"/);
 assert.match(coloredHtml,/style="width:720px;min-width:720px"/);
 assert.doesNotMatch(coloredHtml,/preserveAspectRatio="none"/);
 const shares=sample();shares.calculation_version='financial-focus-8';
 shares.annual.forEach((r,i)=>{r.values.shares={value:(25-i*.2)*1e9,unit:'shares'};});
 shares.quarterly.at(-1).date='2026-03-31';shares.quarterly.at(-1).values.shares={value:24147000000,unit:'shares'};
 const sh=harness(shares);await sh.mount();const sht=sh.root.querySelector('#financial-content').innerHTML.split('data-chart-section="shares"')[1].split('</section>')[0];
 assert.match(sht,/Shares Outstanding/);assert.match(sht,/MRQ/);assert.match(sht,/>24.15<\/text>/);assert.match(sht,/24,147,000,000주/);assert.match(sht,/fill="none"/);
 const kr=harness(sample('DART'));await kr.mount();assert.doesNotMatch(kr.root.querySelector('#financial-content').innerHTML,/data-chart-section="shares"/);
 const one=harness(sample('SEC',['basic_eps']));await one.mount();assert.doesNotMatch(one.root.querySelector('#financial-content').innerHTML,/희석 EPS/);
 const old=harness({...sample(),calculation_version:'old'});await old.mount();assert.match(old.root.querySelector('#financial-content').innerHTML,/이전 형식/);
 for(const version of ['financial-focus-5','financial-focus-7']){
  const stale=harness({...sample(),calculation_version:version});await stale.mount();
  const html=stale.root.querySelector('#financial-content').innerHTML;
  assert.match(html,/이전 형식/);assert.match(html,/재무요약 다시 만들기/);assert.doesNotMatch(html,/data-chart-section|자료 없음/);
  assert.equal(stale.root.querySelector('#financial-refresh').hidden,false);
 }
 for(const provider of ['SEC','DART']){
  const losses=sample(provider);
  for(const frame of ['annual','quarterly']){
   losses[frame][0].values.income.value=-100;
   losses[frame][1].values.income.value=100;
   losses[frame][2].values.income.value=0;
   losses[frame][3].values.income.value=null;
  }
  const h=harness(losses);await h.mount();
  for(const frame of ['annual','quarterly']){
   if(frame==='quarterly')h.root.querySelectorAll('[data-focus-frame]').find(x=>x.dataset.focusFrame==='quarterly').onclick();
   const html=h.root.querySelector('#financial-content').innerHTML;
   const chart=html.split('data-chart-section="profit"')[1].split('</section>')[0];
   assert.equal((chart.match(/rx="2" fill="#ed6572"/g)||[]).length,1);
   assert.match(chart,/rx="2" fill="(?!#ed6572)/);
   assert.match(html,/음수\(적자\)는 빨강/);
  }
 }
 const excluded=harness({provider:'SEC',eligibility:{status:'unsupported',reason:'사업회사 재무정보를 반환할 수 없습니다.'},collected_at:'2026-10-08T00:00:00Z'});
 await excluded.mount();assert.match(excluded.root.querySelector('#financial-content').innerHTML,/사업회사 재무정보를 반환할 수 없습니다/);
 assert.equal(excluded.root.querySelector('#financial-status').textContent,'사업회사 재무정보 대상 제외');
 const scope=sample('DART');scope.performance_metrics.find(x=>x.id==='income').label='순이익 (전체)';scope.data_quality={state:'partial',history_limited:true};
 const scopeHarness=harness(scope);await scopeHarness.mount();assert.match(scopeHarness.root.querySelector('#financial-content').innerHTML,/순이익 \(전체\)/);assert.match(scopeHarness.root.querySelector('#financial-content').innerHTML,/검증된 기간만 표시/);
 console.log('Toss financial UI + business exclusion, explicit income scope and limited-history messaging PASS');
})();

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(dataset={}) { this.dataset=dataset;this.innerHTML='';this.textContent='';this.isConnected=true;this.children=new Map();this.classList={toggle(){}}; }
  querySelector(selector) { if(!this.children.has(selector))this.children.set(selector,new Element());return this.children.get(selector); }
  querySelectorAll(selector) {
    if(selector==='.financial-actions button')return [this.querySelector('#financial-load'),this.querySelector('#financial-refresh')];
    if(selector==='[data-financial-frame]')return this.frames||=(['annual','quarterly'].map(financialFrame=>new Element({financialFrame})));
    const match=selector.match(/^\[data-(metric|period-cell)\]$/);
    if(!match)return [];
    const attr=match[1],key=attr==='metric'?'metric':'periodCell',content=attr==='metric'?this.querySelector('#financial-content').innerHTML:this.querySelector('#financial-period-table').innerHTML;
    this.lists||=new Map();const cacheKey=selector+content;
    if(!this.lists.has(cacheKey))this.lists.set(cacheKey,[...content.matchAll(new RegExp(`data-${attr}="([^"]+)"`,'g'))].map(m=>new Element({[key]:m[1]})));
    return this.lists.get(cacheKey);
  }
  setAttribute(name,value) { this[name]=value; }
}

function harness(result) {
  const root=new Element(),charts=[];
  const context={window:null,document:{querySelector:()=>root},console,setTimeout,clearTimeout,fetch:async()=>({ok:true,json:async()=>({state:'succeeded',result})}),WorkbenchCharts:{remove(){},create(el,series,options){
    const chart={el,series,options,timeScale(){return {applyOptions(){}};},subscribeCrosshairMove(fn){this.move=fn;},subscribeClick(fn){this.click=fn;}};charts.push(chart);options.onReady?.(chart);return chart;
  }},WorkbenchHelp:{term:(text,help)=>`<span data-help="${String(help).replaceAll('"','&quot;')}">${text}</span>`,financial(cell){assert.equal(typeof cell.id,'string');return [cell.label,cell.formula,cell.reason].filter(Boolean).join('\n');}}};
  context.window=context;vm.createContext(context);
  for(const file of ['format.js','financials.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dashboard',file),'utf8'),context);
  return {root,charts,context,mount:()=>context.WorkbenchFinancials.mount({market:'US',symbol:'TEST',isCurrent:()=>true})};
}
const fact=(value,unit='USD',extras={})=>({value,unit,formula:'공시 원값',end:'2025-12-31',sources:[],...extras});
const growth=(value,extras={})=>fact(value,'ratio',{kind:'percent',formula:'전년 동기 대비',...extras});
function row(date,index,basis='basic') {
  return {date,label:date.slice(0,4)+' '+date.slice(5,7),values:{sales:fact(27_240_000+index*1_000_000,'USD',{account_label:'매출(순영업수익)',account:'영업수익',basis:'bank_net_revenue',revenue_note:'순영업수익 기준; 일반기업 매출과 다름'}),operating:fact(index*100_000),income:fact(-5_400_000),eps:fact(-.65,'USD/share'),basic_eps:fact(-.56,'USD/share'),display_eps:fact(basis==='basic'?-.56:-.65,'USD/share',{eps_basis:basis}),shares:fact(9_395_817,'shares')},growth:{sales:growth(.125),operating:growth(null,{display_text:'흑자전환'}),income:growth(null,{display_text:'적자축소'}),eps:growth(null,{display_text:'적자확대'})}};
}
const summary=(id,label,value,unit='USD',extras={})=>({id,label,kind:'money',group:'실적',...fact(value,unit,extras)});
const result={snapshot_id:'new',collected_at:'2026-10-04T00:00:00Z',recalculated_at:'2026-10-04T02:00:00Z',financial_as_of:'2026-06-30',price_as_of:'2026-09-30',standard:'US GAAP · 연결',provider:'SEC',currency:'USD',sources:[],limitations:[],eps_basis:{annual:'basic',quarterly:'diluted'},annual:[2020,2021,2022,2023,2024,2025].map((year,i)=>row(`${year}-12-31`,i)),quarterly:[2024,2025,2026].flatMap(year=>['03-31','06-30','09-30','12-31'].map((day,i)=>row(`${year}-${day}`,i,'diluted'))),summary:[summary('sales','매출 · TTM',27_240_000),summary('eps','기본 EPS · TTM',-.56,'USD/share',{eps_basis:'basic'}),summary('income_growth_4','순이익 전년동기 대비',null,'ratio',{kind:'percent',display_text:'적자축소'}),summary('institution','기관 지분/변화',null,'ratio'),summary('perf252','1년 수익률',.12,'ratio',{kind:'percent'}),summary('forward_pe','Forward P/E',12,'ratio'),summary('eps_next_y','EPS next Y',2,'USD/share'),summary('target_price','목표 주가',99)]};

(async()=>{
  const before=JSON.stringify(result),h=harness(result);await h.mount();
  const html=h.root.querySelector('#financial-content').innerHTML,table=h.root.querySelector('#financial-period-table').innerHTML;
  assert.equal((html.match(/class="financial-group"/g)||[]).length,6);
  assert.match(html,/27\.24 M USD/);assert.doesNotMatch(html,/0\.03 B USD/);
  assert.match(html,/-0\.56/);assert.match(html,/적자축소/);assert.match(html,/기관 지분\/변화/);assert.match(html,/1년 수익률/);
  assert.doesNotMatch(html,/data-metric="(?:forward_pe|eps_next_y|target_price)"/);
  assert.match(table,/US GAAP · 연결/);assert.match(table,/기본 EPS \(USD\/주\)/);assert.match(table,/12\.5%/);
  assert.match(table,/영업수익/);assert.match(table,/흑자전환/);assert.match(table,/적자축소/);assert.match(table,/적자확대/);assert.match(table,/9,395,817/);
  assert.doesNotMatch(table,/2022-12-31/);assert.match(table,/2023-12-31/);assert.match(table,/2025-12-31/);
  assert.equal(h.charts.length,3);assert.equal(h.charts[0].series[0].data.length,3);assert.equal(h.charts[1].series.length,2);
  assert.equal(h.charts[2].series[0].data[0].value,-.56);assert.equal(h.charts[0].series[0].options.priceFormat.formatter(1.2),'1.20');
  h.root.frames.find(b=>b.dataset.financialFrame==='quarterly').onclick();
  const quarters=h.root.querySelector('#financial-period-table').innerHTML;
  assert.match(quarters,/희석 EPS \(USD\/주\)/);assert.doesNotMatch(quarters,/2024-12-31/);assert.match(quarters,/2025-03-31/);assert.match(quarters,/2026-12-31/);
  assert.equal(h.charts.at(-1).series[0].data.length,8);assert.equal(h.charts.at(-1).series[0].data[0].value,-.65);
  assert.equal(JSON.stringify(result),before,'Rendering must not modify persisted API values.');
  h.charts.at(-3).click({time:'2026-12-31'});
  assert.match(h.root.querySelector('#financial-evidence-body').innerHTML,/공시 계정: 영업수익/);
  assert.match(h.root.querySelector('#financial-evidence-body').innerHTML,/매출 기준: 순영업수익 기준; 일반기업 매출과 다름/);
  assert.match(h.root.querySelector('#financial-status').textContent,/수집 .*재계산/);
  assert.match(h.root.querySelector('#financial-title-sales').textContent,/매출\(순영업수익\)/);

  const legacy=JSON.parse(JSON.stringify(result));legacy.snapshot_id='old';delete legacy.eps_basis;
  for(const item of [...legacy.annual,...legacy.quarterly]){delete item.values.display_eps;delete item.growth;delete item.values.sales.account_label;delete item.values.sales.revenue_basis;}
  const old=harness(legacy);await old.mount();assert.equal(old.charts.at(-1).series[0].data[0].value,-.65);assert.match(old.root.querySelector('#financial-period-table').innerHTML,/전년 대비/);
  const kr=JSON.parse(JSON.stringify(result));kr.snapshot_id='kr';kr.currency='KRW';kr.standard='K-IFRS · 연결';kr.provider='DART';
  for(const item of [...kr.annual,...kr.quarterly])for(const cell of Object.values(item.values))if(cell.unit.startsWith('USD'))cell.unit=cell.unit.replace('USD','KRW');
  kr.annual.at(-1).values.display_eps.value=3183.5;
  const korean=harness(kr);await korean.mount();assert.match(korean.root.querySelector('#financial-period-table').innerHTML,/3,184/);assert.match(korean.root.querySelector('#financial-title-eps').textContent,/K-IFRS · 연결 · 기본 EPS/);assert.equal(korean.charts.at(-1).series[0].options.priceFormat.formatter(3183.5),'3,184');
  const empty=harness({...result,snapshot_id:'empty',annual:[],quarterly:[],summary:[]});await empty.mount();assert.match(empty.root.querySelector('#financial-period-table').innerHTML,/표시할 공시 기간이 없습니다/);
  const bankResult={...result,snapshot_id:'bank-status',supplemented_at:'2026-10-04T01:00:00Z',coverage_metadata:{annual_years:[2023,2024,2025],reported_eps_years:[2020,2021,2022,2023,2024,2025],notes:['재무제표 원문은 2023년 이후, 공시 EPS는 2020년 이후 확보했습니다.']},summary:[
    summary('current','유동비율',null,'ratio',{status:'not_applicable',reason:'은행은 유동성 순서로 자산·부채를 표시합니다.'}),
    summary('institution','기관 지분/변화',null,'ratio',{status:'unavailable',reason:'별도 공시자료를 아직 연결하지 않았습니다.'}),
    summary('dividend','주당배당 · 최근 연간',1234.5,'USD/share',{formula:'확정 연간 보통주 주당배당금'}),
    summary('payout','배당성향 · 최근 연간',.35,'ratio',{kind:'percent',formula:'동일 회계연도 보통주 배당 / 순이익'}),
    summary('eps_cagr5','공시 EPS 5년 CAGR',.0834,'ratio',{kind:'percent',status:'comparison_warning',eps_basis:'reported',report_year:2025,report_column:'frmtrm',period_name:'2024년',comparison_note:'과거 공시 EPS의 종류·회계정책 기준이 다를 수 있습니다.'})]};
  const bank=harness(bankResult);await bank.mount();const bankHtml=bank.root.querySelector('#financial-content').innerHTML;
  assert.match(bankHtml,/업종 비적용/);assert.match(bankHtml,/자료 미확보/);assert.match(bankHtml,/class="financial-value not-applicable"/);
  assert.match(bankHtml,/주당배당 · 최근 연간/);assert.match(bankHtml,/1,234\.50/);assert.match(bankHtml,/배당성향 · 최근 연간/);
  assert.match(bankHtml,/8\.34%/);assert.match(bankHtml,/기준 확인/);assert.match(bankHtml,/과거 공시 EPS의 종류·회계정책 기준이 다를 수 있습니다/);assert.match(bankHtml,/재무제표 원문은 2023년 이후/);
  assert.match(bank.root.querySelector('#financial-status').textContent,/수집 .*자료 보완 .*재계산/);
  bank.root.querySelectorAll('[data-metric]').find(b=>b.dataset.metric==='current').onclick();
  assert.match(bank.root.querySelector('#financial-evidence-body').innerHTML,/업종 비적용/);assert.match(bank.root.querySelector('#financial-evidence-body').innerHTML,/유동성 순서/);
  bank.root.querySelectorAll('[data-metric]').find(b=>b.dataset.metric==='eps_cagr5').onclick();
  assert.match(bank.root.querySelector('#financial-evidence-body').innerHTML,/EPS 기준: 공시 EPS/);assert.match(bank.root.querySelector('#financial-evidence-body').innerHTML,/공시 비교열: 전기/);assert.match(bank.root.querySelector('#financial-evidence-body').innerHTML,/공시 기간: 2024년/);
  const qualityResult={...result,snapshot_id:'quality',summary:[
    summary('dividend','지급 주당배당 · TTM',.495,'USD/share',{dividend_basis:'paid'}),
    summary('pe','P/E · 실적 분기 합산 TTM',null,'ratio',{reason:'필수 입력 결측',missing_inputs:[{reason:'TTM 미확보',causes:[{reason:'직접 분기 EPS 미확보'}]}]}),
    summary('sales','매출 · TTM',27951000,'USD',{revision_history:[{value:27950687,filed:'2025-02-01',explained_by_split_or_rounding:true}]})]};
  const qualityBefore=JSON.stringify(qualityResult),quality=harness(qualityResult);await quality.mount();
  const qualityHtml=quality.root.querySelector('#financial-content').innerHTML;
  assert.match(qualityHtml,/배당 기준: 지급/);assert.match(qualityHtml,/직접 분기 EPS 미확보/);
  quality.root.querySelectorAll('[data-metric]').find(b=>b.dataset.metric==='sales').onclick();
  assert.match(quality.root.querySelector('#financial-evidence-body').innerHTML,/27,950,687\.00/);
  assert.match(quality.root.querySelector('#financial-evidence-body').innerHTML,/분할·공시 정밀도 범위에서 일치/);
  assert.equal(JSON.stringify(qualityResult),qualityBefore);
  console.log('Financial UI: six columns, actuals/growth, EPS/dividend basis, 3y/8q, bank evidence, missing causes, revision history and immutability: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});

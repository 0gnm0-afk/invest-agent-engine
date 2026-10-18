const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const node = () => ({classList:{toggle(){}},replaceChildren(){},append(){}});
async function check(code, expected) {
  const banner=node();
  const state={enabled:true,view_ready:true,state:'failed',error:code,display_stale:true,reused:true,targets:{}};
  const context=vm.createContext({$:()=>banner,esc:String,num:String,stamp:String,
    fetch:async()=>({ok:true,json:async()=>state}),location:{hash:'#stock/US/AAA'},
    document:{createElement:node},setTimeout(){},clearTimeout(){}});
  const app=fs.readFileSync(path.join(root,'dashboard/app.js'),'utf8');
  vm.runInContext(app.slice(app.indexOf('async function pollDaily()')),context);
  await context.pollDaily();
  assert.ok(banner.innerHTML.includes(expected));
  assert.ok(banner.innerHTML.includes('같은 날에는 자동으로 재시도하지 않습니다'));
  const elements={};const calls=[];
  const chartContext=vm.createContext({window:{dispatchEvent(){}},Event:class{constructor(type){this.type=type;}},
    document:{querySelector:s=>elements[s]??=(node()),querySelectorAll:()=>[]},
    WorkbenchCharts:{clear(){}},WorkbenchWorkspace:{closeActive:async()=>true,mount:async()=>({range(){}})},
    fetch:async(url,opts)=>{calls.push([url,opts?.method||'GET']);return {ok:true,json:async()=>
      url==='/health'?{token:'synthetic'}:url.startsWith('/api/chart-history')?{history:{state:'failed'}}:
      {bars:[],history:{state:'failed',reason:code,daily_count:0}}};}});
  vm.runInContext(fs.readFileSync(path.join(root,'dashboard/stock-chart.js'),'utf8'),chartContext);
  await chartContext.window.WorkbenchStockChart.mount({stock:{},market:'US',symbol:'AAA',currency:'USD',isCurrent:()=>true});
  assert.ok(elements['#history-status'].textContent.includes(expected));
  assert.equal(calls.filter(([,method])=>method==='POST').length,1);
}
(async()=>{
  await check('market_authentication_http_403_ip_not_allowed','허용 IP');
  await check('market_http_403_ip_not_allowed','허용 IP');
  await check('market_authentication_http_403','인증 거절');
  console.log('PASS: daily banner and chart status, 3 error cases each; fixture-only fetches');
})().catch(e=>{console.error(e);process.exitCode=1;});

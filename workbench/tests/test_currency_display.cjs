const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
global.window = global;
vm.runInThisContext(fs.readFileSync(path.join(__dirname,'../dashboard/format.js'),'utf8'));
for (const [value,currency,expected] of [[1234.5,'KRW','1,235'],[-1234.5,'KRW','-1,235'],
  [1234.5,'USD','1,234.50'],[0,'KRW','0'],[0,'USD','0.00'],[-.001,'USD','0.00'],
  [null,'KRW','미확인'],['','USD','미확인'],[NaN,'KRW','미확인'],['1,234.5','USD','1,234.50']])
  assert.equal(WorkbenchFormat.money(value,currency),expected);
assert.equal(WorkbenchFormat.priceText('하단 1234.50 / 상단 2000.00 · 3.5% · 2026-09-30','KRW'),
  '하단 1,235 / 상단 2,000 · 3.5% · 2026-09-30');
const raw = {pivot:1234.5, features:{ma:{20:1200.5}}, volume:1234.5,structure_risk_pct:.03456};
const original=JSON.stringify(raw), formatted=WorkbenchFormat.analysis(raw,'KRW');
assert.equal(formatted.pivot,'1,235');assert.equal(formatted.features.ma[20],'1,201');
assert.equal(formatted.volume,1234.5);assert.equal(formatted.structure_risk_pct,.03456);
assert.equal(JSON.stringify(raw),original);
// Exercise the actual chart adapter including price axis/crosshair and legend.
let refs, move;
global.LightweightCharts={CrosshairMode:{Normal:0},createChart(){refs=[];return {
  addSeries(type,options){const ref={options,setData(data){this.data=data;}};refs.push(ref);return ref;},
  timeScale(){return {fitContent(){}};},subscribeCrosshairMove(fn){move=fn;},remove(){}};}};
vm.runInThisContext(fs.readFileSync(path.join(__dirname,'../dashboard/charts.js'),'utf8'));
for(const currency of ['KRW','USD']) {
  const data=[{time:'2026-09-30',open:1234.5,high:1235.5,low:1233.5,close:1234.5}],legend={};
  WorkbenchCharts.create({},[{type:'candles',currency,label:currency,data}],{legend});
  const expected=currency==='KRW'?'1,235':'1,234.50';
  assert.equal(refs[0].options.priceFormat.formatter(1234.5),expected);
  assert.equal(refs[0].data[0].close,1234.5);
  move({time:'2026-09-30',seriesData:new Map([[refs[0],data[0]]])});
  assert.ok(legend.textContent.includes(expected));
}
WorkbenchCharts.create({},[{label:'지수',data:[{time:'2026-09-30',value:100.12}]}]);
assert.equal(refs[0].options.priceFormat,undefined);
console.log('Currency formatting, non-mutation, chart axis/legend, sector isolation: PASS');

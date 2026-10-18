'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
global.window={};
vm.runInThisContext(fs.readFileSync('dashboard/format.js','utf8'));
global.WorkbenchFormat=window.WorkbenchFormat;
vm.runInThisContext(fs.readFileSync('dashboard/scenario-monitor.js','utf8'));
assert.deepEqual(window.WorkbenchScenarios.monitoringPlans([
 {id:'legacy',plan_id:'pl_legacy',plan:{account_alias:'preserve',allocation:{quantity:20}}},
 {id:'monitor',plan_id:'pl_monitor',monitoring_only:true},
 {id:'not_a_flag',plan_id:'pl_bad',monitoring_only:'true'}
]).map(r=>r.id),['monitor']);
const monitor={id:'sm_fixture',revision:2,stock_key:'US:SYNTH',case_id:'an_fixture',identity:{currency:'USD',name:'합성 <script>bad()</script>',price_basis:'adjusted'},purpose:'buy',active:true,status:'warning',drawing_id:'channel_fixture',boundary:'lower',workspace_revision:4,timeframe:'1w',plan_record_id:'it_fixture',tolerance_atr:.5,confirmed_response:null,latest:{date:'2026-10-07',close:100.25,atr:2.5,tolerance:1.25,lines:[{name:'lower',value:100},{name:'upper',value:110}],states:{lower:'approach',upper:'clear'}},conversion:{status:'pending'},test_record:true};
let html=window.WorkbenchScenarios.monitorHTML(monitor);
assert.match(html,/100\.25/);assert.match(html,/100\.00/);assert.match(html,/110\.00/);
assert.match(html,/0\.5 × ATR14 = 1\.25/);assert.match(html,/접근/);assert.match(html,/매수 감시 유지/);
assert.match(html,/검증용/);assert.match(html,/대응 기준 미정/);assert(!html.includes('<script>'));
assert.match(html,/작도 channel_fixture · 경계 하단 · 저장 v4/);
assert.match(html,/#stock\/US\/SYNTH\?analysis=an_fixture/);
html=window.WorkbenchScenarios.monitorHTML({...monitor,latest:{...monitor.latest,states:{zone:'reached'}}});
const zoneLevels=html.match(/<div class="scenario-levels">([\s\S]*?)<\/div>/)[1];
assert.match(zoneLevels,/<strong>하단<\/strong> 100\.00 <\/span>/);
assert.match(zoneLevels,/<strong>상단<\/strong> 110\.00 <\/span>/);
assert.match(zoneLevels,/<strong>매수 구간 상태<\/strong> <em>구간 내부 도달<\/em>/);
assert.equal((zoneLevels.match(/도달/g)||[]).length,1);
assert.equal((zoneLevels.match(/<em>/g)||[]).length,1); // Region status is never copied onto each boundary.
html=window.WorkbenchScenarios.monitorHTML({...monitor,purpose:'holding',conversion:{status:'accepted'},latest:{...monitor.latest,superseded:true}});
assert(!html.includes('data-convert='));assert.match(html,/변경 전 기준의 과거 판정/);
html=window.WorkbenchScenarios.monitorHTML({...monitor,issue:'holding_unconfirmed',identity:{currency:'KRW'},latest:{...monitor.latest,lines:[{name:'lower',value:1234.6}]}});
assert.match(html,/1,235/);assert.match(html,/현재 판정은 보류/);
html=window.WorkbenchScenarios.monitorHTML({...monitor,latest:null,active:false,conversion:{status:'declined'}});
assert.match(html,/미확인/);assert(!html.includes('data-convert='));assert.match(html,/감시 재개/);
const evaluations=[
 {revision:1,date:'2026-10-01',close:99999,lines:[{name:'lower',value:88888}]},
 {revision:2,date:'2026-10-05',close:100,lines:[{name:'lower',value:98}]},
 {revision:2,date:'2026-10-06',close:102,lines:[{name:'lower',value:null}]},
 {revision:2,date:'2026-10-08',close:105,lines:[{name:'lower',value:101}]}
];
const svg=window.WorkbenchScenarios.trendHTML({...monitor,evaluations},'USD');
assert.match(svg,/role="img"/);assert.match(svg,/2026-10-05/);assert(!svg.includes('2026-10-01'));
assert.match(svg,/종가 105\.00/);assert.match(svg,/하단 101\.00/);assert(!svg.includes('99,999'));
const seriesPaths=[...svg.matchAll(/<path d="([^"]+)" fill="none"/g)].map(m=>m[1]);
assert.equal((seriesPaths[0].match(/L/g)||[]).length,1); // Only Oct 5 -> 6, never bridges the missing Oct 7.
assert.equal((seriesPaths[0].match(/M/g)||[]).length,2);
assert.equal((seriesPaths[1].match(/L/g)||[]).length,0); // Null boundary is not zero or an interpolated point.
assert.equal(window.WorkbenchScenarios.trendHTML({...monitor,evaluations:[evaluations[0],evaluations[1]]},'USD'),'');
vm.runInThisContext(fs.readFileSync('dashboard/analysis.js','utf8'));
global.location={hash:'#stock/US/SYNTH'};
let requested;
global.fetch=async(url,options)=>({ok:true,json:async()=>{
 if(url==='/health')return {token:'synthetic'};
 const payload=JSON.parse(options.body);
 if(payload.op==='list')return {cases:[{id:'an_old',created_at:'2026-10-01',test_record:false},{id:'an_test',created_at:'2026-10-09',test_record:true},{id:'an_new',created_at:'2026-10-08',test_record:false}]};
 requested=payload.arguments.analysis_key;return {case:{id:requested}};
}});
(async()=>{
 await window.WorkbenchAnalysis.initial({market:'US',symbol:'SYNTH'});assert.equal(requested,'an_new');
 location.hash='#stock/US/SYNTH?analysis=an_old';await window.WorkbenchAnalysis.initial({market:'US',symbol:'SYNTH'});assert.equal(requested,'an_old');
 console.log('scenario UI: latest real analysis resume, explicit history link, per-date boundary values, currency/XSS, pending conversion, superseded/deferred results PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});

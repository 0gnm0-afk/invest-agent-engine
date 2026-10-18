const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
global.window=global;
for(const f of ['format.js','drawing-tools.js'])vm.runInThisContext(fs.readFileSync(path.join(__dirname,'../dashboard',f),'utf8'));
const bars=Array.from({length:10},(_,i)=>({date:`2026-09-${String(i+1).padStart(2,'0')}`}));
assert.deepEqual(DrawingTools.channel([{x:0,y:100},{x:20,y:80},{x:10,y:70}]),[{x:0,y:100},{x:20,y:80},{x:20,y:60},{x:0,y:80}]);
assert.match(DrawingTools.measure([{time:bars[0].date,price:1000},{time:bars[5].date,price:1200}],bars,'USD'),/\+200.00 USD · \+20.00% · 5봉 간격 · 5일/);
assert.match(DrawingTools.measure([{time:bars[2].date,price:1200},{time:bars[0].date,price:1000}],bars,'KRW'),/-200 KRW · -16.67% · 2봉 간격 · 2일/);
class Element {
  constructor(){this.events={};this.attrs={};this.style={};this.classList={add(){}};}
  append(child){this.child=child;}setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}
  replaceChildren(){}addEventListener(name,fn){this.events[name]=fn;}remove(){}
  getBoundingClientRect(){return {left:0,top:0,width:100,height:200};}
  setPointerCapture(){}hasPointerCapture(){return true;}releasePointerCapture(){}
}
global.document={createElementNS:()=>new Element()};global.requestAnimationFrame=()=>1;global.cancelAnimationFrame=()=>{};
let shapes=[],commits=0,lastBefore;
const el=new Element(),chart={timeScale:()=>({width:()=>100,coordinateToLogical:x=>x/10,logicalToCoordinate:i=>i*10}),panes:()=>[{getHeight:()=>200}]};
const drawing=DrawingTools.attach({element:el,chart,series:{coordinateToPrice:y=>200-y,priceToCoordinate:p=>200-p},bars,currency:'KRW',getDrawings:()=>shapes,
 onChange:(next,options={})=>{shapes=next;if(!options.preview){commits++;lastBefore=options.before;}},onSelect(){},onHint(){}});
const svg=el.child;
function event(x,y,target={closest:()=>null}){return {clientX:x,clientY:y,button:0,pointerId:1,target,preventDefault(){},stopPropagation(){}};}
for(const kind of ['trend','channel','horizontal','box','measure','riskreward']) {
 drawing.setMode(kind);
 (kind==='riskreward'?[[10,100],[30,120],[50,60]]:[[10,100],[30,80],[20,60]]).slice(0,DrawingTools.count[kind]).forEach(([x,y])=>svg.events.pointerdown(event(x,y)));
 assert.equal(shapes.at(-1).kind,kind);
}
assert.equal(commits,6);
const id=shapes[0].id,original=JSON.parse(JSON.stringify(shapes[0]));drawing.select(id);
const handle={closest:()=>({getAttribute:()=>id,classList:{contains:()=>false}}),hasAttribute:()=>true,getAttribute:()=> '1'};
svg.events.pointerdown(event(30,80,handle));svg.events.pointermove(event(40,70));svg.events.pointerup(event(40,70));
assert.deepEqual(shapes[0].points,[original.points[0],{time:bars[4].date,price:130}]);assert.deepEqual(lastBefore[0],original);
// Whole-object translation keeps both anchors' distances; cancellation restores it.
const body={closest:()=>({getAttribute:()=>id,classList:{contains:()=>false}}),hasAttribute:()=>false};
const before=JSON.parse(JSON.stringify(shapes));
svg.events.pointerdown(event(20,90,body));svg.events.pointermove(event(30,80));svg.events.pointerup(event(30,80));
assert.equal(shapes[0].points[0].price,before[0].points[0].price+10);
assert.equal(shapes[0].points[1].price,before[0].points[1].price+10);
const translated=JSON.parse(JSON.stringify(shapes));
svg.events.pointerdown(event(20,90,body));svg.events.pointermove(event(30,80));drawing.cancel();assert.deepEqual(shapes,translated);
drawing.setMode('box');svg.events.pointerdown(event(20,100));drawing.cancel();assert.equal(shapes.length,6);
// Risk/reward handle editing, invalid crossing, translation and cancellation.
const ri=shapes.findIndex(s=>s.kind==='riskreward'),rid=shapes[ri].id;
const rrHandle={closest:()=>({getAttribute:()=>rid,classList:{contains:()=>false}}),hasAttribute:()=>true,getAttribute:()=> '2'};
const rrBody={closest:()=>({getAttribute:()=>rid,classList:{contains:()=>false}}),hasAttribute:()=>false};
drawing.select(rid);
svg.events.pointerdown(event(50,60,rrHandle));svg.events.pointermove(event(60,40));svg.events.pointerup(event(60,40));
assert.equal(DrawingTools.riskReward(shapes[ri].points,'USD').ratio,3);
const rrValid=JSON.parse(JSON.stringify(shapes)),cc=commits;
svg.events.pointerdown(event(60,40,rrHandle));svg.events.pointermove(event(60,130));svg.events.pointerup(event(60,130));
assert.deepEqual(shapes,rrValid);assert.equal(commits,cc);
svg.events.pointerdown(event(30,90,rrBody));svg.events.pointermove(event(40,80));svg.events.pointerup(event(40,80));
assert.equal(DrawingTools.riskReward(shapes[ri].points,'USD').ratio,3);
assert.equal(shapes[ri].points[0].price,rrValid[ri].points[0].price+10);
assert.equal(shapes[ri].points[0].time,bars[2].date);
const rrMoved=JSON.parse(JSON.stringify(shapes));
svg.events.pointerdown(event(40,80,rrBody));svg.events.pointermove(event(50,70));drawing.cancel();assert.deepEqual(shapes,rrMoved);
drawing.destroy();console.log('All six tools, channel geometry, KRW/USD measurement, handle/whole drag and cancellation: PASS');

const rr=(prices)=>prices.map((price,i)=>({time:bars[i].date,price}));
assert.equal(DrawingTools.riskReward(rr([100,90,120]),'USD').ratio,2);
assert.equal(DrawingTools.riskReward(rr([100,110,80]),'USD').direction,'매도');
assert.match(DrawingTools.riskReward(rr([1000,900,1250]),'KRW').profit,/250 KRW/);
assert.match(DrawingTools.riskReward(rr([100,90,120]),'USD').profit,/20.00 USD/);
for(const prices of [[100,100,120],[100,90,80],[100,110,120],[100,90,100]])assert.equal(DrawingTools.riskReward(rr(prices),'USD'),null);
assert.equal(DrawingTools.riskReward(rr([100,90,120]).map(p=>({...p,time:bars[0].date})),'USD'),null);
console.log('Risk/reward long/short, ratio, units and invalid geometry: PASS');

// Every tool can be created beyond the last real candle; handle/body drag also cross it.
const axis=DrawingTools.timeline(bars), futureEl=new Element();let futureShapes=[];
const futureChart={...chart,timeScale:()=>({width:()=>100,coordinateToLogical:x=>x/2,logicalToCoordinate:i=>i*2})};
const futureTool=DrawingTools.attach({element:futureEl,chart:futureChart,series:{coordinateToPrice:y=>200-y,priceToCoordinate:p=>200-p},bars:axis,currency:'USD',getDrawings:()=>futureShapes,onChange:next=>{futureShapes=next;},onSelect(){},onHint(){}});
for(const kind of Object.keys(DrawingTools.count)){
 futureTool.setMode(kind);
 (kind==='riskreward'?[[16,100],[40,120],[60,60]]:[[16,100],[40,80],[60,60]]).slice(0,DrawingTools.count[kind]).forEach(([x,y])=>futureEl.child.events.pointerdown(event(x,y)));
 assert.equal(futureShapes.at(-1).kind,kind);
 if(kind!=='horizontal')assert.ok(futureShapes.at(-1).points.some(p=>p.time>bars.at(-1).date));
}
const fid=futureShapes[0].id, fbody={closest:()=>({getAttribute:()=>fid,classList:{contains:()=>false}}),hasAttribute:()=>false};
futureTool.select(fid);const old=structuredClone(futureShapes[0]);
futureEl.child.events.pointerdown(event(20,90,fbody));futureEl.child.events.pointermove(event(50,80));futureEl.child.events.pointerup(event(50,80));
assert.ok(futureShapes[0].points.every(p=>p.time>bars.at(-1).date));
assert.equal(DrawingTools.index(futureShapes[0].points[1].time,axis)-DrawingTools.index(old.points[1].time,axis),15);
assert.match(DrawingTools.measure(futureShapes[0].points,axis,'USD'),/미래 달력/);
const fh={closest:()=>({getAttribute:()=>fid,classList:{contains:()=>false}}),hasAttribute:()=>true,getAttribute:()=> '1'};
futureEl.child.events.pointerdown(event(70,70,fh));futureEl.child.events.pointermove(event(80,60));futureEl.child.events.pointerup(event(80,60));
assert.equal(futureShapes[0].points[1].time,axis[40].date);
futureTool.destroy();console.log('Future tool creation, whole-object/handle movement and measurement: PASS');

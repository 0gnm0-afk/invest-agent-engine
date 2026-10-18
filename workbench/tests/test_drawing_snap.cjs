const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const root=require('node:path').resolve(__dirname,'..');global.window=global;
for(const f of ['format.js','drawing-tools.js'])vm.runInThisContext(fs.readFileSync(root+'/dashboard/'+f,'utf8'));
const D=DrawingTools;
const bars=[
 {date:'2026-09-28',open:90,high:135,low:70,close:100.123456},
 {date:'2026-09-29',open:110,high:140,low:80,close:120},
 {date:'2026-09-30',open:119,high:150,low:60,close:125},
 {date:'2026-10-01',projected:true},{date:'2026-10-02',projected:true}
];
const original=JSON.stringify(bars);
const scale={width:()=>200,logicalToCoordinate:i=>i*40,coordinateToLogical:x=>x/40,getVisibleLogicalRange:()=>({from:0,to:4})};
const series={priceToCoordinate:p=>200-p,coordinateToPrice:y=>200-y};
for(const field of ['open','high','low','close']){
 const snap=D.candleSnap(bars,scale,series,1,200-bars[1][field]+1);
 assert.equal(snap.i,1);assert.equal(snap.time,bars[1].date);assert.equal(snap.field,field);assert.equal(snap.price,bars[1][field]);
}
assert.equal(D.candleSnap(bars,scale,series,0,100).price,100.123456,'source price precision is preserved');
assert.equal(D.candleSnap(bars,scale,series,2,80).price,119,'nearer previous close cannot replace current candle OHLC');
assert.equal(D.candleSnap(bars,scale,series,1,100),null,'previous close alone never attracts the anchor');
assert.equal(D.candleSnap(bars,scale,series,0,60).price,135,'closer right-hand high cannot replace the current high');
assert.equal(D.candleSnap(bars,scale,series,1.49,60).i,1,'nearest date before the half-slot boundary');
assert.equal(D.candleSnap(bars,scale,series,1.51,50).i,2,'nearest date after the half-slot boundary');
let held=D.candleSnap(bars,scale,series,1,60);
assert.equal(D.candleSnap(bars,scale,series,1,54).price,140,'acquire at six pixels');
assert.equal(D.candleSnap(bars,scale,series,1,53),null,'do not acquire beyond six pixels');
assert.equal(D.candleSnap(bars,scale,series,1,50,held).price,140,'retain the same nearest candidate at ten pixels');
assert.equal(D.candleSnap(bars,scale,series,1,49,held),null,'release beyond ten pixels');
assert.equal(D.candleSnap(bars,scale,series,2,61,held),null,'changing date immediately releases a previous candle');
held=D.candleSnap(bars,scale,series,1,90);
assert.equal(D.candleSnap(bars,scale,series,1,83,held).field,'close','a nearer OHLC field replaces the held field');
const separated=[{date:'2026-09-28',open:100,close:118}];
held=D.candleSnap(separated,scale,series,0,100);
assert.equal(D.candleSnap(separated,scale,series,0,91,held).field,'open','a tied nearest held field remains stable');
assert.equal(D.candleSnap(separated,scale,series,0,90,held),null,'a new nearest field must meet acquisition distance');
assert.equal(D.candleSnap(bars,scale,series,3,80),null,'future whitespace never supplies OHLC');
for(const bad of [null,undefined,NaN,Infinity,0,-1]){
 const missing=[{date:'2026-09-28',open:bad,high:bad,low:bad,close:bad}];
 assert.equal(D.candleSnap(missing,scale,series,0,200),null,'invalid prices are not zero');
}
assert.equal(D.candleSnap(bars,scale,series,NaN,80),null);
assert.equal(D.candleSnap(bars,scale,series,1,Infinity),null);
assert.equal(D.candleSnap(bars,{...scale,logicalToCoordinate:()=>-1},series,1,60),null,'offscreen anchor excluded');
assert.equal(D.candleSnap(bars,scale,{priceToCoordinate:()=>null},1,60),null,'unprojectable anchor excluded');
assert.equal(D.closeReference(bars,scale,series,4,96).price,100.123456,'reference keeps source precision');
assert.equal(D.closeReference(bars,scale,series,0,100),null,'reference requires a past candle');
assert.equal(D.closeReference(bars,scale,series,1,80),null,'current close is not a past reference');
assert.equal(D.closeReference(bars,{...scale,getVisibleLogicalRange:()=>({from:1,to:4})},series,4,100),null,'offscreen closes excluded');
assert.equal(D.closeReference([{date:'2026-09-28',close:100,projected:true}],scale,series,4,100),null,'projected close excluded');
assert.equal(D.closeReference([{date:'2026-09-28',close:null}],scale,series,4,200),null,'missing reference is not zero');
assert.equal(D.closeReference([{date:'2026-09-28',close:120},bars[1]],scale,series,4,80).i,1,'equal references prefer the recent candle');
assert.equal(JSON.stringify(bars),original,'source candles are immutable');

class Element {
 constructor(){this.events={};this.attrs={};this.style={};this.classList={add(){},contains:()=>false};this.children=[];}
 append(c){this.children.push(c);}setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}replaceChildren(){this.children=[];}
 addEventListener(k,f){this.events[k]=f;}remove(){}getBoundingClientRect(){return {left:0,top:0,width:200,height:200};}
 setPointerCapture(){}hasPointerCapture(){return true;}releasePointerCapture(){}
}
let render;global.document={createElementNS:()=>new Element()};global.requestAnimationFrame=f=>(render=f,1);global.cancelAnimationFrame=()=>{};
const el=new Element();let shapes=[];
const chart={timeScale:()=>scale,panes:()=>[{getHeight:()=>200}]};
const tool=D.attach({element:el,chart,series,bars,currency:'USD',getDrawings:()=>shapes,onChange:n=>{shapes=n;},onSelect(){},onHint(){}});
const svg=el.children[0],event=(x,y,extra={})=>({clientX:x,clientY:y,button:0,pointerId:1,target:{closest:()=>null},preventDefault(){},stopPropagation(){},...extra});
const guide=name=>svg.children.find(e=>e.attrs.class===name);
tool.setMode('trend');svg.events.pointermove(event(40,62));render();
assert.equal(guide('candle-snap-guide').attrs['data-field'],'high');
assert.equal(guide('candle-snap-guide').attrs['data-source-date'],bars[1].date);
assert.match(guide('candle-snap-guide').children.at(-1).textContent,/고가 140.00 USD/);
assert.equal(guide('drawing-hover-point').attrs.cx,40);assert.equal(guide('drawing-hover-point').attrs.cy,60);
svg.events.pointerdown(event(40,62));svg.events.pointerdown(event(80,141));
assert.deepEqual(shapes[0].points,[{time:bars[1].date,price:140},{time:bars[2].date,price:60}],'new points attach to each hovered candle high and low');
tool.setMode('trend');svg.events.pointermove(event(120,96));render();
const reference=guide('close-reference-guide'),crosshair=guide('drawing-crosshair');
assert.ok(reference,'past close has a passive dotted reference in future space');
assert.equal(reference.attrs['data-source-date'],bars[0].date);
assert.equal(reference.children.length,1,'past reference does not imitate an anchor label or point');
assert.equal(reference.children[0].attrs['stroke-dasharray'],'3 4');
assert.equal(reference.children[0].attrs.x1,0);assert.equal(reference.children[0].attrs.x2,200,'reference extends to the chart right edge');
assert.equal(reference.children[0].attrs.y1,200-bars[0].close,'reference stays at historical price');
assert.equal(guide('candle-snap-guide'),undefined);assert.equal(guide('drawing-hover-point').attrs.cy,96,'reference never moves the point');
assert.equal(crosshair.children[0].attrs.y1,96);assert.equal(crosshair.children[1].attrs.x1,120,'dotted cursor follows the chosen date');
svg.events.pointerdown(event(120,96));svg.events.pointerdown(event(160,82));
assert.deepEqual(shapes[1].points,[{time:'2026-10-01',price:104},{time:'2026-10-02',price:118}],'future anchors keep raw prices despite nearby past closes');
tool.setMode('horizontal');svg.events.pointerdown(event(40,62,{altKey:true}));assert.equal(shapes.at(-1).points[0].price,138,'Alt bypasses current-candle snap');
tool.setSnapEnabled(false);tool.setMode('horizontal');svg.events.pointerdown(event(40,62));assert.equal(shapes.at(-1).points[0].price,138,'toggle off bypasses snap');
tool.setMode('horizontal');svg.events.pointermove(event(120,96,{altKey:true}));render();
assert.ok(guide('close-reference-guide'),'passive reference remains available when snap is disabled');
assert.equal(guide('candle-snap-guide'),undefined);
tool.setSnapEnabled(true);svg.events.pointermove(event(40,62));render();assert.ok(guide('candle-snap-guide'));
svg.events.pointerleave();render();assert.equal(guide('candle-snap-guide'),undefined);assert.equal(guide('drawing-crosshair'),undefined,'leaving clears transient cursor');
svg.events.pointermove(event(120,96));render();tool.cancel();render();assert.equal(guide('close-reference-guide'),undefined,'cancel clears passive reference');
tool.setMode('horizontal');svg.events.pointermove(event(120,96));render();tool.setMode('select');render();assert.equal(guide('close-reference-guide'),undefined,'selection clears passive reference');
const id=shapes[0].id,group={getAttribute:()=>id,classList:{contains:()=>false}};
const handle={closest:()=>group,hasAttribute:()=>true,getAttribute:()=> '1'};
tool.select(id);svg.events.pointerdown(event(80,140,{target:handle}));svg.events.pointermove(event(80,48));render();
assert.equal(guide('candle-snap-guide').attrs['data-field'],'high');
svg.events.pointerup(event(80,48));
assert.deepEqual(shapes[0].points[1],{time:bars[2].date,price:150},'handle uses current candle high');
const before=structuredClone(shapes),body={closest:()=>group,hasAttribute:()=>false};
svg.events.pointerdown(event(80,48,{target:body}));svg.events.pointermove(event(80,51));render();
assert.equal(guide('candle-snap-guide'),undefined,'whole-object movement has no snapping');
svg.events.pointerup(event(80,51));
assert.equal(shapes[0].points[0].price,before[0].points[0].price-3);assert.equal(shapes[0].points[1].price,before[0].points[1].price-3,'whole-object geometry preserved');
const translated=structuredClone(shapes);svg.events.pointerdown(event(80,51,{target:handle}));svg.events.pointermove(event(80,78));tool.cancel();assert.deepEqual(shapes,translated,'cancel restores exact points');
assert.equal(JSON.stringify(bars),original,'interaction does not mutate market data');
tool.destroy();

for(const [timeframe,start,next,end,day] of [
 ['1w','2026-09-07','2026-09-14','2026-09-11','2026-09-10'],
 ['1mo','2026-09-01','2026-10-01','2026-09-30','2026-09-20']
]){
 const frameBars=[{date:start,first_session:start,last_session:end,anchorTimes:{high:end},open:110,high:140,low:80,close:120},{date:next,projected:true}];
 const frameScale={...scale,logicalToCoordinate:i=>80+i*40,coordinateToLogical:x=>(x-80)/40};
 const logical=D.index(day,frameBars,timeframe),x=frameScale.logicalToCoordinate(logical);
 assert.equal(D.timeAt(logical,frameBars,timeframe),day,'aggregate candle internal date round-trips');
 const frameEl=new Element();let saved=[];
 const frameTool=D.attach({element:frameEl,chart:{...chart,timeScale:()=>frameScale},series,bars:frameBars,currency:'USD',timeframe,getDrawings:()=>saved,onChange:n=>{saved=n;},onSelect(){},onHint(){}});
 frameTool.setMode('horizontal');frameEl.children[0].events.pointerdown(event(x,62));
 assert.deepEqual(saved[0].points,[{time:end,price:140}],timeframe+' snap stores actual OHLC date instead of mouse date');
 frameTool.destroy();
}
console.log('Candle OHLC snap: nearest date/field, thresholds, exact prices, passive past reference, future space, Alt/toggle, editing, cancellation and week/month projection PASS');

const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
global.window=global;
for(const f of ['format.js','drawing-tools.js'])vm.runInThisContext(fs.readFileSync(__dirname+'/../dashboard/'+f,'utf8'));
const D=DrawingTools;
const daily=[
 {date:'2025-04-07',open:141.55,high:152.85,low:140.53,close:146.75},
 {date:'2025-04-08',open:151.22,high:152.24,low:143.03,close:144.7},
 {date:'2025-04-09',open:144.415,high:159.55,low:143.905,close:158.71},
 {date:'2025-04-10',open:156.54,high:157.72,low:149.93,close:152.82},
 {date:'2025-04-11',open:152.9,high:157.67,low:152.82,close:157.14},
 {date:'2025-05-06',open:170,high:179,low:160,close:175},
 {date:'2025-05-07',open:175,high:185,low:170,close:180}
];
const agg=(rows,date)=>({date,first_session:rows[0].date,last_session:rows.at(-1).date,sessions:rows.length,
 open:rows[0].open,high:Math.max(...rows.map(b=>b.high)),low:Math.min(...rows.map(b=>b.low)),close:rows.at(-1).close});
const weekly=[agg(daily.slice(0,5),'2025-04-07'),agg(daily.slice(5),'2025-05-05')];
const monthly=[agg(daily.slice(0,5),'2025-04-01'),agg(daily.slice(5),'2025-05-01')];
const frames={'1d':daily,'1w':weekly,'1mo':monthly};
const before=JSON.stringify(frames);
const expected={open:'2025-04-07',high:'2025-04-09',low:'2025-04-07',close:'2025-04-11'};
const scale={width:()=>400,coordinateToLogical:x=>(x-80)/40,logicalToCoordinate:i=>80+i*40,getVisibleLogicalRange:()=>({from:0,to:8})};
const series={priceToCoordinate:p=>400-2*p,coordinateToPrice:y=>(400-y)/2};
for(const frame of ['1w','1mo']){
 const bars=D.withAnchorTimes(frames[frame],daily,frame);
 assert.deepEqual(bars[0].anchorTimes,expected);
 for(const field of Object.keys(expected)){
  const hit=D.candleSnap(bars,scale,series,.25,series.priceToCoordinate(bars[0][field]),null,frame);
  assert.equal(hit.time,expected[field]);assert.equal(hit.price,bars[0][field]);
  const point={time:hit.time,price:hit.price},saved=JSON.stringify(point);
  for(const target of ['1d','1w','1mo']){
   const axis=D.timeline(D.withAnchorTimes(frames[target],daily,target),target);
   const x=D.pointIndex(point,axis,target);
   assert.equal(x+0,target==='1d'?daily.findIndex(b=>b.date===point.time):0,'OHLC anchor lands on source candle in every frame');
   assert.equal(JSON.stringify(point),saved,'projection never rewrites a saved point');
  }
 }
 assert.equal(D.candleSnap(frames[frame],scale,series,0,series.priceToCoordinate(140.53),null,frame),null,'no guessed dates without daily evidence');
 const missing=D.withAnchorTimes(frames[frame],daily.filter(b=>b.date!=='2025-04-09'),frame);
 assert.equal(missing[0].anchorTimes,undefined,'missing daily candle refuses source join');
 const changed=D.withAnchorTimes([{...frames[frame][0],high:160}],daily,frame);
 assert.equal(changed[0].anchorTimes,undefined,'price basis mismatch refuses source join');
 assert.equal(bars[1].anchorTimes.open,'2025-05-06','holiday opening maps to first real session');
 assert.equal(bars[1].anchorTimes.close,'2025-05-07','partial period closes at last actual session');
}
const ties=[{date:'2025-04-07',open:100,high:120,low:80,close:90},{date:'2025-04-08',open:95,high:120,low:80,close:110}];
assert.equal(D.withAnchorTimes([agg(ties,'2025-04-07')],ties,'1w')[0].anchorTimes.low,'2025-04-07','equal extrema use first occurrence consistently');
class Element{
 constructor(){this.children=[];this.events={};this.attrs={};this.style={};this.classList={add(){},contains:()=>false};}
 append(v){this.children.push(v);}setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}
 replaceChildren(){this.children=[];}addEventListener(k,v){this.events[k]=v;}remove(){}
 getBoundingClientRect(){return {left:0,top:0,width:400,height:400};}
 setPointerCapture(){}hasPointerCapture(){return true;}releasePointerCapture(){}
}
let render;global.document={createElementNS:()=>new Element()};global.requestAnimationFrame=f=>(render=f,1);global.cancelAnimationFrame=()=>{};
const event=(x,y,extra={})=>({clientX:x,clientY:y,button:0,pointerId:1,target:{closest:()=>null},preventDefault(){},stopPropagation(){},...extra});
let creations=0;
for(const origin of ['1d','1w','1mo'])for(const field of ['open','high','low','close']){
 const el=new Element(),axis=D.timeline(D.withAnchorTimes(frames[origin],daily,origin),origin);let shapes=[];
 const attach=(element,bars,timeframe)=>D.attach({element,bars,timeframe,chart:{timeScale:()=>scale,panes:()=>[{getHeight:()=>400}]},series,currency:'USD',getDrawings:()=>shapes,onChange:v=>shapes=v,onHint(){},onSelect(){}});
 let tool=attach(el,axis,origin);tool.setMode('trend');
 const firstIndex=origin==='1d'?daily.findIndex(b=>b.date===expected[field]):0;
 const secondIndex=origin==='1d'?5:1;
 el.children[0].events.pointerdown(event(scale.logicalToCoordinate(firstIndex)+(origin==='1d'?0:10),series.priceToCoordinate(axis[firstIndex][field])));
 el.children[0].events.pointerdown(event(scale.logicalToCoordinate(secondIndex),series.priceToCoordinate(axis[secondIndex].low)));
 assert.equal(shapes.length,1);assert.equal(shapes[0].points[0].time,expected[field]);creations++;
 const stored=JSON.stringify(shapes);
 tool.destroy();
 for(const target of ['1d','1w','1mo']){
  shapes=JSON.parse(stored);
  const host=new Element();tool=attach(host,D.timeline(D.withAnchorTimes(frames[target],daily,target),target),target);
  tool.select(shapes[0].id);render();
  const group=host.children[0].children.find(n=>n.attrs['data-id']===shapes[0].id);
  assert.ok(group,'saved line remains visible after switch');
  const handle=group.children.find(n=>n.attrs['data-point']===0);
  const expectedIndex=target==='1d'?daily.findIndex(b=>b.date===expected[field]):0;
  assert.equal(handle.attrs.cx,scale.logicalToCoordinate(expectedIndex));assert.equal(handle.attrs.cy,series.priceToCoordinate(shapes[0].points[0].price));
  assert.equal(JSON.stringify(shapes),stored);tool.destroy();
 }
}
assert.equal(creations,12);

for(const kind of ['trend','channel']){
 const host=new Element(),points=[{time:'2025-04-07',price:140.53},{time:'2025-04-09',price:159.55}];
 if(kind==='channel')points.push({time:'2025-04-11',price:190});
 const shapes=[{id:'same-period',kind,points,color:'#218775',width:2,extend:false}],saved=JSON.stringify(shapes);
 const tool=D.attach({element:host,bars:D.timeline(D.withAnchorTimes(monthly,daily,'1mo'),'1mo'),timeframe:'1mo',
 chart:{timeScale:()=>scale,panes:()=>[{getHeight:()=>400}]},series,currency:'USD',getDrawings:()=>shapes,onChange(){throw Error('render must not save');},onHint(){},onSelect(){}});
 render();
 const group=host.children[0].children.find(n=>n.attrs['data-id']==='same-period');
 assert.ok(group);const line=group.children.find(n=>n.attrs.x1!=null);
 assert.ok(Number.isFinite(line.attrs.x1)&&Number.isFinite(line.attrs.x2));
 assert.notEqual(line.attrs.x1,line.attrs.x2,'different daily anchors in one month remain visible');
 assert.equal(JSON.stringify(shapes),saved);tool.destroy();
}

assert.equal(JSON.stringify(frames),before);
console.log('OHLC anchors: 12 creations, 36 frame projections, exact date/price reload, holidays/partial periods/ties/missing-source PASS');

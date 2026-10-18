const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
global.window=global;
for(const name of ['format.js','drawing-tools.js'])vm.runInThisContext(fs.readFileSync(path.join(__dirname,'../dashboard',name),'utf8'));
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.events={};this.style={};this.classList={add(){}};}
  append(child){this.children.push(child);}
  setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k];}
  replaceChildren(){this.children=[];}addEventListener(k,fn){this.events[k]=fn;}remove(){}
  querySelectorAll(){return this.children.flatMap(c=>[c,...c.querySelectorAll()]);}
  getBoundingClientRect(){return {left:0,top:0,width:100,height:200};}
  setPointerCapture(){this.captured=true;}hasPointerCapture(){return this.captured;}releasePointerCapture(){this.captured=false;}
}
let pending;
global.document={createElementNS:(_,tag)=>new Element(tag)};
global.requestAnimationFrame=fn=>{pending=fn;return 1;};global.cancelAnimationFrame=()=>{};
const color='#218775';
function fixture(drawings,frame='1d',shift=0){
  const bars=Array.from({length:6},(_,i)=>({date:frame==='1mo'?`2026-${String(i+1).padStart(2,'0')}-01`:
    frame==='1w'?new Date(Date.UTC(2026,8,1+i*7)).toISOString().slice(0,10):`2026-09-${String(i+1).padStart(2,'0')}`}));
  let shapes=drawings(bars),changes=0,commits=0;
  const original=structuredClone(shapes),el=new Element('div'),scale={width:()=>100,logicalToCoordinate:i=>i*10+shift,coordinateToLogical:x=>(x-shift)/10};
  const tool=DrawingTools.attach({element:el,chart:{timeScale:()=>scale,panes:()=>[{getHeight:()=>200}]},
    series:{priceToCoordinate:p=>200-p,coordinateToPrice:y=>200-y},bars,timeframe:frame,currency:'USD',getDrawings:()=>shapes,
    onChange:(next,options={})=>{shapes=next;changes++;if(!options.preview)commits++;},onSelect(){},onHint(){}});
  const svg=el.children[0];
  return {tool,svg,bars,original,get shapes(){return shapes;},get changes(){return changes;},get commits(){return commits;},
    render(){pending();return svg.querySelectorAll().filter(n=>n.tag==='line'&&n.attrs.stroke===color).map(n=>({
      x1:+n.attrs.x1,y1:+n.attrs.y1,x2:+n.attrs.x2,y2:+n.attrs.y2,dash:n.attrs['stroke-dasharray']}));}};
}
function drawing(bars,kind='trend',extend=true,reverse=false){
  const points=[{time:bars[1].date,price:80},{time:bars[3].date,price:120}];
  if(reverse)points.reverse();if(kind==='riskreward'){points[0].price=100;points[1].price=80;points.push({time:bars[2].date,price:140});}
  if(kind==='channel')points.push({time:bars[2].date,price:130});
  return {id:'test',kind,points,color,width:2,extend,extension:{left:bars[0].date,right:bars[5].date}};
}
// The saved anchors stay solid, while both projections share exactly their slope.
for(const frame of ['1d','1w','1mo'])for(const reverse of [false,true]){
  const f=fixture(bars=>[drawing(bars,'trend',true,reverse)],frame),lines=f.render(),left=frame==='1d'?10:6,right=frame==='1d'?30:26;
  assert.equal(lines.length,3);
  const edgeLeft=frame==='1d'?0:-4,edgeRight=frame==='1d'?50:46;
  for(const [actual,expected] of lines.slice(0,2).map((l,i)=>[l,i===0?{x1:edgeLeft,y1:120+(left-edgeLeft)*2,x2:left,y2:120,dash:'5 4'}:
    {x1:right,y1:80,x2:edgeRight,y2:80-(edgeRight-right)*2,dash:'5 4'}]))for(const key of Object.keys(expected)){
    if(key==='dash')assert.equal(actual[key],expected[key]);else assert.ok(Math.abs(actual[key]-expected[key])<1e-8);
  }
  assert.equal(lines[2].dash,'');assert.deepEqual([lines[2].x1,lines[2].x2],reverse?[right,left]:[left,right]);
  assert.deepEqual(f.shapes,f.original);assert.equal(f.changes,0);f.tool.destroy();
}
// Channel width is constant; its fill remains in the anchored span.
for(const reverse of [false,true]){
  const f=fixture(bars=>[drawing(bars,'channel',true,reverse)]),lines=f.render();assert.equal(lines.length,9);
  for(const i of [0,1,2]){assert.equal(lines[i+3].y1,lines[i].y1-30);assert.equal(lines[i+3].y2,lines[i].y2-30);
    assert.equal(lines[i+6].y1,lines[i].y1-15);assert.equal(lines[i+6].y2,lines[i].y2-15);}
  assert.equal(lines[2].dash,'');assert.equal(lines[5].dash,'');assert.equal(lines[8].dash,'5 4');
  const polygon=f.svg.querySelectorAll().find(n=>n.tag==='polygon');
  assert.equal(polygon.attrs['fill-opacity'],'0.05');
  assert.equal(f.svg.querySelectorAll().filter(n=>n.tag==='polygon'&&n.attrs['fill-opacity']==='0.025').length,2);
  assert.equal(polygon.attrs.points,reverse?'30,80 10,120 10,90 30,50':'10,120 30,80 30,50 10,90');
  f.tool.destroy();
}
// Opting out preserves old finite drawings, including channel boundaries.
for(const kind of ['trend','channel']){
  const f=fixture(bars=>[drawing(bars,kind,false)]),lines=f.render();assert.equal(lines.length,kind==='trend'?1:3);
  assert.ok(lines.every(l=>l.x1===10&&l.x2===30));f.tool.destroy();
}
// Pan never lengthens a finite extension to the viewport edge.
for(const shift of [-100,100]){
  const f=fixture(bars=>[drawing(bars)],'1d',shift),lines=f.render();assert.equal(lines.length,3);
  assert.equal(lines[0].x1,shift);assert.equal(lines[1].x2,50+shift);
  for(const l of lines)assert.equal((l.y2-l.y1)/(l.x2-l.x1),-2);f.tool.destroy();
}
// Horizontal/flat trends and endpoints on the viewport edge produce no duplicate tail.
const flat=fixture(bars=>[{...drawing(bars),points:[{time:bars[0].date,price:100},{time:bars[3].date,price:100}]}]);
assert.deepEqual(flat.render(),[{x1:30,y1:100,x2:50,y2:100,dash:'5 4'},{x1:0,y1:100,x2:30,y2:100,dash:''}]);flat.tool.destroy();
// Completing a drawing enables projections for trend/channel/box/riskreward, after the last click.
for(const kind of Object.keys(DrawingTools.count)){
  const f=fixture(()=>[]);f.tool.setMode(kind);f.render();
  const positions=kind==='riskreward'?[[10,100],[30,120],[20,60]]:[[10,120],[30,80],[20,70]];
  for(let i=0;i<DrawingTools.count[kind];i++){
    const [x,y]=positions[i];f.svg.events.pointerdown({button:0,clientX:x,clientY:y,preventDefault(){},stopPropagation(){}});
    if(i+1<DrawingTools.count[kind])assert.equal(f.shapes.length,0);
  }
  assert.equal(f.shapes.length,1);assert.equal(f.shapes[0].extend,['trend','channel','box','riskreward'].includes(kind));
  assert.deepEqual(Object.keys(f.shapes[0]).sort(),['color','extend',...(['trend','channel','box','riskreward'].includes(kind)?['extension']:[]),'id','kind','points','width']);f.tool.destroy();
}
// Flat bounds extend every level horizontally; three RR dates define its width.
for(const kind of ['box','riskreward'])for(const frame of ['1d','1w','1mo']){
  const f=fixture(bars=>[drawing(bars,kind)],frame);f.tool.select('test');f.render();
  const tails=f.svg.querySelectorAll().filter(n=>n.tag==='line'&&n.attrs['stroke-dasharray']==='5 4');
  assert.equal(tails.length,kind==='box'?4:6);
  for(const l of tails)assert.equal(l.attrs.y1,l.attrs.y2);
  assert.equal(f.svg.querySelectorAll().filter(n=>n.attrs['data-extension']).length,2);
  assert.deepEqual(f.shapes,f.original);f.tool.destroy();
}
const third=fixture(bars=>[{...drawing(bars,'riskreward'),points:[{time:bars[1].date,price:100},{time:bars[2].date,price:80},{time:bars[4].date,price:140}]}]);
third.tool.select('test');third.render();
assert.equal(DrawingTools.extensionBounds(third.shapes[0],third.bars).right,third.bars[5].date);
third.tool.destroy();
// Both handles change only their own endpoint, commit once on drop and cancel safely.
function event(x,y,target){return {clientX:x,clientY:y,button:0,pointerId:1,target,preventDefault(){},stopPropagation(){}};}
for(const kind of ['trend','channel','box','riskreward'])for(const reverse of [false,true]){
  const f=fixture(bars=>[drawing(bars,kind,true,reverse)]);f.tool.select('test');f.render();
  const handles=f.svg.querySelectorAll().filter(n=>n.attrs['data-extension']);assert.equal(handles.length,2);
  const target=side=>({closest:()=>({getAttribute:()=> 'test',classList:{contains:()=>false}}),getAttribute:k=>k==='data-extension'?side:null,hasAttribute:()=>false});
  const anchors=structuredClone(f.shapes[0].points),left=f.shapes[0].extension.left;
  f.svg.events.pointerdown(event(50,100,target('right')));f.svg.events.pointermove(event(40,-100));
  assert.equal(f.shapes[0].extension.right,f.bars[4].date);assert.equal(f.commits,0);
  f.svg.events.pointerup(event(40,-100));assert.equal(f.commits,1);assert.deepEqual(f.shapes[0].points,anchors);assert.equal(f.shapes[0].extension.left,left);
  f.svg.events.pointerdown(event(40,100,target('right')));f.svg.events.pointermove(event(50,900));f.svg.events.pointerup(event(50,900));
  assert.equal(f.shapes[0].extension.right,f.bars[5].date);assert.equal(f.commits,2);
  const before=structuredClone(f.shapes);f.svg.events.pointerdown(event(0,100,target('left')));f.svg.events.pointermove(event(10,100));f.tool.cancel();assert.deepEqual(f.shapes,before);
  f.svg.events.pointerdown(event(0,100,target('left')));f.svg.events.pointermove(event(45,100));f.svg.events.pointerup(event(45,100));
  assert.equal(f.shapes[0].extension.left,f.bars[1].date);assert.equal(f.shapes[0].extension.right,f.bars[5].date);
  const reloaded=fixture(()=>JSON.parse(JSON.stringify(f.shapes)));reloaded.render();assert.ok(reloaded.svg.querySelectorAll().some(n=>n.tag==='line'&&Number(n.attrs.x2)===50));reloaded.tool.destroy();f.tool.destroy();
}
// Legacy booleans get finite defaults without mutating stored records.
const moved=fixture(bars=>[{...drawing(bars),extension:{left:bars[0].date,right:bars[4].date}}]);
const body={closest:()=>({getAttribute:()=> 'test',classList:{contains:()=>false}}),hasAttribute:()=>false};
moved.tool.select('test');moved.svg.events.pointerdown(event(20,100,body));moved.svg.events.pointermove(event(30,90));moved.svg.events.pointerup(event(30,90));
assert.deepEqual(moved.shapes[0].extension,{left:moved.bars[1].date,right:moved.bars[5].date});
assert.deepEqual(moved.shapes[0].points,[{time:moved.bars[2].date,price:90},{time:moved.bars[4].date,price:130}]);moved.tool.destroy();
const legacy=fixture(bars=>{const d=drawing(bars);delete d.extension;return [d];});
assert.equal(legacy.render()[1].x2,50);assert.deepEqual(legacy.shapes,legacy.original);legacy.tool.destroy();
console.log('Finite extensions: independent drag/drop/cancel, preserved anchors, date persistence, timeframes, reverse order, light fill and legacy defaults: PASS');

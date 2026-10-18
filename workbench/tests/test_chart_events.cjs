const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'..');
function el(){return {style:{},dataset:{},children:[],addEventListener(k,f){this[k]=f},append(...xs){this.children.push(...xs)},insertAdjacentElement(){},remove(){this.removed=true}};}
const document={createElement:el,createTextNode:t=>({textContent:t})};const context={window:{},document,Date,Map,Math};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root,'dashboard/drawing-tools.js'),'utf8'),context);context.DrawingTools=context.window.DrawingTools;
vm.runInContext(fs.readFileSync(path.join(root,'dashboard/chart-events.js'),'utf8'),context);
const events=[{event_date:'2024-01-04',kind:'gap',timeframe:'1d',status:'confirmed',metrics:{}},{event_date:'2024-01-03',kind:'gap',timeframe:'1d',status:'confirmed',confirmed_at:'2024-01-03',gap_pct:4,full_gap:true,metrics:{}},{event_date:'2024-01-03',kind:'high',timeframe:'1w',status:'confirmed',confirmed_at:'2024-02-01',metrics:{}}];
assert.equal(context.window.WorkbenchEvents.grouped(events).length,2);
assert.equal(context.window.WorkbenchEvents.visible(events).length,1);
assert.equal(context.window.WorkbenchEvents.visible([{...events[2],status:'provisional'}]).length,0);
assert.match(context.window.WorkbenchEvents.label(events[2]),/확정/);
for(const frame of ['1d','1w','1mo']){
 const bars=frame==='1d'?[{date:'2024-01-03'},{date:'2024-01-04'}]:[{date:'2024-01-01',first_session:'2024-01-02',last_session:frame==='1w'?'2024-01-05':'2024-01-31'},{date:frame==='1w'?'2024-01-08':'2024-02-01'}];
 let primitive,removed=false,sub,unsub;
 // Actual library indices are discrete. Fractional event coordinates must interpolate.
 const scale={logicalToCoordinate:n=>Number.isInteger(n)?100+n*20:null};
 const chart={timeScale:()=>scale,subscribeCrosshairMove:f=>sub=f,unsubscribeCrosshairMove:f=>unsub=f};
 const series={attachPrimitive:p=>primitive=p,detachPrimitive:p=>{assert.equal(p,primitive);removed=true}};
 const plot=el(),cleanup=context.window.WorkbenchEvents.attach({chart,series,plot,bars,frame,packet:{events,groups:[],policy:{pivot_rule:'weekly-swing-2'}}});
 primitive.attached({requestUpdate(){}});
 let rects=[];primitive.paneViews()[0].renderer().draw({useMediaCoordinateSpace:f=>f({mediaSize:{width:800,height:300},context:{save(){},restore(){},fillRect(...a){rects.push(a)}}})});
 assert.equal(rects.length,1,frame+' same-day band');assert.ok(rects[0][2]<=5);assert.equal(rects[0][3],300);
 cleanup();assert.equal(removed,true);assert.equal(sub,unsub);
}
console.log('chart event projection/grouping/cleanup: PASS (daily, weekly, monthly)');

// The recent provisional leg is visible in every frame, without promoting it.
for(const frame of ['1d','1w','1mo']){
 const pending={...events[2],status:'provisional',confirmed_at:null,selection_state:'open_leg',period_complete:false};
 assert.equal(context.window.WorkbenchEvents.preview(pending),true);
 assert.equal(context.window.WorkbenchEvents.preview({...pending,selection_state:'data_gap'}),false);
 assert.match(context.window.WorkbenchEvents.label(pending),/진행 중인 주/);
 const bars=frame==='1d'?[{date:'2024-01-03'}]:[{date:'2024-01-01',first_session:'2024-01-02',last_session:'2024-01-05'}];
 let primitive,strokes=0,fills=0,dashes;
 const chart={timeScale:()=>({logicalToCoordinate:n=>100+n*20}),subscribeCrosshairMove(){},unsubscribeCrosshairMove(){}};
 const series={attachPrimitive:p=>primitive=p,detachPrimitive(){}};
 const cleanup=context.window.WorkbenchEvents.attach({chart,series,plot:el(),bars,frame,packet:{events:[pending,{...pending,event_date:'2024-01-04',selection_state:'data_gap'}],policy:{pivot_rule:'weekly-swing-2'}}});
 primitive.paneViews()[0].renderer().draw({useMediaCoordinateSpace:f=>f({mediaSize:{width:800,height:300},context:{save(){},restore(){},fillRect(){fills++},setLineDash(a){dashes=[...a]},beginPath(){},moveTo(){},lineTo(){},stroke(){strokes++}}})});
 assert.equal(strokes,1,frame);assert.equal(fills,0);assert.deepEqual(dashes,[4,4]);cleanup();
}
console.log('recent provisional dashed markers: PASS (daily, weekly, monthly)');

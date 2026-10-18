/* Date/price anchors; SVG projection follows the Lightweight Charts public scales. */
window.DrawingTools = (() => {
  const count = {trend:2, channel:3, horizontal:1, box:2, measure:2, riskreward:3};
  const labels = {trend:'추세선',channel:'평행 채널',horizontal:'수평선',box:'박스',measure:'구간 측정',riskreward:'손익비'};
  const extendable = kind => ['trend','channel','box','riskreward'].includes(kind);
  const anchorTimes = d => (d.kind==='riskreward'?d.points:d.points.slice(0,2)).map(p=>p.time).sort();
  const clone = x => JSON.parse(JSON.stringify(x));
  function channel(points) {
    const [a,b,c] = points, slope = (b.y-a.y)/(b.x-a.x);
    const offset = c.y - (a.y + slope*(c.x-a.x));
    return [a,b,{x:b.x,y:b.y+offset},{x:a.x,y:a.y+offset}];
  }
  function extensionBounds(d,bars,frame='1d') {
    const times=anchorTimes(d),left=times[0],right=times.at(-1);
    if(d.extension)return {left:d.extension.left<left?d.extension.left:left,right:d.extension.right>right?d.extension.right:right};
    const day=86400000,padding=Math.max(3,Math.ceil((Date.parse(right)-Date.parse(left))/day/4));
    const shift=(time,n)=>new Date(Date.parse(time)+n*day).toISOString().slice(0,10);
    return {left:shift(left,-padding)<bars[0].date?bars[0].date:shift(left,-padding),
      right:shift(right,padding)>(bars.at(-1).last_session||bars.at(-1).date)?(bars.at(-1).last_session||bars.at(-1).date):shift(right,padding)};
  }
  function boundary(bars, i, frame) {
    if(i+1<bars.length)return Date.parse(bars[i+1].date);
    const d=new Date(bars[i].date+'T00:00:00Z');
    if(frame==='1mo')d.setUTCMonth(d.getUTCMonth()+1);else d.setUTCDate(d.getUTCDate()+(frame==='1w'?7:1));
    return d.getTime();
  }
  // Calendar-only slots: no prices, volumes or indicator values are projected.
  function timeline(bars,frame='1d') {
    if(!bars.length)return [];
    const result=bars.map(b=>({...b})), last=bars.at(-1);
    const end=new Date((last.last_session||last.date)+'T00:00:00Z');end.setUTCFullYear(end.getUTCFullYear()+5);
    const cursor=new Date(last.date+'T00:00:00Z');
    const next=()=>{if(frame==='1mo')cursor.setUTCMonth(cursor.getUTCMonth()+1);else cursor.setUTCDate(cursor.getUTCDate()+(frame==='1w'?7:1));};
    next();
    while(cursor<=end){
      if(frame!=='1d'||![0,6].includes(cursor.getUTCDay()))result.push({date:cursor.toISOString().slice(0,10),projected:true});
      next();
    }
    if(frame!=='1d')for(let i=bars.length-1;i<result.length;i++){
      // A partial current week/month can also contain future drawing anchors.
      result[i].last_session=new Date(boundary(result,i,frame)-86400000).toISOString().slice(0,10);
      result[i].first_session ||= result[i].date;
    }
    result.futureAfter=last.last_session||last.date;
    return result;
  }
  function index(time,bars,frame='1d') {
    if(!Number.isFinite(Date.parse(time))||!bars.length||time<bars[0].date||time>(bars.at(-1).last_session||bars.at(-1).date))return null;
    let lo=0,hi=bars.length;
    while(lo<hi){const m=(lo+hi)>>1;if(bars[m].date<=time)lo=m+1;else hi=m;}
    const i=lo-1,start=Date.parse(bars[i].date),fraction=(Date.parse(time)-start)/(boundary(bars,i,frame)-start);
    // Preserve distinct dates even when both anchors fall inside one week/month.
    return frame==='1d'?i+fraction:i-.4+.8*fraction;
  }
  // Map aggregate OHLC to the actual daily session, without changing source bars.
  function withAnchorTimes(bars,daily,frame='1d') {
    if(frame==='1d')return bars.map(b=>({...b,anchorTimes:Object.fromEntries(['open','high','low','close'].map(k=>[k,b.date]))}));
    const groups=new Map();
    for(const b of daily){
      const d=new Date(b.date+'T00:00:00Z');
      if(frame==='1w')d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7);else d.setUTCDate(1);
      const key=d.toISOString().slice(0,10);
      if(!groups.has(key))groups.set(key,[]);
      groups.get(key).push(b);
    }
    return bars.map(b=>{
      const rows=groups.get(b.date);
      if(!rows?.length)return {...b};
      const first=rows[0],last=rows.at(-1);
      const high=rows.reduce((a,v)=>v.high>a.high?v:a);
      const low=rows.reduce((a,v)=>v.low<a.low?v:a);
      // Refuse to join different snapshots/bases or incomplete source intervals.
      if(first.date!==b.first_session||last.date!==b.last_session||
         first.open!==b.open||last.close!==b.close||high.high!==b.high||low.low!==b.low||
         (b.sessions!=null&&b.sessions!==rows.length))return {...b};
      return {...b,anchorTimes:{open:first.date,high:high.date,low:low.date,close:last.date}};
    });
  }
  function pointIndex(point,bars,frame='1d') {
    const logical=index(point.time,bars,frame);
    if(logical==null||frame==='1d')return logical;
    const i=Math.round(logical),b=bars[i];
    return b&&Object.keys(b.anchorTimes||{}).some(k=>b.anchorTimes[k]===point.time&&b[k]===point.price)?i:logical;
  }
  function coordinate(scale, logical) {
    const base=Math.floor(logical),left=scale.logicalToCoordinate(base),right=scale.logicalToCoordinate(base+1);
    return left==null||right==null?null:left+(logical-base)*(right-left);
  }
  function timeAt(logical,bars,frame='1d') {
    const i=Math.max(0,Math.min(bars.length-1,Math.round(logical)));
    if(frame==='1d')return bars[i].date;
    const start=Date.parse(bars[i].date),fraction=Math.max(0,Math.min(1,(logical-i+.4)/.8));
    const ms=Math.round((start+fraction*(boundary(bars,i,frame)-start))/86400000)*86400000;
    const time=new Date(ms).toISOString().slice(0,10);
    return time<(bars[i].first_session||bars[i].date)?(bars[i].first_session||bars[i].date):time>(bars[i].last_session||bars[i].date)?(bars[i].last_session||bars[i].date):time;
  }
  function measure(points, bars, currency, frame='1d') {
    const [a,b] = points, delta = b.price-a.price;
    const ia=index(a.time,bars,frame), ib=index(b.time,bars,frame);
    const days=Math.round((Date.parse(b.time)-Date.parse(a.time))/86400000);
    return `${delta>=0?'+':''}${WorkbenchFormat.money(delta,currency)} ${currency} · ${delta>=0?'+':''}${(delta/a.price*100).toFixed(2)}% · ${ia==null||ib==null?'미확인':Math.abs(Math.round(ib)-Math.round(ia))}${bars.futureAfter&&[a.time,b.time].some(t=>t>bars.futureAfter)?'칸 간격 (미래 달력)':'봉 간격'} · ${Math.abs(days)}일`;
  }
  function riskReward(points,currency) {
    if(points.length!==3)return null;
    const [entry,stop,target]=points.map(p=>p.price);
    if(!points.every(p=>Number.isFinite(p.price)&&p.price>0&&p.price<=1e15)||!(stop<entry&&entry<target||target<entry&&entry<stop)||new Set(points.map(p=>p.time)).size<2)return null;
    const risk=Math.abs(entry-stop),reward=Math.abs(target-entry),ratio=reward/risk;
    return {entry,stop,target,risk,reward,ratio,direction:stop<entry?'매수':'매도',
      label:'손익비 1 : '+ratio.toFixed(2),
      loss:'손실 '+WorkbenchFormat.money(risk,currency)+' '+currency+' ('+(risk/entry*100).toFixed(2)+'%)',
      profit:'수익 '+WorkbenchFormat.money(reward,currency)+' '+currency+' ('+(reward/entry*100).toFixed(2)+'%)'};
  }
  const priceLabels = {open:'시가',high:'고가',low:'저가',close:'종가'};
  // Only the candle under the pointer can supply a drawing anchor.
  function candleSnap(bars,scale,series,logical,y,previous=null,frame='1d') {
    if(!Number.isFinite(logical)||!Number.isFinite(y))return null;
    const i=Math.round(logical),b=bars[i],x=scale.logicalToCoordinate(i);
    if(!b||b.projected||x==null||x<0||x>scale.width())return null;
    let best=null;
    for(const field of Object.keys(priceLabels)){
      const price=b[field];if(!Number.isFinite(price)||price<=0)continue;
      const cy=series.priceToCoordinate(price);if(cy==null||!Number.isFinite(cy))continue;
      const time=frame==='1d'?b.date:b.anchorTimes?.[field];
      if(!time)continue;
      const c={i,time,field,price,distance:Math.abs(y-cy)};
      if(!best||c.distance<best.distance)best=c;
    }
    // Release on date/nearest-price changes; pixels are zoom independent.
    const held=best&&previous?.i===i&&previous.field===best.field;
    return best&&best.distance<=(held?10:6)?best:null;
  }
  // A nearby past close is only a visual reference, never an anchor price.
  function closeReference(bars,scale,series,logical,y) {
    if(!Number.isFinite(logical)||!Number.isFinite(y))return null;
    const visible=scale.getVisibleLogicalRange?.();
    const first=Math.max(0,Math.ceil(visible?.from??0));
    const last=Math.min(bars.length-1,Math.round(logical)-1,Math.floor(visible?.to??bars.length-1));
    let best=null;
    for(let i=last;i>=first;i--){
      const b=bars[i];if(!b||b.projected||!Number.isFinite(b.close)||b.close<=0)continue;
      const x=scale.logicalToCoordinate(i),cy=series.priceToCoordinate(b.close);
      if(x==null||cy==null||x<0||x>scale.width()||!Number.isFinite(cy))continue;
      const distance=Math.abs(y-cy);
      if(distance<=6&&(!best||distance<best.distance))best={i,time:b.date,price:b.close,distance};
    }
    return best;
  }
  function attach({element, chart, series, bars, currency, timeframe='1d', drawingLimit=300, snapEnabled=true, getDrawings, getOverlays=()=>[], onChange, onSelect, onHint}) {
    const NS='http://www.w3.org/2000/svg';
    const svg=document.createElementNS(NS,'svg');
    svg.classList.add('drawing-layer');svg.setAttribute('aria-label','가격 차트 작도 영역');
    element.append(svg);
    let mode='select', selected=null, draft=[], hover=null, drag=null, frame=0, removed=false, signature='', snapped=null, reference=null;
    function node(tag,attrs,parent=svg) {
      const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);parent.append(n);return n;
    }
    function xy(p,exact=false) {const i=exact?index(p.time,bars,timeframe):pointIndex(p,bars,timeframe);return i==null?null:{x:coordinate(chart.timeScale(),i),y:series.priceToCoordinate(p.price)};}
    function channelAbove(points) {
      const p=project(points,'channel');
      return p.length===3&&p.every(v=>v&&v.x!=null&&v.y!=null)&&p[0].x!==p[1].x&&channel(p)[3].y<p[0].y;
    }
    function point(event) {
      const r=svg.getBoundingClientRect(),x=event.clientX-r.left,y=event.clientY-r.top;
      const logical=chart.timeScale().coordinateToLogical(x), price=series.coordinateToPrice(y);
      if(logical==null||price==null||price<=0||x<0||y<0||x>r.width||y>r.height){snapped=null;reference=null;return null;}
      const editing=count[mode]||drag?.handle!=null;
      snapped=snapEnabled&&!event.altKey&&editing?candleSnap(bars,chart.timeScale(),series,logical,y,snapped,timeframe):null;
      reference=editing?closeReference(bars,chart.timeScale(),series,logical,y):null;
      return {time:snapped?snapped.time:timeAt(logical,bars,timeframe),price:snapped?snapped.price:price};
    }
    function project(points,kind) {
      let p=points.map(v=>xy(v));
      // Keep different dates inside one aggregate candle distinguishable.
      if(['trend','channel'].includes(kind)&&p[0]&&p[1]&&p[0].x===p[1].x&&points[0].time!==points[1].time)p=points.map(v=>xy(v,true));
      return p;
    }
    function extensionPointer(event) {
      const rect=svg.getBoundingClientRect(),x=Math.max(0,Math.min(rect.width,event.clientX-rect.left));
      const logical=chart.timeScale().coordinateToLogical(x);
      return logical==null?null:{time:timeAt(logical,bars,timeframe),price:1};
    }
    function render() {
      if(removed)return;
      const w=chart.timeScale().width(),h=chart.panes()[0].getHeight();
      const all=getDrawings().concat(getOverlays()).map(d=>{const view=['box','riskreward'].includes(d.kind)&&!d.extension?{...d,extend:true}:d;return {d:view,p:project(d.points,d.kind)};}).sort((a,b)=>Number(a.d.id===selected)-Number(b.d.id===selected));
      if(draft.length) {
        const ps=[...draft];if(hover&&ps.length<count[mode])ps.push(hover);
        // Keep the lower baseline visible while the upper anchor is being chosen.
        if(mode==='channel'&&ps.length>=2&&!channelAbove(ps)) {
          const base=ps.slice(0,2);
          all.push({d:{kind:'trend',points:base,color:'#218775',width:2,extend:false},p:project(base,'trend')});
        } else if(ps.length===count[mode])all.push({d:{kind:mode,points:ps,color:'#218775',width:2,extend:false},p:project(ps,mode)});
      }
      const sig=JSON.stringify([w,h,all,mode,selected,draft,hover,snapped,reference]);
      if(signature!==sig) {
        signature=sig;svg.replaceChildren();svg.setAttribute('width',w);svg.setAttribute('height',h);
        svg.style.pointerEvents=count[mode]?'auto':'none';
        svg.style.cursor=count[mode]?'crosshair':'default';
        if(count[mode])node('rect',{width:w,height:h,fill:'transparent','pointer-events':'all'});
        for(const {d,p} of all) {
          if(p.some(v=>!v||v.x==null||v.y==null||!Number.isFinite(v.x+v.y)))continue;
          const g=node('g',{'data-id':d.id||'',opacity:d.id?1:.65,'aria-label':(d.ai?'AI 제안 · ':'')+labels[d.kind],class:d.ai?'ai-proposal':'user-drawing'});
          const [a,b]=p, stroke=d.color;let extensionHandles=null;
          function line(v1,v2,dash='',color=stroke) {
            node('line',{x1:v1.x,y1:v1.y,x2:v2.x,y2:v2.y,stroke:'transparent','stroke-width':14,'pointer-events':mode==='select'?'stroke':'none'},g);
            node('line',{x1:v1.x,y1:v1.y,x2:v2.x,y2:v2.y,stroke:color,'stroke-width':d.width,'stroke-dasharray':d.ai?'7 5':dash,'pointer-events':'none'},g);
          }
          function flatExtensions(levels,left,right) {
            const bounds=extensionBounds(d,bars,timeframe);
            const edgeX=(time,fallback)=>{
              const bounded=time<bars[0].date?bars[0].date:time>(bars.at(-1).last_session||bars.at(-1).date)?(bars.at(-1).last_session||bars.at(-1).date):time;
              const logical=index(bounded,bars,timeframe);
              return logical==null?fallback:coordinate(chart.timeScale(),logical)??fallback;
            };
            const lx=d.extend?Math.min(left,edgeX(bounds.left,left)):left,rx=d.extend?Math.max(right,edgeX(bounds.right,right)):right;
            const ys=levels.map(v=>v.y),mid=(Math.min(...ys)+Math.max(...ys))/2;
            extensionHandles=[{side:'left',x:lx,y:mid},{side:'right',x:rx,y:mid}];
            if(d.extend)for(const {y,color} of levels){
              if(lx<left)line({x:lx,y},{x:left,y},'5 4',color);
              if(rx>right)line({x:right,y},{x:rx,y},'5 4',color);
            }
          }
          if(d.kind==='horizontal') {
            line({x:0,y:a.y},{x:w,y:a.y});
            const text=node('text',{x:8,y:a.y-7,fill:stroke,'font-size':12,'pointer-events':'none'},g);
            text.textContent=WorkbenchFormat.money(d.points[0].price,currency)+' '+currency;
          } else if(d.kind==='riskreward') {
            const result=riskReward(d.points,currency);if(!result)continue;
            const left=Math.min(...p.map(v=>v.x)),right=Math.max(...p.map(v=>v.x)),entry=p[0],stop=p[1],target=p[2];
            for(const [edge,color] of [[stop,'#ee6677'],[target,'#36c595']]){
              node('rect',{x:left,y:Math.min(entry.y,edge.y),width:Math.max(1,right-left),height:Math.abs(edge.y-entry.y),fill:color,'fill-opacity':.18,stroke:color,'stroke-width':d.width,'pointer-events':mode==='select'?'all':'none'},g);
            }
            line({x:left,y:entry.y},{x:right,y:entry.y});
            flatExtensions([{y:entry.y,color:stroke},{y:stop.y,color:'#ee6677'},{y:target.y,color:'#36c595'}],left,right);
            const texts=[result.direction+' · '+result.label,result.profit,result.loss];
            const tx=Math.max(6,Math.min(left+6,w-235)),ty=Math.max(16,Math.min(Math.min(stop.y,target.y)-42,h-48));
            const text=node('text',{x:tx,y:ty,fill:'#e9f2ff','font-size':11,'paint-order':'stroke',stroke:'#202630','stroke-width':3,'pointer-events':'none'},g);
            texts.forEach((t,i)=>node('tspan',{x:tx,dy:i?15:0},text).textContent=t);
          } else if(d.kind==='box'||d.kind==='measure') {
            node('rect',{x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),width:Math.max(1,Math.abs(b.x-a.x)),height:Math.max(1,Math.abs(b.y-a.y)),fill:stroke,'fill-opacity':.10,stroke,'stroke-width':d.width,'pointer-events':mode==='select'?'all':'none'},g);
            if(d.kind==='box')flatExtensions([{y:a.y,color:stroke},{y:b.y,color:stroke}],Math.min(a.x,b.x),Math.max(a.x,b.x));
            if(d.kind==='measure') {
              line(a,b,'4 3');
              const parts=measure(d.points,bars,currency,timeframe).split(' · '), lines=[parts.slice(0,2).join(' · '),parts.slice(2).join(' · ')];
              const labelWidth=Math.min(w-10,Math.max(...lines.map(t=>t.length))*7);
              const x=Math.max(5,Math.min(Math.min(a.x,b.x),w-labelWidth-5)),y=Math.max(17,Math.min(a.y,b.y)-23);
              const text=node('text',{x,y,fill:stroke,'font-size':12,'paint-order':'stroke',stroke:'#fff','stroke-width':3,'pointer-events':'none'},g);
              lines.forEach((value,i)=>{node('tspan',{x,dy:i?15:0},text).textContent=value;});
            }
          } else if(a.x!==b.x) {
            const slope=(b.y-a.y)/(b.x-a.x);
            const [left,right]=a.x<b.x?[a,b]:[b,a],bounds=extensionBounds(d,bars,timeframe);
            const edgeX=(time,fallback)=>{
              const bounded=time<bars[0].date?bars[0].date:time>(bars.at(-1).last_session||bars.at(-1).date)?(bars.at(-1).last_session||bars.at(-1).date):time;
              const logical=index(bounded,bars,timeframe);return logical==null?fallback:coordinate(chart.timeScale(),logical)??fallback;
            };
            const lx=d.extend?Math.min(left.x,edgeX(bounds.left,left.x)):left.x,rx=d.extend?Math.max(right.x,edgeX(bounds.right,right.x)):right.x;
            const offset=d.kind==='channel'?channel(p)[3].y-a.y:0;
            extensionHandles=[{side:'left',x:lx,y:a.y+slope*(lx-a.x)+offset/2},{side:'right',x:rx,y:a.y+slope*(rx-a.x)+offset/2}];
            function trendLine(v1,v2,dash='') {
              // Finite dates are independent of viewport pan/zoom and anchor click order.
              if(d.extend) {
                const [left,right]=v1.x<v2.x?[v1,v2]:[v2,v1];
                if(lx<left.x)line({x:lx,y:left.y+slope*(lx-left.x)},left,'5 4');
                if(rx>right.x)line(right,{x:rx,y:right.y+slope*(rx-right.x)},'5 4');
              }
              line(v1,v2,dash);
            }
            trendLine(a,b);
            if(d.kind==='channel') {
              const q=channel(p), offset=q[3].y-a.y;
              node('polygon',{points:q.map(v=>`${v.x},${v.y}`).join(' '),fill:stroke,'fill-opacity':.05,'pointer-events':mode==='select'?'all':'none'},g);
              if(d.extend)for(const [x1,x2] of [[lx,left.x],[right.x,rx]])if(x2>x1){
                const y1=a.y+slope*(x1-a.x),y2=a.y+slope*(x2-a.x);
                node('polygon',{points:[[x1,y1],[x2,y2],[x2,y2+offset],[x1,y1+offset]].map(v=>v.join(',')).join(' '),fill:stroke,'fill-opacity':.025,'pointer-events':'none'},g);
              }
              trendLine(q[3],q[2]);trendLine({x:a.x,y:a.y+offset/2},{x:b.x,y:b.y+offset/2},'5 4');
            }
          }
          if(d.ai){g.setAttribute('opacity','.7');g.querySelectorAll('*').forEach(n=>n.setAttribute('pointer-events','none'));}
          if(d.id===selected&&mode==='select')p.forEach((v,i)=>node('circle',{cx:v.x,cy:v.y,r:6,fill:'#fff',stroke,'stroke-width':2,'data-point':i,'pointer-events':'all',cursor:'move'},g));
          if(d.id===selected&&mode==='select'&&!d.ai&&extensionHandles)for(const v of extensionHandles){
            const hx=Math.max(9,Math.min(w-9,v.x)),hy=Math.max(9,Math.min(h-9,v.y-18));
            if(v.x>=0&&v.x<=w&&v.y>=0&&v.y<=h)node('line',{x1:v.x,y1:v.y,x2:hx,y2:hy,stroke,'stroke-width':1,opacity:.6,'pointer-events':'none'},g);
            const handle=node('rect',{x:hx-7,y:hy-7,width:14,height:14,rx:3,fill:'#202630',stroke,'stroke-width':2,'data-extension':v.side,'pointer-events':'all',cursor:'ew-resize','aria-label':v.side==='left'?'왼쪽 연장 길이':'오른쪽 연장 길이'},g);
            node('title',{},handle).textContent=(v.side==='left'?'왼쪽':'오른쪽')+' 점선 끝 · 좌우로 드래그';
          }
        }
        for(const p of draft){const v=xy(p);if(v)node('circle',{cx:v.x,cy:v.y,r:4,fill:'#218775','pointer-events':'none'});}
        if(hover&&(count[mode]||drag?.handle!=null)){
          const v=xy(hover);
          if(v&&v.x!=null&&v.y!=null){
            const crosshair=node('g',{class:'drawing-crosshair','pointer-events':'none'});
            node('line',{x1:0,y1:v.y,x2:w,y2:v.y,stroke:'#8795a9','stroke-width':1,'stroke-dasharray':'4 4'},crosshair);
            node('line',{x1:v.x,y1:0,x2:v.x,y2:h,stroke:'#8795a9','stroke-width':1,'stroke-dasharray':'4 4'},crosshair);
            if(reference){
              const sx=chart.timeScale().logicalToCoordinate(reference.i),sy=series.priceToCoordinate(reference.price);
              if(sx!=null&&sy!=null){
                const g=node('g',{class:'close-reference-guide','data-source-date':reference.time,'data-price':reference.price,'pointer-events':'none','aria-label':'과거 종가 참고 · '+reference.time});
                node('line',{x1:sx,y1:sy,x2:w,y2:sy,stroke:'#83d8ff','stroke-width':1,'stroke-dasharray':'3 4',opacity:.65},g);
              }
            }
            if(snapped){
              const g=node('g',{class:'candle-snap-guide','data-source-date':snapped.time,'data-price':snapped.price,'data-field':snapped.field,'pointer-events':'none'});
              const label=priceLabels[snapped.field]+' '+WorkbenchFormat.money(snapped.price,currency)+' '+currency+' · '+snapped.time;
              const width=Math.min(w-8,label.length*7+12),x=Math.max(4,Math.min(v.x-width/2,w-width-4));
              const top=v.y<35?v.y+12:v.y-30;
              node('rect',{x,y:top,width,height:22,rx:3,fill:'#172b3a',stroke:'#83d8ff','stroke-width':.5},g);
              node('text',{x:x+6,y:top+15,fill:'#b9eaff','font-size':11},g).textContent=label;
            }
            node('circle',{class:'drawing-hover-point',cx:v.x,cy:v.y,r:5,fill:snapped?'#83d8ff':'#202630',stroke:snapped?'#e4f7ff':'#83d8ff','stroke-width':2,'pointer-events':'none'});
          }
        }

      }
      frame=requestAnimationFrame(render);
    }
    function select(id) {mode='select';draft=[];hover=null;snapped=null;reference=null;selected=id;onSelect(id);signature='';}
    function setMode(next) {
      mode=next;draft=[];hover=null;snapped=null;reference=null;drag=null;signature='';
      onHint(mode==='channel'?'평행 채널: 시작 하단점 → 끝 하단점 → 상단점 순서로 클릭하세요. Esc로 취소.':mode==='riskreward'?'손익비: 진입가 → 손절가 → 목표가 순서로 클릭하세요. 가로 폭은 세 점의 날짜 범위입니다.':count[mode]?`${labels[mode]}: ${count[mode]}개 지점을 차례로 클릭하세요. Esc로 취소.`:'원형 점은 기준점 수정, 사각 손잡이는 점선 연장 길이, 선·영역은 전체 이동입니다. 빈 영역은 차트 이동·확대에 사용합니다.');
    }
    function down(e) {
      if(e.button!==0)return;
      const rawSide=e.target?.getAttribute?.('data-extension'),side=['left','right'].includes(rawSide)?rawSide:null;
      const p=side?extensionPointer(e):point(e);if(!p)return;
      if(count[mode]) {
        hover=p;
        e.preventDefault();e.stopPropagation();
        if((mode==='trend'||mode==='channel')&&draft.length===1&&draft[0].time===p.time){onHint('두 번째 점은 다른 날짜를 선택하세요.');return;}
        if(mode==='channel'&&draft.length===2&&!channelAbove([...draft,p])){onHint('평행 채널: 하단 기준선 위쪽의 상단점을 클릭하세요.');return;}
        if(mode==='riskreward'&&draft.length===1&&draft[0].price===p.price){onHint('손절가는 진입가와 달라야 합니다.');return;}
        if(mode==='riskreward'&&draft.length===2&&!riskReward([...draft,p],currency)){onHint('목표가는 진입가를 기준으로 손절가의 반대편에 두고, 날짜 범위를 넓혀 주세요.');return;}
        draft.push(p);
        if(draft.length===count[mode]) {
          if(getDrawings().length>=drawingLimit){draft=[];onHint(`종목당 최대 ${drawingLimit}개입니다. 필요 없는 도형을 삭제하세요.`);return;}
          const drawing={id:crypto.randomUUID(),kind:mode,points:clone(draft),color:'#218775',width:2,extend:extendable(mode)};
          if(drawing.extend)drawing.extension=extensionBounds(drawing,bars,timeframe);
          onChange([...getDrawings(),drawing]);setMode('select');select(drawing.id);
        }else if(mode==='channel')onHint(draft.length===1?'평행 채널: 끝 하단점을 클릭하세요.':'평행 채널: 하단 기준선 위쪽의 상단점을 클릭하세요.');else if(mode==='riskreward')onHint(draft.length===1?'손익비: 손절가를 클릭하세요.':'손익비: 진입가 반대편의 목표가를 클릭하세요.');else onHint(`${labels[mode]}: ${count[mode]-draft.length}개 지점을 더 클릭하세요.`);
      } else {
        const group=e.target.closest('[data-id]'),id=group?.getAttribute('data-id');
        if(!id||group.classList.contains('ai-proposal'))return;
        e.preventDefault();e.stopPropagation();select(id);
        const d=getDrawings().find(v=>v.id===id);
        drag={id,start:p,original:clone(d),all:clone(getDrawings()),extension:['left','right'].includes(side)?side:null,handle:e.target.hasAttribute('data-point')?Number(e.target.getAttribute('data-point')):null,moved:false};
        svg.setPointerCapture(e.pointerId);
      }
    }
    function move(e) {
      if(drag?.extension){
        const p=extensionPointer(e);if(!p)return;
        const d=clone(drag.original),times=anchorTimes(d);
        if(['box','riskreward'].includes(d.kind)&&!d.extension)d.extend=true;
        d.extension=d.extend?extensionBounds(d,bars,timeframe):{left:times[0],right:times.at(-1)};
        d.extension[drag.extension]=drag.extension==='left'?(p.time<times[0]?p.time:times[0]):(p.time>times.at(-1)?p.time:times.at(-1));
        d.extend=true;drag.moved=true;hover=null;snapped=null;reference=null;
        onChange(drag.all.map(v=>v.id===d.id?d:v),{preview:true});return;
      }
      const p=point(e);if(!p){hover=null;return;}hover=p;
      if(!drag)return;
      const d=clone(drag.original);
      if(['box','riskreward'].includes(d.kind)&&!d.extension){d.extend=true;d.extension=extensionBounds(d,bars,timeframe);}
      if(drag.handle!=null)d.points[drag.handle]=p;
      else {
        let delta=Math.round(index(p.time,bars,timeframe)-index(drag.start.time,bars,timeframe));
        const extension=d.extend&&extendable(d.kind)?extensionBounds(d,bars,timeframe):d.extension;
        const ids=d.points.map(v=>index(v.time,bars,timeframe));
        if(ids.some(i=>i==null))return;
        const limits=extension?ids.concat(Object.values(extension).map(time=>index(time,bars,timeframe)).filter(i=>i!=null)):ids;
        delta=Math.max(-Math.floor(Math.min(...limits)+.4),Math.min(bars.length-1-Math.floor(Math.max(...limits)+.4),delta));
        const priceDelta=p.price-drag.start.price;
        d.points=d.points.map((v,i)=>({time:delta===0?v.time:timeAt(ids[i]+delta,bars,timeframe),price:v.price+priceDelta}));
        if(extension){d.extension={...extension};for(const side of ['left','right']){
          const logical=index(extension[side],bars,timeframe);
          if(delta!==0&&logical!=null)d.extension[side]=timeAt(logical+delta,bars,timeframe);
        }}
      }
      if(d.points.some(v=>v.price<=0)||((d.kind==='trend'||d.kind==='channel')&&d.points[0].time===d.points[1].time))return;
      if(d.kind==='riskreward'&&!riskReward(d.points,currency))return;
      if(d.extension)d.extension=extensionBounds(d,bars,timeframe);
      drag.moved=true;
      onChange(drag.all.map(v=>v.id===d.id?d:v),{preview:true});
    }
    function up(e) {
      if(!drag)return;
      if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId);
      const before=drag.all,moved=drag.moved;drag=null;snapped=null;reference=null;hover=null;
      if(moved)onChange(getDrawings(),{before});
    }
    function cancel() {
      if(drag){const old=drag.all;drag=null;onChange(old,{preview:true});}
      draft=[];hover=null;snapped=null;reference=null;signature='';
    }
    svg.addEventListener('pointerdown',down);svg.addEventListener('pointermove',move);svg.addEventListener('pointerup',up);
    svg.addEventListener('pointercancel',cancel);
    svg.addEventListener('pointerleave',()=>{if(!drag){hover=null;snapped=null;reference=null;}});
    setMode('select');frame=requestAnimationFrame(render);
    return {isEditing:()=>!!drag||draft.length>0||mode!=='select',setMode,select,cancel,setSnapEnabled(value){snapEnabled=!!value;snapped=null;reference=null;hover=null;signature='';},destroy(){removed=true;cancelAnimationFrame(frame);svg.remove();}};
  }
  return {attach,measure,riskReward,channel,extensionBounds,labels,count,index,timeAt,coordinate,timeline,candleSnap,closeReference,withAnchorTimes,pointIndex};
})();

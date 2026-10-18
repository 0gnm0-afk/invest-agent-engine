window.WorkbenchWorkspace = (() => {
  const clone=x=>JSON.parse(JSON.stringify(x));
  const safe=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let active=null,candleSnapEnabled=true,autoTrendlinesVisible=true;
  async function closeActive() {
    if(!active)return true;
    const current=active;
    if(!await current.save())return false;
    if(active===current){current.destroy();active=null;}return true;
  }
  async function mount({host,plot,stock,market,symbol,currency,legend,isCurrent}) {
    const frame=stock.timeframe||'1d', frameName=({'1d':'일봉','1w':'주봉','1mo':'월봉'})[frame];
    const key=encodeURIComponent(market+':'+symbol),url='/api/chart-state/'+key+'?timeframe='+frame;
    const bars=stock.bars||[], byDate=new Map(bars.map(b=>[b.date,b]));
    if(!bars.length){host.innerHTML='<div class="empty">검증된 가격 이력이 없어 이동평균·작도를 사용할 수 없습니다.</div>';return;}
    let packet;
    try {const r=await fetch(url);if(!r.ok)throw Error();packet=await r.json();}
    catch {host.innerHTML='<div class="notice error">작도 저장소를 읽지 못했습니다. 원본을 보호하기 위해 편집을 열지 않았습니다. 대시보드를 다시 실행한 뒤 재시도하세요.</div>';return;}
    if(!isCurrent())return;
    if(packet.drawing_limit!==300){
      host.innerHTML='<div class="notice error" role="alert">이 창의 대시보드 서버가 이전 버전이라 작도를 공유할 수 없습니다. 저장한 작도는 보존돼 있습니다. 바탕화면 투자 대시보드를 새로 실행해 열린 창을 사용하세요.</div>';
      const historyStatus=document.querySelector('#history-status');
      if(historyStatus)historyStatus.textContent='이전 서버 · 일/주/월 작도 공유 미적용 · 대시보드를 새로 실행하세요.';
      return;
    }
    if(packet.drawing_extensions!=='bounded-v1'){
      host.innerHTML='<div class="notice error" role="alert">점선 연장 손잡이를 저장하려면 대시보드를 다시 실행하세요. 기존 작도는 저장돼 있습니다.</div>';
      return;
    }
    if(packet.average_limit!==7||!packet.extension_kinds?.includes('riskreward')||!packet.extension_kinds?.includes('box')){
      host.innerHTML='<div class="notice error" role="alert">SMA 7개와 박스·손익비 연장을 사용하려면 대시보드를 다시 실행하세요. 기존 설정과 작도는 보존돼 있습니다.</div>';return;
    }
    let anchorBars=DrawingTools.withAnchorTimes(bars,frame==='1d'?bars:[],frame),anchorWarning='';
    if(frame!=='1d'){
      try{
        const r=await fetch('/api/chart/'+key+'?timeframe=1d');
        if(!r.ok)throw Error('daily_anchor_source_unavailable');
        const daily=await r.json();
        if(!isCurrent())return;
        anchorBars=DrawingTools.withAnchorTimes(bars,daily.bars||[],frame);
      }catch{
        if(!isCurrent())return;
      }
      if(anchorBars.some(b=>!b.anchorTimes))anchorWarning=' · 일부 봉의 실제 발생일 미확인: 해당 봉 자동 붙임 보류';
    }
    const drawingBars=DrawingTools.timeline(anchorBars,frame);
    let state=packet.state,revision=packet.revision,lastSaved=JSON.stringify(state),history=[],future=[],selected=null;
    let previewDrawings=null,proposals=[];
    let saving=null,timer=null,dead=false,averageVersion=0,chart,price,drawings,averageRefs=[],averageValues={},hoverTime=null,conflict=false;
    host.innerHTML=`<div class="chart-tools" role="toolbar" aria-label="차트 작도 도구">
      <button data-tool="select" class="selected" title="선택·이동" aria-label="선택·이동"><span aria-hidden="true">↖</span><span>선택·이동</span></button><button data-tool="trend" title="추세선" aria-label="추세선"><span aria-hidden="true">╱</span><span>추세선</span></button><button data-tool="channel" title="평행 채널: 시작 하단점 → 끝 하단점 → 상단점" aria-label="평행 채널"><span aria-hidden="true">∥</span><span>평행 채널</span></button><button data-tool="horizontal" title="수평선" aria-label="수평선"><span aria-hidden="true">―</span><span>수평선</span></button><button data-tool="box" title="박스" aria-label="박스"><span aria-hidden="true">▱</span><span>박스</span></button><button data-tool="measure" title="구간 측정" aria-label="구간 측정"><span aria-hidden="true">↔</span><span>구간 측정</span></button>
      <button data-tool="riskreward" title="손익비: 진입·손절·목표 3점" aria-label="손익비"><span aria-hidden="true">▥</span><span>손익비</span></button>
      <span class="tool-divider"></span><button data-action="snap" aria-pressed="${candleSnapEnabled}" class="${candleSnapEnabled?'selected':''}" title="봉의 시가·고가·저가·종가 중 가까운 가격과 실제 발생일에 붙습니다. Alt로 자유 선택.">봉 스냅</button><button data-action="undo" title="Ctrl+Z">실행 취소</button><button data-action="redo" title="Ctrl+Shift+Z">다시 실행</button><button data-action="delete">선택 삭제</button><button data-action="save" class="primary">저장</button>
      <span class="save-state" role="status" aria-live="polite"></span></div>
      <p class="drawing-hint"></p><div class="chart-controls"><label><input type="checkbox" class="auto-trendlines-toggle" ${autoTrendlinesVisible?'checked':''}>자동 추세선 · trendln · 최근 일봉 60개</label><span class="auto-trendlines-status" role="status"></span><details class="averages-control"><summary>이동평균선 · ${frameName} SMA</summary><div class="average-list"></div><form class="average-add"><label>추가 기간 <input name="period" type="number" min="1" max="500" value="30" required aria-label="추가 SMA 기간"></label><button>추가</button><small>1~500봉 · 최대 7개 · 종가 기준</small></form><p class="average-status" role="status"></p></details><details class="drawing-control"><summary>작도 목록·정밀 편집 <span class="drawing-count"></span></summary><div class="drawing-list"></div><div class="drawing-editor"></div></details></div>`;
    const q=s=>host.querySelector(s),qa=s=>host.querySelectorAll(s);
    if(!packet.drawing_kinds?.includes('riskreward')){q('[data-tool="riskreward"]').disabled=true;q('[data-tool="riskreward"]').title='손익비 저장을 사용하려면 대시보드를 새로 실행하세요.';}
    const pointLabel=(d,i)=>d.kind==='riskreward'?['진입','손절','목표'][i]:d.kind==='channel'?['기준선 시작','기준선 끝','평행선'][i]:'점 '+(i+1);
    const smaLegend=document.createElement('div');smaLegend.className='sma-legend';smaLegend.setAttribute('aria-label','이동평균선 값');
    function renderSmaLegend() {
      const day=hoverTime||bars.at(-1).date;
      smaLegend.replaceChildren();
      if(hoverTime&&hoverTime>bars.at(-1).date){smaLegend.textContent='SMA는 마지막 실제 봉까지 표시합니다.';return;}
      for(const a of state.averages.filter(a=>a.visible)) {
        const value=averageValues[a.period]?.get(day),span=document.createElement('span');
        span.style.color=a.color;span.textContent='SMA'+a.period+': '+(value==null?'미준비':WorkbenchFormat.money(value,currency));smaLegend.append(span);
      }
    }
    function status(text,bad=false){q('.save-state').textContent=text;q('.save-state').classList.toggle('error-text',bad);}
    function dirty(){return JSON.stringify(state)!==lastSaved;}
    function schedule(){clearTimeout(timer);status('저장 대기');timer=setTimeout(()=>save(),600);}
    function push(before){history.push(clone(before));if(history.length>50)history.shift();future=[];}
    function commit(next,before=state) {previewDrawings=null;push(before);state=clone(next);refresh();schedule();}
    function refresh() {
      q('[data-action="undo"]').disabled=!history.length;q('[data-action="redo"]').disabled=!future.length;
      q('[data-action="delete"]').disabled=!selected;
      q('.drawing-count').textContent=`(${state.drawings.length})`;
      q('.drawing-list').innerHTML=state.drawings.map((d,i)=>`<button data-id="${safe(d.id)}" class="${d.id===selected?'selected':''}">${i+1}. ${DrawingTools.labels[d.kind]}</button>`).join('')||'<small>저장한 작도가 없습니다.</small>';
      q('.drawing-list').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>drawings.select(b.dataset.id));
      renderEditor();
    }
    function renderEditor() {
      const d=state.drawings.find(x=>x.id===selected),editor=q('.drawing-editor');
      if(!d){editor.innerHTML='<small>차트의 도형이나 목록을 선택하면 날짜·가격·색상을 수정할 수 있습니다.</small>';return;}
      editor.innerHTML=`<form class="point-form"><strong>${DrawingTools.labels[d.kind]}</strong><div class="point-grid">${d.points.map((p,i)=>`<label>${pointLabel(d,i)} 날짜<input type="date" name="time${i}" value="${safe(p.time)}" required></label><label>${pointLabel(d,i)} 가격 (${currency})<input type="text" inputmode="decimal" name="price${i}" value="${WorkbenchFormat.money(p.price,currency)}" required></label>`).join('')}</div><div class="editor-style"><label>선 색상<input type="color" name="color" value="${d.color}"></label><label>굵기<select name="width">${[1,2,3,4].map(n=>`<option ${d.width===n?'selected':''}>${n}</option>`).join('')}</select></label>${['trend','channel','box','riskreward'].includes(d.kind)?`<label><input type="checkbox" name="extend" ${(d.extend||(['box','riskreward'].includes(d.kind)&&!d.extension))?'checked':''}>점선 연장 · 양쪽 사각 손잡이로 길이 조절</label>`:''}<button>수정 적용</button></div><p class="point-error" role="alert"></p></form>${d.kind==='measure'?`<p class="measurement-result">${safe(DrawingTools.measure(d.points,drawingBars,currency,frame))}</p>`:''}${d.kind==='riskreward'?`<p class="measurement-result">${safe(DrawingTools.riskReward(d.points,currency)?.label)} · 1주 기준 가격 거리, 수수료·슬리피지 제외</p>`:''}`;
      editor.querySelector('form').onsubmit=e=>{
        e.preventDefault();const f=new FormData(e.target),next=clone(d);
        next.points=d.points.map((p,i)=>({time:f.get('time'+i),price:f.get('price'+i)===WorkbenchFormat.money(p.price,currency)?p.price:Number(String(f.get('price'+i)).replaceAll(',',''))}));
        if(next.points.some(p=>DrawingTools.index(p.time,drawingBars,frame)==null||!Number.isFinite(p.price)||p.price<=0||p.price>1e15)) {q('.point-error').textContent=`날짜는 ${drawingBars[0].date}~${drawingBars.at(-1).last_session||drawingBars.at(-1).date}, 가격은 0보다 큰 유효 값을 입력하세요.`;return;}
        if(['trend','channel'].includes(d.kind)&&next.points[0].time===next.points[1].time){q('.point-error').textContent='첫 두 점은 서로 다른 날짜여야 합니다.';return;}
        if(d.kind==='riskreward'&&!DrawingTools.riskReward(next.points,currency)){q('.point-error').textContent='손절·목표는 진입가의 반대편에 두고, 서로 다른 날짜를 포함해야 합니다.';return;}
        next.color=f.get('color');next.width=Number(f.get('width'));next.extend=f.has('extend');
        if(['trend','channel','box','riskreward'].includes(next.kind))next.extension=DrawingTools.extensionBounds(next,drawingBars,frame);
        commit({...state,drawings:state.drawings.map(x=>x.id===d.id?next:x)});
      };
    }
    function renderAverages() {
      q('.average-list').innerHTML=state.averages.map(a=>`<div class="average-row" data-period="${a.period}"><label><input type="checkbox" name="visible" ${a.visible?'checked':''} aria-label="SMA ${a.period} 표시">SMA ${a.period}</label><input type="color" name="color" value="${a.color}" aria-label="SMA ${a.period} 색상"><select name="width" aria-label="SMA ${a.period} 굵기">${[1,2,3,4].map(n=>`<option ${a.width===n?'selected':''}>${n}</option>`).join('')}</select><button type="button" data-remove="${a.period}" aria-label="SMA ${a.period} 삭제">삭제</button></div>`).join('');
      q('.average-list').querySelectorAll('.average-row').forEach(row=>{
        row.onchange=()=>{const period=Number(row.dataset.period);const averages=state.averages.map(a=>a.period===period?{...a,color:row.querySelector('[name="color"]').value,width:Number(row.querySelector('[name="width"]').value),visible:row.querySelector('[name="visible"]').checked}:a);commit({...state,averages});loadAverages();};
        row.querySelector('button').onclick=()=>{commit({...state,averages:state.averages.filter(a=>a.period!==Number(row.dataset.period))});renderAverages();loadAverages();};
      });
    }
    q('.average-add').onsubmit=e=>{
      e.preventDefault();const n=Number(new FormData(e.target).get('period'));
      if(!Number.isInteger(n)||n<1||n>500||state.averages.length>=7||state.averages.some(a=>a.period===n)){q('.average-status').textContent='중복 없이 1~500봉, 최대 7개를 입력하세요.';return;}
      commit({...state,averages:[...state.averages,{period:n,color:'#cc6963',width:2,visible:true}]});renderAverages();loadAverages();
    };
    async function loadAverages() {
      const version=++averageVersion;
      averageRefs.forEach(ref=>chart.removeSeries(ref));averageRefs=[];averageValues={};renderSmaLegend();
      const requested=state.averages.filter(a=>a.visible);q('.average-status').textContent='이동평균 계산 자료를 읽는 중…';
      try {
        const r=await fetch('/api/averages/'+key+'?periods='+requested.map(a=>a.period).join(',')+'&timeframe='+frame);if(!r.ok)throw Error();const values=await r.json();
        if(dead||version!==averageVersion)return;
        for(const a of requested) {
          const ref=chart.addSeries(LightweightCharts.LineSeries,{color:a.color,lineWidth:a.width,title:'',priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,autoscaleInfoProvider:()=>null,priceFormat:{type:'custom',minMove:currency==='KRW'?1:.01,formatter:v=>WorkbenchFormat.money(v,currency)}});
          ref.setData(values[a.period]);averageRefs.push(ref);averageValues[a.period]=new Map(values[a.period].map(p=>[p.time,p.value]));
        }
        renderSmaLegend();
        const unavailable=requested.filter(a=>!(values[a.period]||[]).some(p=>p.value!=null)).map(a=>a.period);
        q('.average-status').textContent=unavailable.length?`이력 부족·결측: SMA ${unavailable.join(', ')} · 없는 값을 채우지 않습니다.`:`Python 계산 · ${frameName} 종가 SMA · 마지막 주/월은 기준일까지의 부분 봉일 수 있습니다.`;
      } catch {if(!dead&&version===averageVersion)q('.average-status').textContent='이동평균 자료 조회 실패. 설정을 다시 적용하거나 화면을 다시 여세요.';}
    }
    async function save() {
      clearTimeout(timer);
      if(saving){await saving;return dirty()?save():true;}
      if(!dirty())return true;
      if(conflict){status('다른 창에서 변경됨 · 새로고침 후 다시 편집하세요. 현재 편집 내용은 화면에 유지됩니다.',true);return false;}
      const snapshot=clone(state);status('저장 중…');
      saving=(async()=>{
        try {
          const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':packet.token},body:JSON.stringify({revision,state:snapshot})});
          if(r.status===409){conflict=true;throw Error('다른 창에서 저장했습니다. 새로고침 후 다시 편집하세요.');}
          if(!r.ok)throw Error('저장 실패 · 저장 버튼으로 재시도하세요.');
          const result=await r.json();revision=result.revision;lastSaved=JSON.stringify(snapshot);window.dispatchEvent(new Event('workbench-drawing-saved'));status('저장됨 · '+new Date(result.updated_at).toLocaleTimeString('ko-KR'));return true;
        } catch(e){status(e.message,true);return false;}
      })();
      const ok=await saving;saving=null;
      if(ok&&dirty())return save();return ok;
    }
    function undo(redo=false) {
      const from=redo?future:history,to=redo?history:future;if(!from.length)return;
      drawings.cancel();previewDrawings=null;to.push(clone(state));state=from.pop();selected=null;drawings.select(null);refresh();renderAverages();loadAverages();schedule();
    }
    function remove() {if(!selected)return;const id=selected;selected=null;drawings.select(null);commit({...state,drawings:state.drawings.filter(x=>x.id!==id)});}
    qa('[data-tool]').forEach(b=>b.onclick=()=>{qa('[data-tool]').forEach(x=>x.classList.toggle('selected',x===b));drawings.setMode(b.dataset.tool);});
    q('[data-action="snap"]').onclick=()=>{candleSnapEnabled=!candleSnapEnabled;const b=q('[data-action="snap"]');b.setAttribute('aria-pressed',String(candleSnapEnabled));b.classList.toggle('selected',candleSnapEnabled);drawings.setSnapEnabled(candleSnapEnabled);};
    q('[data-action="save"]').onclick=save;q('[data-action="undo"]').onclick=()=>undo();q('[data-action="redo"]').onclick=()=>undo(true);q('[data-action="delete"]').onclick=remove;
    function keyboard(e) {
      if(e.target.closest('input,select,textarea'))return;
      if(e.key==='Escape'){drawings.cancel();drawings.setMode('select');qa('[data-tool]').forEach(b=>b.classList.toggle('selected',b.dataset.tool==='select'));}
      else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();undo(e.shiftKey);}
      else if(e.key==='Delete'){e.preventDefault();remove();}
    }
    function beforeUnload(e){if(dirty()){e.preventDefault();e.returnValue='';}}
    chart=WorkbenchCharts.create(plot,[{type:'candles',currency,label:currency,options:{title:''},data:bars.map(b=>({time:b.date,open:b.open,high:b.high,low:b.low,close:b.close})).concat(drawingBars.slice(bars.length).map(b=>({time:b.date})))},{type:'volume',label:'거래량',pane:1,data:bars.map(b=>({time:b.date,...(b.volume==null?{}:{value:b.volume}),color:b.close>=b.open?'#298463':'#994653'})),options:{title:'',priceFormat:{type:'volume'}}}],{height:plot.clientHeight,headroom:.12,lastBars:126,onReady:(c,refs)=>{price=refs[0].ref;}});
    chart.timeScale().setVisibleLogicalRange({from:Math.max(0,bars.length-126),to:bars.length-1+25});
    const autoRefs=[];
    const autoPacket=stock.auto_trendlines;
    for(const line of autoPacket?.lines||[]) {
      const broken=!!line.first_close_break_date,color=broken?'#9aa4b2':line.side==='resistance'?'#e7a448':'#36b8a1';
      for(const [segment,style] of [['solid',0],['extension',2]]) {
        if(!line[segment]?.length)continue;
        const ref=chart.addSeries(LightweightCharts.LineSeries,{color,lineWidth:2,lineStyle:style,title:'',visible:autoTrendlinesVisible,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,autoscaleInfoProvider:()=>null,priceFormat:{type:'custom',minMove:currency==='KRW'?1:.01,formatter:v=>WorkbenchFormat.money(v,currency)}});
        ref.setData(line[segment]);autoRefs.push(ref);
      }
    }
    const autoStatus=q('.auto-trendlines-status');
    autoStatus.textContent=!autoPacket?'자동 추세선 자료 없음 · 서버 재실행 필요':autoPacket.state==='invalid_history'?'가격 자료 확인 필요':autoPacket.lines.length?autoPacket.lines.map(l=>(l.screening?'선별 저항':l.side==='resistance'?'저항':'지지')+' '+WorkbenchFormat.money(l.value_latest,currency)+(l.first_close_break_date?' · 최근 종가 돌파/이탈':'')+' · '+l.touch_count+'개 고저점 회귀').join(' / '):'조건에 맞는 고저점 3개 이상의 회귀선 없음';
    autoStatus.title='일봉 기준 · '+(autoPacket?.as_of||'미확인')+' · trendln 0.1.18 · 확정 고점/저점 회귀 · 점선은 오른쪽 연장'+(frame==='1d'?'':' · 주/월봉에는 각 봉의 마지막 실제 거래일 값 투영');
    autoStatus.title+=(autoPacket?.lines||[]).map(l=>'\n'+(l.side==='resistance'?'저항':'지지')+' 회귀점: '+(l.fit_points||[]).map(p=>p.date+' '+WorkbenchFormat.money(p.price,currency)).join(', ')).join('');
    q('.auto-trendlines-toggle').onchange=e=>{autoTrendlinesVisible=e.target.checked;autoRefs.forEach(ref=>ref.applyOptions({visible:autoTrendlinesVisible}));};
    const detachPan=WorkbenchCharts.enableFreePan(plot,chart,price);
    const detachHoldings=window.WorkbenchTrailing?.attachStock({series:price,host:plot.parentElement,market,symbol,currency,isCurrent});
    const detachEvents=window.WorkbenchEvents?.attach({chart,series:price,plot,bars:drawingBars,frame,packet:stock.price_events});
    function renderPriceLegend(day) {
      if(!legend)return;
      const b=byDate.get(day)||bars.at(-1);
      legend.replaceChildren();
      if(day&&day>bars.at(-1).date){legend.textContent=day+' · 미래 작도 공간 · 가격 자료 없음';legend.append(smaLegend);return;}
      const date=document.createElement('strong');date.textContent=b.date+' · '+frameName;legend.append(date);
      for(const [label,key] of [['O','open'],['H','high'],['L','low'],['C','close']]){const part=document.createElement('span');part.textContent=label+' '+WorkbenchFormat.money(b[key],currency);part.className=b.close>=b.open?'quote-up':'quote-down';legend.append(part);}
      const volume=document.createElement('span');volume.textContent='Vol '+(b.volume==null?'결측':b.volume.toLocaleString('ko-KR'));legend.append(volume);legend.append(smaLegend);
    }
    renderPriceLegend(bars.at(-1).date);
    if(!legend)plot.append(smaLegend);renderSmaLegend();
    chart.subscribeCrosshairMove(p=>{
      hoverTime=p.time?(typeof p.time==='string'?p.time:`${p.time.year}-${String(p.time.month).padStart(2,'0')}-${String(p.time.day).padStart(2,'0')}`):null;
      renderSmaLegend();renderPriceLegend(hoverTime);
    });
    drawings=DrawingTools.attach({element:plot,chart,series:price,bars:drawingBars,currency,timeframe:frame,snapEnabled:candleSnapEnabled,drawingLimit:packet.drawing_limit||300,getDrawings:()=>previewDrawings||state.drawings,getOverlays:()=>proposals,
      onHint:text=>{q('.drawing-hint').textContent=text+' · 봉 스냅: 시가·고가·저가·종가의 실제 발생일·가격 고정 · 과거 종가는 점선 참고만 · Alt로 자유 선택 · 마지막 봉 오른쪽도 작도 가능 · 미래 5년 달력 공간(일봉: 평일, 휴장일 미반영)'+anchorWarning;},
      onSelect:id=>{if(id){host.hidden=false;document.querySelector('#stock-tools-toggle')?.setAttribute('aria-expanded','true');}selected=id;window.dispatchEvent(new Event('workbench-context'));qa('[data-tool]').forEach(b=>b.classList.toggle('selected',b.dataset.tool==='select'));refresh();},
      onChange:(next,options={})=>{
        if(options.preview){previewDrawings=next;return;}
        previewDrawings=null;
        const before=options.before?{...state,drawings:options.before}:state;
        commit({...state,drawings:next},before);
      }});
    document.addEventListener('keydown',keyboard);window.addEventListener('beforeunload',beforeUnload);
    refresh();renderAverages();loadAverages();status(packet.updated_at?'저장본 복원됨':'새 작업 · 변경 시 자동 저장');
    const missing=state.drawings.filter(d=>d.points.some(p=>DrawingTools.index(p.time,drawingBars,frame)==null)).length;
    if(missing)q('.drawing-hint').textContent=`현재 가격 기간 밖의 작도 ${missing}개는 보존하며 차트에 표시하지 않습니다. 목록에서 수정할 수 있습니다.`;
    let contextTimer;
    chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{clearTimeout(contextTimer);contextTimer=setTimeout(()=>{if(!dead)window.dispatchEvent(new Event('workbench-context'));},500);});
    active={canAutoRefresh:()=>!dirty()&&!saving&&!drawings.isEditing(),save,setProposals(value){proposals=value;},context(){return {timeframe:frame,price_scale:'linear',selected_drawing:selected,visible_range:chart.timeScale().getVisibleLogicalRange()};},visibleRange(value){chart.timeScale().setVisibleLogicalRange(value);},async reload(){if(dirty())throw Error('미저장 작도가 있어 다시 읽지 않았습니다.');const r=await fetch(url);if(!r.ok)throw Error('작도 다시 읽기 실패');const v=await r.json();state=v.state;revision=v.revision;lastSaved=JSON.stringify(state);history=[];future=[];selected=null;refresh();renderAverages();await loadAverages();status('채택 결과 저장본 복원됨');},destroy(){dead=true;clearTimeout(timer);clearTimeout(contextTimer);averageVersion++;detachHoldings?.();detachEvents?.();detachPan();drawings.destroy();smaLegend.remove();WorkbenchCharts.remove(chart);document.removeEventListener('keydown',keyboard);window.removeEventListener('beforeunload',beforeUnload);},range(value){price.priceScale().applyOptions({autoScale:true});let from=0;if(value!=='all'){const end=new Date(bars.at(-1).date+'T00:00:00Z');end.setUTCMonth(end.getUTCMonth()-Number(value));const start=end.toISOString().slice(0,10);from=bars.findIndex(b=>b.date>=start);if(from<0)from=bars.length-1;}chart.timeScale().setVisibleLogicalRange({from,to:bars.length-1+Math.min(60,Math.max(10,Math.round((bars.length-from)*.2)))});}};
    return active;
  }
  return {mount,closeActive,current:()=>active};
})();

/* Read-only event primitive; never part of the user's drawing workspace. */
window.WorkbenchEvents = (() => {
  const names={gap:'시가 갭',high:'고점',low:'저점'},frames={'1d':'일','1w':'주','1mo':'월'};
  const priorities={high:'높음 · 상세 조사',medium:'중간 · 간단 조사',low:'낮음 · 기록만'};
  const reasons={confirmed_weekly_swing:'확정 주봉 주요 변곡점',monthly_extreme:'월봉 극점',weekly_monthly_overlap:'주·월 극점 중첩',extreme_with_large_gap:'극점·큰 갭 동반',strong_followup_or_range_shift:'뚜렷한 후속 추세·가격 범위 변화',meaningful_move_volume_or_available_disclosure:'의미 있는 움직임·거래량·확보 공시',large_gap:'큰 시가 갭',provisional_only:'미확정 후보',small_or_quick_return_without_material_evidence:'작은 움직임·뚜렷한 후속 근거 없음'};
  const num=(n,suffix='')=>n==null?'결측':n.toFixed(2)+suffix;
  function label(e) {
    return `${e.event_date} ${frames[e.timeframe]}봉 ${names[e.kind]} · ${e.status==='confirmed'?'확정 '+e.confirmed_at:'잠정 후보'+(e.period_complete===false?' · 진행 중인 주':'')}${e.kind==='gap'?` · ${num(e.gap_pct,'%')} · ${e.full_gap?'완전 갭':'비완전 갭'}`:` · 양쪽 굴곡 ${num(e.prominence_left_pct,'%')} / ${num(e.prominence_right_pct,'%')}`} · 거래량 전일 ${num(e.metrics.volume_change_pct,'%')} / 20일 ${num(e.metrics.volume_ratio_20,'배')}`;
  }
  function grouped(events){const result=new Map();for(const e of events){if(!result.has(e.event_date))result.set(e.event_date,[]);result.get(e.event_date).push(e);}return [...result.entries()];}
  const preview = e => e.kind!=='gap'&&e.status==='provisional'&&['open_leg','awaiting_opposite_confirmation'].includes(e.selection_state);
  const visible = events => events.filter(e=>e.kind!=='gap'&&e.status==='confirmed');
  function attach({chart,series,plot,bars,frame,packet}) {
    const events=packet?.events||[],days=grouped(events),modern=packet?.policy?.pivot_rule==='weekly-swing-2';
    const panel=document.createElement('details');panel.className='price-event-panel';
    const summary=document.createElement('summary');summary.textContent=`주요 변곡점 ${visible(events).length}개 · 잠정 ${events.filter(e=>e.kind!=='gap'&&e.status==='provisional').length}개 · 갭 ${events.filter(e=>e.kind==='gap').length}건 (기본 숨김)`;
    if(!packet)summary.textContent='가격 사건 · 서버 입력 미연결 · 대시보드를 다시 실행하세요.';
    else if(!modern)summary.textContent='주요 변곡점 · 구형 서버 입력 · 대시보드를 다시 실행하세요.';
    else if(packet.state==='unavailable')summary.textContent='가격 사건 · 저장 일봉 자료 미확보';
    const hint=document.createElement('p');hint.textContent=!modern?'새 변곡점 기준은 대시보드 서버를 다시 실행한 뒤 적용됩니다. 아래는 기존 입력입니다.':`회색 실선 띠는 확정점, 회색 점선은 최근 잠정 후보입니다. 잠정점은 새 봉에 따라 이동하거나 사라질 수 있습니다. 주봉 앞뒤 3주 극값·양쪽 굴곡 10% 이상·고저 교대 기준이며, 반대 극점 확인 전 마지막 굴곡과 진행 중인 주는 잠정입니다. 일·주·월봉에서 동일한 실제 발생일을 사용합니다. 기준 ${packet?.as_of||'미확인'} · ${packet?.coverage_complete?'5년 범위 확보':'확보 기간 제한'} · 뉴스 원인 조사는 확정 사건만. 후속 가격으로 정한 조사 순위는 당시의 매매 신호가 아닙니다.`;
    const hover=document.createElement('p');hover.className='price-event-hover';hover.textContent='차트 위에서 해당 봉의 사건·확정일을 확인하세요.';
    const list=document.createElement('div');list.className='price-event-list';list.style.cssText='max-height:260px;overflow:auto';
    const groups=new Map((packet?.groups||[]).map(g=>[g.id,g]));
    for(const [day,rows] of days.slice().reverse()){
      const item=document.createElement('div');item.className='price-event-day';item.dataset.eventDate=day;
      const g=groups.get(rows[0].group_id);item.style.cssText='padding:8px;border-bottom:1px solid #414854';
      const heading=document.createElement('strong');heading.textContent=`${day} · ${priorities[g?.priority]||'순위 미확인'} · 조사 결과는 아래 분석 기록에서 확인`;
      item.append(heading);
      for(const e of rows){const p=document.createElement('div');p.textContent=label(e);item.append(p);}
      const reason=document.createElement('small');reason.textContent=`판정: ${(g?.reasons||[]).map(r=>reasons[r]||r).join(', ')} · 묶음 ${g?.start}~${g?.end} · 근접일이 같은 원인이라는 뜻은 아닙니다.`;item.append(reason);list.append(item);
    }
    panel.append(summary,hint,list);plot.insertAdjacentElement('afterend',panel);panel.insertAdjacentElement('beforebegin',hover);
    let showGaps=false;
    const toggle=document.createElement('input');toggle.type='checkbox';
    const toggleLabel=document.createElement('label');toggleLabel.append(toggle,document.createTextNode(' 시가 갭도 회색선으로 표시'));
    panel.insertAdjacentElement('beforebegin',toggleLabel);
    let requestDraw=()=>{};
    toggle.addEventListener('change',()=>{showGaps=toggle.checked;requestDraw();});
    const points=days.map(([day,rows])=>({day,rows,index:DrawingTools.index(day,bars,frame)})).filter(p=>p.index!=null);
    const renderer={draw(target){target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
      ctx.save();ctx.fillStyle='rgba(160,160,160,0.16)';
      const ts=chart.timeScale();
      for(const p of points){const solid=p.rows.some(e=>e.status==='confirmed'&&((modern&&e.kind!=='gap')||(e.kind==='gap'&&showGaps)));const pending=modern&&p.rows.some(preview);if(!solid&&!pending)continue;const x=DrawingTools.coordinate(ts,p.index);if(x==null||x<0||x>mediaSize.width)continue;
        // At most 5 CSS pixels, deliberately narrower than a candle at close zoom.
        const x2=DrawingTools.coordinate(ts,p.index+1),width=Math.min(5,Math.max(1,Math.abs((x2??x+5)-x)*.45));
        if(solid)ctx.fillRect(x-width/2,0,width,mediaSize.height);
        else {ctx.save();ctx.strokeStyle='rgba(180,180,180,0.55)';ctx.lineWidth=1.5;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,mediaSize.height);ctx.stroke();ctx.restore();}
      }ctx.restore();
    });}};
    const view={zOrder:()=> 'bottom',renderer:()=>renderer};
    const primitive={paneViews:()=>[view],updateAllViews(){},attached({requestUpdate}){requestDraw=requestUpdate;requestUpdate();}};
    series.attachPrimitive(primitive);
    function onCrosshair(p){
      if(!p.time)return;
      const day=typeof p.time==='string'?p.time:`${p.time.year}-${String(p.time.month).padStart(2,'0')}-${String(p.time.day).padStart(2,'0')}`;
      const b=bars.find(b=>b.date===day);if(!b)return;
      if(b.projected){hover.textContent=day+' · 미래 공간 · 가격 사건 없음';return;}
      const rows=events.filter(e=>e.event_date>=(b.first_session||b.date)&&e.event_date<=(b.last_session||b.date));
      hover.textContent=rows.length?rows.map(label).join(' / '):`${day} · 해당 봉 사건 없음`;
    }
    chart.subscribeCrosshairMove(onCrosshair);
    return ()=>{chart.unsubscribeCrosshairMove(onCrosshair);series.detachPrimitive(primitive);panel.remove();hover.remove();toggleLabel.remove();};
  }
  return {attach,grouped,label,visible,preview};
})();

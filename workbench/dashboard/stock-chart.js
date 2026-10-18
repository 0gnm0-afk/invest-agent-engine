window.WorkbenchStockChart = (() => {
  async function mount({stock,market,symbol,currency,isCurrent,context}) {
    const key=encodeURIComponent(market+':'+symbol),host=document.querySelector('#chart-workspace'),plot=document.querySelector('#price-chart');
    const status=document.querySelector('#history-status'),retry=document.querySelector('#history-retry');
    const frames=[...document.querySelectorAll('[data-frame]')],ranges=[...document.querySelectorAll('[data-range]')];
    let workspace,frame=context?.timeframe||'1d',range=context?.range||'6',busy=false;
    function controls(disabled){frames.concat(ranges,[retry]).forEach(b=>b.disabled=disabled);}
    async function render(value) {
      if(!isCurrent())return;
      const h=value.history, local=['local_verified_us','local_verified_kr'].includes(h.kind), labels={complete:'확보 완료',partial:'제공 이력 제한',failed:'조회 실패 · 기존 저장 이력 표시',pending:'다른 창에서 준비 중',not_requested:'기존 저장 이력'};
      status.classList.toggle('error-text',h.state==='failed');
      const ipDenied=['market_authentication_http_403_ip_not_allowed','market_http_403_ip_not_allowed'].includes(h.reason);
      status.textContent=`${local?(h.state==='complete'?'저장 장기 이력 · 검증 완료':h.state==='partial'?'저장 장기 이력 · 자료 주의':h.state==='unavailable'?(h.reason==='saved_history_empty_before_cutoff'?'수집 목표일 이전 제공 자료 없음':'이 종목의 장기 자료 없음 · 기존 이력 표시'):'저장 장기 이력 검증 실패 · 기존 이력 표시'):(labels[h.state]||h.state)} · ${h.first||'미확인'} ~ ${h.last||'미확인'} · 일봉 ${h.daily_count.toLocaleString('ko-KR')}개 → ${({'1d':'일봉','1w':'주봉','1mo':'월봉'})[frame]} ${value.bars.length.toLocaleString('ko-KR')}개 · ${value.provider||'공급자 미확인'} · ${value.price_basis||'조정 기준 미확인'} · 수집 ${h.collected_at||'미확인'}${h.state==='partial'&&!local?' · 2016년까지 제공되지 않은 구간은 채우지 않습니다.':''}${h.state==='failed'?' · '+(ipDenied?'Toss 허용 IP 거절(403) · Toss API 허용 IP 설정 확인 필요':h.reason==='market_authentication_http_403'?'Toss 인증 거절(403) · API 접근 권한 확인 필요':h.reason):''}${h.screening_price_difference?' · 주의: 새 수정주가와 원 스크리닝 가격이 다릅니다. 기존 피벗·작도와 비교 전 조정 기준을 확인하세요.':''} · 주/월 봉 날짜는 기간 시작일, 첫·마지막 봉은 확보된 거래일만 집계합니다. 작도는 일·주·월봉에 공유하며 SMA 설정은 주기별로 저장합니다.`;
      retry.hidden=h.can_retry===false||!['failed','pending'].includes(h.state);
      if(h.daily_update_mode)status.textContent += h.daily_update_mode==='merged'?' · 일일 수집 가격 연결 완료 ('+h.daily_as_of+')':' · 최신 일일 가격 표시 · 과거 이력 연결 보류 ('+({listing_identity_changed:'종목 식별정보 변경',price_basis_changed:'가격 기준 불일치',history_overlap_unavailable:'겹치는 기간 없음',history_overlap_changed:'과거 가격·거래량 변경'}[h.daily_update_reason]||h.daily_update_reason)+')';
      const last=value.bars.at(-1), price=document.querySelector('#history-price');
      if(price)price.textContent=`차트 종가 ${last?WorkbenchFormat.money(last.close,currency)+' '+currency:'표시 가능한 가격 없음'} · 실제 가격일 ${last?(last.last_session||last.date):'없음'}${local?' · 수집 목표일 '+h.requested_end+' · 저장 원본 '+(h.source_first||'미확인')+' ~ '+(h.source_last||'미확인'):''}${h.screening_newer_than_history?' · 주의: 스크리닝 자료가 이 장기 이력보다 최신입니다.':''}`;
      const quote=document.querySelector('#stock-quote-price'), quoteDate=document.querySelector('#stock-quote-date');
      if(quote)quote.textContent=last?WorkbenchFormat.money(last.close,currency):'결측';
      if(quoteDate)quoteDate.textContent='저장 종가 '+(last?(last.last_session||last.date):'없음');
      document.querySelector('.history-info')?.classList.toggle('history-warning',h.state==='failed'||h.state==='partial'||h.screening_price_difference||h.screening_newer_than_history||h.daily_update_mode==='daily_only');
      const details=document.querySelector('#history-quality');
      if(details){
        const q=h.quality;details.hidden=!q;
        if(q)details.querySelector('p').textContent=`원본 ${h.stored_count.toLocaleString('ko-KR')}봉 / 차트 일봉 ${h.daily_count.toLocaleString('ko-KR')}개. 가격 이상 ${q.invalid_ohlc.length}개는 차트·SMA에서 제외하고 원본은 보존합니다. ${q.calendar_policy==='retain_with_warning'?`과거 거래소 달력과 불일치한 ${q.non_sessions.length}봉은 달력의 불확실성을 고려해 차트·SMA에 유지합니다. 달력 기준 공백 ${q.missing_sessions.length}개는 실제 거래 누락으로 확정하지 않으며 채우지 않습니다.`:`비거래일 ${q.non_sessions.length}개는 차트·SMA에서 제외합니다. 거래일 공백 ${q.missing_sessions.length}개는 채우지 않습니다.`} SMA는 표시 가능한 봉의 종가 기준입니다. 최신봉 ${q.latest_session_missing?'누락':'확인'}. 제외 날짜: ${q.excluded_dates.join(', ')||'없음'}. 달력 불일치 날짜: ${q.non_sessions.join(', ')||'없음'}. 달력 기준 공백 날짜: ${q.missing_sessions.join(', ')||'없음'}.`;
      }
      workspace=await WorkbenchWorkspace.mount({host,plot,stock:value,market,symbol,currency,legend:document.querySelector('#price-legend'),isCurrent});
      if(!isCurrent())return;
      workspace?.range(range);
      frames.forEach(b=>b.classList.toggle('selected',b.dataset.frame===frame));
      ranges.forEach(b=>b.classList.toggle('selected',b.dataset.range===range));
    }
    async function change(nextFrame,collect=false) {
      if(busy||!isCurrent())return;
      busy=true;controls(true);
      try {
        if(!await WorkbenchWorkspace.closeActive())return;
        if(!isCurrent())return;
        plot.replaceChildren();host.replaceChildren();
        const priorFrame=frame;frame=nextFrame;
        if(priorFrame!==frame)range=frame==='1d'?'6':'all';
        let pending=false;
        if(collect){
          status.textContent='선택 종목의 저장 장기 이력을 확인하는 중…';
          const health=await fetch('/health');if(!health.ok)throw Error();const {token}=await health.json();
          if(!isCurrent())return;
          const response=await fetch('/api/chart-history/'+key,{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':token},body:JSON.stringify({retry:collect==='retry'})});
          if(!response.ok)throw Error();
          pending=(await response.json()).history.state==='pending';
        }
        if(!isCurrent())return;
        const response=await fetch('/api/chart/'+key+'?timeframe='+frame);if(!response.ok)throw Error();
        const value=await response.json();
        if(pending&&value.history.state==='not_requested')value.history.state='pending';
        await render(value);
      }catch {
        if(isCurrent()){status.textContent='장기 차트를 열지 못했습니다. 대시보드를 다시 실행하거나 재시도하세요.';document.querySelector('.history-info').open=true;retry.hidden=false;}
      }finally{busy=false;if(isCurrent()){controls(false);window.dispatchEvent(new Event('workbench-context'));}}
    }
    frames.forEach(b=>b.onclick=()=>change(b.dataset.frame));
    ranges.forEach(b=>b.onclick=()=>{range=b.dataset.range;workspace?.range(range);ranges.forEach(x=>x.classList.toggle('selected',x===b));window.dispatchEvent(new Event('workbench-context'));});
    retry.onclick=()=>change(frame,'retry');
    await change(frame,true);
    if(context?.visible_range)workspace?.visibleRange(context.visible_range);
    return {ready:()=>!busy,context:()=>({...workspace?.context(),timeframe:frame,range,scenario:context?.scenario||'',price_scale:'linear'}),async restore(value){context=value;range=value.range;await change(value.timeframe);range=value.range;workspace?.range(range);if(value.visible_range)workspace?.visibleRange(value.visible_range);}};
  }
  return {mount};
})();

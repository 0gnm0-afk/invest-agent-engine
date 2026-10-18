/* Common stored-series renderer: price, sector and future evidence/account histories. */
window.WorkbenchCharts = (() => {
  const mounted = [];
  function remove(chart) { const i=mounted.indexOf(chart); if(i>=0){mounted.splice(i,1);chart.remove();} }
  function clear() { mounted.splice(0).forEach(c => c.remove()); }
  function create(el, series, options = {}) {
    if (!window.LightweightCharts) { el.textContent = '차트 라이브러리를 불러오지 못했습니다.'; return; }
    if (!series.some(s => s.data.some(p => p.value != null || p.close != null))) { el.innerHTML = '<div class="empty">이 기간에 표시할 검증된 시계열이 없습니다.</div>'; return; }
    const L = LightweightCharts, dark=!!el.closest?.('.workbench-dark');
    const chart = L.createChart(el, {autoSize: true, height: options.height || 300,
      layout: {background: {color: dark?'#202630':'#ffffff'}, textColor: dark?'#a8b3c3':'#697675', attributionLogo: true, fontFamily: 'Segoe UI, Malgun Gothic, sans-serif'},
      grid: {vertLines: {visible: false}, horzLines: {color: dark?'#313946':'#edf1ef',style:dark?2:0}},
      rightPriceScale: {borderVisible: false,...(dark?{scaleMargins:{top:options.headroom||.1,bottom:.08}}:{})}, timeScale: {borderVisible: false, timeVisible: false},
      localization: {locale: 'ko-KR'}, crosshair: {mode: L.CrosshairMode.Normal}});
    const refs = series.map(s => {
      const ref = chart.addSeries(s.type === 'candles' ? L.CandlestickSeries : s.type === 'volume' ? L.HistogramSeries : L.LineSeries,
        {...(s.type === 'candles' ? {upColor: dark?'#31c58b':'#238b78', downColor: dark?'#ed6572':'#cc6963', borderVisible: false, wickUpColor: dark?'#31c58b':'#238b78', wickDownColor: dark?'#ed6572':'#cc6963'} : {color: s.color || (dark?'#6db8f6':'#218775'), lineWidth: 2}),
         priceLineVisible: false, lastValueVisible: s.type !== 'volume', title: s.label, ...s.options, ...(s.currency ? {priceFormat: {type: 'custom', minMove: s.currency === 'KRW' ? 1 : .01, formatter: value => WorkbenchFormat.money(value, s.currency)}} : {})}, s.pane || 0);
      ref.setData(s.data); return {ref, label: s.label, currency: s.currency};
    });
    if (series.some(s => s.pane === 1)) chart.panes()[1].setHeight(80);
    chart.timeScale().fitContent();
    if (options.lastBars) chart.timeScale().setVisibleLogicalRange({from: Math.max(0, series[0].data.length - options.lastBars), to: series[0].data.length});
    const legend = options.legend;
    if (legend) chart.subscribeCrosshairMove(p => {
      if (!p.time) { legend.textContent = '차트 위에서 날짜별 수치를 확인하세요.'; return; }
      legend.textContent = String(p.time) + '  ·  ' + refs.map(({ref,label,currency}) => {
        const point = p.seriesData.get(ref); return label + ': ' + (point ? (currency ? WorkbenchFormat.money(point.close ?? point.value, currency) : Number(point.close ?? point.value).toLocaleString('ko-KR', {maximumFractionDigits: 2})) : '결측');
      }).join('  /  ');
    });
    options.onReady?.(chart, refs);
    mounted.push(chart); return chart;
  }
  // Freeze the fitted price range before the library starts its native XY drag.
  // Drawing SVGs, axes and the volume pane keep their own gestures.
  function enableFreePan(el, chart, series) {
    function beginPan(e) {
      if (e.button !== 0 || e.isPrimary === false || e.target.tagName !== 'CANVAS') return;
      const rect = el.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
      if (x < 0 || x >= chart.timeScale().width() || y < 0 || y >= chart.panes()[0].getHeight()) return;
      series.priceScale().applyOptions({autoScale: false});
    }
    let previous=chart.timeScale().getVisibleLogicalRange();
    function visibleRangeChanged(next) {
      if(next&&previous&&Math.abs((next.to-next.from)-(previous.to-previous.from))>1e-6){
        series.priceScale().applyOptions({autoScale:true});
      }
      previous=next;
    }
    function zoom(e) {
      const rect=el.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;
      if(x>=0&&x<chart.timeScale().width()&&y>=0&&y<chart.panes()[0].getHeight())series.priceScale().applyOptions({autoScale:true});
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(visibleRangeChanged);
    el.addEventListener('wheel',zoom,{capture:true,passive:true});
    // Capture runs before the chart's mouse handler; it never consumes the event.
    el.addEventListener('pointerdown', beginPan, true);
    return () => {el.removeEventListener('pointerdown',beginPan,true);el.removeEventListener('wheel',zoom,true);chart.timeScale().unsubscribeVisibleLogicalRangeChange(visibleRangeChanged);};
  }
  function lineData(rows, key, scale = 1) { return rows.map(r => ({time: r.date, ...(r[key] == null ? {} : {value: r[key] * scale})})); }
  return {create, clear, remove, lineData, enableFreePan};
})();

/* Presentation only. Keep API values and calculation precision intact. */
window.WorkbenchFormat = (() => {
  const formats = Object.fromEntries([['KRW', 0], ['USD', 2]].map(([currency, digits]) =>
    [currency, new Intl.NumberFormat('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits})]));
  function money(value, currency) {
    if (value == null || value === '' || typeof value === 'boolean') return '미확인';
    const number = Number(typeof value === 'string' ? value.replaceAll(',', '') : value);
    if (!Number.isFinite(number)) return '미확인';
    if (!formats[currency]) return String(value);
    const text = formats[currency].format(number);
    return /^-0(?:\.00)?$/.test(text) ? text.slice(1) : text;
  }
  // Only explicit price labels are adapted in saved prose; ratios/dates stay intact.
  function priceText(text, currency) {
    return String(text).replace(/(피벗|하단|상단)(\s+)(-?\d[\d,]*(?:\.\d+)?)(?![\d.,%])/g,
      (_, label, space, value) => label + space + money(value, currency));
  }
  const priceKeys = new Set(['open','high','low','close','price','pivot','structure_stop','latest_close',
    'breakout_close','lower_exclusive','upper_inclusive','atr14','year_high','year_low',
    'first_half_low','last_half_low','prior_peak','anchor_a_price','anchor_b_price','value_latest','slope_per_bar']);
  function analysis(value, currency, key = '') {
    if (Array.isArray(value)) return value.map(v => analysis(v, currency, key));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) =>
      [k, key === 'ma' && typeof v === 'number' ? money(v, currency) : analysis(v, currency, k)]));
    return priceKeys.has(key) && typeof value === 'number' ? money(value, currency) : value;
  }
  return {money, priceText, analysis};
})();

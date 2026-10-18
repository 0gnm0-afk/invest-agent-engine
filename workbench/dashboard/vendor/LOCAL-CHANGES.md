# Local rendering adjustment — 2026-10-02

Bundled upstream: TradingView Lightweight Charts 5.0.9.
Original SHA-256: `e6bd6516d9ab9cb897c0d6438e3fe1126f2fdce07e68c23836e84d527de81f52`.

The user requested less visible background between candle bodies. Keep the time scale, date range, OHLC coordinates, wicks, volume, indicators and drawing anchors unchanged.

Only the candlestick renderer `Se.et` is adjusted:

1. In its width helper, change the arctangent reducing coefficient from `.2` to `.06` (large-spacing body fill trends to94% instead of80%).
2. Keep the original body/wick parity decrement only at spacing≤4px. At wider spacings, allow even pixel widths to avoid removing an additional body pixel. The existing body positioning uses the same wick center; even widths may straddle it by half a device pixel.
3. The width remains capped by the floored available bitmap spacing. Spacing≤4px retains the original width behavior. A small gap can disappear through pixel rounding at some zoom levels; OHLC values and wick positions do not change.

This is a rendering-only local modification because v5.0.9 does not expose a candle-body width option. Do not replace the library with an upstream download without reviewing/reapplying this patch. All upstream attribution, LICENSE and chart attribution links remain.

Reference: [official width calculation](https://tradingview.github.io/lightweight-charts/docs/4.1/plugins/pixel-perfect-rendering/widths/candlestick), compared to the actual5.0.9 bundled helper before editing. Financial histograms and sector line renderers are not modified.

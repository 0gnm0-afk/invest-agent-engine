"""Shared arithmetic on an already validated, completed OHLCV suffix."""
from statistics import mean

ATR_PERIOD = 14


def sma(bars, period):
    return [None if i + 1 < period else mean(b['close'] for b in bars[i-period+1:i+1])
            for i in range(len(bars))]


def atr(bars, period=ATR_PERIOD):
    # Simple mean of 14 true ranges; previous close is required for each range.
    ranges = [None] + [max(b['high']-b['low'], abs(b['high']-bars[i-1]['close']),
                          abs(b['low']-bars[i-1]['close'])) for i, b in enumerate(bars) if i]
    return [None if i < period else mean(ranges[i-period+1:i+1]) for i in range(len(bars))]


def ratio(numerator, denominator):
    return numerator / denominator if denominator is not None and denominator > 0 else None

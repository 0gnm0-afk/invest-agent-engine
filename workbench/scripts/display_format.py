"""Display adapters. Never apply to persisted JSON/CSV or calculation inputs."""
import re
from invest_agent.trading.report_format import money

def currency_for(item):
    return item.get('currency') or {'KR': 'KRW', 'US': 'USD'}.get(item.get('market'))

def price_text(text, currency):
    return re.sub(r'(피벗|하단|상단)(\s+)(-?\d[\d,]*(?:\.\d+)?)(?![\d.,%])',
                  lambda m: m[1] + m[2] + money(m[3], currency), str(text))

PRICE_KEYS = {'open','high','low','close','price','pivot','structure_stop','latest_close',
              'breakout_close','lower_exclusive','upper_inclusive','atr14','year_high','year_low',
              'first_half_low','last_half_low','prior_peak','anchor_a_price','anchor_b_price','value_latest','slope_per_bar'}

def display_analysis(value, currency, key=''):
    if isinstance(value, list):
        return [display_analysis(v, currency, key) for v in value]
    if isinstance(value, dict):
        return {k: money(v, currency) if key == 'ma' and type(v) in (int, float)
                else display_analysis(v, currency, k) for k,v in value.items()}
    return money(value, currency) if key in PRICE_KEYS and type(value) in (int,float) else value

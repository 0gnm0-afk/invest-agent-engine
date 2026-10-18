"""Market data usability, distinct from receipt and full-history completeness."""
from datetime import datetime
from math import isfinite
import json
from pathlib import Path

from invest_agent.trading.market import usable_tail
from toss_candles import daily_series

LIFECYCLE = json.loads(Path(__file__).with_name('market_lifecycle.json').read_text(encoding='utf-8'))['records']


def quote_time_state(stamp):
    if stamp is None:
        return 'not_provided'
    try:
        return 'provided' if datetime.fromisoformat(stamp).tzinfo else 'invalid'
    except (ValueError, TypeError):
        return 'invalid'


def is_depositary_receipt(row):
    """Provider security type, kept separate from the generic EQUITY instrument."""
    return any(row.get(field) == 'DEPOSITARY_RECEIPT'
               for field in ('security_type', 'securityType'))


def adr_cap_unavailable(row):
    """An ADR quote cannot value unverified underlying-share or receipt units."""
    if not is_depositary_receipt(row):
        return {}
    return {'market_cap': None, 'undated_quote_market_cap': None,
            'market_cap_as_of': None, 'market_cap_currency': None,
            'market_cap_quality': 'unavailable_adr_share_basis',
            'market_cap_basis': 'unverified_depositary_receipt_share_units',
            'market_cap_reason': 'adr_ratio_or_share_units_unverified',
            'market_cap_error': None}


def complete_cap(get, row, sessions):
    """Only a dated, unadjusted completed close can replace an undated quote."""
    unavailable = adr_cap_unavailable(row)
    if unavailable:
        return unavailable
    if row.get('quote_time_state') != 'not_provided':
        return {}
    if row.get('tradable') is False:
        return {'market_cap': None, 'market_cap_as_of': None,
                'market_cap_quality': 'unavailable_not_tradable'}
    shares = row.get('shares_outstanding')
    if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
        return {'market_cap': None, 'market_cap_as_of': None,
                'market_cap_quality': 'unavailable_shares'}
    raw = daily_series(get, row['symbol'], row['market'], sessions[-1:],
                       provider_symbol=row.get('provider_symbol'), adjusted=False, max_pages=1)
    bar = usable_tail(raw['bars'], sessions[-1:])[-1]
    cap = bar['close'] * shares
    if not isfinite(cap) or cap <= 0:
        raise ValueError('invalid_completed_close_market_cap')
    return {'market_cap': cap, 'market_cap_as_of': bar['date'],
            'market_cap_currency': raw['currency'],
            'market_cap_basis': 'toss_unadjusted_completed_close_times_observed_shares_estimate',
            'market_cap_quality': 'completed_close_estimate',
            'market_cap_price': bar['close'],
            'market_cap_price_date': bar['date'],
            'market_cap_price_source': raw['source_url'],
            'market_cap_price_basis': raw['price_basis']}


def lifecycle_record(row, completed_session, records=None):
    """Confirmed termination persists, but never crosses identity or effective date."""
    for record in LIFECYCLE if records is None else records:
        if (record.get('state') == 'confirmed_trading_ended'
                and all(row.get(k) and row.get(k) == record[k] for k in ('market', 'symbol', 'isin_code'))
                and completed_session and record['effective_session'] <= completed_session):
            return record
    return None


def error_disposition(error, row, completed_session):
    code = error.get('error_code')
    if code in ('stale_or_invalid_last_session', 'no_completed_session_bars'):
        record = lifecycle_record(row, completed_session)
        if record:
            return {**record, 'analysis_allowed': False}
        return {'state': 'quarantined_' + code, 'analysis_allowed': False,
                'cause': 'unconfirmed', 'retry_policy': 'next_requested_collection_no_same_run_loop'}
    return {'state': 'collection_failure', 'analysis_allowed': False,
            'cause': code or 'unknown'}


def history_usability(bars, sessions, rule):
    tail = usable_tail(bars, sessions)
    needed = max(rule['ma_period'] + rule['slope_window'] + rule['transition_window'],
                 rule['rs_period'] + 1, rule['activity_window'] + 1)
    return {'continuous_bars': len(tail), 'continuous_start': tail[0]['date'],
            'as_of': tail[-1]['date'], 'liquidity_20_bars_available': len(tail) >= 20,
            'profile_required_bars': needed, 'profile_history_available': len(tail) >= needed,
            'sperandeo_history_available': {str(n): len(tail) >= n for n in (63, 126, 252)},
            'scope': 'history_length_only_not_candidate_or_all_inputs_ready'}

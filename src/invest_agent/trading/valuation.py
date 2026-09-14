"""Annual reference value = forecast EPS or net income × historical PER range."""
from __future__ import annotations

from datetime import date
from decimal import Decimal


def numeric(value) -> Decimal:
    result=Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite valuation input")
    return result


def quantile_type7(values: list[Decimal], fraction: Decimal) -> Decimal:
    ordered=sorted(values)
    if not ordered:
        raise ValueError("No observations")
    rank=(len(ordered)-1)*fraction
    low=int(rank)
    high=min(low+1,len(ordered)-1)
    return ordered[low]+(ordered[high]-ordered[low])*(rank-low)


def reference_bands(bundle: dict) -> dict:
    """Public annual collector inputs use version 2; older saved inputs still load."""
    if bundle.get('schema_version') == 2:
        return annual_reference_bands(bundle)
    from .valuation_legacy import legacy_reference_bands
    return legacy_reference_bands(bundle)


def annual_reference_bands(bundle: dict) -> dict:
    if bundle.get('source') not in ('synthetic', 'sourced_input'):
        raise ValueError('Expected sourced annual input')
    as_of = date.fromisoformat(bundle['as_of'])
    ratios, years, excluded = [], set(), []
    for row in bundle['annual_history']:
        year = int(row['fiscal_year'])
        if year in years:
            raise ValueError('Duplicate annual PER year')
        years.add(year)
        if year > as_of.year or (row.get('period_end') and date.fromisoformat(row['period_end']) > as_of):
            raise ValueError('Future annual history')
        if row.get('per') is None or numeric(row['per']) <= 0:
            excluded.append(year)
            continue
        ratios.append(numeric(row['per']))
    multiples = ({'low': min(ratios), 'median': quantile_type7(ratios, Decimal('0.5')), 'high': max(ratios)}
                 if len(ratios) >= 2 else {})
    periods = []
    for forecast in bundle['forecasts']:
        if forecast.get('period_type') not in ('fiscal_year', 'next_fiscal_year'):
            raise ValueError('Expected explicit annual forecast period')
        period = {**forecast, 'price_band': None, 'market_cap_band': None, 'reasons': []}
        for key, output in (('eps', 'price_band'), ('net_income', 'market_cap_band')):
            if forecast.get(key) is None:
                period['reasons'].append(f'{key}_forecast_missing')
            elif numeric(forecast[key]) <= 0:
                period['reasons'].append(f'{key}_forecast_nonpositive')
            elif multiples:
                period[output] = {k: str(numeric(forecast[key]) * v) for k, v in multiples.items()}
        periods.append(period)
    # Next annual estimate first, then current year; never silently prefer a distant year.
    primary = next((p for p in periods if p.get('fiscal_year') == as_of.year + 1), None)
    if primary is None:
        primary = next((p for p in periods if p.get('period_type') == 'next_fiscal_year'), None)
    if primary is None:
        primary = next((p for p in periods if p.get('fiscal_year') == as_of.year), {})
    price, cap = primary.get('price_band'), primary.get('market_cap_band')
    state = 'available' if price and cap else 'partial' if price or cap else 'unavailable'
    if bundle.get('retrieval_state') != 'available' and state == 'available':
        state = 'partial'
    return {**bundle, 'authority': 'reference_only', 'state': state,
        'method': 'annual_positive_PER_min_median_max', 'observation_count': len(ratios),
        'excluded_years': excluded, 'historical_per': {k:str(v) for k,v in multiples.items()},
        'period_bands': periods, 'forecast_period': primary.get('fiscal_year') or primary.get('period_type'),
        'price_band': price, 'market_cap_band': cap,
        'reasons': (['at_least_two_positive_annual_PER_required'] if not multiples else []) +
                   (primary.get('reasons', []) if primary else ['current_or_next_annual_forecast_missing'])}

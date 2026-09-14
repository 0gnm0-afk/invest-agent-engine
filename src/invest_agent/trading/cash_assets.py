"""Cash-equivalent classification and valuation; never buying power."""
from decimal import Decimal

CASH_TYPES = {'cash', 'cash_equivalent', 'cash_equivalents', 'cma', 'deposit',
              'currency_balance', 'cash_management_account', '현금', '예수금', '발행어음_cma'}
RISK_TYPES = {'stock', 'equity', 'etf', 'adr', 'bond', 'fund'}


def classify(position, mapping=None):
    for key in ('asset_type', 'product_type', 'security_type'):
        value = str(position.get(key, '')).strip().lower()
        if value in CASH_TYPES | RISK_TYPES:
            return {'cash_equivalent': value in CASH_TYPES, 'source': key,
                    'value': value, 'inferred': False}
    mapped = (mapping or {}).get(position.get('symbol'))
    if mapped is not None:
        return {'cash_equivalent': mapped in (True, 'cash_equivalent', 'CMA'),
                'source': 'explicit_mapping', 'inferred': False}
    if position.get('symbol') == 'NHKRCMA030' and position.get('market') == 'KR':
        return {'cash_equivalent': True, 'source':'verified_NH_CMA_product_code',
                'evidence':'NH_saved_CMA_product_and_app_allocation', 'inferred':False}
    # Prior NH normalizer explicitly marked this broker CMA product. Do not infer
    # all NH codes, funds, bonds or interest-bearing products to be cash.
    if position.get('instrument_class_hint') == 'CMA_note_from_broker_label':
        return {'cash_equivalent': True, 'source': 'NH_CMA_broker_name_fallback',
                'inferred': True}
    return {'cash_equivalent': False, 'source': 'no_explicit_cash_classification', 'inferred': False}


def is_cash(position, bundle=None):
    return classify(position, (bundle or {}).get('cash_equivalent_mapping'))['cash_equivalent']


def summarize(bundle):
    from .portfolio import number, broker_allocation
    allocation = broker_allocation(bundle)
    allocated = {(r['account'], r['symbol']): r for r in allocation.get('rows', [])}
    rates = bundle.get('fx_to_base', {})
    base = bundle['base_currency']
    components, issues, reconciliations = [], [], []
    complete = bundle.get('accounts_complete', True)
    risk_value = Decimal(0)
    risk_values = {}

    def component(alias, kind, currency, amount, *, valued=None, symbol=None, classification=None, source=None):
        fx = rates.get(currency)
        amount = None if amount is None else number(amount)
        value = number(valued) if valued is not None else amount * number(fx, positive=True) if amount is not None and fx is not None else None
        reason = 'missing_fx' if fx is None else 'native_amount_unavailable' if amount is None else None
        row = {'account': alias, 'kind': kind, 'symbol': symbol, 'currency': currency,
               'original_amount': str(amount) if amount is not None else None,
               'fx_rate': str(fx) if fx is not None else None, 'as_of': bundle['as_of'],
               'value_base': str(value) if value is not None else None,
               'valuation_source': source or 'native_amount_times_fx',
               'valuation_complete': value is not None and fx is not None,
               'reason': reason, 'classification': classification}
        components.append(row)
        return row

    for account in bundle['accounts']:
        alias = account['alias']
        if account.get('holdings_complete') is False:
            complete = False
            issues.append(f'{alias}:holdings_incomplete')
        cash_positions = [p for p in account['positions'] if is_cash(p, bundle)]
        for p in account['positions']:
            stored = allocated.get((alias, p['symbol']), {}).get('value_krw') if base == 'KRW' else None
            if is_cash(p, bundle):
                native = p.get('native_value', p.get('market_value'))
                if native is None and p.get('currency') == 'KRW':
                    native = p.get('observed_evaluation_krw', stored)
                if native is None and p.get('quantity') is not None and p.get('price') is not None:
                    native = number(p['quantity']) * number(p['price'])
                component(alias, 'CMA' if 'CMA' in str(p.get('instrument_class_hint', '')).upper() else 'cash_equivalent',
                          p['currency'], native, valued=stored, symbol=p['symbol'],
                          classification=classify(p, bundle.get('cash_equivalent_mapping')),
                          source='verified_broker_allocation' if stored is not None else None)
                continue
            try:
                value = number(stored) if stored is not None else number(p['quantity']) * number(p['price']) * number(rates[p['currency']], positive=True)
                risk_value += value
                risk_values[(alias, p['symbol'])] = str(value)
            except (KeyError, TypeError, ValueError):
                complete = False
                issues.append(f'{alias}/{p["symbol"]}:risk_asset_value_unavailable')

        # A normalized cash inventory has an explicit disjointness assertion.
        # The NH adapter's old frozen allocation instead has exact broker sums
        # and a checked zero-debt composition. Its KRW overseas valuation is not
        # a second cash balance: keep native USD and broker-converted KRW once.
        observed = account.get('observed_deposits', {})
        residual = allocated.get((alias, None), {})
        nh_checked = (bundle.get('broker_allocation', {}).get('checks') == 'exact_component_sums_zero_debt_no_foreign_unsettled'
                      and allocation['state'] == 'available' and base == 'KRW'
                      and residual.get('value_krw') is not None
                      and observed.get('US', {}).get('krw_dca') is not None
                      and number(observed['US']['krw_dca']) == number(residual['value_krw'])
                      and observed.get('US', {}).get('fc_dca') is not None
                      and number(observed.get('KR', {}).get('dca', 0)) == 0)
        if nh_checked:
            component(alias, 'foreign_cash', 'USD', observed['US']['fc_dca'],
                      valued=residual['value_krw'], source='NH_exact_allocation_foreign_cash_converted_once')
            component(alias, 'cash', 'KRW', 0, source='NH_observed_KR_dca')
            reconciliations.append({'account': alias, 'state': 'verified',
                'cash_and_CMA_overlap': False, 'basis': 'broker_exact_component_sums_and_zero_debt',
                'duplicate_representation_excluded': 'US.krw_dca_as_KRW_cash',
                'buying_power_verified': False})
        else:
            for currency, amount in account.get('cash', {}).items():
                component(alias, 'cash' if currency == base else 'foreign_cash', currency, amount)
            stated = account.get('cash_reconciliation', {})
            disjoint = stated.get('cash_excludes_cash_equivalents') is True
            uncertain = bool(cash_positions and account.get('cash') and not disjoint)
            if uncertain or account.get('cash_complete') is False:
                complete = False
                issues.append(f'{alias}:' + ('cash_CMA_overlap_unverified' if uncertain else 'cash_inventory_incomplete'))
            reconciliations.append({'account': alias, 'state': 'unavailable' if uncertain else 'verified' if disjoint else 'not_required',
                'cash_and_CMA_overlap': None if uncertain else False,
                'basis': stated.get('source_ref'), 'reason': 'cash_CMA_overlap_unverified' if uncertain else None})
    known_cash = sum((Decimal(r['value_base']) for r in components if r['value_base'] is not None), Decimal(0))
    complete = bool(complete and all(r['valuation_complete'] for r in components))
    # Unknown overlap means the sum is only a component subtotal, not equity.
    equity = risk_value + known_cash if complete else None
    return {'components': components, 'cash_equivalent_value': str(known_cash) if complete else None,
            'calculated_cash_component_subtotal': str(known_cash), 'valuation_complete': complete,
            'unvalued_components': [r for r in components if not r['valuation_complete']],
            'risk_asset_value_base': str(risk_value), 'risk_position_values': risk_values,
            'managed_equity_base': str(equity) if equity is not None else None,
            'cash_weight': str(known_cash / equity) if equity else None,
            'reconciliation': reconciliations, 'issues': issues,
            'verified_investable_cash': None, 'buying_power_reason': 'orderable_cash_and_orders_not_reconciled'}

"""Select compatible disclosure bundles without inventing facts or mixing revisions."""
from copy import deepcopy


def bare(cell):
    return {k:deepcopy(v) for k,v in cell.items() if k not in ('disclosure_candidates','latest_disclosure')}


def retain(selected, candidates):
    result=deepcopy(selected);unique={}
    for c in candidates:
        if c and c.get('value') is not None:
            signature=(c.get('receipt') or c.get('accession'),c.get('start'),c.get('end'),c.get('tag'),c.get('value'))
            unique[signature]=bare(c)
    if unique:result['disclosure_candidates']=list(unique.values())
    return result


def select_bundles(values):
    """Income and balance-sheet groups have independent exact-period cohorts.

    Prefer the most complete filing, then its latest submission. Preserve newer
    isolated facts separately and explicitly disclose any historical selection.
    Conflicting facts inside one filing are never resolved by first-row order.
    """
    ifrs=any(c.get('accounting_basis')=='IFRS' for c in values.values())
    income_keys=['sales','operating','income','net_income']+(['basic_eps','eps'] if ifrs else [])
    for keys in [income_keys,
                 ('assets','liabilities','total_equity','current_assets','current_liabilities'),
                 ('cash_dividends','buybacks','common_income')]:
        cohorts={}
        for key in keys:
            current=values.get(key,{})
            for c in [current,*current.get('disclosure_candidates',[])]:
                filing=c.get('receipt') or c.get('accession')
                if not filing or c.get('value') is None or c.get('period_coverage')=='unknown' or c.get('accounting_basis_verified') is False:continue
                if (c.get('start'),c.get('end'),c.get('unit'),c.get('accounting_basis'))!=(current.get('start'),current.get('end'),current.get('unit'),current.get('accounting_basis')):continue
                cohort_unit=c.get('unit')
                if ifrs and key in income_keys:cohort_unit=cohort_unit.removesuffix('/share')
                cohort=(filing,c.get('start'),c.get('end'),cohort_unit,c.get('accounting_basis'))
                cohorts.setdefault(cohort,{}).setdefault(key,[]).append(c)
        clean={}
        for cohort,fields in cohorts.items():
            selected={k:cs[0] for k,cs in fields.items() if len({c['value'] for c in cs})==1}
            if len(selected)>=2:clean[cohort]=selected
        if not clean:continue
        def rank(item):
            cohort,fields=item
            # Alternative interest tags count as one concept, not extra coverage.
            count=len(set('interest_expense' if k=='interest_nonoperating' else k for k in fields))
            return count,max(c.get('filed') or '' for c in fields.values()),cohort[0]
        cohort,selected=max(clean.items(),key=rank)
        for key,c in selected.items():
            old=values[key];new=retain(c,[old,*old.get('disclosure_candidates',[])])
            new['selected_cohort']=cohort[0]
            if (old.get('receipt') or old.get('accession'),old.get('value'))!=(cohort[0],c['value']):
                new['latest_disclosure']=bare(old)
                new['comparison_note']='동일 공시에 함께 확인된 원값 묶음을 선택했습니다. 개별 최신값은 별도 근거로 보존합니다.'
            values[key]=new
    return values

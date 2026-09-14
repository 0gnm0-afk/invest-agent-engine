"""Version 1 monthly NTM inputs, retained only for saved-input compatibility."""
from datetime import date
from decimal import Decimal

from .valuation import numeric, quantile_type7


def legacy_reference_bands(bundle: dict) -> dict:
    if bundle.get("schema_version")!=1 or bundle.get("source") not in ("synthetic","sourced_input"):
        raise ValueError("Expected versioned, sourced valuation input")
    as_of=date.fromisoformat(bundle["as_of"])
    result={"schema_version":1,"symbol":bundle["symbol"],"as_of":bundle["as_of"],"source":bundle["source"],
            "currency":bundle["currency"],"authority":"reference_only","state":"unavailable",
            "price_band":None,"market_cap_band":None,"reasons":[]}
    forecast=bundle["forecast"]
    if forecast.get("period_type")!="NTM":
        result["reasons"].append("NTM_required_no_FY1_substitution")
        return result
    if not forecast.get("source_ref") or date.fromisoformat(forecast["estimated_at"])>as_of:
        raise ValueError("Forecast must have an available-at-date source")
    ratios,months,excluded=[],set(),[]
    for point in bundle["monthly_history"]:
        price_date=date.fromisoformat(point["price_date"])
        month=price_date.strftime("%Y-%m")
        if month in months:
            raise ValueError("Duplicate PER month")
        months.add(month)
        # Explicit 5-year month window. No automatic outlier deletion.
        age=(as_of.year-price_date.year)*12+as_of.month-price_date.month
        if price_date>as_of or age>=60:
            excluded.append({"month":month,"reason":"outside_window"}); continue
        if point.get("is_month_end_close") is not True or not point.get("source_ref"):
            excluded.append({"month":month,"reason":"missing_month_end_source"}); continue
        if date.fromisoformat(point["eps_known_at"])>price_date:
            excluded.append({"month":month,"reason":"lookahead_eps"}); continue
        if point.get("price_basis")!=point.get("eps_basis") or not point.get("price_basis"):
            excluded.append({"month":month,"reason":"basis_mismatch"}); continue
        eps,price=numeric(point["ttm_diluted_eps"]),numeric(point["close"])
        if eps<=0 or price<=0:
            excluded.append({"month":month,"reason":"nonpositive_eps_or_price"}); continue
        ratios.append(price/eps)
    result.update(observation_count=len(ratios),excluded_months=excluded,forecast_source_ref=forecast["source_ref"])
    if len(ratios)<36:
        result["reasons"].append("minimum_36_eligible_months_required")
        return result
    quartiles={name:quantile_type7(ratios,Decimal(q)) for name,q in (("low","0.25"),("median","0.5"),("high","0.75"))}
    result["historical_per"]={k:str(v) for k,v in quartiles.items()}
    for input_key,output_key in (("diluted_eps","price_band"),("common_net_income","market_cap_band")):
        value=forecast.get(input_key)
        if value is None:
            result["reasons"].append(f"missing_{input_key}"); continue
        estimate=numeric(value)
        if estimate<=0:
            result["reasons"].append(f"nonpositive_{input_key}"); continue
        result[output_key]={k:str(estimate*multiple) for k,multiple in quartiles.items()}
    result["state"]="available" if result["price_band"] and result["market_cap_band"] else "partial" if result["price_band"] or result["market_cap_band"] else "unavailable"
    return result



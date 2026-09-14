"""Display-only formatting; never rounds stored calculation inputs."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


def money(value, currency):
    if value is None:
        return "미확정"
    try:
        number = Decimal(str(value).replace(",", ""))
        if not number.is_finite():
            return "미확정"
        if currency not in ("KRW", "USD"):
            return str(value)
        places = 0 if currency == "KRW" else 2
        rounded = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return f"{abs(rounded) if rounded == 0 else rounded:,.{places}f}"
    except (InvalidOperation, ValueError):
        return str(value)


def prose(value):
    # Sentence breaks do not split decimal points, dates, identifiers or URLs.
    text = str(value).strip()
    text = re.sub(r"(?<![\w/])(-?[0-9][0-9,]*(?:\.[0-9]+)?)\s*(원|달러|KRW|USD)(?![A-Za-z])",
                  lambda m: money(m[1], "KRW" if m[2] in ("원", "KRW") else "USD") + " " + m[2], text)
    return re.sub(r"(?<=[.!?]) +(?=[가-힣A-Z])", "  \n", text)

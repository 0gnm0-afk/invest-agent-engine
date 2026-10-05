"""Internal risk calculation primitives; no storage, providers, or plan adoption."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json


def number(value, *, positive=False, signed=False):
    if isinstance(value, bool) or value is None:
        raise ValueError('missing_or_invalid_number')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid_number') from exc
    if not result.is_finite() or (not signed and result < 0) or (positive and result <= 0):
        raise ValueError('invalid_number')
    return result


def instant(value):
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('timestamp_required') from exc
    if result.tzinfo is None:
        raise ValueError('timezone_required')
    return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def fresh(as_of, evaluated_at, max_age_seconds):
    age = (instant(evaluated_at) - instant(as_of)).total_seconds()
    return 0 <= age <= number(max_age_seconds, positive=True)

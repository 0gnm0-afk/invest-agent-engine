"""Versioned synthetic-input contract for the first vertical slice."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date

SCHEMA_VERSION = 1


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def validate_fixture(raw: dict, markets: list[str]) -> dict:
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Expected fixture schema_version=1")
    if raw.get("source") != "synthetic":
        raise ValueError("TASK-TF-01 accepts explicitly synthetic fixtures only")
    rows = raw.get("observations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("observations must be a nonempty list")
    selected, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid observation")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
        market = row.get("market")
        if market not in ("KR", "US"):
            raise ValueError("Unknown market")
        for field in ("symbol", "exchange", "currency", "price_basis"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"Missing {field}")
        date.fromisoformat(row["session_date"])
        if row.get("complete") is not True:
            raise ValueError("Incomplete bars cannot enter this pipeline")
        for field in ("open", "high", "low", "close", "volume", "trading_value"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Invalid {field}")
            if value < 0 or (field in ("open", "high", "low", "close") and value == 0):
                raise ValueError(f"Invalid {field}")
        if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            raise ValueError("Inconsistent OHLC")
        key = (market, row["exchange"], row["symbol"], row["session_date"])
        if key in seen:
            raise ValueError("Duplicate observation")
        seen.add(key)
        if market in markets:
            selected.append(dict(row))
    if not selected:
        raise ValueError("No observations for selected markets")
    return {"schema_version": SCHEMA_VERSION, "source": "synthetic", "observations": selected}

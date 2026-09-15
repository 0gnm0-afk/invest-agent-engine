"""Small direct-library supplements for fields absent from the remote MCP."""

from __future__ import annotations

import asyncio
import contextlib
import io
import math
import re
import tempfile
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

_XBRL_D_AND_A_FACTS = {
    "DepreciationAndAmortisationExpense",
    "DepreciationAndAmortizationExpense",
}
_XBRL_DEPRECIATION_FACTS = {"DepreciationExpense"}
_XBRL_AMORTISATION_FACTS = {"AmortisationExpense", "AmortizationExpense"}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _normalized_name(row: dict[str, Any]) -> str:
    raw = str(row.get("account_nm") or "").lower()
    return re.sub(r"[\s·ㆍ()_-]", "", raw)


def _account_id(row: dict[str, Any]) -> str:
    return str(row.get("account_id") or "").lower().replace("_", "")


def _first_amount(
    rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]
) -> float | None:
    for row in rows:
        if predicate(row):
            amount = _number(row.get("thstrm_amount"))
            if amount is not None:
                return abs(amount)
    return None


def extract_dart_supplement(rows: list[dict[str, Any]]) -> dict[str, float | str | None]:
    """Extract only D&A, cash and interest-bearing debt from DART all-account rows."""

    cfs_rows = [row for row in rows if row.get("fs_div") == "CFS"]
    ofs_rows = [row for row in rows if row.get("fs_div") == "OFS"]
    selected = cfs_rows or ofs_rows or rows
    basis = "CFS" if cfs_rows else "OFS" if ofs_rows else None

    cash = _first_amount(
        selected,
        lambda row: _account_id(row).endswith("cashandcashequivalents")
        or _normalized_name(row) in {"현금및현금성자산", "현금및현금성자산합계"},
    )

    cash_flow_rows = [row for row in selected if row.get("sj_div") == "CF"] or selected
    combined_d_and_a = _first_amount(
        cash_flow_rows,
        lambda row: (
            "depreciationandamorti" in _account_id(row)
            or (
                "감가상각" in _normalized_name(row)
                and "무형자산상각" in _normalized_name(row)
            )
        ),
    )
    if combined_d_and_a is not None:
        d_and_a: float | None = combined_d_and_a
    else:
        depreciation = _first_amount(
            cash_flow_rows,
            lambda row: "depreciation" in _account_id(row)
            or _normalized_name(row) in {"감가상각비", "유형자산감가상각비"},
        )
        amortization = _first_amount(
            cash_flow_rows,
            lambda row: "amortisation" in _account_id(row)
            or "amortization" in _account_id(row)
            or _normalized_name(row) in {"무형자산상각비", "상각비"},
        )
        d_and_a = (
            depreciation + amortization
            if depreciation is not None and amortization is not None
            else depreciation if depreciation is not None else amortization
        )

    debt_groups: tuple[Callable[[dict[str, Any]], bool], ...] = (
        lambda row: _account_id(row).endswith("shorttermborrowings")
        or _normalized_name(row) in {"단기차입금", "단기차입금합계"},
        lambda row: "currentportionoflongtermborrowings" in _account_id(row)
        or _normalized_name(row) in {"유동성장기차입금", "유동성장기부채"},
        lambda row: (
            _account_id(row).endswith("longtermborrowings")
            and "currentportion" not in _account_id(row)
        )
        or _normalized_name(row) in {"장기차입금", "장기차입금합계"},
        lambda row: "currentportionofbonds" in _account_id(row)
        or _normalized_name(row) in {"유동성사채", "유동사채"},
        lambda row: (
            _account_id(row).endswith("bondsissued")
            and "currentportion" not in _account_id(row)
        )
        or _normalized_name(row) in {"사채", "회사채"},
    )
    debt_parts: list[float] = []
    for predicate in debt_groups:
        amount = _first_amount(selected, predicate)
        if amount is not None:
            debt_parts.append(amount)
    debt = sum(debt_parts) if debt_parts else None

    return {
        "statement_basis": basis,
        "d_and_a": d_and_a,
        "d_and_a_source": "finstate_all" if d_and_a is not None else None,
        "cash": cash,
        "debt": debt,
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_xbrl_amount(
    root: ET.Element,
    fact_names: set[str],
    year: int,
    statement_basis: str,
) -> float | None:
    contexts = {
        element.attrib["id"]: element
        for element in root
        if _local_name(element.tag) == "context" and "id" in element.attrib
    }
    candidates: list[tuple[int, float]] = []

    for fact in root:
        if _local_name(fact.tag) not in fact_names:
            continue
        amount = _number(fact.text)
        context = contexts.get(fact.attrib.get("contextRef", ""))
        if amount is None or context is None:
            continue

        period = next(
            (child for child in context if _local_name(child.tag) == "period"), None
        )
        if period is None:
            continue
        end_date = next(
            (
                child.text
                for child in period
                if _local_name(child.tag) == "endDate" and child.text
            ),
            None,
        )
        if end_date is None or not end_date.startswith(str(year)):
            continue

        members = [
            str(element.text or "")
            for element in context.iter()
            if _local_name(element.tag) == "explicitMember"
        ]
        consolidated = any(member.endswith("ConsolidatedMember") for member in members)
        separate = any(member.endswith("SeparateMember") for member in members)
        if statement_basis == "CFS" and (not consolidated or separate):
            continue
        if statement_basis == "OFS" and consolidated:
            continue

        reported_amount = any(member.endswith("ReportedAmountMember") for member in members)
        unit_is_krw = str(fact.attrib.get("unitRef") or "").upper() == "KRW"
        score = (4 if reported_amount else 0) + (2 if unit_is_krw else 0) - len(members)
        candidates.append((score, abs(amount)))

    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _extract_xbrl_d_and_a_detail(
    xml: bytes,
    year: int,
    statement_basis: str = "CFS",
) -> tuple[float, str] | None:

    root = ET.fromstring(xml)
    combined = _extract_xbrl_amount(root, _XBRL_D_AND_A_FACTS, year, statement_basis)
    if combined is not None:
        return combined, "combined"

    depreciation = _extract_xbrl_amount(
        root, _XBRL_DEPRECIATION_FACTS, year, statement_basis
    )
    amortisation = _extract_xbrl_amount(
        root, _XBRL_AMORTISATION_FACTS, year, statement_basis
    )
    if depreciation is None or amortisation is None:
        return None
    return depreciation + amortisation, "split"


def extract_xbrl_d_and_a(
    xml: bytes,
    year: int,
    statement_basis: str = "CFS",
) -> float | None:
    """Read annual D&A from combined or separate standard XBRL facts."""

    detail = _extract_xbrl_d_and_a_detail(xml, year, statement_basis)
    return detail[0] if detail is not None else None


def _extract_xbrl_d_and_a_detail_from_zip(
    archive_path: Path,
    year: int,
    statement_basis: str = "CFS",
) -> tuple[float, str] | None:
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".xbrl"):
                continue
            detail = _extract_xbrl_d_and_a_detail(
                archive.read(name), year, statement_basis
            )
            if detail is not None:
                return detail
    return None


def extract_xbrl_d_and_a_from_zip(
    archive_path: Path,
    year: int,
    statement_basis: str = "CFS",
) -> float | None:
    """Find the first usable XBRL instance in an Open DART XBRL archive."""

    detail = _extract_xbrl_d_and_a_detail_from_zip(
        archive_path, year, statement_basis
    )
    return detail[0] if detail is not None else None


async def fetch_daily_prices(ticker: str, count: int = 250) -> list[dict[str, float | str]]:
    def _fetch() -> list[dict[str, float | str]]:
        import FinanceDataReader as fdr

        start = (datetime.now(ZoneInfo("Asia/Seoul")).date() - timedelta(days=550)).isoformat()
        frame = fdr.DataReader(ticker, start).tail(count)
        records: list[dict[str, float | str]] = []
        for index, row in frame.iterrows():
            close = _number(row.get("Close"))
            volume = _number(row.get("Volume"))
            if close is None or volume is None:
                continue
            records.append(
                {
                    "date": index.date().isoformat(),
                    "close": close,
                    "volume": volume,
                    "trading_value": close * volume,
                }
            )
        return records

    return await asyncio.to_thread(_fetch)


async def fetch_dart_supplements(
    ticker: str, years: list[int], api_key: str | None
) -> tuple[dict[int, dict[str, float | str | None]], str | None]:
    if not api_key:
        return {}, "DART_API_KEY is not set; D&A, cash and debt remain missing."
    if not years:
        return {}, "No annual years were supplied for DART supplementation."

    def _fetch() -> tuple[dict[int, dict[str, float | str | None]], str | None]:
        import OpenDartReader

        dart = OpenDartReader(api_key)
        supplements: dict[int, dict[str, float | str | None]] = {}
        failed: list[str] = []
        requested_years = set(years)
        minimum_year = min(requested_years)
        comparative_d_and_a: dict[int, tuple[float, str, str]] = {}
        for year in sorted(requested_years, reverse=True):
            last_error: Exception | None = None
            for basis in ("CFS", "OFS"):
                try:
                    # OpenDartReader 0.2.2 prints request details; keep CLI JSON clean.
                    with contextlib.redirect_stdout(io.StringIO()):
                        frame = dart.finstate_all(
                            ticker, year, reprt_code="11011", fs_div=basis
                        )
                    if frame is None or frame.empty:
                        continue
                    records = frame.to_dict("records")
                    supplement = extract_dart_supplement(records)
                    report_id = next(
                        (str(row["rcept_no"]) for row in records if row.get("rcept_no")),
                        None,
                    )
                    supplement["report_id"] = report_id
                    xbrl_error: Exception | None = None
                    must_load_prior = year > minimum_year and year - 1 in requested_years
                    current_detail: tuple[float, str] | None = None
                    if (
                        supplement["d_and_a"] is None
                        and report_id is not None
                        and (year not in comparative_d_and_a or must_load_prior)
                    ):
                        try:
                            with tempfile.TemporaryDirectory(prefix="invest-agent-xbrl-") as temp:
                                archive_path = Path(temp) / f"{report_id}.zip"
                                with contextlib.redirect_stdout(io.StringIO()):
                                    dart.finstate_xml(report_id, save_as=str(archive_path))
                                current_detail = _extract_xbrl_d_and_a_detail_from_zip(
                                    archive_path, year, basis
                                )
                                if must_load_prior:
                                    prior_detail = _extract_xbrl_d_and_a_detail_from_zip(
                                        archive_path, year - 1, basis
                                    )
                                    if prior_detail is not None:
                                        prior_amount, prior_kind = prior_detail
                                        comparative_d_and_a[year - 1] = (
                                            prior_amount,
                                            report_id,
                                            prior_kind,
                                        )
                        except Exception as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
                            xbrl_error = exc
                    if supplement["d_and_a"] is None:
                        comparative_detail = comparative_d_and_a.get(year)
                        use_comparative = comparative_detail is not None and (
                            current_detail is None
                            or {"split": 1, "combined": 2}[comparative_detail[2]]
                            > {"split": 1, "combined": 2}[current_detail[1]]
                        )
                        selected_report_id: str | None
                        if use_comparative and comparative_detail is not None:
                            amount, selected_report_id, kind = comparative_detail
                            origin = "comparative"
                        elif current_detail is not None:
                            amount, kind = current_detail
                            selected_report_id = report_id
                            origin = "current"
                        else:
                            amount = None
                            kind = None
                            selected_report_id = None
                            origin = None
                        if amount is not None and kind is not None:
                            supplement["d_and_a"] = amount
                            supplement["d_and_a_source"] = (
                                f"xbrl_{kind}_standard_fact_{origin}"
                            )
                            supplement["d_and_a_report_id"] = selected_report_id
                    if supplement["d_and_a"] is None:
                        failed.append(
                            f"{year}: XBRL {type(xbrl_error).__name__}"
                            if xbrl_error is not None
                            else f"{year}: D&A standard fact not found in XBRL"
                        )
                    supplements[year] = supplement
                    break
                except Exception as exc:  # library errors vary by DART response status  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
                    last_error = exc
            else:
                failed.append(
                    f"{year}: {type(last_error).__name__}"
                    if last_error is not None
                    else f"{year}: no annual filing"
                )
        return supplements, "; ".join(failed) or None

    return await asyncio.to_thread(_fetch)

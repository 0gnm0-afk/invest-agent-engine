"""Human-readable report rendering for deterministic analysis results."""

from __future__ import annotations

from typing import Any

SCENARIO_LABELS = {
    "bearish": "비관",
    "base": "기본",
    "bullish": "낙관",
}


def _number(value: Any, decimals: int = 2) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "확인 불가"
    return f"{value:,.{decimals}f}"


def _rate(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "확인 불가"
    return f"{value * 100:.2f}%"


def _markdown_cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown_report(result: dict[str, Any]) -> str:
    """Render one analysis result without adding forecasts or trade instructions."""

    ticker = _markdown_cell(result.get("ticker"))
    lines = [
        f"# {ticker} 종목 분석 리포트",
        "",
        f"- 데이터 기준일: {_markdown_cell(result.get('as_of'))}",
        f"- 통화: {_markdown_cell(result.get('currency'))}",
        "- 범위: 사용자 가정 기반 C02 DCF, C08 현금전환 경고, C11 거래대금 참고",
        "",
        "## C02 — 5년 FCFF DCF",
        "",
        "| 시나리오 | 할인율 | 영구성장률 | 기업가치 | 주주가치 | 주당가치 | 현재가 대비 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    c02 = result.get("c02", {})
    scenarios = c02.get("scenarios", {}) if isinstance(c02, dict) else {}
    for name in ("bearish", "base", "bullish"):
        scenario = scenarios.get(name, {})
        lines.append(
            "| "
            + " | ".join(
                [
                    SCENARIO_LABELS[name],
                    _rate(scenario.get("discount_rate")),
                    _rate(scenario.get("terminal_growth_rate")),
                    _number(scenario.get("enterprise_value")),
                    _number(scenario.get("equity_value")),
                    _number(scenario.get("value_per_share")),
                    _number(scenario.get("upside_downside_pct")) + "%",
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "### 사용자 지정 peer 배수",
            "",
            "| 종목 | 이름 | EV/EBIT | PER | PBR | 기준일 |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    peers = c02.get("peers", []) if isinstance(c02, dict) else []
    if peers:
        for peer in peers:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(peer.get("ticker")),
                        _markdown_cell(peer.get("name")),
                        _number(peer.get("ev_to_ebit")),
                        _number(peer.get("per")),
                        _number(peer.get("pbr")),
                        _markdown_cell(peer.get("as_of")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| — | 지정된 peer 없음 | — | — | — | — |")

    c08 = result.get("c08", {})
    historical_range = c08.get("historical_range") if isinstance(c08, dict) else None
    if isinstance(historical_range, dict):
        range_text = (
            f"{_rate(historical_range.get('minimum'))} ~ "
            f"{_rate(historical_range.get('maximum'))} "
            f"({historical_range.get('valid_years')}개년)"
        )
    else:
        range_text = "판단 불가"
    lines.extend(
        [
            "",
            "## C08 — DCF 현금전환 경고",
            "",
            f"- 과거 현금전환율 범위: {range_text}",
            "- 이 경고는 DCF 계산값을 자동으로 수정하지 않는다.",
            "",
            "| 시나리오 | DCF 현금전환율 | 판단 | 신뢰도 |",
            "|---|---:|---|---|",
        ]
    )
    c08_scenarios = c08.get("scenarios", {}) if isinstance(c08, dict) else {}
    for name in ("bearish", "base", "bullish"):
        scenario = c08_scenarios.get(name, {})
        lines.append(
            f"| {SCENARIO_LABELS[name]} | {_rate(scenario.get('dcf_cash_conversion_rate'))} | "
            f"{_markdown_cell(scenario.get('assessment'))} | "
            f"{_markdown_cell(scenario.get('confidence'))} |"
        )

    c11 = result.get("c11", {})
    averages = c11.get("average_trading_value", {}) if isinstance(c11, dict) else {}
    lines.extend(
        [
            "",
            "## C11 — 유동성 참고",
            "",
            f"- 거래대금 기준일: {_markdown_cell(c11.get('as_of'))}",
            f"- 관측 거래일: {_markdown_cell(c11.get('available_trading_days'))}",
            "- 범위: 종가×거래량의 평균 거래대금만 제공",
            "",
            "| 기간 | 평균 거래대금 | 통화 |",
            "|---|---:|---|",
        ]
    )
    for window in ("20d", "60d", "250d"):
        lines.append(
            f"| {window[:-1]}거래일 | {_number(averages.get(window))} | "
            f"{_markdown_cell(c11.get('currency'))} |"
        )
    lines.extend(
        [
            "",
            ("스프레드·호가 깊이·예정 포지션 금액이 없어 평시/스트레스 청산 기간, "
             "거래비용, 시장충격은 판단하지 않는다."),
            "",
            "## 데이터 상태와 한계",
            "",
        ]
    )
    input_data = result.get("input_data", {})
    lines.append(f"- 대상 데이터 상태: {_markdown_cell(input_data.get('target_status'))}")
    missing = input_data.get("target_missing_fields", [])
    lines.append(
        "- 결측 필드: " + (", ".join(str(value) for value in missing) if missing else "없음")
    )
    lines.extend(
        [
            "",
            "### 데이터 출처",
            "",
            "| 데이터 | 출처 | 기준일 | 수집시각 | 오류 |",
            "|---|---|---|---|---|",
        ]
    )
    sources = input_data.get("target_sources", {})
    if isinstance(sources, dict) and sources:
        for name, metadata in sources.items():
            metadata = metadata if isinstance(metadata, dict) else {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        _markdown_cell(name),
                        _markdown_cell(metadata.get("source")),
                        _markdown_cell(metadata.get("as_of")),
                        _markdown_cell(metadata.get("fetched_at")),
                        _markdown_cell(metadata.get("error")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("| — | 출처 정보 없음 | — | — | — |")
    lines.extend(
        [
            "- 전망 FCFF·할인율·영구성장률·예상 EBITDA는 자동 생성하지 않고 사용자 입력만 사용했다.",
            "- 이 리포트는 투자 판단 보조 자료이며 매수·매도 지시가 아니다.",
        ]
    )
    return "\n".join(lines) + "\n"

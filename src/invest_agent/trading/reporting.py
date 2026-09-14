"""Deterministic fixture report, without investment signals or LLM calls."""
from __future__ import annotations

import html


def render_portfolio_report(run_id: str, report_date: str, snapshot: dict) -> dict[str,str]:
    import json
    from datetime import datetime

    from .portfolio import review
    result=review(snapshot,datetime.fromisoformat(snapshot["evaluated_at"]))
    markdown="\n".join(["# 계좌 위험 계산 검토", "", f"- 실행: {run_id}",f"- 보고일: {report_date}",
        f"- 입력 출처: {snapshot['source']} (증권사 API 실연동 아님)",f"- 잔고 기준: {result['as_of']}",
        f"- 기준통화: {result['base_currency']}",f"- 합산 평가액: {result['equity_base']}",
        f"- 확인된 손절 노출: {result['known_stop_exposure_base']}",f"- 손절 노출 전수 계산: {result['stop_exposure_complete']}",
        f"- 오래된 snapshot: {result['stale']}","", "```json",json.dumps(result,ensure_ascii=False,indent=2),"```","",
        "합성 또는 수동 입력 검토입니다. 실제 계좌·미체결·체결 대조와 주문 가능 계획 채택은 아직 연결하지 않았습니다."])
    return {"report.md":markdown,"report.html":'<!doctype html><html lang="ko"><meta charset="utf-8"><body><pre>'+html.escape(markdown)+'</pre></body></html>'}


def render_market_report(run_id: str, report_date: str, snapshot: dict, chart_refs: list[dict] | None = None) -> dict[str,str]:
    from .market import screen
    result = screen(snapshot)
    def safe(value):
        return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ")
    def instrument_label(row: dict) -> str:
        return row.get("name") or row.get("quote_symbol") or row.get("symbol") or "미확정"
    lines = ["# 아침 후보 탐색 — 검토용", "", "자동 매매 규칙이 아닌 관측용 결과입니다.", "",
             f"- 실행: {run_id}", f"- 보고일(KST): {report_date}", f"- 수집 시각: {result['fetched_at']}",
             f"- 시장별 완료 세션: {result['session_dates']}", f"- 탐색 범위: {result['coverage']}",
             "- 거래대금: 종가×거래량 대용값. 실제 체결대금이 아닙니다.",
             "- 후보: universe hard gate → Sperandeo S1~S4 → Taver 위치. 모든 수치는 완료봉 관측용입니다.", ""]
    from .structure_report import candidate_sections
    lines += candidate_sections([r for r in result['rows'] if r['state']=='candidate'],result['universe_audit'])
    lines += ['', '| 시장 | 종목 | 판정 | 관문 탈락/결측 사유 |', '|---|---|---|---|']
    for row in result['rows']:
        if row['state'] != 'candidate':
            cells=[row['market'],instrument_label(row),row['state'],row.get('reason') or row.get('universe',{}).get('universe_rejection_reasons',[])]
            lines.append('| '+' | '.join(safe(v) for v in cells)+' |')
    lines += ['', '후보 0건과 자료 결측을 구분합니다. 관측 가격은 손절/주문 가격으로 채택하지 않습니다.', '']
    from pathlib import Path
    gallery = []
    for ref in chart_refs or []:
        label = safe(f"{ref['symbol']} {ref['timeframe']} {ref['as_of']}")
        filename = Path(ref["path"]).name
        lines += [f"![{label}]({filename})", ""]
        gallery.append(f'<figure><img style="max-width:100%" src="{filename}" alt="{label}"><figcaption>{label}</figcaption></figure>')
    markdown = "\n".join(lines)
    return {"report.md":markdown, "report.html":'<!doctype html><html lang="ko"><meta charset="utf-8"><title>아침 후보</title><body><pre>'+html.escape(markdown)+'</pre>'+"".join(gallery)+'</body></html>'}


def render_report(run_id: str, report_date: str, snapshot: dict) -> dict[str, str]:
    def safe(value):
        return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
    def instrument_label(row: dict) -> str:
        return row.get("name") or row.get("quote_symbol") or row.get("symbol") or "미연결"

    lines = ["# 아침 파이프라인 테스트 리포트", "",
             "**합성 테스트 데이터입니다. 실제 시세·투자 후보·매매 판단 자료가 아닙니다.**", "",
             f"- 실행 ID: {run_id}", f"- 보고일(KST): {report_date}",
             "- 범위: TASK-TF-01 실행·저장·재개 검증", "",
             "| 시장 | 거래소 | 종목 | 완료 세션 | 종가 | 통화 | 거래대금 | 가격 기준 |",
             "|---|---|---|---|---:|---|---:|---|"]
    for row in snapshot["observations"]:
        fields = [row.get("market"), row.get("exchange"), instrument_label(row), row.get("session_date"), row.get("close"), row.get("currency"), row.get("trading_value"), row.get("price_basis")]
        lines.append("| " + " | ".join(safe(v) for v in fields) + " |")
    lines += ["", "후보 필터·차트/LLM 해석·실계좌 위험·가치 밴드는 후속 작업입니다. DCF는 실행하지 않습니다.", ""]
    markdown = "\n".join(lines)
    webpage = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>테스트 리포트</title><body><pre>' + html.escape(markdown) + '</pre></body></html>\n'
    return {"report.md": markdown, "report.html": webpage}

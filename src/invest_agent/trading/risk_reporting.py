"""Freeze the existing policy ledger into a reproducible morning report input."""
from __future__ import annotations

from .portfolio import number
from .risk_budget import reduction_scenarios
from .risk_ledger import RiskLedger


def freeze(store) -> dict:
    ledger = RiskLedger(store)
    result: dict = {kind: ledger.entities(kind) for kind in (
        "position", "monitor", "candidate", "sell_plan", "buy_plan", "strategy", "risk_config", "strategy_state")}
    result["spec_version"] = "R1-R4/1.0"
    result["budgets"] = []
    result["budget_snapshots"] = []
    for group in result["strategy"]:
        ident = group["strategy_group_id"]
        try:
            _, _, snapshot = ledger._budget_inputs(ident)
            result["budget_snapshots"].append(snapshot)
            result["budgets"].append(ledger.budget_status(ident))
        except ValueError as exc:
            result["budgets"].append({"strategy_group_id": ident, "risk_budget_status": "UNAVAILABLE",
                                       "manual_required": True, "block_reasons": [str(exc)]})
    return result


def render(policy: dict) -> list[str]:
    if not policy or not any(policy.get(k) for k in ("position", "buy_plan", "sell_plan", "strategy")):
        return ["", "## R1~R4 사용자 채택 정책", "", "전략계정·채택 정책 미설정: 신규 위험 증가 계획 승인 불가. 사용자 입력이 필요합니다.", ""]

    def cell(value):
        if value is None:
            return "미확인"
        return str(value).replace("|", "\\|").replace("\n", " ")

    def table(headers, rows):
        return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
                *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows), ""]

    output = ["", "## R1~R4 사용자 채택 정책", "", "아래는 실행 시작 시 고정한 원장 기록입니다. 감시 기준일을 확인해야 하며, 계획·경보는 실제 주문이나 체결을 뜻하지 않습니다.", ""]
    if policy.get("persistence", {}).get("state") == "VERSION_CONFLICT":
        output += ["원장에 새 변경이 있어 이번 실행의 과거 기준 계산을 원장에 반영하지 않았습니다. 현재 채택값으로 새 실행이 필요합니다.", ""]
    if policy.get("account_reconciliation"):
        output += ["### 전략계좌 보유 대조", ""]
        output += table(["전략", "대조 상태", "보유 변경 포지션", "승인 보류 사유"],
                        [[r["strategy_group_id"], r["state"], ", ".join(r["changed_positions"]),
                          ", ".join(r["errors"]) or "없음"] for r in policy["account_reconciliation"]])
    monitors = {m["position_id"]: m for m in policy.get("monitor", [])}
    def priority(p):
        """Contract §23: overlapping warnings keep the highest applicable priority."""
        m = monitors.get(p['position_id'], {})
        plans = [s for s in policy.get('sell_plan', []) if s['position_id'] == p['position_id']
                 and s.get('status') not in {'CANCELLED', 'COMPLETED', 'DRAFT', 'INACTIVE_RECOVERED'}]
        active = [s for s in plans if s.get('approved_by_user') is True and s.get('status') == 'ACTIVE']
        actions = {'ACTION_REQUIRED', 'PARTIALLY_EXECUTED', 'CONFIRMED_BREACH'}
        if m.get('state') == 'CONFIRMED_BREACH' or any(
            t.get('status') in actions for s in active
            for t in [s.get('initial_reduction', {}), *s.get('tranches', [])]
        ):
            return 0
        if any(s.get('multiple_breach') for s in active):
            return 1
        if m.get('state') in {None, 'UNSET', 'DATA_UNAVAILABLE'} or m.get('missing_data') or any(
            s.get('data_status') == 'STALE_DATA' for s in plans
        ):
            return 2
        if m.get('state') in {'APPROACHING', 'PROVISIONAL_BREACH', 'REVIEW_REQUIRED'} or any(
            t.get('status') in {'APPROACHING', 'PROVISIONAL_BREACH'} for s in active for t in s.get('tranches', [])
        ):
            return 3
        if p.get('proposed_medium_trend_state') in {'WEAKENING', 'BREAK_CANDIDATE'}:
            return 4
        stop = p.get('current_protection_price')
        if stop is not None and any(
            c['position_id'] == p['position_id'] and number(c['candidate_price']) > number(stop)
            for c in policy.get('candidate', [])
        ):
            return 5
        return 6

    positions = sorted(policy.get('position', []), key=priority)
    for p in positions:
        ident = p["position_id"]
        m = monitors.get(ident, {})
        output += [f"### {cell(p.get('name') or p['symbol'])} · {cell(p['market'])}", ""]
        output += table(["기준일", "가격", "평균단가", "보유 수량", "최초 손절", "현재 보호선", "거리", "거리 비율", "R1 상태"], [[
            m.get("data_as_of", p.get("market_data_session")), p.get("current_market_price"), p.get("average_cost"),
            p.get("current_quantity"), p.get("initial_stop_price"), p.get("current_protection_price"),
            m.get("distance_to_protection"), m.get("distance_pct"),
            m.get("state", "UNSET" if p.get("current_protection_price") is None else "DATA_UNAVAILABLE")]])
        evidence = p.get("medium_trend_evidence", {})
        output += [f"중기 추세 제안: {cell(p.get('proposed_medium_trend_state'))} / 사용자 채택: {cell(p.get('adopted_medium_trend_state'))}",
                   f"근거: {cell(evidence.get('evidence_for'))} / 반대 근거: {cell(evidence.get('evidence_against'))}",
                   f"확인 필요: {cell(m.get('manual_required', True))} / 결측·지연: {cell(m.get('missing_data'))}", ""]
        if m.get("message"):
            output += [cell(m["message"]), ""]
        output += table(["통화", "현재 하방 노출", "보호선 체결 가정 손익", "실시간 제공"], [[
            p.get('currency'), m.get('current_downside_exposure'), m.get('stop_execution_pnl'), m.get('realtime_available', False)]])
        for plan in (s for s in policy.get("sell_plan", []) if s["position_id"] == ident):
            output += [f"매도계획 {cell(plan['sell_plan_id'])}: {cell(plan['status'])} / 종류: {cell(plan['plan_kind'])}", ""]
            reduction = plan.get("initial_reduction", {})
            output += [f"최초 축소 잔량: {cell(reduction.get('remaining_planned_quantity'))} / 상태: {cell(reduction.get('status'))}", ""]
            output += table(["동시 이탈", "이탈 회차", "확인 대상 수량", "현재 잔여 보유", "미체결 회차 예정 합계", "데이터 상태"], [[
                plan.get('multiple_breach', False), plan.get('breached_tranche_ids', []),
                plan.get('action_required_quantity'), plan.get('current_remaining_quantity', p.get('current_quantity')),
                plan.get('pending_planned_quantity'), plan.get('data_status')]])
            output += table(["회차", "가격", "미체결 수량", "판정", "상태", "최종 보호"], [[
                t["tranche_id"], t.get("trigger_price"), t.get("remaining_planned_quantity"), t.get("confirmation_basis"),
                t.get("status"), t["tranche_id"] == plan.get("final_protection_tranche_id")] for t in plan.get("tranches", [])])
        candidates = [c for c in policy.get("candidate", []) if c["position_id"] == ident]
        if candidates:
            price = p.get('current_market_price')
            usable = price is not None and bool(m.get('data_as_of')) and not m.get('missing_data') and m.get('state') != 'DATA_UNAVAILABLE'
            candidate_rows = []
            for c in candidates:
                distance = number(price, positive=True) - number(c['candidate_price'], positive=True) if usable else None
                giveback = max(distance, number('0')) if distance is not None else None
                giveback_pct = giveback / number(price, positive=True) * 100 if giveback is not None else None
                candidate_rows.append([c['candidate_id'], c['candidate_price'], c.get('timeframe'),
                                       c.get('recommended_role'), distance, giveback, giveback_pct,
                                       c.get('basis_description'), c.get('counterevidence'), c.get('data_as_of')])
            output += ['후보 거리 = 현재가 − 후보가(원통화/주). 반납폭은 양수 거리와 현재가 대비 %입니다. 후보와 권장 역할은 미채택 제안이며, 가격 결측·지연 시 거리/반납폭은 미확인입니다.', '']
            output += table(['미채택 후보', '가격', '시간축', '권장 역할', '현재가 대비 거리', '주당 반납폭', '반납폭(%)', '근거', '반대 근거', '기준시점'], candidate_rows)
    for plan in policy.get("buy_plan", []):
        review = plan.get("risk_review") or {}
        output += [f"### 매수계획 {cell(plan['buy_plan_id'])} · {cell(plan['symbol'])}", ""]
        output += table(["종류", "상태", "보호선", "계획 수량", "미체결 수량", "명목위험", "예약 현금", "예상 비중", "초과 비중 승인"], [[
            plan.get("plan_kind"), plan.get("status"), plan.get("adopted_stop_price"), plan.get("total_planned_quantity"),
            plan.get("total_remaining_quantity"), plan.get("total_nominal_planned_risk"), plan.get("total_reserved_cash"),
            plan.get("projected_position_weight_pct"), plan.get("overweight_approval", False)]])
        output += table(["회차", "방식", "가격", "범위", "위험 기준가", "미체결 수량", "상태"], [[
            t["tranche_id"], t.get("trigger_type"), t.get("trigger_price"),
            f"{cell(t.get('price_range_lower'))} ~ {cell(t.get('price_range_upper'))}", t.get("risk_entry_price"),
            t.get("remaining_quantity"), t.get("status")] for t in plan.get("tranches", [])])
        output += [f"차단 사유: {cell(plan.get('block_reasons', []))} / 사용 가능 현금: {cell(review.get('available_cash_to_plan'))}",
                   f"위험 스냅샷: {cell(plan.get('risk_budget_snapshot_id'))} / 사후 체결 검토: {cell(plan.get('post_trade_status'))}", ""]
        deviations = plan.get('execution_deviations') or ([plan['execution_deviation']] if plan.get('execution_deviation') else [])
        if deviations:
            output += ["실제 체결 비교: 계획위험은 이번 체결에 대응하는 승인 잔량까지만 계산합니다. 아래 차이는 원통화 기준이며 미체결 잔량을 체결로 간주하지 않습니다.", ""]
            output += table(["체결 ID", "시각", "통화", "계획 기준가", "실제 가격", "가격 차이", "직전 계획 잔량", "실제 수량", "초과 수량", "대응 계획위험", "실제 명목위험", "위험 차이"], [[
                d.get('execution_id'), d.get('actual_at'), d.get('currency'), d.get('planned_price'), d.get('actual_price'),
                d.get('price_difference'), d.get('planned_remaining_quantity'), d.get('actual_quantity'),
                d.get('quantity_over_remaining'), d.get('planned_nominal_risk_for_fill'), d.get('actual_nominal_risk'),
                d.get('nominal_risk_difference')] for d in deviations])
            post = plan.get('post_trade_review') or {}
            output += [f"사후 검토 스냅샷: {cell(post.get('risk_budget_snapshot_id'))} / 전체 한도 초과액(KRW): {cell(post.get('portfolio_over_budget_amount'))}", ""]
            output += table(["시장", "종목", "사후 예상 위험(KRW)", "종목 한도 초과액(KRW)"], [[
                r.get('market'), r.get('symbol'), r.get('projected_open_risk'), r.get('over_budget_amount')]
                for r in post.get('instruments', [])])
    for budget in policy.get("budgets", []):
        output += [f"### 전략 위험예산 {cell(budget['strategy_group_id'])}", ""]
        output += table(["항목", "값"], [[label, budget.get(field)] for label, field in (
            ("기준 통화", "base_currency"), ("전략 순자산", "strategy_equity"), ("스냅샷 종류", "equity_snapshot_type"),
            ("기준시점", "equity_data_as_of"), ("종목 위험비율", "per_position_risk_pct"), ("전체 위험비율", "portfolio_open_risk_pct"),
            ("종목 예산", "per_position_risk_budget_amount"), ("전체 예산", "portfolio_risk_budget_amount"),
            ("현재 개방위험", "current_portfolio_open_risk"), ("예약 위험", "reserved_planned_risk"),
            ("확인된 예약 위험 소계", "known_reserved_risk_subtotal"), ("위험 미확인 매수계획", "unknown_risk_reservations"),
            ("예상 개방위험", "projected_portfolio_open_risk"), ("잔여 여력", "remaining_portfolio_risk_capacity"),
            ("위험 미확인 포지션", "unknown_risk_positions"), ("미해결 이탈", "breached_unresolved_positions"),
            ("공식 일간 검토", "official_daily_review_status"),
            ("지연 데이터", "stale_data_components"), ("환율 결측", "fx_missing_components"),
            ("전체 한도 초과액", "portfolio_over_budget_amount"), ("확인 필요", "manual_required"),
            ("상태", "risk_budget_status"), ("차단 사유", "block_reasons"))])
        output += ["종목별 위험 금액은 KRW이며, 비중은 0~1 비율입니다. 예약 매수는 예정 위험에 포함하고 미체결 매도는 차감하지 않습니다.", ""]
        output += table(["시장", "종목", "수량", "현재가", "평균단가", "보호선", "개방위험(KRW)", "보호선 가정 손익(KRW)", "위험 상태"], [[
            p.get('market'), p.get('symbol'), p.get('current_quantity'), p.get('current_market_price'),
            p.get('average_cost'), p.get('current_protection_price'), p.get('position_open_risk'),
            p.get('stop_execution_pnl'), p.get('risk_status')] for p in budget.get('positions', [])])
        output += table(["시장", "종목", "종목 예산", "현재 위험", "예약 추가매수 위험", "추가매수 후 위험", "현재 비중", "추가매수 후 비중", "한도 초과액"], [[
            r.get('market'), r.get('symbol'), r.get('risk_budget_amount'), r.get('current_open_risk'),
            r.get('reserved_planned_risk'), r.get('projected_open_risk'), r.get('current_position_weight_pct'),
            r.get('projected_position_weight_pct'), r.get('over_budget_amount')] for r in budget.get('instruments', [])])
        scenarios = reduction_scenarios(budget, policy.get('sell_plan', []))
        output += ['오버웨이트 승인은 표시된 계획에만 해당합니다. 종료 계획의 과거 승인이나 다른 계획의 승인을 새 추가매수에 적용하지 않습니다. 확인 필요는 위험예산 검토 상태이며 매도 지시가 아닙니다.', '']
        output += table(['시장', '종목', '계획별 오버웨이트 승인', '사용자 확인 필요', '검토 사유'], [[
            r.get('market'), r.get('symbol'), r.get('overweight_approval_by_plan') or '승인 기록 없음',
            r.get('manual_required'), r.get('review_reasons')] for r in budget.get('instruments', [])])
        if scenarios:
            output += ["부분축소 가정: 각 행은 현재 보유량에서 해당 회차만 체결됐다고 보는 독립 시나리오입니다. 행끼리 합산하지 않으며 현재 위험·현금을 변경하지 않습니다.", ""]
            output += table(["승인 매도계획", "회차", "가정 수량", "위험 감소(KRW)", "축소 후 포지션 위험", "매수예약 포함 전체 위험", "상태"], [[
                s['sell_plan_id'], s['tranche_id'], s['quantity'], s['risk_reduction'], s['position_risk_after'],
                s['portfolio_risk_after_including_buy_reservations'], s['state']] for s in scenarios])
        else:
            output += ["부분축소 시나리오: 사용할 수 있는 승인 매도 수량이 없어 미설정입니다.", ""]
    return output

"""One morning report with independent market, account and valuation components."""
from __future__ import annotations

import copy
import html
import json
from datetime import datetime
from pathlib import Path


def _input(config_path: Path, filename) -> dict:
    if filename is None:
        return {"state":"missing", "reason":"not_configured"}
    if not isinstance(filename,str):
        return {"state":"unavailable", "reason":"input_path_must_be_text"}
    path=(config_path.parent/filename).resolve()
    try:
        value=json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value,dict):
            raise ValueError("Input must be an object")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
        return {"state":"loaded", "payload":value}
    except (OSError,ValueError):
        return {"state":"unavailable", "reason":"input_file_unreadable_or_invalid"}


def prepare(config_path: Path, evaluated_at: str) -> tuple[dict,dict]:
    config=json.loads(config_path.read_text(encoding="utf-8-sig"))
    if config.get("schema_version") != 1:
        raise ValueError("Morning config schema_version must be 1")
    if not isinstance(config.get('backup_on_completion',True),bool):
        raise ValueError('backup_on_completion must be boolean')  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    if config.get("screen_config") and config.get("market_snapshot"):
        raise ValueError("Choose live screen_config or frozen market_snapshot, not both")
    valuation_paths=config.get("valuation_inputs",[])
    if not isinstance(valuation_paths,list):
        raise ValueError("valuation_inputs must be a list")  # noqa: TRY004 -- Preserve the existing ValueError validation contract for callers.
    snapshot={"schema_version":1,"source":"morning_bundle","workflow":"morning","evaluated_at":evaluated_at,
              'backup_on_completion':config.get('backup_on_completion',True),
              "account_input":_input(config_path,config.get("account_snapshot")),
              "scenario_input":_input(config_path,config.get("scenario_inputs")),
              "order_input":_input(config_path,config.get("order_snapshot")),
              "market_input":_input(config_path,config.get("market_snapshot") or config.get("screen_config")),
              "market_mode":"replay" if config.get("market_snapshot") else "live",
              "valuation_inputs":[_input(config_path,p) for p in valuation_paths],
              "valuation_collection":config.get("valuation_collection"),
              'holding_review_policy':config.get('holding_review_policy',{}),
              "llm_state":"deferred_by_user"}
    return snapshot,config


def holdings(snapshot: dict) -> list[dict]:
    from .cash_assets import is_cash
    payload=snapshot.get("account_input",{}).get("payload",{})
    rows: list[dict]=[]
    accounts=payload.get("accounts",[])
    if not isinstance(accounts,list):
        return rows
    for account in accounts:
        if not isinstance(account,dict) or not isinstance(account.get("positions",[]),list):
            continue
        for position in account.get("positions",[]):
            if not isinstance(position,dict):
                continue
            if is_cash(position, payload):
                continue
            rows.append({"account":account.get("alias","unknown"),"symbol":position.get("symbol","unknown"),
                         "quote_symbol":position.get("quote_symbol"), "market":position.get("market"),
                         "benchmark":position.get("benchmark"), "name": position.get("name")})
    return rows


class CollectorUnavailableError(RuntimeError):
    """The required runtime collector is missing or differs from frozen inputs."""


def market_component(snapshot: dict, collector=None) -> dict:
    entry=snapshot["market_input"]
    held=holdings(snapshot)
    if entry["state"] != "loaded":
        return {"state":"unavailable","reason":entry["reason"],"held_chart_missing":[r["symbol"] for r in held]}
    required = entry['payload'].get('collector_id') if snapshot['market_mode'] != 'replay' else None
    if required and (collector is None or getattr(collector, 'collector_id', None) != required or
                     not entry['payload'].get('collector_version') or
                     getattr(collector, 'collector_version', None) != entry['payload']['collector_version']):
        raise CollectorUnavailableError('The frozen market collector and version are required to resume collection')
    try:
        if snapshot["market_mode"] == "replay":
            market=copy.deepcopy(entry["payload"])
        else:
            from .market_provider import collect
            request=copy.deepcopy(entry["payload"])
            request["additional_universe"]=[{"market":p["market"],"symbol":p["quote_symbol"],"benchmark":p["benchmark"]}
                for p in held if p["market"] in ("KR","US") and p["quote_symbol"] and p["benchmark"]]
            request["risk_universe"] = [{"market": p["market"], "symbol": p["quote_symbol"] or p["symbol"]}
                                        for p in held if p["market"] in ("KR", "US")]
            market=(collector if required else collect)(request,datetime.fromisoformat(snapshot["evaluated_at"]))
        if market.get("source") not in ("live_public","synthetic"):
            raise ValueError("Unsupported market snapshot source")
        from .market import screen
        previous=snapshot.get('previous_market',{})
        for key in ('previous_screen','previous_run_id','previous_session_dates'):
            market.pop(key,None)
        if previous.get('state')=='available':
            market['previous_screen']=copy.deepcopy(previous['rows'])
            market['previous_run_id']=previous['run_id']
            market['previous_session_dates']=previous['session_dates']
        market['held_symbols'] = [p['quote_symbol'] or p['symbol'] for p in held]
        result=screen(market)
        # Explicit union: chart generation may not drop a held stock just because
        # the candidate filter rejected it. Missing identifiers remain visible.
        symbols={r["symbol"] for r in market["series"]}
        held_symbols=[p["quote_symbol"] or p["symbol"] for p in held]
        market["held_symbols"]=held_symbols
        missing=[p for p in held_symbols if p not in symbols]
        incomplete=result["unavailable_count"] or result["unavailable_scope_count"] or missing
        state="unavailable" if incomplete and not market["series"] else "partial" if incomplete else "available"
        return {"state":state,
                "mode":snapshot["market_mode"],"screen":result,"snapshot":market,"held_chart_missing":missing,
                'comparison':{k:v for k,v in previous.items() if k!='rows'}}
    except CollectorUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
        return {"state":"unavailable","reason":"market_component_failed","error_type":type(exc).__name__,
                "held_chart_missing":[r["symbol"] for r in held]}


def portfolio_component(snapshot: dict, market: dict | None = None) -> dict:
    entry=snapshot["account_input"]
    if entry["state"] != "loaded":
        return {"state":"unavailable","reason":entry["reason"],"positions":[]}
    try:
        from .portfolio import review
        result=review(entry["payload"],datetime.fromisoformat(snapshot["evaluated_at"]))
        if market is not None:
            from .holding_review import attach
            result = attach(result, entry['payload'], market, snapshot.get('holding_review_policy',{}))
        from .portfolio_changes import compare
        result['changes']=compare(result,snapshot.get('previous_portfolio',{}))
        from .policy_review import inspect
        result['policy_review']=inspect(entry['payload'],result,snapshot.get('plan_records',[]))
        orders=snapshot.get('order_input',{'state':'missing'})
        capacity=None
        result['reconciliation']={'state':'not_configured'}
        if orders['state']!='missing':
            capacity={'reconciled':False}
            try:
                if orders['state']!='loaded': raise ValueError('order_snapshot_unreadable')
                from .capacity import reconcile
                capacity=reconcile(entry['payload'],orders['payload'],snapshot.get('plan_records',[]),
                                   datetime.fromisoformat(snapshot['evaluated_at']))
                result['reconciliation']={'state':'partial' if capacity['purchase_blockers'] else 'available','capacity':capacity}
                if capacity.get('reconciled') is True and not capacity['purchase_blockers']:
                    result['verified_investable_cash'] = {
                        'by_account':{alias:values.get('orderable_cash') for alias,values in capacity.get('accounts',{}).items()},
                        'as_of':capacity.get('as_of'), 'basis':'reconciled_orderable_cash_after_reservations'}
            except (ValueError,KeyError,TypeError) as exc:
                result['reconciliation']={'state':'unavailable','reason':str(exc)}
        scenario=snapshot.get('scenario_input',{'state':'missing'})
        result['scenarios']={'state':'not_configured','rows':[]}
        if scenario['state']=='loaded':
            try:
                from .scenarios import evaluate
                result['scenarios']=evaluate(entry['payload'],result,scenario['payload'],snapshot.get('plan_records',[]),capacity)
            except (ValueError,KeyError,TypeError) as exc:
                result['scenarios']={'state':'unavailable','reason':str(exc),'rows':[]}
        elif scenario['state']!='missing':
            result['scenarios']={'state':'unavailable','reason':scenario.get('reason'),'rows':[]}
        result["component_state"]="partial" if result["stale"] or result["errors"] or not result["stop_exposure_complete"] else "available"
        if result['scenarios']['state'] in ('unavailable','partial'):
            result['component_state']='partial'
        if result['reconciliation']['state'] in ('unavailable','partial'):
            result['component_state']='partial'
        return {"state":result["component_state"],"result":result}
    except Exception as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
        # Keep every imported holding visible even when validation prevents sums.
        return {"state":"unavailable","reason":"account_validation_failed","error_type":type(exc).__name__,
                "positions":[{**r,"state":"unavailable"} for r in holdings(snapshot)]}


def valuation_component(snapshot: dict, market: dict, collector=None) -> dict:
    from .valuation import reference_bands
    targets={r["symbol"] for r in market.get("screen",{}).get("rows",[]) if r["state"]=="candidate"}
    targets.update(r["quote_symbol"] or r["symbol"] for r in holdings(snapshot))
    output, errors={},[]
    entries = list(snapshot["valuation_inputs"])
    policy = snapshot.get('valuation_collection')
    if policy:
        if collector is None or getattr(collector, 'collector_version', None) != policy['version']:
            raise ValueError('Valuation collector missing or version changed')
        held = {r['quote_symbol'] or r['symbol'] for r in holdings(snapshot) if r.get('market') in ('KR', 'US')}
        manual = {e.get('payload', {}).get('symbol') for e in entries if e['state'] == 'loaded'}
        candidates = sorted(targets - held - manual)
        limit = policy.get('candidate_limit', 10)
        if not isinstance(limit, int) or limit < 0:
            raise ValueError('Invalid valuation candidate limit')
        try:
            entries.extend(collector(sorted(held - manual) + candidates[:limit]))
        except Exception as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
            errors.append(type(exc).__name__ + ': valuation_collection_failed')
    for entry in entries:
        if entry["state"] != "loaded":
            errors.append(entry["reason"]); continue
        try:
            bundle=entry["payload"]
            if bundle["as_of"] > snapshot["evaluated_at"][:10]:
                raise ValueError("Valuation as_of is later than the morning snapshot")
            if targets and bundle["symbol"] not in targets:
                continue
            if bundle["symbol"] in output:
                raise ValueError("Duplicate valuation symbol")
            value=reference_bands(bundle)
            value["input_source"]=bundle["source"]
            output[bundle["symbol"]]=value
        except Exception as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
            errors.append(type(exc).__name__+": valuation_input_failed")
    for target in sorted(targets):
        output.setdefault(target,{"symbol":target,"state":"unavailable","price_band":None,"market_cap_band":None,
                                  "reasons":["valuation_not_collected_or_unsupported"]})
    return {"state":"available" if output and not errors and all(v["state"]=="available" for v in output.values()) else "partial" if output else "unavailable",
            "rows":list(output.values()),"errors":errors,"scope":"candidates_and_holdings"}


def render(run_id: str, report_date: str, snapshot: dict, results: dict, root: Path, folder: Path) -> tuple[dict,list,dict]:
    market,portfolio,valuation=(results[k] for k in ("market","portfolio","valuation"))
    from .report_format import money
    from .report_names import company_names, company_label
    names = company_names([*market.get('snapshot', {}).get('series', []),
                           *market.get('screen', {}).get('rows', []),
                           *portfolio.get('result', {}).get('positions', []),
                           *valuation.get('rows', [])])
    def money_band(value, currency):
        if not isinstance(value, dict):
            return value
        return ' / '.join(money(value.get(key), currency) for key in ('low', 'median', 'high'))
    def safe(v):
        return html.escape('미확정' if v is None else str(v)).replace("|","&#124;").replace("\n"," ").replace("\r"," ")
    def percent(v):
        return "미확정" if v is None else f"{float(v)*100:.2f}%"
    def instrument_label(row: dict) -> str:
        return company_label(row, names)
    def display_label(value: object) -> str:
        """Use a stored instrument name for human-facing held-instrument text."""
        if not isinstance(value, str):
            return str(value)
        if value in names:
            return names[value]
        candidates: list[dict] = []
        candidates.extend(p.get("positions",[]) if isinstance(p, dict) else [])
        candidates.extend(portfolio.get("positions",[]) if isinstance(portfolio.get("positions",[]), list) else [])
        candidates.extend(allocation.get("rows",[]) if isinstance(allocation, dict) and isinstance(allocation.get("rows",[]), list) else [])
        for row in candidates:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            identifiers = {row.get("symbol"), row.get("quote_symbol"), row.get("instrument")}
            if value in identifiers:
                return str(row["name"])
        return value
    state_names={"available":"계산 완료","partial":"일부 미확정","unavailable":"미연결/계산 불가"}
    body=["# 아침 검토 리포트", "", f"- 보고일(KST): {report_date}",f"- 실행 ID: {run_id}",
          f"- 계산 기준 시각: {snapshot['evaluated_at']}",f"- 시장 입력 방식: {snapshot['market_mode']} (replay는 저장자료 재생)",
          "- LLM 해석은 별도 report-llm에 표시됩니다. 이 원본은 Python 계산과 로컬 차트입니다.", "",
          "| 구성 | 상태 |", "|---|---|",f"| 시장 탐색 | {state_names[market['state']]} |",f"| 보유 위험 | {state_names[portfolio['state']]} |",f"| 참고 가치 밴드 | {state_names[valuation['state']]} |", "",
          "## 보유 전수 점검", ""]
    p=portfolio.get("result",{})
    from .portfolio import broker_allocation
    allocation=broker_allocation(snapshot.get('account_input',{}).get('payload',{}))
    if p:
        from .holding_report import render as render_holdings
        body += render_holdings(p)
        body += [f"- 계좌 자료: {safe(p['source'])}, 기준 {safe(p['as_of'])}; 오래된 자료: {p['stale']}",
                 f"- 합산 평가액: {money(p['equity_base'],p['base_currency'])} {safe(p['base_currency'])}",
                 f"- 확인된 손절 노출: {money(p['known_stop_exposure_base'],p['base_currency'])}; 전수 계산 여부: {p['stop_exposure_complete']}",""]
        if allocation['state']=='available' and 'cash_assets' not in p:
            body += [f"- 증권사 평가 기준 순자산: {safe(allocation['total_krw'])} KRW", '',
                      '아래 자산 구성 비중은 앱과 대조한 증권사 평가 기준입니다. 현금·미체결 대조 및 매수 여력 계산과 구분합니다.', '',
                      '| 계좌 별칭 | 자산 | 평가액(KRW) | 비중 |', '|---|---|---:|---:|']
            for row in allocation['rows']:
                label=instrument_label(row)
                if label == "미확정":
                    label='주식·CMA 외 잔액 (매수가능액 아님)'
                body.append('| '+' | '.join(safe(v) for v in [row['account'],label,row['value_krw'],percent(row['weight'])])+' |')
            body += ['']
        if allocation['state']!='available' and any('gross_cash_balance_unavailable' in e for e in p.get('errors',[])):
            body += ['전체 현금 잔고가 확인되지 않아 합산 평가액·비중은 미확정입니다. 매수 가능 금액으로 전체 현금을 대체하지 않습니다.', '']
    else:
        body += [f"미확정: {safe(portfolio.get('reason'))}",""]
    positions=p.get("positions",portfolio.get("positions",[]))
    body += ["| 계좌 별칭 | 종목 | 상태 | 비중 | 손절 거리 | 손절 노출(기준통화) | 손절가 기준 손익(비용 전) |", "|---|---|---|---:|---:|---:|---:|"]
    priority={'stop_breached':0,'needs_stop':1,'unavailable':1,'review':2}
    for position in sorted(positions,key=lambda row:(priority.get(row.get('state'),1),float(row.get('stop_distance_pct') or 0))):
        cells=[position.get("account"),instrument_label(position),position.get("state"),percent(position.get("weight")),percent(position.get("stop_distance_pct")),money(position.get("stop_exposure_base"),p.get("base_currency")),money(position.get('stop_pnl_before_costs_base'),p.get('base_currency'))]
        body.append("| "+" | ".join(safe(v) for v in cells)+" |")
    policy=p.get('policy_review',{})
    changes=p.get('changes',{})
    if changes.get('state')=='available':
        labels={'identity_unconfirmed':'종목 식별 미확정','current_data_stale':'현재 자료 오래됨',
            'previous_data_stale':'이전 자료 오래됨','new_observation':'새로 관측됨','older_snapshot':'자료 시각 역행',
            'same_observation':'동일 관측 반복','same_time_revision':'동일 시각 자료 수정',
            'current_stop_or_price_unavailable':'손절/가격 자료 미확정','stop_now_available':'손절 자료 확인됨',
            'stop_adoption_changed':'손절 채택/가격 변경','breach_persists':'손절선 이탈 관측 지속',
            'new_stop_breach':'새 손절선 이탈 관측','above_stop_again_execution_unknown':'손절선 위 재관측·체결 별도 확인',
            'closer_to_stop':'손절선에 가까워짐','farther_from_stop':'손절선에서 멀어짐','distance_unchanged':'거리 변화 없음',
            'not_observed_no_execution_inference':'현재 자료에서 미관측·매도 여부 미확정',
            'zero_quantity_observation_no_execution_inference':'보유 수량 0 관측·체결 이력 별도 확인',
            'positive_quantity_observed':'보유 수량 발생 관측·체결 이력 별도 확인','no_baseline':'비교 자료 없음'}
        body += ['', '### 보유 경보의 변화', '',
                 f"이전 보고일 {safe(changes['previous_report_date'])}: {safe(changes['previous_as_of'])} → 현재 자료 {safe(changes['current_as_of'])}.",
                 '', '| 계좌 | 종목 | 변화 | 이전 손절 거리 | 현재 손절 거리 | 이전 → 현재 수량 |', '|---|---|---|---:|---:|---|']
        for row in changes['rows']:
            body.append('| '+' | '.join(safe(v) for v in [row['account'],instrument_label(row),labels[row['change']],
                percent(row.get('previous_distance')),percent(row.get('current_distance')),
                f"{row.get('previous_quantity') if row.get('previous_quantity') is not None else '미확정'} → {row.get('current_quantity') if row.get('current_quantity') is not None else '미확정'}"])+' |')
        body += ['', '손절 채택이 바뀌거나 자료가 오래되면 거리 변화를 가격 경보로 해석하지 않습니다. 보유 미관측이나 가격 회복으로 체결·청산을 추정하지 않습니다.']
    if policy:
        reasons={
            'market_identity_required':'시장 식별자가 없어 계좌 간 동일 종목 합산을 확정할 수 없습니다',
            'stale_account_snapshot':'계좌 자료가 허용된 유효시간을 지났습니다',
            'incomplete_equity_or_fx':'잔고 또는 환율이 부족해 전체 평가액을 확정할 수 없습니다',
            'more_than_five_held_instruments':'기존 최대 5종목 기본 구조를 초과했습니다',
            'adopted_stop_or_price_basis_unavailable':'채택 손절선 또는 가격 기준을 확인해야 합니다',
            'adopted_stop_reached_execution_unknown':'채택 손절선에 도달했습니다. 실제 체결 여부는 별도 확인이 필요합니다',
            'plan_account_source_mismatch':'계획과 계좌의 실자료·합성자료 구분이 다릅니다',
            'account_not_in_snapshot':'계획의 계좌가 현재 잔고 자료에 없습니다',
            'ambiguous_position_identity':'계획과 연결할 보유 종목이 중복되어 대조가 필요합니다',
            'currency_mismatch':'계획과 잔고의 통화가 다릅니다',
            'recorded_inventory_differs_from_account':'계획 원장의 체결 수량과 계좌 보유 수량이 다릅니다',
            'current_adopted_stop_required':'현재 채택한 손절선이 필요합니다',
            'current_stop_differs_from_original_plan_recalculation_required':'현재 손절 채택과 원 계획이 달라 위험 재계산이 필요합니다',
            'recorded_holding_missing_from_account':'계획 원장에 남은 보유가 계좌 자료에 없습니다',
            'broker_orders_and_fills_not_reconciled':'증권사 미체결·체결 내역 대조가 연결되지 않았습니다',
            'GDD_adoption_not_connected':'신규·추가 계획의 사용자 판단 기록이 연결되지 않았습니다'}
        body += ['', '### 계좌 합산 비중·계획 대조', '',
                 f"보유 종목 수: {safe(policy['held_instrument_count'] if policy['held_instrument_count'] is not None else '미확정')} / 기존 기본 구조 최대 5종목. 종목별 20%는 기본 비중이며 자동 매도 기준이나 하드캡이 아닙니다.",
                 '', '| 시장 | 종목 | 계좌 합산 비중 | 기본 비중 초과 |', '|---|---|---:|---|']
        for row in policy['instruments']:
            body.append('| '+' | '.join(safe(v) for v in [row['market'],instrument_label(row),percent(row['weight']),'기본20% 초과(정보)' if row['above_basic_weight'] else '없음' if row['weight'] is not None else '미확정'])+' |')
        for issue in policy['issues']:
            body += ['',f"확인 필요: {safe(issue.get('account',''))} {safe(display_label(instrument_label(issue)))} — {safe(reasons.get(issue['reason'],issue['reason']))}"]
        for check in policy['plan_checks']:
            body += ['',f"계획 {safe(check['plan_id'])}: 공식 추가안 미확정 — {safe(' / '.join(reasons.get(reason,reason) for reason in check['reasons']))}"]
        body += ['', '위 표는 관찰·대조 결과입니다. 현재 채택 손절을 자동 이동하지 않으며 목표가/RR 미입력은 보고서 작성을 막지 않습니다.']
    scenarios=p.get('scenarios',{})
    reconciliation=p.get('reconciliation',{})
    if reconciliation.get('state') not in (None,'not_configured'):
        body += ['', '### 미체결·로컬 예약 대조', '', '입력된 주문·잔고 자료의 대조 결과입니다. 증권사에 새로 접속하거나 주문하지 않습니다.']
        capacity=reconciliation.get('capacity')
        if capacity:
            body += ['',f"대조 기준: {safe(capacity['as_of'])}, 출처: {safe(capacity['source_ref'])}. 증권사 미체결 {capacity['coverage']['broker_orders']}건 / 연결된 로컬 예약 {capacity['coverage']['linked_reservations']}건 / 별도 로컬 예약 {capacity['coverage']['local_only_reservations']}건."]
            body += ['',f"예약 손절 노출: {safe(capacity['reserved_loss_base'] if capacity['reserved_loss_base'] is not None else '미확정')} {safe(p.get('base_currency'))}."]
            if capacity['purchase_blockers']:
                body += ['', '신규·추가매수 여력은 미확정입니다. 예약 위험 입력 또는 계획 원장 경보를 확인해야 합니다.']
        else:
            body += ['',f"대조 미확정: {safe(reconciliation.get('reason'))}. 기존 잔고 표는 유지하며 이 입력으로 새 수량을 확정하지 않습니다."]
    if scenarios.get('state') not in (None,'not_configured'):
        body += ['', '### 추가매수·축소 시나리오', '',
                 '각 행은 같은 계좌 자료를 기준으로 한 독립 대안입니다. 여러 행의 수량을 합쳐 실행하는 계획이 아닙니다. 매수 수량은 입력 예산 내 상한 후보입니다.', '',
                 '| 대안 | 계좌 | 종목 | 방향 | 상태 | 수량 | 분할 수량 | 추가 후 종목 비중 | 추가 후 손절 노출(기준통화) |',
                 '|---|---|---|---|---|---:|---|---:|---:|']
        for row in scenarios.get('rows',[]):
            body.append('| '+' | '.join(safe(v) for v in [row.get('scenario_id'),row.get('account_alias'),instrument_label(row),
                '매수 검토' if row.get('side')=='buy' else '축소 검토',
                {'preview':'계산된 대안','blocked':'조건 미충족','needs_input':'입력 필요'}.get(row['state'],row['state']),
                row.get('quantity','미확정'),row.get('split_quantities','미확정'),percent(row.get('post_instrument_weight')),
                row.get('post_portfolio_stop_exposure_base','해당 없음')])+' |')
            if row.get('reason') or row.get('reasons'):
                body += ['',f"대안 {safe(row.get('scenario_id'))} 확인 항목: {safe(row.get('reason') or row.get('reasons'))}"]
            if row.get('estimated_realized_pnl') is not None:
                body += ['',f"대안 {safe(row.get('scenario_id'))} 예상 실현손익: {money(row['estimated_realized_pnl'],row['currency'])} {safe(row['currency'])} (입력 비용 가정 반영)"]
            if row.get('post_add_average_cost') is not None:
                body += ['',f"대안 {safe(row.get('scenario_id'))}: 필요 현금 {money(row['cash_required'],row['currency'])} {safe(row['currency'])}, 추가 후 평균단가 {money(row['post_add_average_cost'],row['currency'])} {safe(row['currency'])}. 예약분까지 포함한 손절 노출 {money(row['committed_portfolio_stop_exposure_base'],row['base_currency'])} {safe(row['base_currency'])}."]
            if row.get('basic_weight_review_required'):
                body += ['', '추가 후 20% 기본 비중을 초과하므로 재배분 조건을 별도로 검토해야 합니다.']
        if scenarios.get('reason'):
            body += ['',f"시나리오 입력 확인 필요: {safe(scenarios['reason'])}"]
        body += ['', '미체결·로컬 예약을 반영한 잔여 여력 입력을 사용합니다. 자동 주문·계획 채택은 하지 않으며 사전 분할계획의 손실 증액 예외는 아직 적용하지 않습니다.']
    body += ["", "손절 이탈·손절 미입력·오래된 잔고를 먼저 확인합니다. 가격 이탈 후 실제 체결 가격은 별도이며 이 리포트는 주문하지 않습니다.", "", "## 새로 볼 후보", ""]
    s=market.get("screen",{})
    coverage=s.get("coverage",{})
    sessions=", ".join(f"{k}: {v}" for k,v in s.get("session_dates",{}).items()) or "미확정"
    listing_limits=", ".join(f"{k} {v['requested']}개" for k,v in (coverage.get("listings") or {}).items())
    coverage_text=f"{listing_limits or '지정 종목군'} / 조회 요청 {coverage.get('requested','미확정')}개, 수신 {coverage.get('received','미확정')}개"
    if not coverage.get("market_wide"):
        coverage_text+=" (전 종목 탐색 아님)"
    body += [f"- 시세 수집 기준: {safe(s.get('fetched_at','미확정'))}",f"- 완료 세션: {safe(sessions)}",f"- 탐색 범위: {safe(coverage_text)}",
             "- 거래대금은 종가×거래량 대용값입니다. 관측 가격은 채택 손절 가격이 아닙니다.", "",
             "## 스페란데오 후보 → 테이버 진입 위치 검토", ""]
    from .structure_report import candidate_sections
    candidates=[r for r in s.get("rows",[]) if r["state"]=="candidate"]
    body += candidate_sections(candidates, s.get('universe_audit', {}))
    if not candidates:
        body += ["", "현재 저장 결과에 후보가 없습니다. 시장 자료 미확정과 실제 후보 0건을 상태 표에서 구분하세요."]
    comparison=market.get('comparison',{})
    if comparison.get('state')=='available':
        names={'new':'새 후보','released':'조건 해제','unchanged':'변화 없음','unknown':'비교 미확정','revised':'같은 완료봉 재계산'}
        body += ['', '### 직전 아침과 비교', '',
                 f"비교 보고일: {safe(comparison['report_date'])}, 실행: {safe(comparison['run_id'])}. 휴장일에는 같은 완료봉의 관측을 반복할 수 있습니다.",
                 '', '| 시장 | 종목 | 변화 |', '|---|---|---|']
        for row in s.get('rows',[]):
            if row['state']=='candidate' or row.get('change') in ('released','revised') or row.get('previous_candidate'):
                body.append('| '+' | '.join(safe(v) for v in [row['market'],instrument_label(row),names.get(row.get('change'),'비교 미확정')])+' |')
        body += ['', '조건 해제는 검토용 필터의 변화이며 매도 지시가 아닙니다. 자료 누락은 비교 미확정으로 남깁니다.']
    else:
        body += ['', '비교 가능한 직전 아침 관측이 없어 후보의 신규 여부는 미확정입니다.']
    for issue in coverage.get("scope_errors",[]):
        body += ["",f"탐색 범위 누락: {safe(issue['scope'])} / {safe(issue['stage'])} / {safe(issue['reason'])}. 후보 0건으로 해석하지 않습니다."]
    body += ["",f"분석 불가/부적격: {s.get('unavailable_count','미확정')}개. 탈락 사유 전수는 market 단계 JSON에 저장했습니다.",
             f"보유 차트 미연결: {safe([display_label(value) for value in market.get('held_chart_missing',[])])}", "", "## 두 참고 가치 밴드", "",
             "| 종목 | 입력 출처 | 기준일 | 통화 | 상태 | EPS×PER 주가 밴드 | 순이익×PER 시총 밴드 |", "|---|---|---|---|---|---|---|"]
    for row in valuation.get("rows",[]):
        body.append("| "+" | ".join(safe(v) for v in
                  (instrument_label(row),
                   row.get("input_source","미연결"), row.get("as_of","미연결"), row.get("currency","미연결"),
                   row.get("state","미연결"), money_band(row.get("price_band","미연결"),row.get("currency")), money_band(row.get("market_cap_band","미연결"),row.get("currency"))))+" |")
    for row in valuation.get('rows', []):
        if row.get('schema_version') == 2:
            def band_text(band, divisor=1):
                return '미확정' if not band else ' / '.join(money(float(band[k])/divisor,row["currency"]) for k in ('low','median','high'))
            cap_unit = '원' if row['currency'] == 'KRW' else '달러'
            divisor = 1
            body += ['', f"### {safe(instrument_label(row))} 연도별 참고 밴드", '',
                     '| 전망 기간 | 예상 EPS | 예상 순이익 | 주가 하단 / 중앙 / 상단 | 시총 하단 / 중앙 / 상단 |', '|---|---:|---:|---:|---:|']
            for period in row.get('period_bands', []):
                label = period.get('fiscal_year') or '다음 회계연도(정확한 연도 미확인)'
                eps = '미확정' if period.get('eps') is None else f"{money(period['eps'],row['currency'])} {safe(row['currency'])}"
                income = '미확정' if period.get('net_income') is None else f"{money(float(period['net_income'])/divisor,row['currency'])} {cap_unit}"
                body.append(f"| {safe(label)} | {eps} | {income} | {band_text(period['price_band'])} {safe(row['currency'])} | {band_text(period['market_cap_band'], divisor)} {cap_unit} |")
            observations = ', '.join(f"{p['fiscal_year']}: {p.get('per')}배" for p in row['annual_history'])
            body += ['', f"과거 연간 PER: {safe(observations)}. 수집 상태: {safe(row.get('retrieval_state'))}."]
            for kind, evidence in row.get('provenance', {}).items():
                body += [f"- {safe(kind)} 조회 시각: {safe(evidence.get('fetched_at'))}; " + ', '.join(safe(url) for url in evidence.get('urls', []))]
    body += ["", "주가 = 연간 예상 EPS × 과거 PER 최소·중앙·최대. 시총 = 연간 예상 순이익 × 같은 PER 밴드(영업이익은 사용하지 않음). 양수인 과거 연간 PER 최소 2개를 사용하며, 내년 전망을 우선하고 없으면 올해를 대표로 표시합니다. 미국 next_fiscal_year의 정확한 연도는 미확인입니다. EPS와 순이익은 독립 계산하며 없는 값은 미확정입니다. DCF·자동 익절 목표는 계산하지 않습니다.", ""]
    plans=snapshot.get("plan_records",[])
    if plans:
        body += ["## 저장된 분할 계획", "", "로컬 계획 기록입니다. 증권사 잔고·미체결 자동 대조나 주문 실행을 뜻하지 않습니다.", "",
                 "| 계획 | 입력 출처 | 종목 | 회차 | 방향 | 예정 수량 | 체결 수량 | 예약 수량 | 미배정 수량 |", "|---|---|---|---|---|---:|---:|---:|---:|"]
        for plan in plans:
            for tranche in plan["tranches"]:
                cells=[plan["plan_id"],plan["source"],plan["symbol"],tranche["tranche_id"],tranche["side"],tranche["quantity"],
                       tranche["filled_quantity"],tranche["reserved_quantity"],tranche["uncommitted_quantity"]]
                body.append("| "+" | ".join(safe(v) for v in cells)+" |")
            if plan["alerts"]:
                body += ["",f"계획 {safe(plan['plan_id'])} 대조 필요: {safe(', '.join(plan['alerts']))}",""]
        body += ["", "계획의 현금·손실 예산은 비용/슬리피지 가정을 포함한 누적 사용 한도입니다. 현재 계좌 손실이나 실제 현금 잔고와 구분합니다.",""]
    from .risk_reporting import render as render_risk_policy
    body += render_risk_policy(results.get("portfolio", {}).get("risk_policy", snapshot.get("risk_policy", {})))
    refs=[]
    chart_state={"state":"not_available" if not market.get("snapshot") else "available"}
    if market.get("snapshot"):
        try:
            from .charts import generate
            refs=generate(market["snapshot"],root,folder)
        except Exception as exc:  # noqa: BLE001 -- Boundary records failures and isolates optional provider/component errors.
            chart_state={"state":"unavailable","error_type":type(exc).__name__}
            body += [f"차트 생성 미완료: {type(exc).__name__}. 시장 표·보유 위험·가치 결과는 유지했습니다.",""]
    for ref in refs:
        filename=Path(ref["path"]).name
        caption=safe(f"{display_label(ref['symbol'])} {ref['timeframe']} {ref['as_of']}")
        body += [f"![{caption}]({filename})",""]
    markdown="\n".join(body)
    from .report_html import render_html
    webpage = render_html(markdown)
    return {"report.md":markdown,"report.html":webpage},refs,chart_state

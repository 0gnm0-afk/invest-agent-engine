"""Trade-cycle HTTP/MCP adapter; saved evidence and browser-only confirmation."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

from analysis_collaboration import Forbidden, fields, identity
from analysis_service import digest
from chart_workspace import Conflict
from trailing_warnings import equity

OPS = {'cycle_list', 'cycle_read', 'cycle_context', 'cycle_request_draft',
       'cycle_agent_input', 'cycle_calculate', 'cycle_save_draft', 'cycle_approve', 'cycle_fill'}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
        return str(n) if n.is_finite() and n >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def context(service, stock_key, account_alias=None):
    data, stocks = service.view()
    stock = {**service.histories.saved.stocks, **stocks}.get(stock_key)
    if stock is None:
        raise ValueError('unknown_stock')
    currency = stock.get('currency') or {'KR': 'KRW', 'US': 'USD'}.get(stock_key.split(':')[0])
    bundle = data['accounts']
    accounts = []
    for a in bundle.get('accounts', []):
        cash = a.get('cash', {})
        deposits = a.get('observed_deposits', {})
        value = cash.get(currency) if currency in cash else (
            deposits.get('KR', {}).get('dca') if currency == 'KRW' else deposits.get('US', {}).get('fc_dca'))
        positions = [p for p in a.get('positions', []) if equity(p) and p.get('market') == stock.get('market')
                     and (p.get('quote_symbol') or p.get('symbol')) == stock.get('symbol')]
        stable = stock.get('isin_code') or stock.get('isin') or stock.get('stable_id')
        invalid = any(p.get('data_unavailable_reason') or (p.get('currency') and p['currency'] != currency)
                      or (stable and (p.get('isin_code') or p.get('isin'))
                          and (p.get('isin_code') or p.get('isin')) != stable) for p in positions)
        holding = ({'quantity': 0, 'average_price': '0'} if a.get('holdings_complete')
                   else {'quantity': None, 'average_price': None})
        if invalid:
            holding = {'quantity': None, 'average_price': None}
        elif positions:
            quantities = [number(p.get('quantity')) for p in positions]
            costs = [number(p.get('average_cost', p.get('average_price', p.get('avg_price')))) for p in positions]
            total = sum(Decimal(q) for q in quantities) if all(q is not None for q in quantities) else None
            if total is not None and total == total.to_integral_value():
                holding = {'quantity': int(total), 'average_price': (
                    str(sum(Decimal(q)*Decimal(c) for q,c in zip(quantities,costs))/total)
                    if total and all(c is not None for c in costs) else '0' if not total else None)}
            else:
                holding = {'quantity': None, 'average_price': None}
        accounts.append({'alias': a.get('alias'), 'cash': number(value), 'holding': holding,
                         'cash_complete': a.get('cash_complete'), 'holdings_complete': a.get('holdings_complete')})
    if account_alias is not None and not any(a['alias'] == account_alias for a in accounts):
        raise ValueError('unknown_account')
    selected = next((a for a in accounts if a['alias'] == account_alias), None) if account_alias else next(
        (a for a in accounts if a['cash'] is not None), accounts[0] if accounts else None)
    cases = service.rows('analysis_cases')
    case = next((c for c in reversed(cases) if c['stock_key'] == stock_key), None)
    chart = service.histories.view(stock, '1d')
    return {'stock_key': stock_key, 'name': stock.get('name', stock_key), 'currency': currency,
            'accounts': accounts, 'account_alias': selected['alias'] if selected else None,
            'budget': selected['cash'] if selected else None,
            'initial_holding': deepcopy(selected['holding']) if selected else {'quantity': 0, 'average_price': '0'},
            'chart': chart, 'analysis_key': case['id'] if case else None,
            'as_of': bundle.get('as_of'), 'stale': bundle.get('stale'),
            'query_state': bundle.get('query_state'), 'cash_basis': 'saved_cash_not_orderable_guarantee'}


def empty_plan(ctx, budget):
    step = {'confirmation_price': None, 'assumed_fill_price': None, 'condition': 'Agent 초안 생성 대기',
            'trigger': {'kind': 'manual', 'evaluation': 'daily_close'}}
    return {'title': ctx['name']+' 매매 시나리오', 'currency': ctx['currency'], 'budget': budget,
            'account_alias': ctx['account_alias'], 'initial_holding': ctx['initial_holding'],
            'buy_steps': [deepcopy(step) for _ in range(3)], 'sell_steps': [deepcopy(step) for _ in range(3)],
            'stop': {**deepcopy(step), 'trigger': {'kind': 'manual', 'evaluation': 'daily_close'}},
            'scenarios': {k: {'label': label, 'events': []} for k,label in (
                ('bullish','상승'), ('neutral','횡보'), ('bearish','하락'))}}


def source_for(service, ctx, origin):
    cid = ctx['analysis_key']
    if cid is None:
        cid = service.dispatch('create', {'stock_key': ctx['stock_key'], 'context': {'timeframe': '1d'}},
                               origin, principal='collaboration')['case']['id']
    case = service.case(cid)
    snap = service.capture(case)
    return {'analysis_key': cid, 'snapshot_id': snap['id'], 'snapshot_sha256': digest(snap),
            'identity': identity(snap), 'as_of': ctx['as_of'],
            'test_record': case.get('test_record', False)}, snap


def packet(service, cycle):
    source = cycle['draft']['source']
    snap = service.snapshot(source['analysis_key'], source['snapshot_id'])
    if digest(snap) != source['snapshot_sha256']:
        raise ValueError('cycle_source_integrity_failed')
    same_stock = [c for c in service.rows('analysis_cases') if c['stock_key'] == cycle['stock_key']]
    records = []
    for case in same_stock:
        records.extend(service.rows('analysis_items', case['id']))
    # Source IDs and explicit truncation remain visible. No unrelated account data is sent.
    recent = records[-30:]
    plan = cycle['draft']['plan']
    chart = snap['chart']
    return {'cycle_id': cycle['cycle_id'], 'expected_revision': cycle['revision'], 'source': source,
            'plan': plan, 'actual_fills': cycle.get('fills', []),
            'chart': {**{k:v for k,v in chart.items() if k!='bars'}, 'bars': chart.get('bars', [])[-260:],
                      'earlier_bars_omitted': max(0,len(chart.get('bars', []))-260)},
            'workspace': snap['workspace'], 'geometry': snap['geometry'],
            'price_events': snap.get('price_events'), 'financials': snap['financials'],
            'records': recent, 'earlier_records_omitted': max(0,len(records)-30),
            'collaboration': service.collaboration.read(service.case(source['analysis_key']), ''),
            'contract': 'docs/trade-cycle-contract-v1.md'}


def links(service, cycle, origin):
    url = origin+'/#cycle?id='+quote(cycle['cycle_id'])
    prompt = (f"투자 워크벤치 매매 사이클 {cycle['cycle_id']}의 Agent 시나리오 초안을 생성해줘. "
              "analysis_cycle_input으로 저장 차트·기술/정성 근거·이전 논의·있으면 사용자 작도를 읽고 "
              "docs/trade-cycle-contract-v1.md 계약에 맞춰 상승·횡보·하락 경로를 제안해줘. "
              "매수·매도는 각각 최대3회 20/30/50의6순열이며 1.5%/6% 제한은 쓰지 않아. "
              "기존 예산·보유 입력은 바꾸지 말고 사실/가정/미정을 구분해. "
              "analysis_cycle_save_draft로 초안만 저장하고 재조회해. 채택·체결·새 수집·발송은 하지 마. "
              f"돌아갈 화면: {url}")
    return {'dashboard_url': url, 'prompt': prompt,
            'codex_url': 'codex://new?'+urlencode({'path': str(service.root), 'prompt': prompt})}


def dispatch(service, op, args, origin, principal, session):
    from trade_cycle import TradeCycleStore, calculate
    from trade_cycle_agent import manager_for
    store = TradeCycleStore(service.store.path)
    if principal == 'risk' and op not in ('cycle_list','cycle_read'):
        raise Forbidden('risk_cannot_write_trade_cycles')
    if op in ('cycle_request_draft','cycle_approve','cycle_fill') and (principal != 'browser' or not session):
        raise Forbidden('local_browser_confirmation_required')
    if op == 'cycle_save_draft' and principal not in ('browser','collaboration'):
        raise Forbidden('collaboration_or_browser_required')
    manager = manager_for(service)
    if op == 'cycle_context':
        fields(args, ('stock_key','account_alias'), ('stock_key',))
        return context(service,args['stock_key'],args.get('account_alias'))
    if op == 'cycle_list':
        fields(args, ('stock_key',))
        return store.list(args.get('stock_key'))
    if op == 'cycle_read':
        fields(args, ('cycle_id',), ('cycle_id',))
        cycle = store.read(args['cycle_id'])
        return {'cycle': cycle, 'job': manager.status(cycle['cycle_id']), 'links': links(service,cycle,origin)}
    if op == 'cycle_agent_input':
        fields(args, ('cycle_id',), ('cycle_id',))
        return packet(service,store.read(args['cycle_id']))
    if op == 'cycle_calculate':
        fields(args, ('plan','fills','cycle_id','adopted_combination_id'), ('plan',))
        if args.get('cycle_id'):
            cycle = store.read(args['cycle_id'])
            return calculate(args['plan'],cycle['fills'],(cycle['adopted'] or {}).get('combination_id'))
        return calculate(args['plan'],args.get('fills',[]),args.get('adopted_combination_id'))
    if op == 'cycle_request_draft':
        fields(args, ('stock_key','account_alias','budget','cycle_id','expected_revision','reentry_from'), ('stock_key',))
        ctx = context(service,args['stock_key'],args.get('account_alias'))
        budget = number(args['budget']) if 'budget' in args else ctx['budget']
        if 'budget' in args and budget is None:
            raise ValueError('invalid_budget')
        if args.get('cycle_id'):
            cycle = store.read(args['cycle_id'])
            if cycle['stock_key'] != args['stock_key']:
                raise ValueError('cycle_stock_mismatch')
            if manager.status(cycle['cycle_id']).get('state') in ('queued','running'):
                return {'cycle':cycle,'job':manager.status(cycle['cycle_id']),'links':links(service,cycle,origin)}
            if cycle['revision'] != args.get('expected_revision'):
                raise Conflict('cycle_revision_changed')
            # Generating a revision must preserve actual accounting and the user's edited draft.
            plan = deepcopy(cycle['draft']['plan'])
            plan['budget'] = budget if 'budget' in args else plan['budget']
            source, _ = source_for(service,ctx,origin)
            cycle = store.save_draft(cycle['stock_key'],plan,cycle_id=cycle['cycle_id'],
                                     expected_revision=cycle['revision'],source=source)
        else:
            source, _ = source_for(service,ctx,origin)
            if args.get('reentry_from'):
                old = store.read(args['reentry_from'])
                if old['stock_key'] != ctx['stock_key']:
                    raise ValueError('cycle_stock_mismatch')
                source['reentry_from'] = old['cycle_id']
                ctx['initial_holding'] = {'quantity':0,'average_price':'0'}
            cycle = store.save_draft(ctx['stock_key'],empty_plan(ctx,budget),source=source)
        job = manager.start(cycle,packet(service,cycle))
        return {'cycle':cycle,'job':job,'links':links(service,cycle,origin)}
    if op == 'cycle_save_draft':
        fields(args, ('cycle_id','expected_revision','plan'), ('cycle_id','expected_revision','plan'))
        cycle = store.read(args['cycle_id'])
        plan = deepcopy(args['plan'])
        for name in ('currency','account_alias'):
            if plan.get(name) != cycle['draft']['plan'].get(name):
                raise ValueError('cycle_identity_immutable')
        if principal == 'collaboration':
            for name in ('budget','initial_holding'):
                if plan.get(name) != cycle['draft']['plan'].get(name):
                    raise ValueError('agent_cannot_change_account_inputs')
        return {'cycle':store.save_draft(cycle['stock_key'],plan,cycle_id=cycle['cycle_id'],
                                        expected_revision=args['expected_revision'],source=cycle['draft']['source'])}
    if op == 'cycle_approve':
        fields(args, ('cycle_id','draft_version','combination_id','expected_revision'),
               ('cycle_id','draft_version','combination_id','expected_revision'))
        cycle = store.read(args['cycle_id'])
        packet(service,cycle)  # Verify immutable evidence before accepting its exact version.
        return {'cycle':store.adopt(args['cycle_id'],args['draft_version'],args['combination_id'],
                                   args['expected_revision'],principal='browser')}
    if op == 'cycle_fill':
        fields(args, ('cycle_id','expected_revision','fill'), ('cycle_id','expected_revision','fill'))
        return {'cycle':store.record_fill(args['cycle_id'],args['fill'],args['expected_revision'],principal='browser')}
    raise ValueError('unknown_cycle_operation')

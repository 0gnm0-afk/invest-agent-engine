"""Browser-confirmed scenario watches over immutable adopted analysis plans."""
from contextlib import closing

from analysis_collaboration import Forbidden, fields, integer
from chart_workspace import Conflict

OPS = {'monitor_list', 'monitor_register', 'monitor_update', 'monitor_conversion'}


def dispatch(service, op, args, origin, principal, session):
    from scenario_monitor import ScenarioStore
    store = ScenarioStore(service.store.path)
    if op == 'monitor_list':
        fields(args, ('stock_key',))
        key = args.get('stock_key')
        if key is not None and (not isinstance(key, str) or len(key) > 100):
            raise ValueError('invalid_stock_key')
        from datetime import datetime, timezone
        from daily_launch import plan_for
        checked_at = datetime.now(timezone.utc)
        value = store.read(key, now=checked_at, targets=plan_for(checked_at)['targets'])
        for monitor in value['monitors']:
            monitor['detail_url'] = service.links(service.case(monitor['case_id']), origin)['dashboard_url']
        return value
    if principal != 'browser' or not session:
        raise Forbidden('local_browser_confirmation_required')
    if op == 'monitor_register':
        names = ('analysis_key', 'plan_record_id', 'drawing_id', 'workspace_revision', 'purpose', 'boundary', 'tolerance_atr')
        fields(args, names, names)
        integer(args['workspace_revision'])
        case = service.case(args['analysis_key'])
        plan = service.collaboration.plan_record(case['id'], args['plan_record_id'])
        with closing(service.store.connect()) as db:
            active = service.collaboration.active(service.collaboration.read_rows(case['id'], db))
        adoption = active.get(plan['plan_id'])
        if not adoption or adoption['plan_record_id'] != plan['id']:
            raise ValueError('plan_version_not_adopted')
        service.collaboration.verify(adoption)
        snap = service.snapshot(case['id'], plan['snapshot_id'])
        if plan['snapshot_sha256'] != service_digest(snap):
            raise ValueError('plan_snapshot_integrity_failed')
        references = [c for c in plan['plan']['conditions']
                      if (c.get('drawing_ref') or {}).get('drawing_id') == args['drawing_id']]
        if not references or any(c['evaluation'] != 'daily_close' for c in references):
            raise ValueError('daily_close_drawing_plan_required')
        if snap['workspace']['revision'] != args['workspace_revision']:
            raise Conflict('confirmed_drawing_revision_changed')
        # Only recorded and user-adopted response text enters the deterministic alert.
        responses = list(dict.fromkeys(c['statement'] for c in references if c.get('statement')))
        drawing = next(d for d in snap['workspace']['state']['drawings'] if d['id'] == args['drawing_id'])
        mapping = {'low':'lower', 'high':'upper', 'entry':'entry', 'stop':'stop', 'target':'target', 'line':'line'}
        if drawing['kind'] == 'channel':
            from analysis_geometry import geometry
            shape = next(d for d in geometry(snap['workspace'], snap['chart'], snap['context'])['drawings'] if d['id']==drawing['id'])
            offset = shape['calculations'].get('channel_offset')
            if offset is None: raise ValueError('drawing_projection_unavailable')
            mapping.update(line='lower' if offset >= 0 else 'upper', other_line='upper' if offset >= 0 else 'lower')
        by_boundary = {}
        for condition in references:
            name = mapping[condition['drawing_ref']['line']]
            statement = condition.get('statement')
            if statement:
                by_boundary.setdefault(name, [])
                if statement not in by_boundary[name]: by_boundary[name].append(statement)
        by_boundary = {k:'\n'.join(v) for k,v in by_boundary.items()}
        labels = {'lower':'하단', 'upper':'상단', 'entry':'진입', 'stop':'무효화', 'target':'목표', 'line':'기준선'}
        by_boundary['zone'] = responses[0] if len(responses)==1 else '\n'.join(labels[k]+': '+v for k,v in by_boundary.items())
        result = store.register(stock_key=case['stock_key'], identity=plan['identity'], case_id=case['id'],
            plan_record_id=plan['id'], plan_id=plan['plan_id'], plan_version=plan['plan_version'],
            drawing_id=args['drawing_id'], workspace_revision=args['workspace_revision'],
            timeframe=snap['chart']['timeframe'], purpose=args['purpose'], boundary=args['boundary'],
            tolerance_atr=args['tolerance_atr'], confirmed_response='\n'.join(responses) or None,
            confirmed_responses=by_boundary, account_alias=plan['plan']['account_alias'],
            detail_url=service.links(case, origin)['dashboard_url'], chart=snap['chart'], test_record=case['test_record'])
        return result
    if op == 'monitor_update':
        fields(args, ('id', 'revision', 'tolerance_atr', 'active'), ('id', 'revision'))
        integer(args['revision'])
        return store.configure(args['id'], args['revision'], **{k:args[k] for k in ('tolerance_atr', 'active') if k in args})
    if op == 'monitor_conversion':
        fields(args, ('id', 'revision', 'accept'), ('id', 'revision', 'accept'))
        integer(args['revision'])
        if type(args['accept']) is not bool:
            raise ValueError('invalid_conversion_decision')
        if args['accept']:
            from datetime import datetime, timezone
            from scenario_monitor import account_status
            monitor = next((m for m in store.read()['monitors'] if m['id']==args['id']), None)
            if monitor is None: raise ValueError('monitor_not_found')
            data, _ = service.view()
            fresh, held = account_status(data['accounts'], monitor, datetime.now(timezone.utc))
            if not fresh or not held: raise ValueError('holding_confirmation_stale_or_missing')
        return store.resolve_conversion(args['id'], args['revision'], args['accept'])
    raise ValueError('unknown_monitor_operation')


def service_digest(value):
    from analysis_service import digest
    return digest(value)

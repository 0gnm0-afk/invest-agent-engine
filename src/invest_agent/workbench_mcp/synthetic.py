"""Small in-memory transport example; not a replacement workbench backend."""
from copy import deepcopy

KEY = 'synthetic-analysis'
SNAPSHOT = 'synthetic-snapshot'
RECORDS = []
INPUT = {'synthetic': True, 'analysis_key': KEY, 'snapshot_id': SNAPSHOT,
         'stock_key': 'SYNTHETIC:DEMO', 'as_of': '2026-10-01',
         'business_evidence': 'Fictional company; no investment conclusion.',
         'open_questions': ['Assumptions remain to be discussed.']}


def call(op, arguments):
    if op == 'list':
        cases = [deepcopy(INPUT)] if arguments.get('stock_key') in (None, INPUT['stock_key']) else []
        return {'synthetic': True, 'cases': cases}
    if arguments.get('analysis_key') != KEY:
        raise ValueError('synthetic_analysis_not_found')
    if op == 'read':
        if arguments.get('snapshot_id') not in (None, SNAPSHOT):
            raise ValueError('synthetic_snapshot_not_found')
        if arguments.get('section', 'summary') != 'summary':
            raise ValueError('synthetic_example_only_supports_summary')
        return deepcopy(INPUT)
    if op == 'detail':
        return {'synthetic': True, 'analysis_key': KEY, 'records': deepcopy(RECORDS)}
    if op == 'record':
        if arguments.get('snapshot_id') != SNAPSHOT:
            raise ValueError('synthetic_snapshot_not_found')
        if arguments.get('kind') not in ('report', 'review'):
            raise ValueError('invalid_record_kind')
        record = deepcopy(arguments)
        record['record_id'] = 'synthetic-record-'+str(len(RECORDS)+1)
        RECORDS.append(record)
        return {'synthetic': True, 'persisted': False, 'record_id': record['record_id'],
                'snapshot_id': SNAPSHOT, 'verified': True, 'storage': 'process_memory'}
    raise ValueError('synthetic_example_does_not_implement_this_operation')

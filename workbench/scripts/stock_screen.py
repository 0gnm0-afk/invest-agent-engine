"""Bounded local CPU workers around the unchanged public engine screen."""
from concurrent.futures import ProcessPoolExecutor
import socket
from unittest.mock import patch
from invest_agent.trading.market import screen
from invest_agent.trading.sperandeo import sort_key
from invest_agent.trading.universe_gate import audit


def _batch(snapshot):
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('screen_network_forbidden')):
        return screen(snapshot)['rows']


def screen_market(snapshot, *, workers=4, chunk_size=128):
    if workers < 2 or len(snapshot['series']) <= chunk_size or snapshot.get('previous_screen'):
        return screen(snapshot)
    # Per-stock calculations are independent. Keep engine behavior for history runs.
    metadata = {k:v for k,v in snapshot.items() if k not in ('series','universe','errors','risk_series','risk_errors')}
    universe = {(r['market'],r['symbol']):r for r in snapshot['universe']}
    batches = []
    for start in range(0,len(snapshot['series']),chunk_size):
        series = snapshot['series'][start:start+chunk_size]
        batches.append({**metadata,'series':series,'errors':[],
                        'universe':[universe[(r['market'],r['symbol'])] for r in series]})
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for batch in pool.map(_batch,batches):
            rows.extend(batch)
            print(f'stock_screen: {len(rows)}/{len(snapshot["series"])} series',flush=True)
    # The engine sorts received series first, then appends collection errors.
    result = screen({**metadata,'series':[],'universe':snapshot['universe'],'errors':snapshot['errors']})
    rows.sort(key=sort_key)
    result['rows'] = rows + result['rows']
    result['candidate_count'] = sum(r['state']=='candidate' for r in result['rows'])
    result['unavailable_count'] = sum(r['state']=='unavailable' for r in result['rows'])
    result['universe_audit'] = audit(result['rows'])
    return result

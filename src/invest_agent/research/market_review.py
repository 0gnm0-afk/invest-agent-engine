"""Synthetic-only end-to-end research runner; no network or account configuration."""
import argparse
from collections import defaultdict
import hashlib
import html
import json
from pathlib import Path

from invest_agent.trading.market import screen, check_bars
from .candidate_context import enrich
from .priority_review import build as prioritize, charts, settings
from .sector_metrics import group_metrics
from .synthetic_market import fixture


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def analyze(snapshot, groups, limit=2):
    """Synthetic basis is mandatory; invalid stocks remain visible as unavailable."""
    if snapshot.get('source') != 'synthetic':
        raise ValueError('synthetic_source_required')
    settings({'limit_per_market':limit})
    series = {}
    for item in snapshot['series']:
        key = (item['market'], item['symbol'])
        if key in series:
            raise ValueError('duplicate_series_identity')
        if item.get('price_basis') != 'synthetic' or item.get('provider') != 'synthetic':
            raise ValueError('synthetic_basis_required')
        series[key] = item
    for market in ('KR', 'US'):
        sessions = snapshot['sessions'][market]
        if not sessions or sessions != sorted(set(sessions)):
            raise ValueError('invalid_sessions')
    grouped, seen = defaultdict(list), set()
    for group in groups:
        key = (group['market'], group['symbol'])
        if key in seen or key not in series:
            raise ValueError('duplicate_or_unknown_classification')
        if group.get('group_taxonomy') != 'SYNTHETIC':
            raise ValueError('synthetic_classification_required')
        seen.add(key)
        if group['group_state'] == 'matched':
            grouped[(group['market'], group['group_code'])].append(group)
    # Exclude whole invalid series from sector denominators; retain screen error rows.
    valid, quality = {}, []
    for key, item in series.items():
        try:
            if any(b.get('complete') is False for b in item['bars']):
                raise ValueError('incomplete_bar')
            check_bars(item['bars'], snapshot['sessions'][key[0]])
            valid[key] = item
        except (ValueError, KeyError, TypeError, IndexError):
            quality.append({'market':key[0], 'symbol':key[1], 'reason':'invalid_or_incomplete_series'})
    # The legacy screen permits a contiguous suffix. This new runner uses one strict
    # quality boundary for both stock and sector calculations, with no silent repair.
    checked = {**snapshot, 'series':list(valid.values()), 'errors':[
        *snapshot['errors'], *quality]}
    baseline = screen(checked)
    observations = []
    for (market, code), members in sorted(grouped.items()):
        benchmark = snapshot['benchmarks'].get('SYNTH_INDEX_'+market)
        if benchmark is not None and benchmark.get('price_basis') != 'synthetic':
            raise ValueError('synthetic_benchmark_required')
        observations.append({'market':market, 'group_code':code, 'group_name':members[0]['group_name'],
                             **group_metrics(members, valid, snapshot['sessions'][market], benchmark)})
    sectors = {'source':'synthetic', 'applied_at':snapshot['fetched_at'], 'observations':observations,
               'markets':{m:{'completed_session':v[-1]} for m,v in snapshot['sessions'].items()}}
    context = enrich(baseline, sectors, groups)
    review = prioritize(context, checked, {'limit_per_market':limit})
    return {'screen':baseline, 'sectors':sectors, 'context':context, 'priority':review,
            'quality':{'source':'synthetic', 'state':'partial' if quality else 'complete',
                       'excluded_series':quality, 'input_count':len(series), 'valid_count':len(valid)}}


def render(result, chart_result):
    esc = lambda value: html.escape(str(value), quote=True)
    refs = {(c['market'],c['symbol']):c for c in chart_result['rows']}
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic market review</title>',
             '<style>body{font-family:system-ui;max-width:1100px;margin:32px auto;padding:16px;line-height:1.6}',
             'table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #ddd}img{max-width:100%}</style>',
             '<h1>Synthetic market review</h1><p>Invented companies, prices, sectors and weekday sessions. ',
             'Research order only; no trading signals, backtest or live data.</p>',
             '<p>As of: '+esc(result['screen']['session_dates'])+' · quality: '+esc(result['quality']['state'])+'</p>',
             '<p><a href="screen.json">Original screen</a> · <a href="sectors.json">Sector calculations</a> · ',
             '<a href="priority.json">Every selection reason</a> · <a href="quality.json">Quality exclusions</a></p>',
             '<h2>Sector observations</h2><table><tr><th>Market / sector</th><th>RS63 / RS126</th>',
             '<th>Above SMA60</th><th>Volume activity median</th><th>Members used / excluded (63)</th></tr>']
    fmt = lambda v: 'unavailable' if v is None else f'{v:.2%}'
    for obs in result['sectors']['observations']:
        h = obs['horizons']
        parts.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [
            obs['market']+' / '+obs['group_name'], fmt(h['63']['relative_strength'])+' / '+fmt(h['126']['relative_strength']),
            fmt(obs['breadth_sma60']['fraction']), obs['activity']['median_ratio'],
            str(h['63']['used'])+' / '+str(h['63']['excluded'])])+'</tr>')
    parts.append('</table><p>Small synthetic cohorts; per-metric members and exclusions are in sectors.json.</p>')
    for market, group in result['priority']['markets'].items():
        parts.append('<h2>'+market+': '+str(group['selected_count'])+' priority / '+str(group['candidate_count'])+' candidates</h2>')
        for entry in group['entries']:
            label = esc(entry['symbol'])
            ref = refs.get((market,entry['symbol']))
            if ref and ref['state'] == 'available':
                label = '<a href="'+esc(ref['path'])+'">'+label+'</a>'
            parts.append('<p>'+str(entry['rank'])+'. '+label+' · '+esc(entry['selection_reason'])+'<br>'+esc('; '.join(entry['reasons']))+'</p>')
    parts.append('<p>Charts: '+str(chart_result['available'])+' / '+str(chart_result['requested'])+'. '+
                 'Charts use confirmed current-window anchors, not historical selection.</p></html>')
    return ''.join(parts)


def run(output, limit=2):
    output = Path(output).resolve()
    package = Path(__file__).resolve().parents[3]
    if output.is_relative_to(package) or output.exists():
        raise ValueError('choose_new_output_directory_outside_package')
    snapshot, groups = fixture()
    result = analyze(snapshot, groups, limit)
    output.mkdir(parents=True, exist_ok=False)
    write(output/'synthetic-input.json', {'snapshot':snapshot, 'groups':groups})
    for name, value in result.items():
        write(output/(name+'.json'), value)
    chart_result = charts(result['priority'],result['context'],snapshot,output)
    write(output/'charts.json',chart_result)
    (output/'report.html').write_text(render(result,chart_result),encoding='utf-8')
    write(output/'artifact-hashes.json', {p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in sorted(output.rglob('*')) if p.is_file()})
    return {'synthetic':True, 'state':'failed' if chart_result['failed'] else result['quality']['state'],
            'session_dates':result['screen']['session_dates'], 'candidates':result['screen']['candidate_count'],
            'charts':{k:chart_result[k] for k in ('requested','available','failed')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--limit-per-market',type=int,default=2)
    args = parser.parse_args()
    try:
        result = run(args.output,args.limit_per_market)
    except (ValueError, OSError):
        parser.exit(2,'Could not run: use a new directory outside the package and a nonnegative limit.\n')
    print(json.dumps(result,indent=2))
    return 1 if result['state']=='failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())

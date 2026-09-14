"""One completed-bar fact builder shared by candidate and holding reviews."""
from statistics import mean

from .structure_math import sma, atr, ratio


def build(series, snapshot, *, structure=None, charts=()):
    from .market import usable_tail
    from .charts import completed_week_bars
    from .sperandeo import analyze
    from .taver import analyze as taver
    identity = {k:series.get(k) for k in ('symbol','market','currency','price_basis','provider','collected_at')}
    result = {**identity, 'version':'holding_candidate_facts_v1', 'state':'unavailable',
              'as_of':None, 'daily_bars':[], 'weekly_bars':[], 'moving_averages':[],
              'volume':None, 'relative_strength':None, 'range_52w':None,
              'sperandeo':None, 'taver':None, 'atr':None, 'charts':list(charts),
              'missing':[], 'calculation_source':'Python:technical_context/structure_math/sperandeo/taver',
              'authority':'observations_only_no_orders'}
    try:
        sessions = snapshot['sessions'][series['market']]
        # Never consume explicitly incomplete bars, even if supplied in sessions.
        complete_bars = [b for b in series['bars'] if b.get('complete') is not False]
        incomplete_dates = {b['date'] for b in series['bars'] if b.get('complete') is False}
        while sessions and sessions[-1] in incomplete_dates:
            sessions = sessions[:-1]
        bars = usable_tail(complete_bars, sessions)
        weekly = completed_week_bars(bars, bars[-1]['date'])
    except (KeyError, ValueError, IndexError) as exc:
        result['missing'] = [str(exc)]
        return result
    structure = structure or analyze(bars)
    location = taver(bars, series.get('market'), structure)
    averages = []
    for timeframe, source, periods in [('daily',bars,sorted(set([5,10,15,20]+location.get('ma_periods',[])))), ('weekly',weekly,[10,30])]:
        for period in periods:
            values = sma(source,period)
            value = values[-1] if values else None
            prior = values[-2] if len(values)>1 else None
            averages.append({'line_type':'SMA','timeframe':timeframe,'period':period,
                'value':value, 'prior_value':prior, 'slope_one_bar':None if prior is None else value-prior,
                'slope_pct_one_bar':ratio(value-prior,prior) if prior is not None else None,
                'as_of':source[-1]['date'] if source else None,
                'state':'available' if value is not None else 'unavailable_insufficient_bars'})
    window = snapshot.get('profile',{}).get(series.get('market'),{}).get('rs_period')
    benchmarks = snapshot.get('benchmarks',{})
    benchmark = benchmarks.get(series.get('benchmark'),{}) if isinstance(benchmarks,dict) else next((b for b in benchmarks if b.get('symbol')==series.get('benchmark')), {})
    rs = {'period':window, 'benchmark':series.get('benchmark'), 'value':None,'prior_value':None,
          'state':'unavailable', 'reason':'benchmark_or_period_missing'}
    if window and len(bars)>window:
        by_date = {b['date']:b['close'] for b in benchmark.get('bars',[]) if b.get('complete') is not False}
        def rel(end):
            first,last = bars[end-window],bars[end]
            if by_date.get(first['date'],0)>0 and by_date.get(last['date'],0)>0:
                return (last['close']/first['close'])/(by_date[last['date']]/by_date[first['date']])-1
        rs['value'] = rel(len(bars)-1)
        rs['prior_value'] = rel(len(bars)-2) if len(bars)>window+1 else None
        if rs['value'] is not None: rs.update(state='available',reason=None)
    v = [b['volume'] for b in bars]
    vol = {'latest':v[-1], 'previous':v[-2] if len(v)>1 else None,
           'ratio_to_previous':ratio(v[-1],v[-2]) if len(v)>1 else None,
           'average_prior_20':mean(v[-21:-1]) if len(v)>20 else None}
    vol['ratio_to_prior_20'] = ratio(v[-1],vol['average_prior_20'])
    extreme = {'window':252,'state':'available' if len(bars)>=252 else 'unavailable_insufficient_bars',
               'high':max(b['high'] for b in bars[-252:]) if len(bars)>=252 else None,
               'low':min(b['low'] for b in bars[-252:]) if len(bars)>=252 else None}
    extreme['distance_from_high_pct'] = ratio(bars[-1]['close']-extreme['high'],extreme['high']) if extreme['high'] else None
    result.update(state='available',as_of=bars[-1]['date'],daily_bars=bars,weekly_bars=weekly,
                  moving_averages=averages,volume=vol,relative_strength=rs,range_52w=extreme,
                  sperandeo=structure,taver=location,
                  atr={'period':14,'value':atr(bars,14)[-1],'as_of':bars[-1]['date'],
                       'method':'simple_mean_true_range','purpose':'existing_structure_location_only_not_warning_approval'})
    result['missing'] = [k for k,v in [('relative_strength',rs),('range_52w',extreme)] if v['state']!='available']
    if not weekly: result['missing'].append('completed_weekly_bars_missing')
    return result

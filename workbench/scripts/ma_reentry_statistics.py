"""Locked statistics, crossed clusters, right censoring and fixed-cohort comparisons."""
import argparse
from pathlib import Path
from ma_reentry_research import read, write, sha, STRATEGIES, SEED, mdd
import numpy as np
import pandas as pd


def crossed_bootstrap(g, value, reps=2000, family=12):
    # Whole symbol and whole sale-quarter resampling; preserve within-cell observations.
    cells = g.assign(value=value).groupby(['symbol', 'quarter']).value.agg(['sum', 'count'])
    sy = sorted(g.symbol.unique()); qu = sorted(g.quarter.unique())
    sums = cells['sum'].unstack(fill_value=0).reindex(index=sy, columns=qu, fill_value=0).to_numpy()
    counts = cells['count'].unstack(fill_value=0).reindex(index=sy, columns=qu, fill_value=0).to_numpy()
    rng = np.random.default_rng(SEED)
    ws = rng.multinomial(len(sy), np.full(len(sy), 1 / len(sy)), size=reps)
    wt = rng.multinomial(len(qu), np.full(len(qu), 1 / len(qu)), size=reps)
    num = np.sum((ws @ sums) * wt, axis=1); den = np.sum((ws @ counts) * wt, axis=1)
    draws = num[den > 0] / den[den > 0]
    return dict(ci95=np.quantile(draws, [.025, .975]).tolist(),
                family_ci95=np.quantile(draws, [.025 / family, 1 - .025 / family]).tolist(),
                stock_clusters=len(sy), quarter_clusters=len(qu), reps=len(draws))


def km(g):
    # Waiting for an executable rebound, observed in exchange sessions.
    times = g.wait_signal.to_numpy(int); events = g.completed.to_numpy(bool)
    risk = len(g); surv = 1.; result = {}; median = None
    for t in np.unique(times):
        at = times == t; failures = np.sum(at & events)
        if risk: surv *= 1 - failures / risk
        if median is None and surv <= .5: median = int(t)
        result[int(t)] = float(surv); risk -= int(at.sum())
    return {'median': median, **{f'survival_{n}': result[max([t for t in result if t <= n])] if any(t <= n for t in result) else 1. for n in (5, 10, 20, 60)}}


def summary(g, inference=False):
    c = g[g.completed]; n = len(c)
    out = dict(total=len(g), n=n, open=len(g) - n, stocks=g.symbol.nunique(),
               gap_cycles=int(g.gap_spanning.sum()), stale_open=int((~g.completed & g.stale_end).sum()))
    out.update({'km_' + k: v for k, v in km(g).items()})
    if not n: return out
    cheaper = c.discount > 1e-12; dearer = c.discount < -1e-12
    out.update(cheaper=float(cheaper.mean()), dearer=float(dearer.mean()), equal=float((~cheaper & ~dearer).mean()),
        cheap_mean=float(c.loc[cheaper, 'discount'].mean()), dear_mean=float(-c.loc[dearer, 'discount'].mean()),
        net_win=float((c.net_share > 0).mean()), gross_mean=float(c.gross_share.mean()),
        net_mean=float(c.net_share.mean()), net_pnl_mean=float(c.net_pnl.mean()), stress_mean=float(c.stress_share.mean()),
        net_log_mean=float(np.log1p(c.net_share).mean()), equal_stock_net=float(c.groupby('symbol').net_share.mean().mean()),
        net_positive_mean=float(c.loc[c.net_share > 0, 'net_share'].mean()), net_negative_mean=float(c.loc[c.net_share < 0, 'net_share'].mean()),
        immediate=float(((c.wait_signal <= 3) & (c.signal_low_dd < .01)).mean()),
        no_fall1pct=float((c.signal_low_dd < .01).mean()), rebound3=float((c.wait_signal <= 3).mean()),
        giveback5=float(((c.trade_low_dd >= .05) & (c.discount <= 0)).mean()),
        giveback10=float(((c.trade_low_dd >= .10) & (c.discount <= 0)).mean()),
        giveback5_conditional=float((c.loc[c.trade_low_dd >= .05, 'discount'] <= 0).mean()),
        giveback10_conditional=float((c.loc[c.trade_low_dd >= .10, 'discount'] <= 0).mean()),
        large_drop5=float((c.trade_low_dd >= .05).mean()), large_drop10=float((c.trade_low_dd >= .10).mean()))
    for col in ('discount', 'net_share', 'gross_share', 'signal_low_dd', 'signal_close_dd', 'interval_close_mdd',
                'trade_low_dd', 'trade_upside', 'signal_end_return', 'wait_signal', 'cash_sessions', 'calendar_days'):
        v = c[col].dropna().to_numpy()
        out[col + '_mean'] = float(v.mean())
        for label, q in [('p05', .05), ('p25', .25), ('median', .5), ('p75', .75), ('p95', .95)]:
            out[col + '_' + label] = float(np.quantile(v, q))
    if inference:
        out['log_inference'] = crossed_bootstrap(c, np.log1p(c.net_share))
        out['share_inference'] = crossed_bootstrap(c, c.net_share)
    return out


def cohort_stats(out):
    result = []; annual = []
    for market in ('KR', 'US'):
        for period in ('full', 'oos'):
            g = pd.read_csv(out / f'{market}_{period}_cohort.csv'); dates = pd.to_datetime(g.date)
            years = (dates.iloc[-1] - dates.iloc[0]).days / 365.25
            for strategy in STRATEGIES:
                for mode in ('gross', 'base', 'stress', 'income', 'interest4', 'stale_zero'):
                    key = strategy + '_' + mode
                    if key not in g: continue
                    v = g[key].to_numpy(); b = g['hold_income' if mode == 'income' else 'hold_stale_zero' if mode == 'stale_zero' else 'hold'].to_numpy()
                    row = dict(market=market, period=period, strategy=strategy, mode=mode, years=years,
                        final=v[-1], hold_final=b[-1], relative=v[-1] / b[-1] - 1,
                        cagr=v[-1] ** (1 / years) - 1, hold_cagr=b[-1] ** (1 / years) - 1,
                        mdd=mdd(v), hold_mdd=mdd(b), start=g.date.iloc[0], end=g.date.iloc[-1])
                    if mode == 'base':
                        daily = np.diff(np.log(np.r_[1., v])) - np.diff(np.log(np.r_[1., b]))
                        quarters = pd.Series(daily).groupby(dates.dt.to_period('Q')).sum().to_numpy()
                        rng = np.random.default_rng(SEED)
                        boot = rng.choice(quarters, (2000, len(quarters)), replace=True).sum(axis=1) / years
                        row['annual_log_difference'] = float(np.log(v[-1] / b[-1]) / years)
                        row['annual_log_ci95_low'], row['annual_log_ci95_high'] = np.quantile(boot, [.025, .975])
                        row['annual_log_family_low'], row['annual_log_family_high'] = np.quantile(boot, [.025 / 12, 1 - .025 / 12])
                        row['quarters'] = len(quarters)
                        pv = 1.; pb = 1.
                        for year, ix in g.groupby(dates.dt.year).groups.items():
                            j = max(ix); vr = v[j] / pv - 1; br = b[j] / pb - 1
                            annual.append(dict(market=market, period=period, strategy=strategy, year=int(year),
                                               strategy_return=vr, hold_return=br, difference=vr - br))
                            pv = v[j]; pb = b[j]
                    result.append(row)
    pd.DataFrame(result).to_csv(out / 'cohort_summary.csv', index=False)
    pd.DataFrame(annual).to_csv(out / 'cohort_annual.csv', index=False)
    return result


def summarize(out):
    g = pd.read_csv(out / 'cycles.csv.gz', dtype={'symbol': str})
    p = pd.read_csv(out / 'paths.csv.gz', dtype={'symbol': str})
    results = []; conditions = []; annual = []; pathrows = []
    for (market, strategy), sub in g.groupby(['market', 'strategy'], sort=True):
        for period in ('all', 'train', 'oos'):
            x = sub if period == 'all' else sub[sub.period == period]
            if len(x): results.append(dict(market=market, strategy=strategy, period=period, **summary(x, True)))
        for period in ('all', 'oos'):
            x = sub if period == 'all' else sub[sub.period == period]
            for col in ('group', 'vol', 'regime'):
                for group, cell in x.groupby(col):
                    conditions.append(dict(market=market, strategy=strategy, period=period, dimension=col, group=group, **summary(cell)))
        for year, cell in sub.groupby('year'):
            annual.append(dict(market=market, strategy=strategy, year=int(year), **summary(cell)))
        print('statistics', market, strategy, flush=True)
    for keys, sub in p.groupby(['market', 'strategy', 'period', 'mode']):
        x = sub[sub.years >= 1]
        pathrows.append(dict(zip(['market', 'strategy', 'period', 'mode'], keys)) | dict(
            stocks=len(sub), one_year_stocks=len(x), mean_relative=float(sub.relative.mean()),
            median_relative=float(sub.relative.median()), win_fraction=float((sub.relative > 0).mean()),
            p05_relative=float(sub.relative.quantile(.05)), p95_relative=float(sub.relative.quantile(.95)),
            cagr_difference=float((x.cagr - x.hold_cagr).mean()), mean_mdd=float(sub.mdd.mean()),
            hold_mean_mdd=float(sub.hold_mdd.mean()), median_mdd_improvement=float((sub.hold_mdd - sub.mdd).median()),
            cash_fraction_end=float((sub.cash > 0).mean()), cash_weight_end=float((sub.cash / sub.final).mean()),
            mean_cash_time=float((sub.cash_days / (365.25 * sub.years.replace(0, np.nan))).mean()),
            stale=int(sub.stale_end.sum())))
    # Contribution by historical year and exclusion of each year; no optimized rule selection.
    jackknife = []
    for (market, strategy), sub in g[g.completed].groupby(['market', 'strategy']):
        for year in sorted(sub.year.unique()):
            x = sub[sub.year != year]
            jackknife.append(dict(market=market, strategy=strategy, omitted_year=int(year),
                                   n=len(x), mean_net_share=float(x.net_share.mean()), mean_log_share=float(np.log1p(x.net_share).mean())))
    pd.DataFrame(conditions).to_csv(out / 'conditions.csv', index=False)
    pd.DataFrame(annual).to_csv(out / 'annual.csv', index=False)
    pd.DataFrame(pathrows).to_csv(out / 'path_summary.csv', index=False)
    pd.DataFrame(jackknife).to_csv(out / 'leave_one_year_out.csv', index=False)
    cohorts = cohort_stats(out)
    prog = pd.read_csv(out / 'progression.csv.gz', dtype={'symbol': str}); progression = []
    for (market, status), x in prog.groupby(['market', 'status']):
        progression.append(dict(market=market, status=status, n=len(x), fraction=len(x) / len(prog[prog.market == market]),
            price_mean=float(x.price_change.mean()), price_median=float(x.price_change.median()),
            sessions_mean=float(x.sessions.mean()), sessions_median=float(x.sessions.median())))
    # Open-cycle valuation plus identification of illustrative tail/giveback cases.
    opened = g[~g.completed].groupby(['market', 'strategy']).agg(
        n=('symbol', 'size'), mean_marked_relative=('marked_relative', 'mean'),
        median_marked_relative=('marked_relative', 'median'), max_wait=('wait_signal', 'max'),
        mean_wait=('wait_signal', 'mean'), stale=('stale_end', 'sum'), gap=('gap_spanning', 'sum')).reset_index()
    opened.to_csv(out / 'open_cycles.csv', index=False)
    examples = []
    for (market, strategy), x in g[g.completed].groupby(['market', 'strategy']):
        for typ, cell in [('best', x.nlargest(2, 'net_share')), ('worst', x.nsmallest(2, 'net_share')),
                          ('giveback', x[(x.trade_low_dd >= .05) & (x.discount <= 0)].nlargest(2, 'trade_low_dd'))]:
            examples.extend(cell.assign(example=typ).to_dict('records'))
    pd.DataFrame(examples).to_csv(out / 'examples.csv', index=False)
    write(out / 'results.json', dict(protocol_sha256=sha(out / 'protocol.json'), summaries=results,
                                    paths=pathrows, cohorts=cohorts, progression=progression))
    flat = [{k: v for k, v in r.items() if not isinstance(v, dict)} for r in results]
    pd.DataFrame(flat).to_csv(out / 'summary.csv', index=False)
    print('summaries complete', len(results), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--out', type=Path, required=True); a=p.parse_args(); summarize(a.out)

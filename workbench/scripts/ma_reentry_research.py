"""Frozen six-strategy research. Offline sources, next-open ledger, no operations."""
from __future__ import annotations
import argparse, csv, gzip, json, sys, math
import shutil
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'runtime/ma-dd-describe-20261017'
sys.path.insert(0, str(SOURCE / 'dependencies'))
import numpy as np
import pandas as pd
from ma_dd_describe import read, write, sha, prepare_kr, us_stock, segment_indices, features

STRATEGIES = ['AC', 'AG', 'AE', 'BC', 'BG', 'BE']
BASE = {'KR': (.0035, .0005), 'US': (.0005, .0005)}
STRESS = {'KR': (.005, .002), 'US': (.0025, .0025)}
SEED = 20261017


def less(a, b):
    return (a - b) < -1e-12 * np.maximum(np.abs(a), np.abs(b))


def signal_arrays(close):
    ma = {k: pd.Series(close).rolling(k).mean().to_numpy() for k in (5, 10, 20)}
    A = less(ma[5], ma[20]) & less(ma[10], ma[20])
    B = less(ma[5], ma[10]) & less(ma[10], ma[20])
    warm = np.isfinite(ma[20]) & np.r_[False, np.isfinite(ma[20][:-1])]
    C = less(ma[5], close) & np.r_[False, ~less(ma[5][:-1], close[:-1])] & np.r_[False, np.isfinite(ma[5][:-1])]
    G = less(ma[10], ma[5]) & np.r_[False, ~less(ma[10][:-1], ma[5][:-1])] & np.r_[False, np.isfinite(ma[10][:-1])]
    return dict(A=A, B=B, sellA=A & ~np.r_[False, A[:-1]] & warm,
                sellB=B & ~np.r_[False, B[:-1]] & warm, C=C, G=G, E=C | G)


def trading_segments(g, calendar):
    original, qa = segment_indices(g, calendar)
    segments = []
    positions = calendar.get_indexer(g.date)
    for ix in original:
        if segments and positions[ix[0]] == positions[segments[-1][-1]] + 1:
            segments[-1] = np.concatenate([segments[-1], ix])
        else: segments.append(ix)
    qa['calendar_only_breaks_merged'] = len(original) - len(segments)
    qa['segments'] = len(segments)
    return segments, qa


def build_arrays(g, calendar, market):
    segments, qa = trading_segments(g, calendar)
    ix = np.concatenate(segments) if segments else np.array([], dtype=int)
    x = g.iloc[ix].reset_index(drop=True)
    z = {k: x[k].to_numpy(float) for k in ('open', 'high', 'low', 'close', 'cap', 'rank')}
    z['date'] = x.date.dt.strftime('%Y-%m-%d').to_numpy(dtype='U10')
    z['day'] = x.date.to_numpy('datetime64[D]').astype('int64')
    z['pos'] = calendar.get_indexer(x.date)
    z['segment'] = np.concatenate([np.full(len(s), n, dtype=int) for n, s in enumerate(segments)]) if segments else ix
    z['eligible'] = (z['rank'] >= 1) & (z['rank'] <= 200) if market == 'KR' else z['cap'] >= 2e9
    z['sigma'] = np.full(len(x), np.nan)
    for name in ('A', 'B', 'sellA', 'sellB', 'C', 'G', 'E'):
        z[name] = np.zeros(len(x), dtype=bool)
    off = 0
    for s in segments:
        sub = g.iloc[s].reset_index(drop=True)
        sl = slice(off, off + len(s)); off += len(s)
        for k, v in signal_arrays(sub.close.to_numpy(float)).items(): z[k][sl] = v
        z['sigma'][sl] = features(sub)['sigma']
    return z, qa, ix


def prepare(out, source=SOURCE):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'arrays').mkdir(exist_ok=True)
    if (out / 'prepared.json').exists(): raise FileExistsError('prepared outputs immutable')
    (out / 'protocol.json').write_bytes((ROOT / 'scripts/ma_reentry_protocol.json').read_bytes())
    manifest = []; input_hashes = []; failed = []; markets = {}
    for market in ('KR', 'US'):
        if market == 'KR':
            for r in read(source / 'kr_sources.json')['files']:
                assert sha(r['path']) == r['sha256']; input_hashes.append(r)
            stocks, calendar = prepare_kr(source, audit_out=out)
        else:
            import exchange_calendars as xc
            calendar = xc.get_calendar('XNYS', start='2015-01-01', end='2026-10-16').sessions.tz_localize(None)
            stocks = [r for r in read(source / 'us_acquisition.json') if r['status'] == 'ready']
        calendar = calendar[calendar <= '2026-10-15']
        ret_sum = np.zeros(len(calendar)); ret_n = np.zeros(len(calendar)); vols = []
        for num, item in enumerate(stocks):
            symbol = item[0] if market == 'KR' else item['symbol']
            if market == 'US':
                folder = source / 'raw/us' / symbol
                old = next((r for r in read(source / 'US_audit.json')['stocks'] if r['symbol'] == symbol), None) if num == 0 else oldmap.get(symbol)
                if num == 0: oldmap = {r['symbol']: r for r in read(source / 'US_audit.json')['stocks']}
                if old is not None:
                    for kind in ('price', 'shares'):
                        p = Path(old[kind + '_path']); assert sha(p) == old[kind + '_sha256']
                        input_hashes.append({'path': str(p), 'sha256': old[kind + '_sha256']})
                try: g, record = us_stock(source, item)
                except (KeyError, AssertionError, ValueError) as e:
                    failed.append({'market': market, 'symbol': symbol, 'error': type(e).__name__}); continue
            else: _, g, record = item
            g = g[g.date <= '2026-10-15'].reset_index(drop=True)
            z, qa, ix = build_arrays(g, calendar, market)
            if not len(ix): continue
            if not np.any(z['eligible'] & (z['date'] >= '2016-01-01')): continue
            if market == 'US':
                ch = read(source / 'raw/us' / symbol / 'chart_daily.json')['chart']['result'][0]
                adj = np.asarray(ch['indicators'].get('adjclose', [{}])[0].get('adjclose', [np.nan] * len(g)), float)[:len(g)]
                z['tr_factor'] = adj[ix] / z['close']
            else: z['tr_factor'] = np.ones(len(ix))
            # Market proxy uses only previous-close eligibility and consecutive sessions.
            valid = np.r_[False, z['eligible'][:-1] & (np.diff(z['pos']) == 1) & (np.diff(z['segment']) == 0)]
            rr = np.r_[0., z['close'][1:] / z['close'][:-1] - 1]
            np.add.at(ret_sum, z['pos'][valid], rr[valid]); np.add.at(ret_n, z['pos'][valid], 1)
            train = z['eligible'] & (z['date'] >= '2016-01-01') & (z['date'] <= '2022-12-31') & np.isfinite(z['sigma'])
            vols.append(z['sigma'][train])
            # Do not store prices of symbols never eligible; proxy still retains their eligible returns.
            if np.any(z['eligible'] & (z['date'] >= '2016-01-01')):
                p = out / 'arrays' / f'{market}_{symbol}.npz'; np.savez_compressed(p, **z)
                manifest.append(dict(market=market, symbol=symbol, path=str(p), sha256=sha(p), bars=len(ix),
                                     first=z['date'][0], last=z['date'][-1], **qa))
            if (num + 1) % 250 == 0: print('prepare', market, num + 1, '/', len(stocks), flush=True)
        r = np.divide(ret_sum, ret_n, out=np.zeros_like(ret_sum), where=ret_n > 0)
        index = np.cumprod(1 + r)
        ma = pd.Series(index).rolling(200).mean().to_numpy()
        regime = np.where(~np.isfinite(ma), 'unknown', np.where(index >= ma, 'up', 'down'))
        regime = np.concatenate([['unknown'], regime[:-1]])
        cuts = np.quantile(np.concatenate(vols), [.25, .5, .75]).tolist()
        frame = pd.DataFrame(dict(date=calendar.strftime('%Y-%m-%d'), sample_return=r, contributors=ret_n, index=index, regime=regime))
        frame.to_csv(out / f'{market}_market_proxy.csv', index=False)
        markets[market] = dict(vol_cuts=cuts, train_vol_rows=sum(len(v) for v in vols), sessions=len(calendar))
    write(out / 'prepared.json', dict(protocol_sha256=sha(out / 'protocol.json'), stocks=manifest,
                                     input_hashes=input_hashes, failures=failed, markets=markets))
    print('prepared', len(manifest), 'stocks', len(input_hashes), 'hashes', failed, flush=True)


def repair_calendar(out, draft):
    """Reuse hashed unfinished derived inputs; normal prepare also uses corrected session logic."""
    out.mkdir(parents=True, exist_ok=True); (out/'arrays').mkdir(exist_ok=True)
    meta=read(draft/'prepared.json'); records=[]; vols=[]; merged=0
    proxy=pd.read_csv(draft/'KR_market_proxy.csv'); n=len(proxy)
    ret_sum=np.zeros(n);ret_n=np.zeros(n)
    for rec in meta['stocks']:
        p=draft/'arrays'/Path(rec['path']).name;assert sha(p)==rec['sha256']
        r=dict(rec);r['path']=str(p.resolve())
        if r['market']=='KR':
            z=load_arrays(p);newseg=np.cumsum(np.r_[0,np.diff(z['pos'])!=1])
            delta=len(np.unique(z['segment']))-len(np.unique(newseg));merged+=delta
            z['segment']=newseg
            for seg in np.unique(newseg):
                ix=np.flatnonzero(newseg==seg)
                for k,v in signal_arrays(z['close'][ix]).items():z[k][ix]=v
                z['sigma'][ix]=features(pd.DataFrame({k:z[k][ix] for k in ('close','high','low')}))['sigma']
            valid=np.r_[False,z['eligible'][:-1]&(np.diff(z['pos'])==1)]
            rr=np.r_[0,z['close'][1:]/z['close'][:-1]-1]
            np.add.at(ret_sum,z['pos'][valid],rr[valid]);np.add.at(ret_n,z['pos'][valid],1)
            train=z['eligible']&(z['date']>='2016-01-01')&(z['date']<='2022-12-31')&np.isfinite(z['sigma'])
            vols.append(z['sigma'][train])
            dst=out/'arrays'/p.name;np.savez_compressed(dst,**z)
            r.update(path=str(dst.resolve()),sha256=sha(dst),segments=len(np.unique(newseg)),calendar_only_breaks_merged=delta)
        records.append(r)
    rr=np.divide(ret_sum,ret_n,out=np.zeros(n),where=ret_n>0);index=np.cumprod(1+rr)
    ma=pd.Series(index).rolling(200).mean().to_numpy();regime=np.where(~np.isfinite(ma),'unknown',np.where(index>=ma,'up','down'))
    proxy=pd.DataFrame(dict(date=proxy.date,sample_return=rr,contributors=ret_n,index=index,regime=np.concatenate([['unknown'],regime[:-1]])))
    proxy.to_csv(out/'KR_market_proxy.csv',index=False)
    for name in ['US_market_proxy.csv','kr_sources.json']:shutil.copyfile(draft/name,out/name)
    (out/'protocol.json').write_bytes((ROOT/'scripts/ma_reentry_protocol.json').read_bytes())
    meta['protocol_sha256']=sha(out/'protocol.json');meta['stocks']=records
    meta['markets']['KR'].update(vol_cuts=np.quantile(np.concatenate(vols),[.25,.5,.75]).tolist(),train_vol_rows=sum(len(v) for v in vols))
    meta['calendar_correction']=dict(merged_boundaries=merged,draft=str(draft),reason='Calendar-adjacent sessions across long national holiday must not reset SMA; stopped unfinished simulation before summary/outcome inspection.')
    write(out/'prepared.json',meta);print('reused inputs, merged holiday boundaries',merged,flush=True)


def load_arrays(path):
    with np.load(path, allow_pickle=False) as x: return {k: x[k] for k in x.files}


def fill_index(z, t, delay=1):
    j = t + delay
    if j < len(z['close']) and z['pos'][j] - z['pos'][t] == delay and z['segment'][j] == z['segment'][t]:
        return j
    return None


def inception(z, start):
    for t in np.flatnonzero(z['eligible'] & (z['date'] >= start)):
        j = fill_index(z, int(t))
        if j is not None: return int(t), j
    return None


def cycles(z, strategy, start='2016-01-01', delay=1):
    inc = inception(z, start)
    if inc is None: return [], None, dict(unfilled_sales=0, ignored_sales=0, unfilled_buys=0)
    _, begin = inc
    sells = np.flatnonzero(z['sell' + strategy[0]] & z['eligible'] & (z['date'] >= start))
    rebounds = np.flatnonzero(z[strategy[1]])
    result = []; cursor = begin - 1; unfilled_sales = 0; unfilled_buys = 0; ignored = 0
    for t in sells:
        t = int(t)
        if t < cursor: ignored += 1; continue
        s = fill_index(z, t, delay)
        if s is None: unfilled_sales += 1; continue
        if s < begin: continue
        b = None; r = None
        for v in rebounds[np.searchsorted(rebounds, max(t + 1, s)):]:
            v = int(v); r = v; b = fill_index(z, v, delay)
            if b is not None: break
            unfilled_buys += 1
        result.append(dict(t=t, s=s, r=r, b=b))
        cursor = b if b is not None else len(z['close'])
    return result, begin, dict(unfilled_sales=unfilled_sales, ignored_sales=ignored, unfilled_buys=unfilled_buys)


def wealth(z, cs, begin, sellcost=0., buycost=0., income=False, market='KR', interest=0.):
    """Independent capital sleeves; cash is used only for its matching buy."""
    n = len(z['close']); c = z['close']; o = z['open']; d = z['day']
    factor = np.ones(n)
    if income:
        factor = z['tr_factor'] / z['tr_factor'][begin] if market == 'US' else np.exp(.04 * (d - d[begin]) / 365.25)
        if not np.all(np.isfinite(factor[begin:]) & (factor[begin:] > 0)): return None
    c = c * factor; o = o * factor
    path = np.full(n, np.nan); bh = c / o[begin]; bh[:begin] = np.nan
    q = 1 / o[begin]; cursor = begin; cash = 0.; cash_days = 0
    for row in cs:
        s, b = row['s'], row['b']; end = b if b is not None else n
        path[cursor:s] = q * c[cursor:s]
        cash = q * .5 * o[s] * (1 - sellcost); q *= .5
        growth = np.exp(interest * (d[s:end] - d[s]) / 365.25)
        path[s:end] = q * c[s:end] + cash * growth
        cash_days += int((d[b] if b is not None else d[-1]) - d[s])
        if b is not None:
            cash *= math.exp(interest * (d[b] - d[s]) / 365.25)
            q += cash / (o[b] * (1 + buycost)); cash = 0.; cursor = b
        else:
            cash *= math.exp(interest * (d[-1] - d[s]) / 365.25); cursor = n
    path[cursor:] = q * c[cursor:]
    return dict(path=path, bh=bh, q=q, cash=cash, cash_days=cash_days,
                final=float(path[-1]), relative=float(path[-1] / bh[-1] - 1))


def mdd(x):
    x = np.r_[1., np.asarray(x)[np.isfinite(x)]]
    return float(np.max(1 - x / np.maximum.accumulate(x)))


def cycle_rows(z, cs, market, symbol, strategy, regime, cuts):
    out = []; c = z['close']; o = z['open']; lo = z['low']; ds = z['date']; days = z['day']; pos = z['pos']
    sc, bc = BASE[market]
    for k, row in enumerate(cs):
        t, s, r, b = (row[n] for n in ('t', 's', 'r', 'b'))
        end = r if b is not None else len(c) - 1
        stop = b if b is not None else len(c)
        sell, buy = o[s], o[b] if b is not None else np.nan
        path = c[t:end + 1]; run = np.maximum.accumulate(path)
        sig_low = max(0., 1 - lo[t + 1:end + 1].min() / c[t]) if end > t else 0.
        sig_close = max(0., 1 - c[t:end + 1].min() / c[t])
        trade_low = max(0., 1 - min(lo[s:stop].min(), buy if b is not None else c[-1]) / sell)
        trade_high = max(0., max(z['high'][s:stop].max(), buy if b is not None else c[-1]) / sell - 1)
        gross = .5 * (sell / buy - 1) if b is not None else np.nan
        net = .5 * ((1 - sc) / (1 + bc) * sell / buy - 1) if b is not None else np.nan
        period = 'oos' if ds[t] >= '2023-04-01' else ('train' if ds[t] <= '2022-12-31' and b is not None and ds[b] <= '2022-12-31' else 'boundary')
        group = ('rank1_50' if z['rank'][t] <= 50 else 'rank51_100' if z['rank'][t] <= 100 else 'rank101_200') if market == 'KR' else ('mega' if z['cap'][t] >= 200e9 else 'large' if z['cap'][t] >= 10e9 else 'mid')
        sigma = z['sigma'][t]; vb = 'missing' if not np.isfinite(sigma) else 'Q' + str(np.searchsorted(cuts, sigma, side='right') + 1)
        mark = c[-1]
        out.append(dict(market=market, symbol=symbol, strategy=strategy, cycle=k, signal_date=ds[t], sell_date=ds[s],
            rebound_date=ds[r] if r is not None else '', buy_date=ds[b] if b is not None else '', end_date=ds[end],
            signal_i=t, sell_i=s, rebound_i=r, buy_i=b, completed=b is not None,
            status='completed' if b is not None else 'signal_unfilled' if r is not None else 'no_rebound',
            sell=sell, buy=buy, signal_close=c[t], terminal_close=mark,
            wait_signal=int(pos[end] - pos[t]), cash_sessions=int(pos[b if b is not None else -1] - pos[s]),
            calendar_days=int(days[b if b is not None else -1] - days[s]),
            signal_low_dd=sig_low, signal_close_dd=sig_close, interval_close_mdd=float(np.max(1 - path / run)),
            trade_low_dd=trade_low, trade_upside=trade_high, signal_end_return=c[end] / c[t] - 1,
            discount=1 - buy / sell, gross_share=gross, net_share=net,
            net_pnl=.5 * ((1 - sc) / (1 + bc) - buy / sell) if b is not None else np.nan,
            stress_share=.5 * ((1 - STRESS[market][0]) / (1 + STRESS[market][1]) * sell / buy - 1) if b is not None else np.nan,
            marked_relative=.5 * ((1 - sc) * sell / mark - 1) if b is None else net,
            gap_spanning=bool(z['segment'][s] != z['segment'][b if b is not None else -1]),
            stale_end=bool(ds[-1] < '2026-10-15'), cap=z['cap'][t], rank=z['rank'][t], group=group,
            sigma=sigma, vol=vb, regime=regime.get(ds[t], 'unknown'), year=int(ds[t][:4]),
            quarter=ds[t][:4] + 'Q' + str((int(ds[t][5:7]) - 1) // 3 + 1), period=period))
    return out


def progression(z, market, symbol):
    rows = []
    for t in np.flatnonzero(z['sellA'] & z['eligible'] & (z['date'] >= '2016-01-01')):
        t = int(t); r = t; status = 'already_B' if z['B'][t] else 'censored'
        if not z['B'][t]:
            for r in range(t + 1, len(z['close'])):
                if z['segment'][r] != z['segment'][t]: r -= 1; break
                if not z['A'][r]: status = 'ended_without_B'; break
                if z['B'][r]: status = 'later_B'; break
        rows.append(dict(market=market, symbol=symbol, date=z['date'][t], status=status,
                         sessions=int(z['pos'][r] - z['pos'][t]), price_change=z['close'][r] / z['close'][t] - 1))
    return rows


def run(out):
    meta = read(out / 'prepared.json'); assert meta['protocol_sha256'] == sha(out / 'protocol.json')
    if (out / 'cycles.csv.gz').exists(): raise FileExistsError('run outputs already exist')
    row_count = 0; paths = []; progresses = []; audits = []; cohorts = {}
    cycle_file = gzip.open(out / 'cycles.csv.gz', 'wt', encoding='utf-8', newline='')
    writer = None
    proxies = {m: pd.read_csv(out / f'{m}_market_proxy.csv') for m in ('KR', 'US')}
    for m in ('KR', 'US'):
        for period, start in [('full', '2016-01-01'), ('oos', '2023-04-01')]:
            dates = proxies[m].date.to_numpy(); first = int(np.flatnonzero(dates >= start)[0])
            cohorts[(m, period)] = dict(dates=dates[first + 1:], signal_date=dates[first], values={}, n=0, stale=0)
    for num, record in enumerate(meta['stocks']):
        market, symbol = record['market'], record['symbol']; assert sha(record['path']) == record['sha256']
        z = load_arrays(record['path']); proxy = proxies[market]
        regime = dict(zip(proxy.date, proxy.regime)); cuts = meta['markets'][market]['vol_cuts']
        progresses.extend(progression(z, market, symbol))
        for period, start in [('full', '2016-01-01'), ('oos', '2023-04-01')]:
            cohort = cohorts[(market, period)]; inc = inception(z, start)
            if inc is None: continue
            member = z['date'][inc[0]] == cohort['signal_date']
            if member: cohort['n'] += 1; cohort['stale'] += int(z['date'][-1] < '2026-10-15')
            for strategy in STRATEGIES:
                cs, begin, audit = cycles(z, strategy, start)
                if begin is None: continue
                audits.append(dict(market=market, symbol=symbol, strategy=strategy, period=period, **audit))
                if period == 'full':
                    newrows = cycle_rows(z, cs, market, symbol, strategy, regime, cuts)
                    if newrows:
                        if writer is None:
                            writer = csv.DictWriter(cycle_file, fieldnames=list(newrows[0])); writer.writeheader()
                        writer.writerows(newrows); row_count += len(newrows)
                modes = dict(gross=(0., 0., False, 0.), base=(*BASE[market], False, 0.),
                             stress=(*STRESS[market], False, 0.), income=(*BASE[market], True, 0.),
                             interest4=(*BASE[market], False, .04))
                for mode, (sc, bc, income, interest) in modes.items():
                    w = wealth(z, cs, begin, sc, bc, income, market, interest)
                    if w is None: continue
                    years = (z['day'][-1] - z['day'][begin]) / 365.25
                    bh = float(w['bh'][-1]); final = w['final']
                    paths.append(dict(market=market, symbol=symbol, strategy=strategy, period=period, mode=mode,
                        start=z['date'][begin], end=z['date'][-1], years=years, cycles=len(cs), completed=sum(x['b'] is not None for x in cs),
                        final=final, hold_final=bh, relative=w['relative'], cagr=final ** (1 / years) - 1 if years >= 1 else np.nan,
                        hold_cagr=bh ** (1 / years) - 1 if years >= 1 else np.nan,
                        mdd=mdd(w['path']), hold_mdd=mdd(w['bh']), cash=w['cash'], cash_days=w['cash_days'],
                        fixed_cohort=member, stale_end=z['date'][-1] < '2026-10-15', gaps=record['segments'] - 1))
                    if member:
                        # Last observation carried forward ONLY as a disclosed valuation, never a fill.
                        aligned = pd.Series(w['path'][begin:], index=z['date'][begin:]).reindex(cohort['dates']).ffill().to_numpy()
                        bhalign = pd.Series(w['bh'][begin:], index=z['date'][begin:]).reindex(cohort['dates']).ffill().to_numpy()
                        assert np.isfinite(aligned).all() and np.isfinite(bhalign).all()
                        key = strategy + '_' + mode
                        cohort['values'][key] = cohort['values'].get(key, 0.) + aligned
                        bhkey = 'hold_income' if income else 'hold'
                        if strategy == 'AC' and mode in ('base', 'income'):
                            cohort['values'][bhkey] = cohort['values'].get(bhkey, 0.) + bhalign
                        if mode == 'base':
                            stresspath = aligned.copy(); stresshold = bhalign.copy()
                            if z['date'][-1] < '2026-10-15':
                                after = cohort['dates'] > z['date'][-1]
                                stresspath[after] = w['cash']; stresshold[after] = 0.
                            cohort['values'][strategy + '_stale_zero'] = cohort['values'].get(strategy + '_stale_zero', 0.) + stresspath
                            if strategy == 'AC': cohort['values']['hold_stale_zero'] = cohort['values'].get('hold_stale_zero', 0.) + stresshold
                # Execution sensitivity, unchanged signals, an extra whole session lag.
                delayed, begin2, _ = cycles(z, strategy, start, delay=2)
                w = wealth(z, delayed, begin2, *BASE[market])
                paths.append(dict(market=market, symbol=symbol, strategy=strategy, period=period, mode='delay2',
                    start=z['date'][begin2], end=z['date'][-1], years=years, cycles=len(delayed), completed=sum(x['b'] is not None for x in delayed),
                    final=w['final'], hold_final=w['bh'][-1], relative=w['relative'],
                    cagr=w['final'] ** (1 / years) - 1 if years >= 1 else np.nan,
                    hold_cagr=w['bh'][-1] ** (1 / years) - 1 if years >= 1 else np.nan,
                    mdd=mdd(w['path']), hold_mdd=mdd(w['bh']), cash=w['cash'], cash_days=w['cash_days'],
                    fixed_cohort=member, stale_end=z['date'][-1] < '2026-10-15', gaps=record['segments'] - 1))
        if (num + 1) % 100 == 0: print('simulate', num + 1, '/', len(meta['stocks']), 'cycles', row_count, flush=True)
    cycle_file.close()
    pd.DataFrame(paths).to_csv(out / 'paths.csv.gz', index=False, compression='gzip')
    pd.DataFrame(progresses).to_csv(out / 'progression.csv.gz', index=False, compression='gzip')
    pd.DataFrame(audits).to_csv(out / 'execution_audit.csv.gz', index=False, compression='gzip')
    co = []
    for (market, period), v in cohorts.items():
        frame = pd.DataFrame({'date': v['dates'], **{k: x / v['n'] for k, x in v['values'].items()}})
        frame.to_csv(out / f'{market}_{period}_cohort.csv', index=False)
        co.append(dict(market=market, period=period, n=v['n'], stale=v['stale'], signal_date=v['signal_date']))
    write(out / 'simulation.json', dict(protocol_sha256=meta['protocol_sha256'], cycles=row_count, paths=len(paths), cohorts=co))
    print('simulation complete', row_count, len(paths), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('command', choices=['prepare', 'run','repair-calendar']); p.add_argument('--out', type=Path, required=True);p.add_argument('--draft',type=Path)
    a = p.parse_args()
    if a.command=='repair-calendar':repair_calendar(a.out,a.draft)
    else:globals()[a.command](a.out)

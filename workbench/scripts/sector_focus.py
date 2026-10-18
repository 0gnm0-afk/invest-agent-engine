"""Offline attention changes, separate from stock eligibility and original reports."""
import hashlib
import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

from sector_signals import prepare, cohort, divide, change
from sector_observation import equal_weight_index
from invest_agent.trading.store import instance_lock
from invest_agent.trading.runner import atomic_write

DEFAULTS = dict(window=5, baseline=20, history=40, min_members=5,
                min_coverage=.8, min_change=.10, min_acceleration=.05,
                min_participation=.5, min_persistence=3, price_floor=-.005,
                home_limit=3)


def settings(overrides=None):
    c = DEFAULTS | (overrides or {})
    if set(c) != set(DEFAULTS):
        raise ValueError('unknown_focus_setting')
    for k, v in c.items():
        if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v):
            raise ValueError('invalid_focus_setting')
    for k in ('window', 'baseline', 'history', 'min_members', 'min_persistence', 'home_limit'):
        if not isinstance(c[k], int) or c[k] < 1:
            raise ValueError('invalid_focus_period')
    if not (0 < c['min_coverage'] <= 1 and 0 < c['min_participation'] <= 1
            and c['min_change'] > 0 and c['min_acceleration'] > 0
            and c['min_persistence'] <= c['window'] and c['price_floor'] <= 0):
        raise ValueError('invalid_focus_threshold')
    return c


def baskets(groups, sectors):
    result = {(s['market'], s['group_code']): dict(market=s['market'], code=s['group_code'],
               name=s['group_name'], level='sector', parent=None, members=[]) for s in sectors}
    for g in groups:
        if g.get('group_state') != 'matched':
            continue
        key = (g['market'], g['group_code'])
        if key in result:
            result[key]['members'].append(g['symbol'])
        # Only the directly verified Finviz industry in the adopted sector evidence.
        # Partial KR company samples cannot establish a whole industry basket.
        if g['market'] == 'US':
            industries = {p['industry'] for p in g.get('evidence', [])
                          if p.get('industry') and p.get('sector') == g['group_name']}
            if len(industries) == 1:
                name = industries.pop()
                code = 'industry-' + hashlib.sha256((g['group_code']+'|'+name).encode()).hexdigest()[:16]
                item = result.setdefault(('US', code), dict(market='US', code=code, name=name,
                    level='industry', parent=g['group_name'], members=[]))
                item['members'].append(g['symbol'])
    return list(result.values())


def classify(m, c):
    rules = [('growth', '활동 증가율', c['min_change']),
             ('acceleration', '증가율의 가속', c['min_acceleration']),
             ('participation', '활동 증가 동참', c['min_participation']),
             ('persistent_days', '지속 일수', c['min_persistence']),
             ('return_recent', '가격 약세 제한', c['price_floor'])]
    m['checks'] = [{'label': label, 'passed': m.get(key) >= threshold if m.get(key) is not None else None}
                   for key, label, threshold in rules]
    m['checks'].append({'label':'시장 내 추정 거래 비중 증가',
                        'passed':m['share_change'] > 0 if m.get('share_change') is not None else None})
    if m['issues']:
        return 'unavailable', '자료 확인 필요'
    if m['growth'] is None or m['acceleration'] is None:
        return 'unavailable', '비교 기준 부족'
    if m['growth'] >= c['min_change'] and m['return_recent'] < c['price_floor']:
        return 'price_weak', '활동 증가 · 가격 약세'
    if all(x['passed'] for x in m['checks']):
        return 'building', '평소 아래서 개선' if m['activity_now'] < 1 else '관심 증가 가속'
    if m['growth'] > 0 and m['acceleration'] < 0:
        return 'slowing', '활동 증가 · 속도 둔화'
    return 'watch', '조건 일부 미충족'


def metrics(members, eligible, totals, n, b, c):
    ratios, valid, excluded = {}, [], {}
    for symbol in members:
        bars = eligible[symbol]
        values = []
        for i in range(b, b+3*n):
            base = st.fmean(x['volume'] for x in bars[i-b:i])
            if base <= 0:
                break
            values.append(bars[i]['volume']/base)
        if len(values) != 3*n:
            excluded[symbol] = 'relative_volume_baseline_zero'
        elif st.fmean(values[:n]) <= 0 or st.fmean(values[n:2*n]) <= 0:
            excluded[symbol] = 'comparison_activity_zero'
        else:
            ratios[symbol] = values
            valid.append(symbol)
    if not valid:
        return dict(issues=['상대 거래량 비교 불가'], activity_now=None, growth=None, acceleration=None, activity_used=0, activity_exclusions=excluded), []
    daily = [st.median(ratios[s][i] for s in valid) for i in range(3*n)]
    old, prev, now = [st.fmean(daily[i:i+n]) for i in (0, n, 2*n)]
    growth, previous_growth = change(now, prev), change(prev, old)
    stock_growth = {s: change(st.fmean(ratios[s][-n:]), st.fmean(ratios[s][-2*n:-n])) for s in valid}
    curve = equal_weight_index([[x['close'] for x in eligible[s]] for s in members])
    proxy = [sum(eligible[s][i]['close']*eligible[s][i]['volume'] for s in members) for i in range(b+3*n)]
    share_now = divide(sum(proxy[-n:]), sum(totals[-n:]))
    share_prev = divide(sum(proxy[-2*n:-n]), sum(totals[-2*n:-n]))
    recent_proxy = {s: sum(x['close']*x['volume'] for x in eligible[s][-n:]) for s in members}
    contributors = sorted(valid, key=lambda s: (-(stock_growth[s] if stock_growth[s] is not None else -1e100), s))
    leaders = [dict(symbol=s, growth=stock_growth[s], activity=st.fmean(ratios[s][-n:]),
                    return_recent=eligible[s][-1]['close']/eligible[s][-n-1]['close']-1)
               for s in contributors[:5]]
    issues = []
    if len(valid) < c['min_members'] or len(valid)/max(1,len(members)) < c['min_coverage']:
        issues.append('상대 거래량 유효 구성 부족: 최소 종목 수 또는 비율 미달')
    if share_now is None or share_prev is None:
        issues.append('시장 거래 비중 분모 부족')
    return dict(issues=issues, activity_now=now, activity_previous=prev, activity_earlier=old,
                growth=growth, previous_growth=previous_growth,
                acceleration=growth-previous_growth if growth is not None and previous_growth is not None else None,
                participation=sum(stock_growth[s] is not None and stock_growth[s]>0 for s in valid)/len(valid),
                persistent_days=sum(v>prev for v in daily[-n:]),
                return_recent=curve[-1]/curve[-n-1]-1,
                share_now=share_now, share_change=change(share_now,share_prev) if share_now is not None else None,
                top3_share=divide(sum(sorted(recent_proxy.values(),reverse=True)[:3]),sum(recent_proxy.values())),
                activity_used=len(valid), activity_exclusions=excluded), leaders


def build(snapshot, groups, sectors, overrides=None):
    c = settings(overrides); n, b = c['window'], c['baseline']; required=b+3*n
    out = dict(schema=1, settings=c, markets={}, items=[], network_calls=0,
               limitations=['relative_volume_not_net_cash_flow', 'current_classification_not_historical_backtest',
                            'early_observation_rule_not_validated_prediction', 'KR_uses_existing_WICS_sectors'])
    definitions = baskets(groups, sectors)
    names = {(r['market'],r['symbol']):r.get('name',r['symbol']) for r in snapshot.get('universe',[])}
    for market in ('KR','US'):
        if not snapshot.get('sessions',{}).get(market):
            continue
        sessions, universe, prepared = prepare(snapshot, market, snapshot['sessions'][market][-1], c)
        rows = [r for r in groups if r['market']==market]
        classification_coverage = sum(r.get('group_state')=='matched' for r in rows)/max(1,len(universe))
        def freshness(selected):
            values = [r.get('classification_freshness',{}) for r in selected if r.get('group_state')=='matched']
            dates = [v['oldest_observed_at'] for v in values if v.get('oldest_observed_at')]
            ends = [v['latest_observed_at'] for v in values if v.get('latest_observed_at')]
            source_dates = [v['source_as_of'] for v in values if v.get('source_as_of')]
            return dict(user_defined=sum(v.get('state')=='user_defined' for v in values),
                        refresh_due=sum(v.get('state')=='refresh_due' for v in values),
                        observed_from=min(dates) if dates else None, observed_until=max(ends) if ends else None,
                        source_as_of=min(source_dates) if source_dates else None)
        items = [dict(d,history=[],current={},leaders=[],classification=freshness([r for r in rows if r['symbol'] in d['members']]))
                 for d in definitions if d['market']==market]
        for end in range(max(required-1,len(sessions)-c['history']),len(sessions)):
            dates=sessions[end-required+1:end+1]; eligible,excluded=cohort(prepared,dates)
            totals=[sum(v[i]['close']*v[i]['volume'] for v in eligible.values()) for i in range(required)]
            for item in items:
                members=[s for s in item['members'] if s in eligible]
                m,leaders = metrics(members,eligible,totals,n,b,c) if members else (dict(issues=['유효 구성 없음']),[])
                issues=m['issues']
                if len(members)<c['min_members']:issues.append('최소 구성 종목 부족')
                if len(members)/max(1,len(item['members']))<c['min_coverage']:issues.append('구성 시세 비율 부족')
                if len(eligible)/max(1,len(universe))<c['min_coverage']:issues.append('시장 시세 비율 부족')
                if classification_coverage<c['min_coverage']:issues.append('시장 분류 비율 부족·미확인 또는 충돌')
                m.update(date=dates[-1],used=len(members),matched=len(item['members']),
                    start=dates[0],current_start=dates[-n],previous_start=dates[-2*n],earlier_start=dates[-3*n],
                    cohort_hash=hashlib.sha256('|'.join(sorted(members)).encode()).hexdigest())
                m['state'],m['label']=classify(m,c)
                item['history'].append(m)
                if end==len(sessions)-1:
                    item['current']=m
                    item['leaders']=[dict(r,name=names.get((market,r['symbol']),r['symbol'])) for r in leaders]
        for item in items:
            if not item['history']:
                item['current']=dict(state='unavailable',label='관측 기간 부족',issues=[str(required)+'개 이상 완료봉 필요'],date=sessions[-1])
            item['member_count']=len(item.pop('members'))
        candidates=[i for i in items if i['current']['state']=='building']
        # Fine industries first, then broad sectors; each list compares acceleration, never size.
        candidates.sort(key=lambda i:(i['level']!='industry',-i['current']['acceleration'],-i['current']['growth'],i['code']))
        picks=candidates[:c['home_limit']]
        out['markets'][market]=dict(as_of=sessions[-1],classification=freshness(rows),classification_coverage=classification_coverage,
            matched=sum(r.get('group_state')=='matched' for r in rows),target=len(universe),
            industry_members=sum(d['member_count'] for d in items if d['level']=='industry'),
            selected=[i['code'] for i in picks],qualifying=len(candidates),
            unavailable=sum(i['current']['state']=='unavailable' for i in items))
        out['items'].extend(items)
    return out


def cached(root, run, snapshot, groups, sectors, source_hash, overrides=None):
    """Code/input addressed cache: unchanged launches never recalculate these metrics."""
    dependencies=['sector_focus.py','sector_signals.py','sector_observation.py','sector_basis.py']
    key_data=dict(source=source_hash,groups=groups,sectors=[(s['market'],s['group_code'],s['group_name']) for s in sectors],
                  settings=settings(overrides),code={n:hashlib.sha256((Path(__file__).parent/n).read_bytes()).hexdigest() for n in dependencies})
    key=hashlib.sha256(json.dumps(key_data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    folder=Path(root)/'runtime/sector-focus'/key; file=folder/'focus.json'
    with instance_lock(folder):
        if file.exists():
            raw=file.read_bytes(); meta=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('focus_cache_hash_mismatch')
            return json.loads(raw)
        result=build(snapshot,groups,sectors,overrides)
        raw=json.dumps(result,ensure_ascii=False,allow_nan=False).encode()
        atomic_write(file,raw.decode())
        atomic_write(folder/'manifest.json',json.dumps(dict(sha256=hashlib.sha256(raw).hexdigest(),run=str(run),key=key)))
        return result


def screen_groups(entries, universe):
    lookup={(r['market'],r['symbol']):r for r in universe}
    result=[]
    for market in ('KR','US'):
        currency={'KR':'KRW','US':'USD'}[market]
        for route,label in [('pdf_momentum','추세, 조정, 돌파'),('sperandeo','스페란데오 S3·S4'),('base_watch','바닥·눌림 횡보'),('downtrend_approach','하락 추세선 근접')]:
            rows=[]
            for e in entries:
                if e['market']!=market or route not in e.get('origins',[]):continue
                q=lookup.get((market,e['symbol']),{})
                cap=q.get('market_cap')
                valid=(isinstance(cap,(int,float)) and not isinstance(cap,bool) and math.isfinite(cap) and cap>0
                       and q.get('market_cap_currency')==currency and bool(q.get('market_cap_as_of')))
                e['display_cap'] = dict(value=cap if valid else None,currency=currency,
                    as_of=q.get('market_cap_as_of'),basis=q.get('market_cap_basis'))
                rows.append(e)
            rows.sort(key=lambda e:(e['display_cap']['value'] is None,-(e['display_cap']['value'] or 0),e['symbol']))
            known=[r for r in rows if r['display_cap']['value'] is not None]
            result.append(dict(market=market,route=route,label=label,count=len(rows),
                               missing_cap=len(rows)-len(known),top=[r['symbol'] for r in known[:3]],
                               ordered=[r['symbol'] for r in rows]))
    return result

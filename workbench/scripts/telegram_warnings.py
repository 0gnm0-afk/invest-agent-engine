"""Private, finite Telegram delivery for committed completed-session warnings."""
import argparse
import base64
from contextlib import closing
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.request
from urllib.parse import quote

from invest_agent.trading.report_format import money
from invest_agent.trading.runner import atomic_write
from invest_agent.trading.store import instance_lock, BusyError

ROOT = Path(__file__).resolve().parents[1]


def utc():
    return datetime.now(timezone.utc).isoformat()


def protect(*args,**kwargs):
    raise ValueError('credential_storage_not_in_public_package')



def configure(root):
    raise ValueError('configure_an_external_notification_adapter')



def settings(root):
    return None



class DeliveryError(Exception):
    def __init__(self, state, code):
        self.state, self.code = state, code
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api(config,method,payload):
    raise DeliveryError('failed','notification_transport_not_configured')



def message(e):
    if e['kind'] == 'trade_cycle':
        from trade_cycle_alerts import message as cycle_message
        return cycle_message(e)
    body = base_message(e)
    if e.get('cycle_context'):
        from trade_cycle_alerts import context_text
        body += '\n\n' + context_text(e)
    return body


def base_message(e):
    if e['kind'] == 'scenario':
        return scenario_message(e)
    currency = e['currency']
    price = lambda v: money(v, currency)+('원' if currency == 'KRW' else ' USD')
    if e.get('policy') == 'high95-approach-v2':
        return approach_message(e)
    trailing = e['kind'] == 'trailing'
    title = '트레일링 경고선 도달' if trailing else '5일 이동평균선 접촉·이탈'
    difference = (e['close']/e['threshold']-1)*100
    lines = [f'🔔 투자에이전트 | {title}', '',
        f"종목: {e.get('name') or e['symbol']} ({e['symbol']} · {e['market']})",
        f"기준: {e['date']} 해당 시장 정규장 완료 일봉",
        f"종가: {price(e['close'])}",
        f"{'최종 트레일링' if trailing else '당일 5일선'}: {price(e['threshold'])}",
        f'기준선 대비: {difference:+.2f}%', '', f"발생 이유: {e['reason']}"]
    if trailing:
        lines += ['종가가 유지 중인 최종 경고선 이하로 확인됐습니다. 보유 판단과 대응 계획을 점검할 시점입니다.',
                  '경고선 후보 = max(H − 2.5 × ATR14, H × 95%). 이전 경고선과 평단보다 낮추지 않습니다.']
    else:
        lines += ['전일에는 5일선 위였으나, 이번 종가가 당일 종가를 포함한 5일선 이하로 내려왔습니다.',
                  '트레일링 경고와 별개인 단기 흐름 변화 알림입니다.']
    for key, label in [('high','감시 시작 이후 최고가 H'), ('atr','ATR14'), ('line','최종 트레일링'), ('ma5','당일 5일선')]:
        if e.get(key) is not None and key != ('line' if trailing else 'ma5'):
            lines.append(f'{label}: {price(e[key])}')
    lines += ['', '다음 행동: 종목 상세에서 저장 시나리오와 최신 근거를 확인하세요.',
              f"분석 요청: {e['symbol']}의 저장 분석과 논의를 이어 읽고 이번 {'트레일링' if trailing else '5일선'} 경고의 의미와 미정 대응을 함께 검토해줘.",
              '완료 일봉 기준 알림이며 장중 실시간 감시가 아닙니다.',
              '같은 경고는 반복 전송하지 않으며, 조건 회복 후 다시 발생하면 새로 알립니다.',
              '확인: PC 투자 대시보드 → 가격 위험 경고. 자동 주문은 실행하지 않습니다.']
    if e.get('detail_url'): lines.append('종목 상세: '+e['detail_url'])
    return '\n'.join(lines)


def approach_message(e):
    price = lambda v: money(v, e['currency'])+('원' if e['currency']=='KRW' else ' USD')
    label = {'trailing':'트레일링선', 'ma5':'5일 이동평균선', 'ma10':'10일 이동평균선'}[e['kind']]
    situation = '접근' if e['close'] > e['threshold'] else '접촉·이탈'
    lines = [f'🔔 투자에이전트 | {label} 0.5 ATR {situation}', '',
        f"종목: {e.get('name') or e['symbol']} ({e['symbol']} · {e['market']})",
        f"기준: {e['date']} 해당 시장 정규장 완료 일봉",
        f"종가: {price(e['close'])}", f"{label}: {price(e['threshold'])}",
        f"알림 기준: {price(e['alert_threshold'])} 이하 (기준선 + 0.5 × ATR14)",
        f"접근 폭: {price(e['tolerance'])} · ATR14: {price(e['atr'])}",
        f"기준선 대비: {(e['close']/e['threshold']-1)*100:+.2f}%", '',
        '발생 이유: '+e['reason'],
        '트레일링선 = 감시 시작 이후 장중 최고가 H × 95%. 평단 하한은 적용하지 않습니다.',
        f"감시 최고가 H: {price(e['high'])}",
        '다음 행동: 종목 상세에서 저장 시나리오와 최신 근거를 확인하세요.',
        f"분석 요청: {e['symbol']}의 저장 분석·논의를 이어 읽고 이번 {label} 접근 경고와 미정 대응을 함께 검토해줘.",
        '각 선은 독립 알림입니다. 기준선 + 0.5 ATR 위로 회복한 뒤 다시 진입하면 새로 알립니다.',
        '완료 일봉 기준 알림 · 장중 실시간 감시 및 자동 주문 없음.']
    if e.get('detail_url'): lines.append('종목 상세: '+e['detail_url'])
    return '\n'.join(lines)


def current_detail_link(root, event):
    """Use the current local endpoint; never include its token/capabilities in a link."""
    try:
        origin=json.loads((Path(root)/'runtime/dashboard/analysis-endpoint.json').read_text(encoding='utf-8-sig')).get('url')
        if not isinstance(origin,str) or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}',origin): return event.get('detail_url')
        if event.get('kind') == 'trade_cycle':
            return origin+'/#cycle?id='+quote(event['cycle_id'],safe='')
        route=origin+'/#stock/'+quote(event['market'],safe='')+'/'+quote(event['symbol'],safe='')
        return route+('?analysis='+quote(event['case_id'],safe='') if event.get('case_id') else '')
    except (OSError,ValueError,KeyError): return event.get('detail_url')


def scenario_message(e):
    price = lambda v: money(v, e['currency'])+('원' if e['currency']=='KRW' else ' USD')
    event = {'approach':'접근', 'reached':'도달', 'breakout':'돌파', 'breakdown':'이탈'}[e['state']]
    boundary = {'lower':'하단', 'upper':'상단', 'entry':'진입 기준', 'stop':'손절 기준', 'target':'목표 기준', 'line':'기준선', 'zone':'매수 구간'}[e['boundary']]
    purpose = '매수 감시' if e['purpose']=='buy' else '보유 시나리오'
    next_action = {'approach':'저장한 대응 조건과 미정 질문을 확인하세요.',
                   'reached':'저장 시나리오의 도달 조건과 대응을 확인하세요.',
                   'breakout':'상단 돌파의 유지 여부와 저장한 대응을 검토하세요.',
                   'breakdown':'기준선 이탈과 저장한 반증·대응 조건을 검토하세요.'}[e['state']]
    lines = [f'🔔 투자에이전트 | {purpose} {boundary} {event}',
             f"종목: {e.get('name') or e['symbol']} ({e['symbol']} · {e['market']})",
             f"기준: {e['date']} 최신 완료 거래일 종가",
             (f"종가 {price(e['close'])}" if e['boundary']=='zone' else f"종가 {price(e['close'])} · {boundary} {price(e['threshold'])}"),
             f"접근 허용폭: {price(e['tolerance'])} (ATR14 {price(e['atr'])})",
             '다음 행동: '+next_action]
    if e.get('zone_lower') is not None:
        lines.insert(4,'매수 구간: '+price(e['zone_lower'])+' ~ '+price(e['zone_upper']))
    if e.get('confirmed_response'):
        lines.append('사용자 확정 대응: '+e['confirmed_response'][:1000])
    else:
        lines.append('대응 기준 미정: 사용자 판단을 함께 정리하세요.')
    lines.append(f"분석 요청: {e['symbol']}의 저장 분석·논의를 이어 읽고 {e['date']} {boundary} {event} 사건을 기존 시나리오와 대조해 미정 대응을 함께 검토해줘. 분석 키 {e['case_id']}")
    lines.append('종목 상세: '+(e.get('detail_url') or f"PC 투자 대시보드 → {e['symbol']} 종목 상세"))
    lines.append('완료 종가 판정 · 자동 주문 없음')
    return '\n'.join(lines)


def dispatch(root=ROOT, targets=None, *, config=None, sender=None, include_trailing=True, include_scenarios=True, include_cycles=True):
    root = Path(root)
    folder = root/'runtime/telegram-delivery'
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with instance_lock(folder):
            config = config or settings(root)
            if not config:
                return {'state':'disabled'}
            if targets is None:
                from daily_launch import plan_for
                targets = plan_for(datetime.now(timezone.utc))['targets']
            from trailing_warnings import TrailingStore
            # Keep legacy events in storage, but do not send obsolete warning rules.
            events = [e for e in TrailingStore(root/'runtime/trailing-warnings.sqlite3').read()['events']
                      if e.get('policy') == 'high95-approach-v2'] if include_trailing else []
            from scenario_monitor import ScenarioStore
            scenario = ScenarioStore(root/'runtime/chart-workspaces.sqlite3').read() if include_scenarios else {'monitors':[], 'events':[]}
            monitors = {m['id']:m for m in scenario['monitors']}
            for event in scenario['events']:
                monitor = monitors.get(event['monitor_id'])
                if (event.get('test_record') or not monitor or not monitor['active'] or monitor.get('issue')
                        or monitor['revision'] != event['monitor_revision']):
                    continue
                # Preserve the monitor's account scope when matching an approved cycle.
                events.append({**event, 'account':monitor.get('account_alias')})
            from trade_cycle_alerts import delivery_events
            cycle_context_failed = False
            try:
                events = delivery_events(root, events, targets, include_cycles=include_cycles)
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                # A bad new cycle record must not suppress an existing price warning.
                cycle_context_failed = True
            events.sort(key=lambda e:e['created_at'], reverse=True)
            sender = sender or (lambda text: api(config, 'sendMessage', {'chat_id':config['chat'], 'text':text}))
            with closing(sqlite3.connect(folder/'delivery.sqlite3', timeout=30)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,state TEXT,attempts INTEGER,message_id INTEGER,error TEXT,updated_at TEXT)')
                # Interrupted requests may have reached Telegram. Never blindly resend them.
                db.execute("UPDATE deliveries SET state='unknown',error='interrupted_delivery' WHERE state='sending'")
                db.commit()
                for event in reversed(events):
                    if event['created_at'] < config['enabled_at']:
                        continue
                    row = db.execute('SELECT state,attempts FROM deliveries WHERE id=?',(event['id'],)).fetchone()
                    if row and (row[0] != 'failed' or row[1] >= 3):
                        continue
                    attempts = row[1] if row else 0
                    if event['date'] != targets.get(event['market']):
                        db.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?,?)', (event['id'],'expired',attempts,None,'completed_session_changed',utc()))
                        continue
                    body = message({**event,'detail_url':current_detail_link(root,event)})
                    db.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?,?)',(event['id'],'sending',attempts+1,None,None,utc()))
                    db.commit()
                    try:
                        result = sender(body)
                        if not isinstance(result.get('message_id'), int):
                            raise DeliveryError('unknown', 'telegram_response_unconfirmed')
                        state, mid, error = 'sent', result['message_id'], None
                    except DeliveryError as exc:
                        state, mid, error = exc.state, None, exc.code
                    except Exception:
                        state, mid, error = 'unknown', None, 'telegram_response_unconfirmed'
                    db.execute('UPDATE deliveries SET state=?,message_id=?,error=?,updated_at=? WHERE id=?',(state,mid,error,utc(),event['id']))
                    db.commit()
                    if state != 'sent':
                        break  # Avoid flooding a failing/rate-limited endpoint.
                counts = dict(db.execute('SELECT state,count(*) FROM deliveries GROUP BY state'))
            result = dict(state='attention' if counts.get('failed') or counts.get('unknown') else 'ready', counts=counts, checked_at=utc())
            if cycle_context_failed: result['trade_cycle_state'] = 'unavailable'
            atomic_write(folder/'latest.json', json.dumps(result))
            return result
    except BusyError:
        return {'state':'busy'}
    except Exception:
        result = {'state':'unavailable','error':'telegram_delivery_unavailable','checked_at':utc()}
        atomic_write(folder/'latest.json',json.dumps(result))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        if args.configure:
            result = configure(ROOT)
        elif args.check:
            result = {'state':'configured' if settings(ROOT) else 'disabled'}
        else:
            result = dispatch()
    except Exception:
        result = {'state':'unavailable','error':'telegram_configuration_unavailable'}
    print(json.dumps(result))
    return 0 if result['state'] in ('ready','configured','disabled') else 1


if __name__ == '__main__':
    raise SystemExit(main())

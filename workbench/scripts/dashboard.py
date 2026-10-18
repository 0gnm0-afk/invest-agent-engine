"""Loopback dashboard with synthetic defaults; serves only curated views and bundled assets."""
from datetime import datetime, timezone
import argparse
import json
import mimetypes
import secrets
import sqlite3
from http.cookies import SimpleCookie
from daily_launch import plan_for
from public_boundaries import RetryConflict
from public_boundaries import ReviewConflict
from invest_agent.trading.store import instance_lock
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from dashboard_data import ROOT, build, StockSearch
from chart_history import HistoryStore, timeframe
from financial_store import FinancialStore
from financial_summary import selected_stock
from chart_workspace import WorkspaceStore, Conflict, moving_averages
from analysis_service import AnalysisService
from price_events import build as price_events
from analysis_collaboration import Forbidden
from trailing_warnings import TrailingStore


def handler_for(data, stocks, assets, token, workspace_path=None, *, views=None, daily_status=None, daily_retry=None,
                analysis_capabilities=None, analysis_dependencies=None, risk_options=None, offline=False, exclusion_review=None, launch=None):
    views = views if views is not None else {'view': (data, stocks)}
    store = WorkspaceStore(workspace_path or ROOT / "runtime/chart-workspaces.sqlite3")
    histories = (analysis_dependencies['histories'] if analysis_dependencies else
                 HistoryStore((Path(workspace_path).parent if workspace_path else ROOT / 'runtime') / 'chart-history.sqlite3'))
    search_listings = {history.market + ':' + symbol: row
                       for history in histories.saved.markets.values()
                       for symbol, row in getattr(history, 'listings', {}).items()}
    search_cache = [None, None]
    financials = (analysis_dependencies['financials'] if analysis_dependencies else
                  FinancialStore((Path(workspace_path).parent if workspace_path else ROOT / 'runtime') / 'financials'))
    analyses = AnalysisService(store, histories, financials, lambda: views['view'], ROOT if workspace_path is None else Path(workspace_path).parent)
    capabilities = dict(analysis_capabilities or {})
    if set(capabilities) - {'collaboration', 'risk'} or any(not isinstance(v,str) or not v or v == token for v in capabilities.values()) or len(set(capabilities.values())) != len(capabilities):
        raise ValueError('invalid_analysis_capabilities')
    browser_session = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, kind='application/json; charset=utf-8', status=200, cookie=False):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            if cookie:
                self.send_header('Set-Cookie', 'workbench_confirm_' + str(self.server.server_port) + '=' + browser_session + '; HttpOnly; SameSite=Strict; Path=/api/analysis-rpc')
                self.send_header('Set-Cookie', 'workbench_scope_confirm=' + browser_session + '; HttpOnly; SameSite=Strict; Path=/api/market-exclusions')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            data, current_stocks = views['view']
            stocks = {**histories.saved.stocks, **current_stocks}
            expected = '127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Host') != expected or self.headers.get('Origin', 'http://' + expected) != 'http://' + expected:
                return self.send(b'{"error":"local_origin_required"}', status=403)
            path = urllib.parse.urlsplit(self.path).path
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
            try:
                frame = timeframe(query.get('timeframe', ['1d'])[0])
            except ValueError:
                return self.send(b'{"error":"invalid_timeframe"}', status=400)
            if path == '/health':
                return self.send(json.dumps({'app': 'invest-workbench', 'token': token, 'analysis_protocol': 1, 'price_events_protocol': 1, 'pivot_rule': 'weekly-swing-2', 'collaboration_protocol': 1, 'scenario_monitor_protocol': 1, 'trade_cycle_protocol': 1, 'report_reading_protocol': 2, 'offline': offline, 'workspace': str(ROOT)}).encode(), cookie=self.headers.get('Sec-Fetch-Site') == 'same-origin')
            if path == '/api/daily-update':
                return self.send(json.dumps(daily_status() if daily_status else {'enabled':False}, ensure_ascii=False).encode('utf-8'))
            if path == '/api/market-exclusions':
                if query:
                    return self.send(b'{"error":"invalid_exclusion_request"}', status=400)
                try:
                    result = exclusion_review.status() if exclusion_review else {'issues':[], 'job':None, 'busy':False, 'view_ready':False}
                    return self.send(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'))
                except (ValueError, KeyError, TypeError, OSError):
                    return self.send(b'{"error":"exclusion_review_unavailable"}', status=503)
            if path == '/api/trailing':
                target = (Path(workspace_path).parent if workspace_path else ROOT/'runtime')/'trailing-warnings.sqlite3'
                return self.send(json.dumps(TrailingStore(target).public(datetime.now(timezone.utc), plan_for(datetime.now(timezone.utc))['targets']), ensure_ascii=False, allow_nan=False).encode('utf-8'))
            if path == '/api/home':
                return self.send(json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8'))
            if path == '/api/stock-search':
                if set(query) - {'q'} or len(query.get('q', [''])) != 1:
                    return self.send(b'{"error":"invalid_search_query"}', status=400)
                try:
                    phrase = query.get('q', [''])[0]
                    if len(phrase) > 100:
                        raise ValueError('invalid_search_query')
                    cached_source, index = search_cache
                    if cached_source is not current_stocks:
                        index = StockSearch(stocks, search_listings)
                        search_cache[:] = [current_stocks, index]
                    return self.send(json.dumps(index.search(phrase), ensure_ascii=False, allow_nan=False).encode('utf-8'))
                except ValueError:
                    return self.send(b'{"error":"invalid_search_query"}', status=400)
            if path.startswith('/api/financials/'):
                key = urllib.parse.unquote(path[len('/api/financials/'):])
                if key not in stocks:
                    return self.send(b'{"error":"unknown_stock"}', status=404)
                try:
                    return self.send(json.dumps(financials.read(key), ensure_ascii=False, allow_nan=False).encode('utf-8'))
                except (ValueError, OSError):
                    return self.send(b'{"error":"financial_read_failed"}', status=503)
            if path.startswith('/api/chart-state/'):
                key = urllib.parse.unquote(path[len('/api/chart-state/'):])
                if key not in stocks:
                    return self.send(b'{"error":"unknown_stock"}', status=404)
                try:
                    result = store.shared(key, frame)
                    return self.send(json.dumps({**result, 'token': token}, ensure_ascii=False).encode('utf-8'))
                except (sqlite3.Error, OSError, ValueError):
                    return self.send(b'{"error":"workspace_read_failed"}', status=503)
            if path.startswith('/api/averages/'):
                key = urllib.parse.unquote(path[len('/api/averages/'):])
                if key not in stocks:
                    return self.send(b'{"error":"unknown_stock"}', status=404)
                try:
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
                    periods = [int(n) for n in query.get('periods', ['20,50,150,200'])[0].split(',') if n]
                    result = moving_averages(histories.view(stocks[key], frame), periods)
                    return self.send(json.dumps(result, allow_nan=False).encode())
                except (ValueError, TypeError):
                    return self.send(b'{"error":"invalid_periods"}', status=400)
            if path.startswith('/api/chart/'):
                key = urllib.parse.unquote(path[len('/api/chart/'):])
                if key not in stocks:
                    return self.send(b'{"error":"unknown_stock"}', status=404)
                value = histories.view(stocks[key], frame)
                daily = value if frame == '1d' else histories.view(stocks[key], '1d')
                value['price_events'] = price_events(daily, financials.read(key))
                from recent_trendlines import overlay
                entry = next((e for e in data.get('market',{}).get('entries',[]) if e['market']+':'+e['symbol']==key),{})
                signals = entry.get('route_signals',{})
                line = signals.get('downtrend_approach',{}).get('trendline') or signals.get('sperandeo',{}).get('resistance_trendline')
                value['auto_trendlines'] = overlay(daily['bars'],value['bars'],screening_line=line)
                return self.send(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
            if path.startswith('/api/stock/'):
                key = urllib.parse.unquote(path[len('/api/stock/'):])
                value = stocks.get(key)
                if value is None:
                    value = {'state': 'unavailable', 'reason': 'saved_price_series_missing', 'bars': []}
                elif exclusion_review:
                    from market_scope import match
                    excluded = match(value)
                    if excluded:
                        value = {**value,'state':'excluded','reason':excluded['reason'],'bars':[]}
                return self.send(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
            names = {'/report-reader.js':'report-reader.js','/report-reader.css':'report-reader.css','/chart-events.js':'chart-events.js', '/trailing.js':'trailing.js', '/market-exclusions.js':'market-exclusions.js', '/market-exclusions.css':'market-exclusions.css', '/risk-workbench.js': 'risk-workbench.js', '/risk-workbench.css': 'risk-workbench.css', '/collaboration.js': 'collaboration.js', '/analysis.js': 'analysis.js', '/': 'index.html', '/app.js': 'app.js', '/tooltips.js': 'tooltips.js', '/financial-focus.js': 'financial-focus.js', '/financial-focus.css': 'financial-focus.css', '/financials.css': 'financials.css', '/style.css': 'style.css',
                     '/trade-cycle.js':'trade-cycle.js', '/trade-cycle.css':'trade-cycle.css', '/scenario-preview.js':'scenario-preview.js', '/scenario-monitor.js':'scenario-monitor.js', '/scenario-monitor.css':'scenario-monitor.css',
                     '/stock-chart.js': 'stock-chart.js', '/drawing-tools.js': 'drawing-tools.js', '/chart-workspace.js': 'chart-workspace.js', '/chart-tools.css': 'chart-tools.css', '/format.js': 'format.js', '/charts.js': 'charts.js', '/workbench.svg': 'workbench.svg',
                     '/vendor/lightweight-charts.js': 'vendor/lightweight-charts.js',
                     '/vendor/NOTICE': 'vendor/NOTICE', '/vendor/LICENSE': 'vendor/LICENSE'}
            if path not in names:
                return self.send(b'{"error":"not_found"}', status=404)
            file = assets / names[path]
            if not file.is_file():
                return self.send(b'{"error":"asset_missing"}', status=404)
            kind = mimetypes.guess_type(file.name)[0] or 'text/plain'
            if file.suffix == '.js':
                kind = 'text/javascript'
            return self.send(file.read_bytes(), kind + ('; charset=utf-8' if kind.startswith('text/') else ''))

        def do_POST(self):
            data, stocks = views['view']
            stocks = {**histories.saved.stocks, **stocks}
            path = urllib.parse.urlsplit(self.path).path
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
            try:
                frame = timeframe(query.get('timeframe', ['1d'])[0])
            except ValueError:
                return self.send(b'{"error":"invalid_timeframe"}', status=400)
            if path not in ('/api/launch', '/api/daily-update', '/api/market-exclusions') and not path.startswith(('/api/chart-state/', '/api/chart-history/', '/api/financials/', '/api/analysis-rpc')):
                return self.send(b'{"error":"read_only"}', status=405)
            expected = '127.0.0.1:' + str(self.server.server_port)
            supplied_token = self.headers.get('X-Workbench-Token', '')
            role = next((name for name, secret in capabilities.items() if secrets.compare_digest(supplied_token, secret)), None) if path == '/api/analysis-rpc' else None
            if (self.headers.get('Host') != expected or self.headers.get('Origin') != 'http://' + expected
                    or not (role or secrets.compare_digest(supplied_token, token))):
                return self.send(b'{"error":"local_origin_required"}', status=403)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(b'{"error":"json_required"}', status=415)
            key = urllib.parse.unquote(path.rsplit('/', 1)[-1])
            if path not in ('/api/launch', '/api/analysis-rpc', '/api/daily-update', '/api/market-exclusions') and key not in stocks:
                return self.send(b'{"error":"unknown_stock"}', status=404)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 131072:
                    return self.send(b'{"error":"invalid_body_size"}', status=413)
                self.connection.settimeout(5)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except ValueError:
                    if path == '/api/daily-update':
                        return self.send(b'{"error":"invalid_daily_request"}', status=400)
                    raise
                if path == '/api/launch':
                    if (query or not isinstance(body, dict) or set(body) != {'daily_refresh', 'retry_market', 'saved_accounts'}
                            or any(type(v) is not bool for v in body.values())
                            or body['retry_market'] and not body['daily_refresh']):
                        return self.send(b'{"error":"invalid_launch_request"}', status=400)
                    if offline or not launch:
                        return self.send(b'{"error":"launch_unavailable"}', status=409)
                    return self.send(json.dumps(launch(**body), ensure_ascii=False).encode('utf-8'), status=202)
                if path == '/api/daily-update':
                    if (query or not isinstance(body, dict) or set(body) != {'retry', 'attempt_id'}
                            or body['retry'] is not True or not isinstance(body['attempt_id'], str)
                            or not 0 < len(body['attempt_id']) <= 128):
                        return self.send(b'{"error":"invalid_daily_request"}', status=400)
                    if not daily_retry:
                        return self.send(b'{"error":"daily_retry_unavailable"}', status=503)
                    try:
                        result = daily_retry(body['attempt_id'])
                    except RetryConflict:
                        return self.send(b'{"error":"daily_attempt_changed"}', status=409)
                    return self.send(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'), status=202)
                if path == '/api/market-exclusions':
                    cookies = SimpleCookie(self.headers.get('Cookie', ''))
                    candidate = cookies.get('workbench_scope_confirm')
                    if self.headers.get('Sec-Fetch-Site') != 'same-origin' or not candidate or not secrets.compare_digest(candidate.value, browser_session):
                        return self.send(b'{"error":"local_browser_confirmation_required"}', status=403)
                    if query or not isinstance(body,dict):
                        return self.send(b'{"error":"invalid_exclusion_request"}', status=400)
                    action = body.get('action')
                    if not (action == 'approve' and set(body)=={'action','review_id','issue_ids'} or action == 'retry' and set(body)=={'action','job_id'} and isinstance(body['job_id'],str) or action == 'recollect' and set(body)=={'action','review_id','issue_ids','provider'} and body['provider'] in ('toss','nh')):
                        return self.send(b'{"error":"invalid_exclusion_request"}', status=400)
                    if action=='recollect' and offline:
                        return self.send(b'{"error":"offline_collection_disabled"}', status=403)
                    if not exclusion_review:
                        return self.send(b'{"error":"exclusion_review_unavailable"}', status=503)
                    try:
                        if action=='recollect':
                            result=exclusion_review.recollect(body['review_id'],body['issue_ids'],body['provider'])
                        else:
                            result = exclusion_review.approve(body['review_id'],body['issue_ids']) if action=='approve' else exclusion_review.retry(body['job_id'])
                    except ReviewConflict:
                        return self.send(b'{"error":"exclusion_review_changed"}', status=409)
                    except ValueError as exc:
                        known = {'invalid_exclusion_request','invalid_exclusion_selection','empty_market_scope'}
                        code = str(exc) if str(exc) in known else 'exclusion_review_unavailable'
                        return self.send(json.dumps({'error':code}).encode(), status=400 if code in known else 503)
                    except (OSError, KeyError, TypeError):
                        return self.send(b'{"error":"exclusion_review_unavailable"}', status=503)
                    return self.send(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'), status=202)
                if path == '/api/analysis-rpc':
                    if not isinstance(body, dict) or set(body) != {'op', 'arguments'}:
                        raise ValueError('invalid_analysis_request')
                    try:
                        if offline and body['op'] == 'refresh':
                            return self.send(b'{"error":"saved_only_session_refresh_disabled"}', status=403)
                        principal = role or 'legacy'
                        if role is None and self.headers.get('Sec-Fetch-Site') == 'same-origin':
                            cookies = SimpleCookie(self.headers.get('Cookie', ''))
                            candidate = cookies.get('workbench_confirm_' + str(self.server.server_port))
                            if candidate and secrets.compare_digest(candidate.value, browser_session): principal = 'browser'
                        if body['op'] in ('decide', 'holding') and principal != 'browser':
                            raise Forbidden('local_browser_confirmation_required')
                        service = analyses
                        result = service.dispatch(body['op'], body['arguments'], 'http://' + expected,
                                                   principal=principal, session=browser_session if principal == 'browser' else role)
                        return self.send(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'))
                    except Forbidden as exc:
                        return self.send(json.dumps({'error':str(exc)}).encode(), status=403)
                    except Conflict as exc:
                        return self.send(json.dumps({'error':str(exc)}).encode(), status=409)
                    except (ValueError, KeyError, TypeError) as exc:
                        # Validation messages only; never forward arbitrary provider/OS exceptions.
                        message = str(exc) if isinstance(exc, ValueError) and str(exc).replace('_','').isalnum() else 'invalid_analysis_request'
                        return self.send(json.dumps({'error':message}).encode(), status=400)
                if path.startswith('/api/financials/'):
                    if offline:
                        return self.send(b'{"error":"saved_only_session_refresh_disabled"}', status=403)
                    if not isinstance(body, dict) or set(body) != {'refresh'} or type(body['refresh']) is not bool:
                        raise ValueError('invalid_financial_request')
                    stock = {**stocks[key], **histories.view(stocks[key], '1d')}
                    if key == 'KR:005930.KS':
                        _, stock = selected_stock('KR', '005930.KS', histories)
                    result = financials.start(key, stock, refresh=body['refresh'])
                    return self.send(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'), status=202 if result['state'] == 'running' else 200)
                if path.startswith('/api/chart-history/'):
                    if not isinstance(body, dict) or set(body) != {'retry'} or type(body['retry']) is not bool:
                        raise ValueError('invalid_history_request')
                    result = histories.view(stocks[key], frame) if offline else histories.ensure(stocks[key], retry=body['retry'])
                    return self.send(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'))
                if not isinstance(body, dict) or set(body) != {'revision', 'state'}:
                    raise ValueError('invalid_body')
                result = store.shared(key, frame, revision=body['revision'], state=body['state'])
                from scenario_monitor import ScenarioStore
                ScenarioStore(store.path).sync_workspace(key)
                self.send(json.dumps(result).encode())
            except Conflict:
                self.send(b'{"error":"workspace_changed_elsewhere"}', status=409)
            except (ValueError, TypeError, KeyError):
                self.send(b'{"error":"invalid_workspace"}', status=400)
            except (sqlite3.Error, OSError):
                self.send(b'{"error":"workspace_save_failed"}', status=503)
    return Handler



def main():
    from public_demo import main as run
    run()

if __name__ == '__main__':
    main()

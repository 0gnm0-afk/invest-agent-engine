"""Explicit loopback transport adapted from the internal workbench bridge.

No automatic dashboard launch, credential discovery outside the chosen root,
external refresh on startup, redirects, proxies or legacy permission downgrade.
"""
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def endpoint():
    configured = os.environ.get('INVEST_WORKBENCH_ROOT')
    if not configured:
        raise ValueError('workbench_root_required_or_set_synthetic_mode')
    root = Path(configured).resolve(strict=True)
    value = json.loads((root/'runtime/dashboard/analysis-endpoint.json').read_text(encoding='utf-8-sig'))
    url = urllib.parse.urlsplit(value['url'])
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
            or url.path or url.query or url.fragment or url.username or url.password):
        raise ValueError('invalid_local_endpoint')
    with _opener().open(value['url']+'/health', timeout=3) as response:
        health = json.load(response)
    if health.get('analysis_protocol') != 1 or health.get('workspace') != str(root):
        raise ValueError('incompatible_dashboard')
    return {'url': value['url'], 'analysis_capabilities': value.get('analysis_capabilities', {})}


def call(op, arguments):
    if os.environ.get('INVEST_MCP_MODE') == 'synthetic':
        from .synthetic import call as synthetic_call
        return synthetic_call(op, arguments)
    if os.environ.get('INVEST_MCP_MODE', 'local') != 'local':
        raise ValueError('invalid_mcp_mode')
    role = os.environ.get('INVEST_ANALYSIS_ROLE', 'collaboration')
    if role not in ('collaboration', 'risk'):
        raise ValueError('invalid_analysis_role')
    try:
        target = endpoint()
        capability = target['analysis_capabilities'].get(role)
        if not isinstance(capability, str) or not capability:
            raise ValueError('dedicated_role_capability_required')
        request = urllib.request.Request(target['url']+'/api/analysis-rpc',
            data=json.dumps({'op': op, 'arguments': arguments}, allow_nan=False).encode(),
            headers={'Content-Type': 'application/json', 'Origin': target['url'],
                     'X-Workbench-Token': capability})
        with _opener().open(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Backend error bodies may contain private input. Do not echo them.
        raise ValueError('workbench_http_error_'+str(error.code)) from None
    except (OSError, KeyError, json.JSONDecodeError):
        raise ValueError('workbench_connection_or_configuration_error') from None

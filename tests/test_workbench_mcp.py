"""Publication connection checks with fictional data and a local mock backend."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from invest_agent.workbench_mcp import bridge


class MCPConnectionTests(unittest.TestCase):
    def test_explicit_root_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'workbench_root_required'):
                bridge.call('list', {})

    def test_nonloopback_endpoint_rejected_before_request(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'runtime/dashboard/analysis-endpoint.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'url': 'https://example.invalid'}), encoding='utf-8')
            with patch.dict(os.environ, {'INVEST_WORKBENCH_ROOT': folder, 'INVEST_MCP_MODE': 'local'}):
                with patch.object(bridge, '_opener') as opener:
                    with self.assertRaisesRegex(ValueError, 'invalid_local_endpoint'):
                        bridge.call('list', {})
                    opener.assert_not_called()

    def test_role_transport_and_private_error_not_echoed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = str(Path(folder).resolve())
            seen = []
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_GET(self):
                    self.send_response(200); self.end_headers()
                    self.wfile.write(json.dumps({'analysis_protocol': 1, 'workspace': root}).encode())
                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    seen.append((self.headers['X-Workbench-Token'], self.headers['Origin'], body))
                    self.send_response(409 if body['op'] == 'conflict' else 200)
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'fictional-private-detail'} if body['op']=='conflict' else {'ok': True}).encode())
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = 'http://127.0.0.1:'+str(server.server_port)
                path = Path(folder)/'runtime/dashboard/analysis-endpoint.json'
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'url': url, 'analysis_capabilities': {'risk': 'synthetic-capability'}}), encoding='utf-8')
                with patch.dict(os.environ, {'INVEST_WORKBENCH_ROOT': folder, 'INVEST_MCP_MODE': 'local', 'INVEST_ANALYSIS_ROLE': 'risk'}):
                    self.assertEqual(bridge.call('risk_overview', {'account_alias': None}), {'ok': True})
                    self.assertEqual(seen[-1], ('synthetic-capability', url, {'op': 'risk_overview', 'arguments': {'account_alias': None}}))
                    with self.assertRaisesRegex(ValueError, '^workbench_http_error_409$'):
                        bridge.call('conflict', {})
                with patch.dict(os.environ, {'INVEST_WORKBENCH_ROOT': folder, 'INVEST_MCP_MODE': 'local', 'INVEST_ANALYSIS_ROLE': 'collaboration'}):
                    with self.assertRaisesRegex(ValueError, 'dedicated_role_capability_required'):
                        bridge.call('list', {})
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_synthetic_does_not_access_local_backend(self):
        with patch.dict(os.environ, {'INVEST_MCP_MODE': 'synthetic'}):
            with patch.object(bridge, 'endpoint', side_effect=AssertionError('must not access private root')):
                self.assertTrue(bridge.call('list', {})['synthetic'])
                with self.assertRaisesRegex(ValueError, 'does_not_implement'):
                    bridge.call('risk_calculate', {'analysis_key': 'synthetic-analysis'})

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'optional mcp dependency not installed')
    def test_stdio_read_save_reread(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-B', str(root/'examples/workbench_mcp_demo.py')],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['read_save_reread'], 'PASS')


if __name__ == '__main__':
    unittest.main()

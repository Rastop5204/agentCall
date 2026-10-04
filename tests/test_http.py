import http.client
import json
import tempfile
import threading
import unittest

from gateway.store import Store
from gateway.service import Gateway
from gateway.server import make_server


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.app = Gateway(self.store)
        self.server = make_server(self.app, '127.0.0.1', 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.close()
        self.tmp.cleanup()

    def request(self, path, method='GET', data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        h = dict(headers or {})
        body = json.dumps(data) if data is not None else None
        if body is not None:
            h['Content-Type'] = 'application/json'
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        result = (resp.status, dict(resp.getheaders()), json.loads(raw) if raw else None)
        conn.close()
        return result

    def test_health_public_but_config_and_token_require_ui_session(self):
        self.assertEqual(self.request('/api/health')[0], 200)
        self.assertEqual(self.request('/api/config')[0], 401)
        self.assertEqual(self.request('/api/token')[0], 401)
        self.assertEqual(self.request('/api/token', headers={'Authorization': 'Bearer '+self.store.token()})[0], 403)

    def test_csrf_cookie_and_header_both_required(self):
        status, headers, data = self.request('/api/session')
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertEqual(self.request('/api/config', headers={'Cookie': cookie})[0], 401)
        self.assertEqual(self.request('/api/config', headers={'Cookie': cookie, 'X-CSRF-Token': data['csrf_token']})[0], 200)

    def test_cross_origin_and_rebinding_hosts_rejected(self):
        self.assertEqual(self.request('/api/session', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/health', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/session', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_agent_can_create_and_inspect_recorded_failure(self):
        headers = {'Authorization': 'Bearer '+self.store.token(), 'Idempotency-Key': 'request-id-123'}
        status, _, data = self.request('/api/notify', 'POST', {'subject':'Complete', 'body':'Done'}, headers)
        self.assertEqual(status, 409)
        self.assertIn('request_id', data)
        status, _, record = self.request('/api/requests/'+data['request_id'], headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(self.request('/api/records', headers=headers)[0], 403)

    def test_invalid_json_and_invalid_wait_are_structured_errors(self):
        headers = {'Authorization': 'Bearer '+self.store.token()}
        self.assertEqual(self.request('/api/notify','POST',[],headers)[0], 400)
        status, _, data = self.request('/api/requests/missing?wait=nan',headers=headers)
        self.assertEqual(status, 422)
        self.assertEqual(data['error']['code'], 'INVALID_INPUT')


if __name__ == '__main__':
    unittest.main()

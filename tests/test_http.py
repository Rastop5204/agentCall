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

    def test_agent_can_probe_channels_but_cannot_access_wechat_login_or_contacts(self):
        headers = {'Authorization': 'Bearer ' + self.store.token()}
        status, _, data = self.request('/api/channels', headers=headers)
        self.assertEqual(status, 200)
        self.assertFalse(data['available'])
        self.assertNotIn('qr_code', data['wechat'])
        for path in ('/api/wechat', '/api/wechat/contacts'):
            self.assertEqual(self.request(path, headers=headers)[0], 403)
        self.assertEqual(self.request('/api/wechat/login', 'POST', {}, headers)[0], 403)

    def test_wechat_check_failures_are_persistent_records(self):
        _, headers, session = self.request('/api/session')
        ui = {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': session['csrf_token']}
        status, _, data = self.request('/api/wechat/test', 'POST', {}, ui)
        self.assertEqual(status, 200)
        self.assertFalse(data['ok'])
        record = self.store.get(data['record_id'])
        self.assertEqual(record['channel'], 'wechat')
        self.assertEqual(record['status'], 'failed')

    def test_csrf_cookie_and_header_both_required(self):
        status, headers, data = self.request('/api/session')
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertEqual(self.request('/api/config', headers={'Cookie': cookie})[0], 401)
        self.assertEqual(self.request('/api/config', headers={'Cookie': cookie, 'X-CSRF-Token': data['csrf_token']})[0], 200)

    def test_inbox_requires_auth_and_agent_cannot_enable_mode(self):
        agent = {'Authorization': 'Bearer ' + self.store.token()}
        self.assertEqual(self.request('/api/inbox')[0], 401)
        self.assertEqual(self.request('/api/inbox', headers=agent)[0], 200)
        self.assertEqual(self.request('/api/efficient-mode', headers=agent)[0], 200)
        self.assertEqual(self.request('/api/efficient-mode', 'PUT', {'enabled': True}, agent)[0], 403)
        self.assertEqual(self.request('/api/inbox/claim', 'POST', {'consumer_id': 'session-1'}, agent)[0], 409)
        for path in ('/api/inbox?limit=0', '/api/inbox?limit=51', '/api/inbox?limit=nan'):
            self.assertEqual(self.request(path, headers=agent)[0], 422)

    def test_cross_origin_and_rebinding_hosts_rejected(self):
        self.assertEqual(self.request('/api/session', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/health', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/session', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_live_http_long_poll_capture_and_explicit_ack(self):
        from tests.test_channels import FakeWechat
        from gateway.store import stamp
        import time
        self.app.channels.wechat = FakeWechat()
        self.app.save_wechat_config({'enabled': True, 'target_contact_id': 'user-1'})
        _, headers, session = self.request('/api/session')
        ui = {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': session['csrf_token']}
        self.assertEqual(self.request('/api/efficient-mode', 'PUT', {'enabled': True}, ui)[0], 200)
        agent = {'Authorization': 'Bearer ' + self.store.token()}
        data = {'consumer_id': 'codex-session-http', 'wait': 2}
        result = []
        listener = threading.Thread(target=lambda: result.append(self.request('/api/inbox/claim', 'POST', data, agent)))
        listener.start()
        time.sleep(.05)
        self.app.receive_wechat({'message_id': 'http-incoming', 'from_contact_id': 'user-1',
            'account_id': 'bot-1', 'body': '新指令', 'received_at': stamp()})
        listener.join(3)
        self.assertFalse(listener.is_alive())
        self.assertEqual(result[0][0], 200)
        event = result[0][2]['items'][0]
        self.assertEqual(event['body'], '新指令')
        self.assertEqual(self.store.get(event['record_id'])['status'], 'received')
        ack = {'consumer_id': data['consumer_id'], 'ids': [event['id']]}
        self.assertEqual(self.request('/api/inbox/ack', 'POST', ack, agent)[0], 200)
        self.assertEqual(self.store.get(event['record_id'])['status'], 'read')
        self.assertEqual(self.request('/api/inbox', headers=agent)[2]['items'], [])

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

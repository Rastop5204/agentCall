import asyncio
import base64
import collections
import importlib.util
import json
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from gateway.wechat import (FILEHELPER_ID, QR_URL, WechatError, WechatGateway,
                            _WxBotClient, _format_deadline)

def _real_httpx_installed():
    try:
        return importlib.util.find_spec("httpx") is not None
    except (ValueError, ModuleNotFoundError):
        return False  # a bare stub from a sibling test module is in sys.modules


if not _real_httpx_installed():
    import sys
    sys.modules.setdefault("httpx", types.ModuleType("httpx"))


class FakeClient:
    def __init__(self, connection):
        self.account = {"id": "account-1", "name": "Agent 微信"}
        self.started = threading.Event()
        self.disconnected = False
        self.sent = []
        self.probes = 0
        self.fail_send = False
        self.closed = False

    async def run(self, callback):
        self.callback = callback
        callback("login", self.account)
        self.started.set()
        await asyncio.Future()

    async def probe(self):
        self.probes += 1
        if self.disconnected:
            raise ConnectionError("secret service-token")
        return self.account

    async def send(self, text):
        self.sent.append(text)
        if self.fail_send:
            raise ConnectionError("secret service-token")
        return "wx-message-1"

    async def close(self):
        self.closed = True


class DeadlineFormatTests(unittest.TestCase):
    def test_beijing_today_tomorrow_and_beyond(self):
        now = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)  # Beijing 20:00
        self.assertEqual(_format_deadline('2026-10-06T12:52:33.000+00:00', now=now), '今天 20:52:33')
        self.assertEqual(_format_deadline('2026-10-06T16:00:00Z', now=now), '明天 00:00:00')
        self.assertEqual(_format_deadline('2026-10-07T12:52:33Z', now=now), '明天 20:52:33')
        self.assertEqual(_format_deadline('2026-10-09T06:00:00+00:00', now=now), '10月9日 14:00:00')
        self.assertEqual(_format_deadline(None, now=now), '以记录页面为准')
        self.assertEqual(_format_deadline('not-a-time', now=now), '以记录页面为准')


class WechatGatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.messages, self.events = [], []
        self.client = FakeClient({})
        self.gateway = WechatGateway(self.directory.name, self.messages.append,
                                     self.events.append, _client_factory=lambda c: self.client)
        self.config = {"enabled": True}
        self.gateway.start(self.config)
        self.assertTrue(self.client.started.wait(2))

    def tearDown(self):
        self.gateway.stop()
        self.directory.cleanup()

    def record(self):
        return {"id": "req123", "kind": "ask", "subject": "需要选择", "body": "继续还是暂停？",
                "wechat_account_id": "account-1", "target_contact_id": "filehelper",
                "deadline_at": "2026-10-05T01:02:03Z", "agent_name": "Codex"}

    def test_status_does_not_probe_network_and_does_not_expose_secrets(self):
        status = self.gateway.status()
        self.assertEqual(self.client.probes, 0)
        self.assertNotIn("secret", str(status))
        status["account"]["id"] = "changed"
        self.assertEqual(self.gateway.status()["account"]["id"], "account-1")

    def test_check_probes_transport_and_hides_connection_secrets(self):
        self.assertTrue(self.gateway.check()["available"])
        self.assertEqual(self.client.probes, 1)
        self.client.disconnected = True
        status = self.gateway.check()
        self.assertFalse(status["available"])
        self.assertFalse(status["logged_in"])
        self.assertNotIn("secret", str(status))

    def test_reply_processing_error_does_not_blame_disk_or_log_message(self):
        def fail(_):
            raise ValueError('private reply content')
        self.gateway.on_message = fail
        with self.assertLogs('gateway.wechat', level='ERROR') as logs:
            self.gateway._event('message', {})
        self.assertNotIn('private reply content', str(logs.output))
        self.assertIn('ValueError', str(logs.output))
        error = self.gateway.status()['error']
        self.assertEqual(error['code'], 'WECHAT_REPLY_SAVE_FAILED')
        self.assertIn('内部错误', error['hint'])

    def test_send_checks_fresh_health_and_embeds_correlation_and_deadline(self):
        record = self.record()
        result = self.gateway.send(record)
        self.assertEqual(result["message_id"], "wx-message-1")
        self.assertEqual(self.client.probes, 2)
        text = self.client.sent[0]
        self.assertIn("Codex · Request\n", text)
        self.assertIn("[AC:req123]", text)
        self.assertIn("回复截止：" + _format_deadline(record["deadline_at"]), text)
        self.assertIn("继续还是暂停？", text)
        self.assertNotIn("请使用引用回复", text)

    def test_send_appends_quote_hint_only_with_concurrent_waiting_asks(self):
        self.gateway.send(self.record(), concurrent_waiting=True)
        text = self.client.sent[0]
        self.assertIn("当前有多条消息等待回复，请使用引用回复", text)
        notify = self.record()
        notify["kind"] = "notify"
        self.gateway.send(notify, concurrent_waiting=True)
        self.assertNotIn("回复截止", self.client.sent[1])
        self.assertNotIn("请使用引用回复", self.client.sent[1])
        self.assertIn("Codex · Notice", self.client.sent[1])

    def test_send_note_probes_account_and_delivers_one_line(self):
        self.gateway.send_note("account-1", FILEHELPER_ID, "Codex 已读")
        self.assertEqual(self.client.sent[-1], "Codex 已读")
        with self.assertRaises(WechatError) as raised:
            self.gateway.send_note("other-account", FILEHELPER_ID, "x")
        self.assertTrue(raised.exception.safe_to_fallback)

    def test_send_note_refuses_to_block_the_wechat_event_thread(self):
        self.gateway._thread = threading.current_thread()
        with self.assertRaises(WechatError) as raised:
            self.gateway.send_note("account-1", FILEHELPER_ID, "x")
        self.assertEqual(raised.exception.code, "WECHAT_FEEDBACK_THREAD")
        self.assertEqual(self.client.sent, [])

    def test_no_send_to_target_bound_to_another_account(self):
        record = self.record()
        record["wechat_account_id"] = "other-account"
        with self.assertRaises(WechatError) as raised:
            self.gateway.send(record)
        self.assertTrue(raised.exception.safe_to_fallback)
        self.assertEqual(raised.exception.code, "WECHAT_ACCOUNT_CHANGED")
        self.assertEqual(self.client.sent, [])

    def test_record_from_the_contact_based_transport_is_rejected_safely(self):
        record = self.record()
        record["target_contact_id"] = "wxid_old_contact"
        with self.assertRaises(WechatError) as raised:
            self.gateway.send(record)
        self.assertTrue(raised.exception.safe_to_fallback)
        self.assertEqual(raised.exception.code, "WECHAT_ACCOUNT_CHANGED")
        self.assertEqual(self.client.sent, [])

    def test_account_change_during_preflight_is_caught_before_send(self):
        record = self.record()

        async def switching_probe():
            self.client.account = {"id": "new-account", "name": "不同账号"}
            return self.client.account
        self.client.probe = switching_probe
        self.gateway._event("login", self.client.account)
        with self.assertRaises(WechatError) as raised:
            self.gateway.send(record)
        self.assertEqual(raised.exception.code, "WECHAT_ACCOUNT_CHANGED")
        self.assertTrue(raised.exception.safe_to_fallback)
        self.assertEqual(self.client.sent, [])

    def test_send_failure_is_uncertain_and_must_not_fallback(self):
        self.client.fail_send = True
        with self.assertRaises(WechatError) as raised:
            self.gateway.send(self.record())
        self.assertEqual(raised.exception.code, "WECHAT_SEND_UNCERTAIN")
        self.assertFalse(raised.exception.safe_to_fallback)
        self.assertNotIn("secret", str(raised.exception.as_dict()))

    def test_shutdown_after_submission_does_not_allow_email_fallback(self):
        started = threading.Event()
        failures = []
        async def pending_send(text):
            started.set()
            await asyncio.Future()
        self.client.send = pending_send
        def submit():
            try:
                self.gateway.send(self.record())
            except WechatError as exc:
                failures.append(exc)
        worker = threading.Thread(target=submit)
        worker.start()
        self.assertTrue(started.wait(2))
        self.gateway.stop()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "WECHAT_SEND_UNCERTAIN")
        self.assertFalse(failures[0].safe_to_fallback)

    def test_disconnected_cached_login_never_sends(self):
        self.client.disconnected = True
        with self.assertRaises(WechatError) as raised:
            self.gateway.send(self.record())
        self.assertTrue(raised.exception.safe_to_fallback)
        self.assertEqual(self.client.sent, [])

    def test_waiting_keeps_qr_but_expiry_clears_it(self):
        self.gateway._event("logout", {})
        self.assertIsNone(self.gateway.status()["account"])
        self.gateway._event("scan", {"qr_code": QR_URL.format(uuid="uuid-1"), "qr_status": "Waiting"})
        self.assertEqual(self.gateway.status()["state"], "awaiting_scan")
        self.assertFalse(self.gateway.status()["logged_in"])
        self.assertIn("uuid-1", self.gateway.status()["qr_code"])
        self.assertNotIn("uuid-1", str(self.events))
        self.gateway._event("scan", {"qr_code": None, "qr_status": "Expired"})
        self.assertIsNone(self.gateway.status()["qr_code"])

    def test_cached_scanned_event_requires_the_current_waiting_qr(self):
        self.gateway._event('logout', {})
        self.gateway._event('scan', {'qr_code': 'old', 'qr_status': 'Scanned'})
        self.assertIsNone(self.gateway.status()['qr_code'])
        self.gateway._event('scan', {'qr_code': 'new', 'qr_status': 'Waiting'})
        self.gateway._event('scan', {'qr_code': 'old', 'qr_status': 'Scanned'})
        self.assertEqual(self.gateway.status()['qr_status'], 'Waiting')
        self.gateway._event('scan', {'qr_code': 'new', 'qr_status': 'Scanned'})
        self.assertEqual(self.gateway.status()['qr_status'], 'Scanned')

    def test_explicit_refresh_deletes_session_and_ignores_previous_client_events(self):
        old_callback = self.client.callback
        state_path = Path(self.directory.name) / "wxbot" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text('{}', encoding="utf-8")
        replacement = FakeClient({})
        connections = []
        def factory(connection):
            connections.append(connection.copy())
            return replacement
        self.gateway._factory = factory
        self.gateway.start(self.config, reset_login=True)
        self.assertTrue(replacement.started.wait(2))
        self.assertFalse(state_path.exists())  # fresh scan drops the saved session
        self.gateway._event('scan', {'qr_code': 'new', 'qr_status': 'Waiting'})
        old_callback('scan', {'qr_code': 'old', 'qr_status': 'Scanned'})
        self.assertEqual(self.gateway.status()['qr_code'], 'new')
        self.assertEqual(self.gateway.status()['qr_status'], 'Waiting')
        self.assertEqual(connections[0]['state_path'], state_path)

    def test_restart_with_same_config_keeps_login_session(self):
        self.gateway.start(dict(self.config))
        self.assertFalse(self.client.closed)
        self.assertEqual(self.gateway.status()["account"]["id"], "account-1")

    def test_stop_closes_client_without_logging_out(self):
        self.gateway.stop()
        self.assertTrue(self.client.closed)
        self.assertEqual(self.gateway.status()["state"], "disabled")


class FakeBot:
    """Scripted stand-in for the vendored WeChatHelperBot."""

    def __init__(self, *, logged_in=False, has_auth=False, user_name="wxid_self",
                 uuid="uuid-1", messages=(), send_result="srv-1", login_codes=(),
                 synckey=None):
        self.is_logged_in = logged_in
        self._has_auth_value = has_auth
        self.user_name = user_name if logged_in else ""
        self.synckey = synckey if synckey is not None else (
            {"Count": 1, "List": [{"Key": 1, "Val": 5}]} if logged_in else {"Count": 0, "List": []})
        self.uuid = uuid
        self.messages = list(messages)
        self.send_result = send_result
        self.login_codes = list(login_codes)
        self.last_login_message = "need_qr"
        self.started = self.stopped = False
        self.saved = 0
        self.resets = 0
        self.sent = []
        self.login_avatar = ""
        self.avatar_fetch_result = None  # None or (bytes, content_type)

    def _has_auth(self):
        return self._has_auth_value

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def save_session(self):
        self.saved += 1

    async def ensure_login_uuid(self):
        if not self.uuid:
            self.uuid = "uuid-2"
        return self.uuid

    async def check_login_status(self, poll=True):
        if self.login_codes:
            code = self.login_codes.pop(0)
            self.last_login_message = {408: "qr_wait_scan", 201: "scanned_wait_confirm",
                                       200: "logged_in", 400: "qr_expired"}[code]
            if code == 200:
                self.is_logged_in, self._has_auth_value = True, True
                self.user_name = "wxid_self"
                self.synckey = {"Count": 1, "List": [{"Key": 1, "Val": 9}]}
            elif code == 400:
                self.uuid = ""
        return self.is_logged_in

    async def get_latest_messages(self, limit=50):
        messages, self.messages = self.messages[:limit], self.messages[limit:]
        return messages

    async def send_text(self, message):
        self.sent.append(message)
        return self.send_result

    async def fetch_self_avatar(self):
        return self.avatar_fetch_result

    def reset_session(self, delete_file=False):
        self.resets += 1
        self.is_logged_in = False
        self._has_auth_value = False
        self.user_name = ""
        self.login_avatar = ""
        self.synckey = {"Count": 0, "List": []}


def adapter_client(bot):
    client = object.__new__(_WxBotClient)
    client.bot = bot
    client._callback = lambda event, payload: None
    client._seen, client._seen_set = collections.deque(maxlen=1000), set()
    client._sent_texts = collections.deque(maxlen=50)
    client._last_uuid, client._scan_state = None, None
    client._avatar_fetch = None
    return client


class WxBotAdapterTests(unittest.IsolatedAsyncioTestCase):
    def client(self, **kwargs):
        bot = FakeBot(**kwargs)
        events = []

        client = adapter_client(bot)
        client._callback = lambda event, payload: events.append((event, payload))
        return client, bot, events

    async def test_restored_session_emits_login_immediately(self):
        client, bot, events = self.client(logged_in=True, has_auth=True)
        run = asyncio.create_task(client.run(client._callback))
        try:
            await asyncio.wait_for(self._saw(events, 'login'), 2)
            self.assertEqual(events[0][1], {"id": "wxid_self", "name": ""})
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    async def test_kicked_session_verifies_then_resets_and_falls_back_to_qr(self):
        # First wire check (408 → still not logged in) confirms the kick;
        # the QR flow then completes a fresh login (200).
        client, bot, events = self.client(logged_in=False, has_auth=True,
                                          login_codes=[408, 200])
        run = asyncio.create_task(client.run(client._callback))
        try:
            await asyncio.wait_for(self._saw(events, 'logout'), 2)
            self.assertEqual(bot.resets, 1)
            await asyncio.wait_for(self._saw(events, 'login'), 2)
            qr_events = [p for e, p in events if e == 'scan' and p['qr_status'] == 'Waiting']
            self.assertTrue(qr_events and 'uuid-1' in qr_events[0]['qr_code'])
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    async def test_partial_login_with_fresh_auth_recovers_without_reset(self):
        class PartialBot(FakeBot):
            async def check_login_status(self, poll=True):
                self.is_logged_in, self.user_name = True, "wxid_self"
                self.synckey = {"Count": 1, "List": [{"Key": 1, "Val": 9}]}
                return True
        bot = PartialBot(logged_in=False, has_auth=True)
        events = []
        client = adapter_client(bot)
        client._callback = lambda event, payload: events.append((event, payload))
        run = asyncio.create_task(client.run(client._callback))
        try:
            await asyncio.wait_for(self._saw(events, 'login'), 2)
            self.assertEqual([e for e, _ in events if e == 'logout'], [])
            self.assertEqual(bot.resets, 0)  # fresh credentials were kept
            self.assertGreaterEqual(bot.saved, 1)
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    async def test_restored_login_without_synckey_starts_fresh_qr(self):
        client, bot, events = self.client(logged_in=True, has_auth=True,
                                          synckey={"Count": 0, "List": []},
                                          login_codes=[200])
        run = asyncio.create_task(client.run(client._callback))
        try:
            await asyncio.wait_for(self._saw(events, 'login'), 2)
            self.assertEqual(events[0][0], 'scan')  # no premature login event
            self.assertEqual(events[0][1]['qr_status'], 'Waiting')
            self.assertEqual(bot.resets, 1)
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    async def test_scan_lifecycle_waiting_scanned_login(self):
        client, bot, events = self.client(login_codes=[408, 201, 200])
        run = asyncio.create_task(client.run(client._callback))
        try:
            await asyncio.wait_for(self._saw(events, 'login'), 2)
            waiting = [p for e, p in events if e == 'scan' and p['qr_status'] == 'Waiting']
            scanned = [p for e, p in events if e == 'scan' and p['qr_status'] == 'Scanned']
            self.assertEqual(len(waiting), 1)
            self.assertEqual(len(scanned), 1)  # Scanned is emitted once per uuid
            self.assertIn('uuid-1', waiting[0]['qr_code'])
            self.assertGreaterEqual(bot.saved, 1)  # session persisted on login
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    async def test_expired_qr_clears_and_next_cycle_fetches_a_new_one(self):
        client, bot, events = self.client(login_codes=[400, 200])
        run = asyncio.create_task(client.run(client._callback))
        try:
            await asyncio.wait_for(self._saw(events, 'login'), 2)
            expired = [p for e, p in events if e == 'scan' and p['qr_status'] == 'Expired']
            self.assertEqual(expired, [{'qr_code': None, 'qr_status': 'Expired'}])
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    async def test_receive_phase_forwards_messages_and_persists_synckey(self):
        messages = [
            {"id": "in-1", "type": "text", "text": "我选 A [AC:req123]", "is_mine": True,
             "create_time": 1791162000},
            {"id": "in-2", "type": "text", "text": "「原话」<br/>- - - - - - - - - - - - - - -<br/>就选 B",
             "is_mine": True, "create_time": 1791162001, "reference_id": "87318229"},
            {"id": "in-3", "type": "text", "text": "系统通知", "is_mine": False, "create_time": 1791162002},
            {"id": "in-4", "type": "image", "text": "[Image]", "is_mine": True, "create_time": 1791162003},
            {"id": "in-5", "type": "text", "text": "无时间戳", "is_mine": True, "create_time": 0},
        ]
        client, bot, events = self.client(logged_in=True, has_auth=True, messages=messages)
        run = asyncio.create_task(client.run(client._callback))
        try:
            await asyncio.wait_for(self._saw(events, 'message'), 2)
            forwarded = [p for e, p in events if e == 'message']
            self.assertEqual([m['message_id'] for m in forwarded], ['in-1', 'in-2'])
            first = forwarded[0]
            self.assertEqual(first['from_contact_id'], FILEHELPER_ID)
            self.assertEqual(first['account_id'], 'wxid_self')
            self.assertEqual(first['body'], '我选 A [AC:req123]')
            self.assertEqual(first['received_at'], '2026-10-05T01:00:00Z')
            second = forwarded[1]
            self.assertEqual(second['reference_id'], '87318229')
            self.assertIn('\n- - - - - - - - - - - - - - -\n', second['body'])
            self.assertGreaterEqual(bot.saved, 1)
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    async def test_echo_guard_drops_own_send_returning_under_a_fresh_id(self):
        client, bot, events = self.client()
        self.assertEqual(await client.send("你好，文件传输助手 [AC:req1]"), "srv-1")
        client._forward({"id": "echo-9", "type": "text", "is_mine": True,
                         "text": "你好，文件传输助手 [AC:req1]", "create_time": 1791162000})
        self.assertEqual([e for e, _ in events if e == 'message'], [])
        client._forward({"id": "in-7", "type": "text", "is_mine": True,
                         "text": "收到", "create_time": 1791162000})
        self.assertEqual(len([e for e, _ in events if e == 'message']), 1)

    async def test_send_persists_msgid_and_dedups_it_on_receive(self):
        client, bot, events = self.client()
        await client.send("外发内容")
        client._forward({"id": "srv-1", "type": "text", "is_mine": True,
                         "text": "其他文字", "create_time": 1791162000})
        self.assertEqual([e for e, _ in events if e == 'message'], [])

    async def test_logout_during_receive_resets_session(self):
        class KickedBot(FakeBot):
            async def get_latest_messages(self, limit=50):
                self.is_logged_in = False
                return []
        client, bot, events = self.client(logged_in=True, has_auth=True)
        client.bot = KickedBot(logged_in=True, has_auth=True)
        run = asyncio.create_task(client.run(client._callback))
        try:
            await asyncio.wait_for(self._saw(events, 'logout'), 2)
            self.assertEqual(client.bot.resets, 1)
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)

    async def test_avatar_from_scan_capture_shows_until_login_state_is_lost(self):
        client, bot, events = self.client()
        self.assertEqual(await client.avatar(), {"account_id": None, "image": None})
        bot.login_avatar = "data:image/jpeg;base64,QUFB"
        # 待确认阶段（未登录）即可显示
        self.assertEqual(await client.avatar(), {"account_id": None, "image": "data:image/jpeg;base64,QUFB"})
        # 登录后沿用捕获值，不触发拉取
        bot.is_logged_in, bot.user_name, bot._has_auth_value = True, "wxid_self", True
        self.assertEqual(await client.avatar(), {"account_id": "wxid_self", "image": "data:image/jpeg;base64,QUFB"})
        self.assertIsNone(bot.avatar_fetch_result)
        # 登录状态失效后头像清空
        bot.reset_session()
        self.assertEqual(await client.avatar(), {"account_id": None, "image": None})

    async def test_restored_session_fetches_avatar_once_and_caches(self):
        client, bot, events = self.client(logged_in=True, has_auth=True)
        payload = b"\xff\xd8fake"
        bot.avatar_fetch_result = (payload, "image/jpeg")
        expected = {"account_id": "wxid_self",
                    "image": "data:image/jpeg;base64," + base64.b64encode(payload).decode()}
        self.assertEqual(await client.avatar(), expected)
        bot.avatar_fetch_result = None  # 缓存期内不再发请求
        self.assertEqual(await client.avatar(), expected)

    async def _saw(self, events, kind):
        while not any(event == kind for event, _ in events):
            await asyncio.sleep(0.01)


@unittest.skipUnless(_real_httpx_installed(),
        "protocol integration runs wherever real httpx is installed")
class WxBotProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_login_send_receive_logout_over_mock_wire(self):
        import httpx
        from gateway.wxbot import direct_bot

        state = {"login_polls": 0, "sync_has_msg": True, "sync_done": False, "sent": []}
        entry = "szfilehelper.weixin.qq.com"

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/jslogin":
                return httpx.Response(200, text='window.QRLogin.code = 200; window.QRLogin.uuid = "mock-uuid-1";')
            if path == "/cgi-bin/mmwebwx-bin/login":
                state["login_polls"] += 1
                if state["login_polls"] == 1:
                    return httpx.Response(200, text="window.code=408;")
                if state["login_polls"] == 2:
                    return httpx.Response(200, text="window.code=201; window.userAvatar = '';")
                redirect = f"https://{entry}/cgi-bin/mmwebwx-bin/webwxnewloginpage?ticket=t1&uuid=mock-uuid-1&lang=zh_CN&scan=s1"
                return httpx.Response(200, text=f'window.code=200;window.redirect_uri="{redirect}";')
            if path == "/cgi-bin/mmwebwx-bin/webwxnewloginpage":
                xml = ("<error><ret>0</ret><message></message><skey>@sk1</skey>"
                       "<wxsid>sid1</wxsid><wxuin>4242</wxuin><pass_ticket>pt1</pass_ticket>"
                       "<isgrayscale>1</isgrayscale></error>")
                return httpx.Response(200, text=xml, headers={"content-type": "application/xml"})
            if path == "/cgi-bin/mmwebwx-bin/webwxinit":
                return httpx.Response(200, json={
                    "BaseResponse": {"Ret": 0},
                    "User": {"UserName": "wxid_mock_self", "Uin": 4242, "NickName": "测试账号"},
                    "SyncKey": {"Count": 1, "List": [{"Key": 1, "Val": 100}]},
                })
            if path == "/cgi-bin/mmwebwx-bin/synccheck":
                if state["sync_has_msg"]:
                    return httpx.Response(200, text='window.synccheck={retcode:"0",selector:"2"};')
                return httpx.Response(200, text='window.synccheck={retcode:"0",selector:"0"};')
            if path == "/cgi-bin/mmwebwx-bin/webwxsync":
                if state["sync_done"]:
                    return httpx.Response(200, json={"BaseResponse": {"Ret": 0},
                                                     "SyncKey": {"Count": 1, "List": [{"Key": 1, "Val": 200}]}})
                state["sync_done"] = True
                refer = ("<appmsg><title>收到</title><type>57</type>"
                         "<refermsg><svrid>88001</svrid><displayname>Agent</displayname>"
                         "<content>请确认</content></refermsg></appmsg>")
                return httpx.Response(200, json={
                    "BaseResponse": {"Ret": 0},
                    "SyncKey": {"Count": 1, "List": [{"Key": 1, "Val": 200}]},
                    "AddMsgList": [
                        {"MsgId": "mock-in-1", "MsgType": 1, "Content": "好的 [AC:req-m1]",
                         "FromUserName": "wxid_mock_self", "ToUserName": "filehelper",
                         "CreateTime": 1791162000},
                        {"MsgId": "mock-in-2", "MsgType": 49, "AppMsgType": 57, "Content": refer,
                         "FromUserName": "wxid_mock_self", "ToUserName": "filehelper",
                         "CreateTime": 1791162001},
                    ],
                })
            if path == "/cgi-bin/mmwebwx-bin/webwxsendmsg":
                body = json.loads(request.content)
                state["sent"].append(body)
                return httpx.Response(200, json={"BaseResponse": {"Ret": 0}, "MsgID": "mock-srv-1",
                                                 "LocalID": body["Msg"]["LocalID"]})
            return httpx.Response(404, text="unexpected " + path)

        transport = httpx.MockTransport(handler)
        fake_httpx = types.SimpleNamespace(
            Timeout=httpx.Timeout,
            AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        )
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state_path = Path(tmp.name) / "state.json"
        events = []
        with patch.object(direct_bot, "httpx", fake_httpx):
            client = _WxBotClient({"state_path": state_path})
            run = asyncio.create_task(client.run(lambda event, payload: events.append((event, payload))))
            try:
                await self._saw(events, "message", timeout=10)
                waiting = [p for e, p in events if e == "scan" and p["qr_status"] == "Waiting"]
                scanned = [p for e, p in events if e == "scan" and p["qr_status"] == "Scanned"]
                self.assertTrue(waiting and "mock-uuid-1" in waiting[0]["qr_code"])
                self.assertEqual(len(scanned), 1)
                login = next(p for e, p in events if e == "login")
                self.assertEqual(login, {"id": "wxid_mock_self", "name": ""})
                messages = [p for e, p in events if e == "message"]
                self.assertEqual(messages[0], {"message_id": "mock-in-1",
                    "from_contact_id": "filehelper", "account_id": "wxid_mock_self",
                    "body": "好的 [AC:req-m1]", "received_at": "2026-10-05T01:00:00Z"})
                self.assertEqual(messages[1]["message_id"], "mock-in-2")
                self.assertEqual(messages[1]["reference_id"], "88001")
                self.assertIn("「Agent：请确认」", messages[1]["body"])
                self.assertIn("收到", messages[1]["body"])
                # Sends multiplex onto the receiver loop's client.
                self.assertEqual(await client.send("外发通知 [AC:req-m2]"), "mock-srv-1")
                self.assertEqual(len(state["sent"]), 1)
                self.assertEqual(state["sent"][0]["Msg"]["Content"], "外发通知 [AC:req-m2]")
                self.assertEqual(state["sent"][0]["Msg"]["ToUserName"], "filehelper")
                # The echo of our own send arrives under the same server MsgID.
                state["sync_has_msg"], state["sync_done"] = True, False
                # (no further webwxsync AddMsgList for it — server MsgIDs are
                # filtered by the adapter's remembered send ids)
                self.assertTrue(state_path.exists())  # session persisted
            finally:
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
                await client.close()
        self.assertTrue(events)

    async def _saw(self, events, kind, timeout=5):
        deadline = time.monotonic() + timeout
        while not any(event == kind for event, _ in events):
            if time.monotonic() > deadline:
                self.fail(f"missing {kind} in {events}")
            await asyncio.sleep(0.01)


if __name__ == "__main__":
    unittest.main()

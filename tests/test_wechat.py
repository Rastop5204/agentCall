import asyncio
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from gateway.wechat import WechatError, WechatGateway, _SDKClient, _connection, _format_deadline


class FakeClient:
    def __init__(self, connection):
        self.account = {"id": "account-1", "name": "Agent 微信"}
        self.started = threading.Event()
        self.disconnected = False
        self.sent = []
        self.probes = 0
        self.fail_send = False
        self.fail_contact = False
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

    async def contacts(self, query, limit):
        return [{"id": "friend-1", "name": "用户", "alias": "目标"}][:limit]

    async def contact(self, contact_id):
        if contact_id != "friend-1" or self.fail_contact:
            raise WechatError("WECHAT_CONTACT_NOT_FOUND", "联系人不存在。", safe_to_fallback=True)
        return {"id": "friend-1", "name": "用户", "alias": "目标"}

    async def send(self, contact_id, text):
        self.sent.append((contact_id, text))
        if self.fail_send:
            raise ConnectionError("secret service-token")
        return "wx-message-1"

    async def close(self):
        self.closed = True


class DeadlineFormatTests(unittest.TestCase):
    def test_beijing_today_tomorrow_and_beyond(self):
        now = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)  # Beijing 20:00
        self.assertEqual(_format_deadline('2026-10-06T12:52:33.000+00:00', now=now), '今天 20:52:33')
        self.assertEqual(_format_deadline('2026-10-06T16:00:00+00:00', now=now), '明天 00:00:00')
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
        self.config = {"enabled": True, "mode": "external", "service_endpoint": "grpcs://wx.example.com:8788",
                       "service_token": "secret", "target_contact_id": "friend-1", "account_id": "account-1"}
        self.gateway.start(self.config)
        self.assertTrue(self.client.started.wait(2))

    def tearDown(self):
        self.gateway.stop()
        self.directory.cleanup()

    def record(self):
        return {"id": "req123", "kind": "ask", "subject": "需要选择", "body": "继续还是暂停？",
                "wechat_account_id": "account-1", "target_contact_id": "friend-1",
                "deadline_at": "2026-10-05T01:02:03Z", "agent_name": "Codex"}

    def test_every_check_probes_transport_even_with_cached_login(self):
        self.assertTrue(self.gateway.check()["available"])
        self.assertTrue(self.gateway.check()["available"])
        self.assertEqual(self.client.probes, 2)
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

    def test_login_rejection_remains_visible_after_failed_health_probes(self):
        self.client.account = None
        self.gateway._event('login_failed', {'return_code': '1203'})
        self.gateway._event('error', {})
        status = self.gateway.check()
        self.assertFalse(status['logged_in'])
        self.assertIsNone(status['qr_code'])
        self.assertEqual(status['error']['code'], 'WECHAT_LOGIN_REJECTED')
        self.assertIn('1203', status['error']['message'])
        self.client.disconnected = True
        self.assertEqual(self.gateway.check()['error']['code'], 'WECHAT_LOGIN_REJECTED')
        self.assertTrue(any(e['type'] == 'login_failed' for e in self.events))

    def test_status_does_not_probe_network_and_does_not_expose_secrets(self):
        status = self.gateway.status()
        self.assertEqual(self.client.probes, 0)
        self.assertNotIn("service_token", status)
        status["account"]["id"] = "changed"
        self.assertEqual(self.gateway.status()["account"]["id"], "account-1")

    def test_send_checks_fresh_health_and_embeds_correlation_and_deadline(self):
        record = self.record()
        result = self.gateway.send(record)
        self.assertEqual(result["message_id"], "wx-message-1")
        self.assertEqual(self.client.probes, 2)
        target, text = self.client.sent[0]
        self.assertEqual(target, "friend-1")
        self.assertIn("Codex · Request\n", text)
        self.assertIn("[AC:req123]", text)
        self.assertIn("回复截止：" + _format_deadline(record["deadline_at"]), text)
        self.assertIn("继续还是暂停？", text)
        self.assertNotIn("请使用引用回复", text)

    def test_send_appends_quote_hint_only_with_concurrent_waiting_asks(self):
        self.gateway.send(self.record(), concurrent_waiting=True)
        text = self.client.sent[0][1]
        self.assertIn("当前有多条消息等待回复，请使用引用回复", text)
        notify = self.record()
        notify["kind"] = "notify"
        self.gateway.send(notify, concurrent_waiting=True)
        self.assertNotIn("回复截止", self.client.sent[1][1])
        self.assertNotIn("请使用引用回复", self.client.sent[1][1])
        self.assertIn("Codex · Notice", self.client.sent[1][1])

    def test_send_note_probes_account_and_delivers_one_line(self):
        self.gateway.send_note("account-1", "friend-1", "Codex 已读")
        self.assertEqual(self.client.sent[-1], ("friend-1", "Codex 已读"))
        with self.assertRaises(WechatError) as raised:
            self.gateway.send_note("other-account", "friend-1", "x")
        self.assertTrue(raised.exception.safe_to_fallback)

    def test_send_note_refuses_to_block_the_wechat_event_thread(self):
        self.gateway._thread = threading.current_thread()
        with self.assertRaises(WechatError) as raised:
            self.gateway.send_note("account-1", "friend-1", "x")
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

    def test_missing_contact_is_safe_to_fallback_without_send(self):
        self.client.fail_contact = True
        with self.assertRaises(WechatError) as raised:
            self.gateway.send(self.record())
        self.assertTrue(raised.exception.safe_to_fallback)
        self.assertEqual(self.client.sent, [])

    def test_account_change_during_contact_lookup_is_caught_before_send(self):
        async def changing_contact(contact_id):
            self.client.account = {"id": "new-account", "name": "不同账号"}
            self.gateway._event("login", self.client.account)
            return {"id": contact_id, "name": "朋友", "alias": ""}
        self.client.contact = changing_contact
        with self.assertRaises(WechatError) as raised:
            self.gateway.send(self.record())
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
        async def pending_send(contact_id, text):
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

    def test_poll_timeout_keeps_qr_but_cancel_clears_it(self):
        self.gateway._event("logout", {})
        self.assertIsNone(self.gateway.status()["account"])
        self.gateway._event("scan", {"qr_code": "wx://login/qr-token", "qr_status": "Waiting"})
        self.assertEqual(self.gateway.status()["state"], "awaiting_scan")
        self.assertFalse(self.gateway.status()["logged_in"])
        self.assertNotIn("qr-token", str(self.events))
        self.gateway._event("scan", {"qr_code": "wx://login/qr-token", "qr_status": "Timeout"})
        self.assertEqual(self.gateway.status()["qr_code"], "wx://login/qr-token")
        self.gateway._event("scan", {"qr_code": "cancelled", "qr_status": "Cancel"})
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
        self.gateway._event('login_failed', {'return_code': '1203'})
        self.gateway._event('scan', {'qr_code': 'old', 'qr_status': 'Waiting'})
        self.assertEqual(self.gateway.status()['error']['code'], 'WECHAT_LOGIN_REJECTED')

    def test_explicit_refresh_restarts_remote_and_ignores_previous_client_events(self):
        old_callback = self.client.callback
        replacement = FakeClient({})
        connections = []
        def factory(connection):
            connections.append(connection.copy())
            return replacement
        self.gateway._factory = factory
        self.gateway.start(self.config, reset_login=True)
        self.assertTrue(replacement.started.wait(2))
        self.gateway._event('scan', {'qr_code': 'new', 'qr_status': 'Waiting'})
        old_callback('scan', {'qr_code': 'old', 'qr_status': 'Scanned'})
        self.assertEqual(self.gateway.status()['qr_code'], 'new')
        self.assertEqual(self.gateway.status()['qr_status'], 'Waiting')
        self.assertTrue(connections[0]['reset_login'])

    def test_saved_target_change_does_not_restart_login_session(self):
        changed = dict(self.config, target_contact_id="friend-2")
        self.gateway.start(changed)
        self.assertFalse(self.client.closed)
        self.assertEqual(self.gateway.status()["account"]["id"], "account-1")

    def test_stop_closes_client_without_logging_out(self):
        self.gateway.stop()
        self.assertTrue(self.client.closed)
        self.assertEqual(self.gateway.status()["state"], "disabled")

    def test_contacts_require_live_account(self):
        self.assertEqual(self.gateway.contact("friend-1")["id"], "friend-1")
        self.client.account = None
        with self.assertRaises(WechatError):
            self.gateway.contacts()


class WechatConfigurationTests(unittest.TestCase):
    def test_local_token_is_read_from_persistent_file_and_endpoint_is_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "wechat-bridge").mkdir()
            Path(directory, "wechat-bridge", "agentcall-wechat-token").write_text("private-local-token")
            connection = _connection({"mode": "local", "service_endpoint": "evil.example.com"}, directory)
            self.assertEqual(connection, {"host": "agentcall-wechat", "port": 8788,
                                          "tls": False, "token": "private-local-token"})

    def test_external_plaintext_and_embedded_credentials_are_rejected(self):
        for endpoint in ("grpc://example.com:80", "http://example.com", "https://user:pass@example.com", "https://example.com/path"):
            with self.subTest(endpoint=endpoint), self.assertRaises(WechatError):
                _connection({"mode": "external", "service_endpoint": endpoint, "service_token": "private"}, "/tmp")

    def test_external_connection_requires_verified_tls(self):
        connection = _connection({"mode": "external", "service_endpoint": "example.com:8788", "service_token": "private"}, "/tmp")
        self.assertTrue(connection["tls"])
        self.assertEqual(connection["host"], "example.com")


class WechatInboundTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_error_forwards_only_the_protocol_code(self):
        client = object.__new__(_SDKClient)
        captured = []
        client._callback = lambda event, payload: captured.append((event, payload))
        client._error_event(SimpleNamespace(data='secret-cookie AGENTCALL_LOGIN_REJECTED:1203'))
        self.assertEqual(captured, [('login_failed', {'return_code': '1203'})])
        client._error_event(SimpleNamespace(data='secret login URL'))
        self.assertEqual(captured[-1], ('error', {}))

    async def test_only_direct_nonself_text_with_real_timestamp_is_forwarded(self):
        client = object.__new__(_SDKClient)
        client.puppet = SimpleNamespace(login_user_id="own-account")
        captured = []
        client._callback = lambda event, payload: captured.append((event, payload))

        class Message:
            payload = SimpleNamespace(timestamp=1791162000000)
            async def ready(self): pass
            def is_self(self): return self.own
            def room(self): return self.room_id
            def type(self): return self.message_type
            def text(self): return "我选择 A [AC:req123]"
            def talker(self): return SimpleNamespace(get_id=lambda: "friend-1")

        message = Message()
        client.bot = SimpleNamespace(Message=SimpleNamespace(load=lambda _: message))
        for own, room, kind in ((True, None, 6), (False, "room-1", 6), (False, None, 3)):
            message.own, message.room_id, message.message_type = own, room, kind
            await client._message(SimpleNamespace(message_id="incoming-1"))
        self.assertEqual(captured, [])
        message.own, message.room_id, message.message_type = False, None, 6
        await client._message(SimpleNamespace(message_id="incoming-1"))
        event, payload = captured[0]
        self.assertEqual(event, "message")
        self.assertEqual(payload["account_id"], "own-account")
        self.assertEqual(payload["from_contact_id"], "friend-1")
        self.assertEqual(payload["body"], "我选择 A [AC:req123]")
        self.assertTrue(payload["received_at"].endswith("Z"))
        captured.clear()
        message.payload.timestamp = None
        await client._message(SimpleNamespace(message_id="incoming-old"))
        self.assertEqual(captured, [])

    async def test_quoted_reply_line_breaks_are_normalized_before_forwarding(self):
        client = object.__new__(_SDKClient)
        client.puppet = SimpleNamespace(login_user_id="own-account")
        captured = []
        client._callback = lambda event, payload: captured.append((event, payload))

        class Message:
            payload = SimpleNamespace(timestamp=1791162000000)
            own, room_id, message_type = False, None, 6
            async def ready(self): pass
            def is_self(self): return False
            def room(self): return None
            def type(self): return 6
            def text(self):
                return "「原话」<br/>- - - - - - - - - - - - - - -<br/>就选 A"
            def talker(self): return SimpleNamespace(get_id=lambda: "friend-1")

        client.bot = SimpleNamespace(Message=SimpleNamespace(load=lambda _: Message()))
        await client._message(SimpleNamespace(message_id="incoming-1"))
        self.assertEqual(captured[0][1]["body"], "「原话」\n- - - - - - - - - - - - - - -\n就选 A")

    async def test_probe_requires_dong_event_not_only_ding_rpc_success(self):
        client = object.__new__(_SDKClient)
        client._dongs = {}
        async def ding(nonce): pass
        client.puppet = SimpleNamespace(ding=ding, login_user_id="cached-login")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(client.probe(), .03)
        self.assertEqual(client._dongs, {})

    async def test_logout_seen_before_probe_dong_cannot_use_cached_account(self):
        client = object.__new__(_SDKClient)
        client._dongs = {}
        async def ding(nonce):
            client.puppet.login_user_id = None
            client._dong(SimpleNamespace(data=nonce))
        client.puppet = SimpleNamespace(ding=ding, login_user_id="cached-login")
        self.assertIsNone(await client.probe())


# This integration test is run inside the Python 3.10 Docker image. It uses the
# real pinned SDK over a real local gRPC socket, without any WeChat account.
import importlib.util
import json
import sys


@unittest.skipUnless(importlib.util.find_spec("wechaty") and sys.version_info < (3, 11),
                     "SDK integration runs in the Python 3.10 Docker image")
class WechatSDKIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_qr_login_probe_contact_send_receive_and_disconnect(self):
        # Docker Desktop bind mounts make asyncio's source-line debug inspection
        # disproportionately slow; the protocol itself remains fully exercised.
        asyncio.get_running_loop().set_debug(False)
        from grpclib.server import Server
        from grpclib.const import Handler, Cardinality
        from wechaty_grpc.wechaty import puppet as proto

        class PuppetServer:
            def __init__(self):
                self.queue = asyncio.Queue()
                self.authorization = []
                self.sent = []
                self.operations = []

            def __mapping__(self):
                methods = {}
                for name, cardinality in (("Start", Cardinality.UNARY_UNARY), ("Stop", Cardinality.UNARY_UNARY),
                    ("Event", Cardinality.UNARY_STREAM), ("Ding", Cardinality.UNARY_UNARY),
                    ("ContactList", Cardinality.UNARY_UNARY), ("ContactPayload", Cardinality.UNARY_UNARY),
                    ("MessageSendText", Cardinality.UNARY_UNARY), ("MessagePayload", Cardinality.UNARY_UNARY)):
                    methods["/wechaty.Puppet/" + name] = Handler(getattr(self, name), cardinality,
                        getattr(proto, name + "Request"), getattr(proto, name + "Response"))
                return methods

            async def event(self, kind, payload):
                await self.queue.put(proto.EventResponse(type=kind, payload=json.dumps(payload)))

            async def Stop(self, stream):
                self.operations.append('Stop')
                await stream.recv_message()
                await stream.send_message(proto.StopResponse())

            async def Start(self, stream):
                self.operations.append("Start")
                await stream.recv_message()
                self.authorization.append(stream.metadata.get("authorization"))
                await self.event(proto.EventType.EVENT_TYPE_SCAN,
                                 {"status": 2, "qrcode": "https://wx.qq.com/qrcode/fake-test-qr"})
                await stream.send_message(proto.StartResponse())

            async def Event(self, stream):
                self.operations.append("Event")
                await stream.recv_message()
                self.authorization.append(stream.metadata.get("authorization"))
                await stream.send_message(proto.EventResponse(type=proto.EventType.EVENT_TYPE_SCAN,
                    payload=json.dumps({'status': 3, 'qrcode': 'old-cached-scanned-qr'})))
                await stream.send_message(proto.EventResponse(type=proto.EventType.EVENT_TYPE_HEARTBEAT,
                                                               payload='{"data":"ready"}'))
                self.operations.append("heartbeat-sent")
                while True:
                    event = await self.queue.get()
                    if event is None:
                        return
                    await stream.send_message(event)
                    self.operations.append("event-sent:" + str(event.type))

            async def Ding(self, stream):
                request = await stream.recv_message()
                await self.event(proto.EventType.EVENT_TYPE_DONG, {"data": request.data})
                await stream.send_message(proto.DingResponse())

            async def ContactList(self, stream):
                await stream.recv_message()
                await stream.send_message(proto.ContactListResponse(ids=["sdk-account", "sdk-friend"]))

            async def ContactPayload(self, stream):
                request = await stream.recv_message()
                await stream.send_message(proto.ContactPayloadResponse(id=request.id,
                    name="SDK 帐号" if request.id == "sdk-account" else "SDK 用户", friend=True))

            async def MessageSendText(self, stream):
                request = await stream.recv_message()
                self.sent.append((request.conversation_id, request.text))
                await stream.send_message(proto.MessageSendTextResponse(id="sdk-sent-1"))

            async def MessagePayload(self, stream):
                request = await stream.recv_message()
                await stream.send_message(proto.MessagePayloadResponse(id=request.id,
                    text="SDK 回复 [AC:request-1]", timestamp=1791162000,
                    type=7, from_id="sdk-friend", to_id="sdk-account"))

        service = PuppetServer()
        server = Server([service])
        await server.start("127.0.0.1", 0)
        port = server._server.sockets[0].getsockname()[1]
        cache = tempfile.TemporaryDirectory()
        self.addCleanup(cache.cleanup)
        client = _SDKClient({"host": "127.0.0.1", "port": port, "tls": False,
                             "token": "fake-sdk-token", "cache_dir": cache.name, 'reset_login': True})
        events = []
        run = asyncio.create_task(client.run(lambda name, payload: events.append((name, payload))))

        async def received(name):
            async def poll():
                while not any(event[0] == name for event in events):
                    if run.done():
                        await run
                    await asyncio.sleep(.01)
                return next(payload for kind, payload in events if kind == name)
            try:
                return await asyncio.wait_for(poll(), 30)
            except asyncio.TimeoutError:
                stack = [frame.f_lineno for frame in run.get_stack()]
                self.fail(f"Missing {name}; server={service.operations}, events={events}, client_lines={stack}")

        try:
            qr = await received("scan")
            self.assertEqual(qr["qr_status"], "Waiting")
            self.assertIn("fake-test-qr", qr["qr_code"])
            self.assertEqual(service.operations[:2], ['Stop', 'Event'])
            self.assertFalse(any(p.get('qr_code') == 'old-cached-scanned-qr' for _, p in events))
            self.assertIsNone(await client.probe())
            await service.event(proto.EventType.EVENT_TYPE_ERROR, {'data': 'AGENTCALL_LOGIN_REJECTED:1203'})
            self.assertEqual(await received('login_failed'), {'return_code': '1203'})
            await service.event(proto.EventType.EVENT_TYPE_LOGIN, {"contactId": "sdk-account"})
            await received("login")
            self.assertEqual((await client.probe())["id"], "sdk-account")
            self.assertEqual(await client.contacts(), [{"id": "sdk-friend", "name": "SDK 用户", "alias": ""}])
            self.assertEqual((await client.contact("sdk-friend"))["id"], "sdk-friend")
            self.assertEqual(await client.send("sdk-friend", "待回复 [AC:request-1]"), "sdk-sent-1")
            self.assertEqual(service.sent, [("sdk-friend", "待回复 [AC:request-1]")])
            message = client.bot.Message.load("sdk-incoming-1")
            await message.ready()
            self.assertFalse(message.is_self())
            self.assertIsNone(message.room())
            self.assertEqual(int(message.type()), 6)
            self.assertEqual(message.talker().get_id(), "sdk-friend")
            await service.event(proto.EventType.EVENT_TYPE_MESSAGE, {"messageId": "sdk-incoming-1"})
            reply = await received("message")
            self.assertEqual(reply["body"], "SDK 回复 [AC:request-1]")
            self.assertEqual(reply["from_contact_id"], "sdk-friend")
            self.assertEqual(reply["account_id"], "sdk-account")
            self.assertTrue(service.authorization)
            self.assertEqual(set(service.authorization), {"Wechaty fake-sdk-token"})
            await service.event(proto.EventType.EVENT_TYPE_LOGOUT, {"contactId": "sdk-account", "data": "exit"})
            await received("logout")
            self.assertIsNone(await client.probe())
            await service.queue.put(None)
            with self.assertRaises(ConnectionError):
                await asyncio.wait_for(run, 2)
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            await client.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()

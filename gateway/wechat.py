"""Thread-safe Python Wechaty transport with active, event-stream health probes.

The SDK is loaded only when WeChat is enabled. Account sessions live in the
puppet service's persisted memory card; this process never implements WeChat's
protocol or stores cookies. The 0.10.7 SDK is pinned and runs on Python 3.10.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import logging
import os
import ssl
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PROBE_TIMEOUT = 3
OPERATION_TIMEOUT = 20
LOCAL_ENDPOINT = "agentcall-wechat:8788"
LOCAL_TOKEN_FILE = "agentcall-wechat-token"
MAX_TEXT = 32000


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class WechatError(Exception):
    def __init__(self, code, message, hint="", safe_to_fallback=False):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint
        self.safe_to_fallback = safe_to_fallback

    def as_dict(self):
        return {"code": self.code, "message": self.message, "hint": self.hint,
                "safe_to_fallback": self.safe_to_fallback}


def _error(exc, *, sending=False):
    if sending:
        return WechatError("WECHAT_SEND_UNCERTAIN", "微信发送结果无法确认。",
                           "消息可能已送达；为避免重复通知，本次不会自动改发邮件。请检查微信记录。")
    if isinstance(exc, WechatError):
        return exc
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return WechatError("WECHAT_SDK_MISSING", "微信组件尚未安装。",
                           "请使用项目的 Docker 启动器安装 Python Wechaty 及本地微信服务。", True)
    if getattr(getattr(exc, "status", None), "name", "") in {"UNAUTHENTICATED", "PERMISSION_DENIED"}:
        return WechatError("WECHAT_AUTH_FAILED", "微信服务拒绝了连接凭据。",
                           "请检查 Puppet Service 令牌是否正确或已过期。", True)
    if isinstance(exc, ssl.SSLError):
        return WechatError("WECHAT_TLS_FAILED", "微信服务的加密连接验证失败。",
                           "检查外部服务地址、证书和系统时间；不要关闭证书验证。", True)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError)):
        return WechatError("WECHAT_UNAVAILABLE", "微信服务未及时响应。",
                           "请检查微信登录状态和微信服务；可用的邮箱配置会接替新的请求。", True)
    return WechatError("WECHAT_UNAVAILABLE", "无法连接微信服务。",
                       "请确认本地微信容器已启动，或检查外部 Puppet Service 地址和令牌。", True)


def _connection(config, data_dir):
    """Only the bundled Docker service is allowed plaintext gRPC."""
    if config.get("mode", "local") == "local":
        token_path = Path(data_dir) / "wechat-bridge" / LOCAL_TOKEN_FILE
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError:
            raise WechatError("WECHAT_LOCAL_SERVICE_MISSING", "本地微信服务尚未就绪。",
                              "请使用项目的 Docker 启动器启动微信服务。", True) from None
        if not token:
            raise WechatError("WECHAT_LOCAL_SERVICE_MISSING", "本地微信服务尚未就绪。",
                              "请重启微信服务以生成连接凭据。", True)
        return {"host": "agentcall-wechat", "port": 8788, "tls": False, "token": token}
    endpoint = str(config.get("service_endpoint") or "").strip()
    parsed = urlsplit(endpoint if "://" in endpoint else "https://" + endpoint)
    try:
        port = parsed.port or 443
    except ValueError:
        port = 0
    if (parsed.scheme not in {"https", "grpcs"} or not parsed.hostname or not port
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise WechatError("WECHAT_CONFIG_INVALID", "微信服务地址格式不正确。",
                          "外部服务必须使用支持 TLS 的主机及端口，例如 grpcs://wechat.example.com:8788。", True)
    token = str(config.get("service_token") or "").strip()
    if not token:
        raise WechatError("WECHAT_CONFIG_INVALID", "尚未配置微信服务令牌。", "请填写 Puppet Service 令牌。", True)
    return {"host": parsed.hostname, "port": port, "tls": True, "token": token}


class _SDKClient:
    """A narrow adapter around the published SDK's puppet and user models.

    The upstream initializer prints credentials, performs blocking ICMP probes,
    and constructs a plaintext channel. Overriding that small initializer keeps
    the official protocol implementation while enforcing this app's transport
    policy. The stock plugin event bridge is intentionally not started: it opens
    an unrelated HTTP listener and hides connection errors in unbounded retries.
    """
    def __init__(self, connection):
        if sys.version_info >= (3, 11):
            raise WechatError("WECHAT_RUNTIME_UNSUPPORTED", "当前 Python 版本不兼容微信 SDK。",
                              "请使用项目 Docker 启动器；镜像包含已验证的 Python 3.10 环境。", True)
        if connection.get("cache_dir"):
            Path(connection["cache_dir"]).mkdir(mode=0o700, parents=True, exist_ok=True)
            # The published SDK resolves this variable while importing schema.py.
            os.environ["CACHE_DIR"] = str(connection["cache_dir"])
        from pyee import AsyncIOEventEmitter
        from grpclib.client import Channel
        from wechaty import Wechaty, WechatyOptions
        from wechaty_puppet import Puppet
        from wechaty_puppet.schemas.puppet import PuppetOptions
        from wechaty_puppet_service import PuppetService
        from wechaty_grpc.wechaty import PuppetStub

        # The legacy SDK logs message bodies and QR tokens at ordinary levels.
        # This app records only its own deliberately selected diagnostic fields.
        for name in list(logging.Logger.manager.loggerDict):
            if any(part in name.lower() for part in ("wechaty", "puppet", "contact", "message")):
                logging.getLogger(name).disabled = True

        class SecurePuppetService(PuppetService):
            def __init__(self):
                options = PuppetOptions()
                options.token = connection["token"]
                options.end_point = connection["host"] + ":" + str(connection["port"])
                Puppet.__init__(self, options, "agentcall")
                self.channel = None
                self._puppet_stub = None
                self._event_stream = AsyncIOEventEmitter()
                self.login_user_id = None

            def _init_puppet(self):
                context = ssl.create_default_context() if connection["tls"] else None
                self.channel = Channel(host=connection["host"], port=connection["port"], ssl=context)
                # This is the authority authentication used by the pinned SDK /
                # Wechaty 0.65 server. TLS hostname verification uses Channel.host.
                self.channel._authority = connection["token"]
                self._puppet_stub = PuppetStub(self.channel, metadata={
                    "authorization": "Wechaty " + connection["token"]})

        self.puppet = SecurePuppetService()
        self.bot = Wechaty(WechatyOptions(name="agentcall", puppet=self.puppet, puppet_options=PuppetOptions()))
        self._dongs = {}
        self._callback = None
        self._stream_task = None

    async def run(self, callback):
        self._callback = callback
        await self.bot.init_puppet()
        self.puppet.on("scan", lambda payload: callback("scan", {
            "qr_code": payload.qrcode, "qr_status": getattr(payload.status, "name", str(payload.status))}))
        self.puppet.on("login", lambda payload: callback("login", {"id": payload.contact_id, "name": ""}))
        self.puppet.on("logout", lambda payload: callback("logout", {}))
        self.puppet.on("ready", lambda payload: callback("ready", {}))
        self.puppet.on("error", lambda payload: callback("error", {}))
        self.puppet.on("dong", self._dong)
        self.puppet.on("message", self._message)
        # AsyncIOEventEmitter sends asynchronous handler failures here.
        self.puppet._event_stream.on("error", lambda *args: callback("error", {}))
        self.puppet._init_puppet()
        ready = asyncio.Event()
        self.puppet.on("heartbeat", lambda payload: ready.set())
        # Subscribe before Start so an immediate QR/login event cannot be lost.
        self._stream_task = asyncio.create_task(self.puppet._listen_for_event())
        ready_task = asyncio.create_task(ready.wait())
        try:
            await asyncio.wait({self._stream_task, ready_task}, timeout=3,
                               return_when=asyncio.FIRST_COMPLETED)
            if self._stream_task.done():
                await self._stream_task
                raise ConnectionError("event stream ended")
            await asyncio.wait_for(self.puppet.puppet_stub.start(), OPERATION_TIMEOUT)
            await self._stream_task
        finally:
            ready_task.cancel()
            await asyncio.gather(ready_task, return_exceptions=True)
        raise ConnectionError("event stream ended")

    def _dong(self, payload):
        future = self._dongs.get(payload.data)
        if future is not None and not future.done():
            future.set_result(None)

    async def probe(self):
        nonce = "agentcall-" + uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._dongs[nonce] = future
        try:
            await self.puppet.ding(nonce)
            # A DONG event proves both the RPC and incoming stream are alive.
            # Logout events preceding it have already updated login_user_id.
            await asyncio.wait_for(future, PROBE_TIMEOUT)
            account_id = self.puppet.login_user_id
            if not account_id:
                return None
            payload = await self.puppet.contact_payload(account_id)
            if account_id != self.puppet.login_user_id:
                return None
            return {"id": account_id, "name": getattr(payload, "name", "") or ""}
        finally:
            self._dongs.pop(nonce, None)

    async def contacts(self, query="", limit=100):
        ids = await self.puppet.contact_list()
        own_id = self.puppet.login_user_id
        result = []
        # Bound server work even if a corrupt service returns an enormous list.
        for contact_id in ids[:5000]:
            if contact_id == own_id:
                continue
            payload = await self.puppet.contact_payload(contact_id)
            if getattr(payload, "friend", True) is False:
                continue
            item = {"id": contact_id, "name": getattr(payload, "name", "") or "",
                    "alias": getattr(payload, "alias", "") or ""}
            if query.casefold() not in " ".join(item.values()).casefold():
                continue
            result.append(item)
            if len(result) >= limit:
                break
        return result

    async def contact(self, contact_id):
        ids = await self.puppet.contact_list()
        if contact_id not in ids or contact_id == self.puppet.login_user_id:
            raise WechatError("WECHAT_CONTACT_NOT_FOUND", "目标不是当前微信账号的联系人。",
                              "请先在微信中添加好友，再刷新联系人并选择目标。", True)
        payload = await self.puppet.contact_payload(contact_id)
        if getattr(payload, "friend", True) is False:
            raise WechatError("WECHAT_CONTACT_NOT_FOUND", "目标微信联系人尚未添加为好友。",
                              "请在微信中完成好友添加后重新选择。", True)
        return {"id": contact_id, "name": getattr(payload, "name", "") or "",
                "alias": getattr(payload, "alias", "") or ""}

    async def send(self, contact_id, text):
        return await self.puppet.message_send_text(contact_id, text)

    async def _message(self, payload):
        try:
            message = self.bot.Message.load(payload.message_id)
            await message.ready()
            if message.is_self() or message.room() is not None:
                return
            # Node puppet sends wire type 7; the pinned Python adapter maps it
            # to its own MESSAGE_TYPE_TEXT = 6 (7 there means video).
            if int(message.type()) != 6:
                return
            body = message.text()
            if not body or len(body) > MAX_TEXT:
                return
            talker_id = message.talker().get_id()
            account_id = self.puppet.login_user_id
            if not account_id or talker_id == account_id:
                return
            timestamp = getattr(message.payload, "timestamp", None)
            if not isinstance(timestamp, (int, float)) or timestamp <= 0:
                return  # Without receipt time an old message must not decide a new request.
            if timestamp > 100000000000:
                timestamp /= 1000
            received_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            self._callback("message", {"message_id": payload.message_id,
                "from_contact_id": talker_id, "account_id": account_id,
                "body": body, "received_at": received_at})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._callback("receive_error", {})

    async def close(self):
        # Closing the client must not log the account out or erase the puppet's
        # memory card; a container restart can reconnect to its persisted session.
        if self.puppet.channel is not None:
            self.puppet.channel.close()
        if self._stream_task:
            self._stream_task.cancel()
            await asyncio.gather(self._stream_task, return_exceptions=True)
        for future in self._dongs.values():
            if not future.done():
                future.cancel()
        self._dongs.clear()


class WechatGateway:
    def __init__(self, data_dir, on_message=None, on_event=None, *, _client_factory=None):
        self.data_dir = Path(data_dir)
        self.on_message = on_message or (lambda item: None)
        self.on_event = on_event or (lambda item: None)
        self._factory = _client_factory or _SDKClient
        self._lock = threading.RLock()
        self._lifecycle = threading.RLock()
        self._config = {}
        self._thread = None
        self._loop = None
        self._client = None
        self._task = None
        self._stopping = False
        self._state = {"state": "disabled", "logged_in": False, "available": False,
                       "account": None, "qr_code": None, "qr_status": None,
                       "qr_updated_at": None, "error": None, "last_checked_at": None}

    def _set(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _report(self, event, error=None):
        item = {"type": event, "at": _now()}
        if error:
            item["error"] = error.as_dict()
        try:
            self.on_event(item)
        except Exception:
            # Storage errors must not leak credentials or kill the receiver loop.
            pass

    def status(self, probe=False):
        if probe:
            return self.check()
        with self._lock:
            return copy.deepcopy(self._state)

    def start(self, config):
        with self._lifecycle:
            config = copy.deepcopy(config or {})
            keys = ("enabled", "mode", "service_endpoint", "service_token")
            same = all(config.get(key) == self._config.get(key) for key in keys)
            if same and self._thread and self._thread.is_alive():
                self._config = config
                return self.status()
            self.stop()
            self._config = config
            if not config.get("enabled"):
                return self.status()
            try:
                connection = _connection(config, self.data_dir)
            except Exception as exc:
                error = _error(exc)
                self._set(state="error", error=error.as_dict())
                self._report("connection_failed", error)
                return self.status()
            self._stopping = False
            self._set(state="connecting", error=None)
            self._thread = threading.Thread(target=self._run_thread, args=(connection,), name="agentcall-wechat", daemon=True)
            self._thread.start()
            return self.status()

    def _run_thread(self, connection):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._task = loop.create_task(self._run(connection))
        try:
            loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._loop = None
            self._client = None

    async def _run(self, connection):
        connection = dict(connection, cache_dir=str(self.data_dir / "wechat-sdk"))
        delay = 1
        while not self._stopping:
            client = None
            try:
                client = self._factory(connection)
                self._client = client
                await client.run(self._event)
                if not self._stopping:
                    raise ConnectionError("event stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = _error(exc)
                self._set(state="error", available=False, logged_in=False, account=None,
                          qr_code=None, error=error.as_dict())
                self._report("connection_failed", error)
            finally:
                self._client = None
                if client:
                    try:
                        await asyncio.wait_for(client.close(), 3)
                    except Exception:
                        pass
            if not self._stopping:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    def _event(self, event, payload):
        if event == "scan":
            state = str(payload.get("qr_status") or "").lower()
            # Web WeChat's 408 is a long-poll timeout, not QR invalidation.
            # The pinned puppet labels it Timeout and supplies a usable QR.
            expired = state in {"cancel", "unknown", "1", "0"}
            self._set(state="awaiting_scan", available=False, logged_in=False, account=None,
                      qr_code=None if expired else payload.get("qr_code"),
                      qr_status=payload.get("qr_status"), qr_updated_at=_now(), error=None)
        elif event == "login":
            self._set(state="logged_in", logged_in=True, available=False, account=payload,
                      qr_code=None, qr_status="confirmed", error=None)
            self._report("logged_in")
        elif event == "logout":
            self._set(state="logged_out", logged_in=False, available=False, account=None, qr_code=None,
                      error=WechatError("WECHAT_NOT_LOGGED_IN", "微信已退出登录。", "请重新扫码登录；新请求将优先使用可用的邮箱。", True).as_dict())
            self._report("logged_out")
        elif event in {"error", "receive_error"}:
            error = WechatError("WECHAT_RECEIVE_FAILED", "微信消息接收出现异常。", "后台将重连；请检查微信状态及记录。", True)
            self._set(available=False, error=error.as_dict())
            self._report("receive_failed", error)
        elif event == "message":
            try:
                self.on_message(payload)
            except Exception:
                error = WechatError("WECHAT_REPLY_SAVE_FAILED", "微信回复未能保存。", "请检查数据目录权限和磁盘空间。", True)
                self._set(error=error.as_dict())
                self._report("reply_save_failed", error)

    def _call(self, coroutine, timeout=OPERATION_TIMEOUT):
        loop = self._loop
        if not loop or loop.is_closed() or self._stopping:
            coroutine.close()
            raise WechatError("WECHAT_UNAVAILABLE", "微信服务尚未连接。", "请检查微信配置或稍后重试。", True)
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    async def _probe(self):
        if self._client is None:
            raise ConnectionError("not connected")
        return await asyncio.wait_for(self._client.probe(), PROBE_TIMEOUT)

    def check(self):
        if not self._config.get("enabled"):
            self._set(last_checked_at=_now())
            return self.status()
        try:
            account = self._call(self._probe(), PROBE_TIMEOUT + 1)
            if account:
                self._set(state="logged_in", logged_in=True, available=True, account=account,
                          qr_code=None, error=None, last_checked_at=_now())
            else:
                state = "awaiting_scan" if self.status().get("qr_code") else "logged_out"
                self._set(state=state, logged_in=False, available=False, account=None, last_checked_at=_now(),
                          error=WechatError("WECHAT_NOT_LOGGED_IN", "微信尚未登录。", "请扫描二维码并在手机上确认登录。", True).as_dict())
        except Exception as exc:
            error = _error(exc)
            self._set(state="error", available=False, logged_in=False, account=None,
                      error=error.as_dict(), last_checked_at=_now())
        return self.status()

    def _require_login(self):
        status = self.check()
        if not status["available"]:
            detail = status.get("error") or {"code": "WECHAT_DISABLED", "message": "微信配置未启用。", "hint": "请启用并扫码登录。"}
            raise WechatError(detail["code"], detail["message"], detail.get("hint", ""), True)
        return status

    async def _contacts(self, query, limit):
        if self._client is None:
            raise ConnectionError("not connected")
        return await self._client.contacts(query, limit)

    def contacts(self, query="", limit=100):
        self._require_login()
        try:
            return self._call(self._contacts(str(query)[:100], max(1, min(int(limit), 100))))
        except Exception as exc:
            raise _error(exc) from None

    async def _contact(self, contact_id):
        if self._client is None:
            raise ConnectionError("not connected")
        return await self._client.contact(contact_id)

    def contact(self, contact_id):
        started = time.monotonic()
        self._require_login()
        try:
            return self._call(self._contact(contact_id), max(.01, 5 - (time.monotonic() - started)))
        except Exception as exc:
            raise _error(exc) from None

    async def _send(self, record, account_id):
        client = self._client
        if client is None:
            raise WechatError("WECHAT_UNAVAILABLE", "微信连接已断开。", "将尝试可用的邮箱。", True)
        # Revalidate identity and recipient inside the same event loop as send.
        target = record.get("target_contact_id")
        async def preflight():
            current = await client.probe()
            if not current or current["id"] != account_id:
                raise WechatError("WECHAT_ACCOUNT_CHANGED", "微信登录账号发生变化。", "请重新选择目标联系人。", True)
            await client.contact(target)
        try:
            await asyncio.wait_for(preflight(), 5)
        except Exception as exc:
            raise _error(exc) from None
        account = self.status().get("account") or {}
        if self._client is not client or account.get("id") != account_id:
            raise WechatError("WECHAT_ACCOUNT_CHANGED", "微信连接或登录账号发生变化。",
                              "请确认登录状态后重试。", True)
        text = "agentCall · " + (record.get("agent_name") or "Agent") + "\n" + record["subject"] + "\n\n" + record["body"].rstrip()
        text += "\n\n[AC:" + record["id"] + "]"
        if record["kind"] == "ask":
            text += ("\n请直接回复；同时存在多个问题时，请在回复中保留上面的 [AC:…] 标识。"
                     "\n回复截止：" + str(record.get("deadline_at") or "以记录页面为准") +
                     "。逾期回复仍会记录，但不会作为本次等待的及时决策。")
        try:
            message_id = await asyncio.wait_for(client.send(target, text), 10)
        except Exception as exc:
            raise _error(exc, sending=True) from None
        return {"message_id": message_id or None}

    def send(self, record):
        status = self._require_login()
        account_id = record.get("wechat_account_id") or record.get("account_id")
        if not account_id or account_id != status["account"]["id"]:
            raise WechatError("WECHAT_ACCOUNT_CHANGED", "微信账号与请求绑定的账号不一致。", "请重新选择目标联系人后创建请求。", True)
        if not record.get("target_contact_id"):
            raise WechatError("WECHAT_CONTACT_NOT_FOUND", "尚未选择微信目标联系人。", "请在配置中选择已有联系人。", True)
        try:
            return self._call(self._send(record, account_id), OPERATION_TIMEOUT)
        except (TimeoutError, concurrent.futures.TimeoutError, concurrent.futures.CancelledError) as exc:
            # A deadline or shutdown cancellation can race with send acceptance.
            # Do not classify cancellation as a safe preflight connection failure.
            raise _error(exc, sending=True) from None
        except Exception as exc:
            raise _error(exc) from None

    def stop(self):
        with self._lifecycle:
            self._stopping = True
            loop, task, thread = self._loop, self._task, self._thread
            if loop and not loop.is_closed() and task:
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass
            if thread and thread is not threading.current_thread():
                thread.join(timeout=5)
            self._thread = None
            self._set(state="disabled", logged_in=False, available=False, account=None,
                      qr_code=None, qr_status=None, error=None)

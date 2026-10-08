"""Thread-safe 文件传输助手 (filehelper) web-protocol transport.

The vendored protocol core in gateway/wxbot talks to WeChat's web filehelper
endpoints directly; this module wraps it in the same gateway façade the rest
of the app already uses. The conversation is the account's own filehelper
self-chat, so there are no contacts: the target anchor is the constant
"filehelper" and the account is auto-bound on login.

Health semantics differ from the old gRPC transport: there is no ding/dong
round-trip. probe() is state-local; freshness is bounded by the receiver
loop's synccheck cycle (~40 s). Sends are still preflighted against the
logged-in identity and classified WECHAT_SEND_UNCERTAIN on failure, so a
stale session can never silently fall back to email.
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import copy
import logging
import re
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from gateway.store import BEIJING

PROBE_TIMEOUT = 3
OPERATION_TIMEOUT = 20
MAX_TEXT = 32000
FILEHELPER_ID = "filehelper"
FILEHELPER_NAME = "文件传输助手"
QR_URL = "https://login.weixin.qq.com/l/{uuid}"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _format_deadline(value, *, now=None):
    """Render a UTC deadline as Beijing time with 今天/明天 day labels."""
    try:
        deadline = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return '以记录页面为准'
    if deadline.tzinfo is None:
        return '以记录页面为准'
    moment = deadline.astimezone(BEIJING)
    days = (moment.date() - (now or datetime.now(timezone.utc)).astimezone(BEIJING).date()).days
    if days == 0:
        return moment.strftime('今天 %H:%M:%S')
    if days == 1:
        return moment.strftime('明天 %H:%M:%S')
    return f'{moment.month}月{moment.day}日 {moment:%H:%M:%S}'


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
                           "请安装 httpx（或使用项目的 Docker 启动器）后重启服务。", True)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError)):
        return WechatError("WECHAT_UNAVAILABLE", "微信服务未及时响应。",
                           "请检查微信登录状态和网络；可用的邮箱配置会接替新的请求。", True)
    return WechatError("WECHAT_UNAVAILABLE", "无法连接微信服务。",
                       "请检查网络与微信登录状态；可用的邮箱配置会接替新的请求。", True)


class _WxBotClient:
    """Adapter around the vendored filehelper protocol core.

    The receiver loop below is the only place login polling and synccheck run;
    sends multiplex onto the same event loop through the shared AsyncClient.
    Inbound items keep the gateway's transport-agnostic contract, anchored to
    the constant filehelper conversation.
    """
    def __init__(self, connection):
        from gateway.wxbot.direct_bot import WeChatHelperBot  # lazy: keeps stdlib-only checkouts importable
        self.bot = WeChatHelperBot(state_path=connection["state_path"])
        self._callback = None
        self._seen: deque = deque(maxlen=1000)
        self._seen_set: set = set()
        self._sent_texts: deque = deque(maxlen=50)  # (monotonic, text) echo guards
        self._last_uuid = None
        self._scan_state = None
        self._avatar_fetch = None  # (account_id, expires_at, data_uri | None)

    async def avatar(self):
        """Account avatar as a data URI, from scan capture or a cached fetch.

        The 201-captured avatar lives from 待确认 until reset_session; restored
        sessions fall back to one bounded webwxgeticon fetch per hour.
        """
        bot = self.bot
        if bot.login_avatar:
            return {"account_id": bot.user_name or None, "image": bot.login_avatar}
        if not bot.is_logged_in or not bot.user_name:
            return {"account_id": None, "image": None}
        cached = self._avatar_fetch
        if cached and cached[0] == bot.user_name and cached[1] > time.monotonic():
            return {"account_id": bot.user_name, "image": cached[2]}
        image = None
        fetched = await bot.fetch_self_avatar()
        if fetched:
            body, content_type = fetched
            image = "data:" + content_type + ";base64," + base64.b64encode(body).decode()
        self._avatar_fetch = (bot.user_name, time.monotonic() + (3600 if image else 60), image)
        return {"account_id": bot.user_name, "image": image}

    def _remember(self, message_id):
        if message_id in self._seen_set:
            return
        self._seen_set.add(message_id)
        self._seen.append(message_id)
        if len(self._seen_set) > self._seen.maxlen + 100:
            self._seen_set &= set(self._seen)

    async def run(self, callback):
        self._callback = callback
        await self.bot.start()
        if self.bot.is_logged_in and not (self.bot.synckey or {}).get("List"):
            # Restored credentials without an initial sync state look logged
            # in but can never receive messages; drop them for a fresh login.
            self.bot.reset_session()
        elif self.bot.is_logged_in and self.bot.user_name:
            callback("login", {"id": self.bot.user_name, "name": ""})
        while True:
            if not self.bot.is_logged_in:
                if self.bot._has_auth():
                    # Credentials without a session: either kicked, or a login
                    # whose init failed after the auth fields were fetched.
                    # Verify on the wire before dropping anything — a fresh
                    # partial login can recover; without this check the stale
                    # credentials would wedge (upstream behavior) and every
                    # flaky init would force a brand-new QR scan.
                    if await self.bot.check_login_status(poll=True):
                        callback("login", {"id": self.bot.user_name, "name": ""})
                        await self.bot.save_session()
                        continue
                    callback("logout", {})
                    self.bot.reset_session()
                    continue
                await self._await_scan()
            else:
                messages = await self.bot.get_latest_messages(limit=50)
                if self.bot.is_logged_in:
                    for item in messages:
                        self._forward(item)
                    await self.bot.save_session()
                elif self.bot._has_auth():
                    callback("logout", {})
                    self.bot.reset_session()
            await asyncio.sleep(0.5)

    async def _await_scan(self):
        bot = self.bot
        try:
            uuid = await bot.ensure_login_uuid()
        except Exception:
            raise ConnectionError("login uuid fetch failed") from None
        if not uuid:
            raise ConnectionError("no login uuid available")
        if uuid != self._last_uuid:
            self._last_uuid, self._scan_state = uuid, None
            self._callback("scan", {"qr_code": QR_URL.format(uuid=uuid), "qr_status": "Waiting"})
        await bot.check_login_status(poll=True)  # ~25 s server-side long poll
        if bot.is_logged_in:
            self._callback("login", {"id": bot.user_name, "name": ""})
            await bot.save_session()
            return
        status = bot.last_login_message
        if status == "scanned_wait_confirm" and self._scan_state != "Scanned":
            self._scan_state = "Scanned"
            self._callback("scan", {"qr_code": QR_URL.format(uuid=uuid), "qr_status": "Scanned"})
        elif status == "qr_expired":
            # The server dropped the uuid; emit the stale QR removal once and
            # let the next cycle fetch a fresh one.
            self._last_uuid, self._scan_state = None, None
            self._callback("scan", {"qr_code": None, "qr_status": "Expired"})

    def _forward(self, item):
        if item.get("type") != "text" or not item.get("is_mine"):
            return  # filehelper system notices and media are not task input.
        message_id = str(item.get("id") or "")
        if not message_id or message_id in self._seen_set:
            return
        try:
            create_time = int(item.get("create_time") or 0)
        except (TypeError, ValueError):
            create_time = 0
        if create_time <= 0:
            return  # Without receipt time an old message must not decide a new request.
        body = re.sub(r'<br\s*/?>', '\n', str(item.get("text") or ""))
        if not body or len(body) > MAX_TEXT:
            return
        self._remember(message_id)
        now = time.monotonic()
        while self._sent_texts and now - self._sent_texts[0][0] > 30:
            self._sent_texts.popleft()
        if any(text == body for _, text in self._sent_texts):
            # Belt-and-braces echo guard: if the server echoes our own send
            # under a fresh id, an exact-text match within seconds of sending
            # is our message coming back, not a user reply.
            return
        payload = {"message_id": message_id, "from_contact_id": FILEHELPER_ID,
                   "account_id": self.bot.user_name, "body": body,
                   "received_at": datetime.fromtimestamp(create_time, timezone.utc
                       ).isoformat(timespec="seconds").replace("+00:00", "Z")}
        if item.get("reference_id"):
            payload["reference_id"] = str(item["reference_id"])
        self._callback("message", payload)

    async def probe(self):
        # State-local: freshness is guaranteed by the receiver loop's synccheck
        # cycle, not by a wire round-trip on every probe.
        if self.bot.is_logged_in and self.bot.user_name:
            return {"id": self.bot.user_name, "name": ""}
        return None

    async def send(self, text):
        message_id = await self.bot.send_text(text)
        if message_id:
            self._sent_texts.append((time.monotonic(), text))
            self._remember(message_id)
        return message_id

    async def close(self):
        # Persist the session and close the client without logging out; a
        # restart reconnects to the saved session without a new scan.
        try:
            await asyncio.wait_for(self.bot.stop(), 3)
        except Exception:
            pass


class WechatGateway:
    def __init__(self, data_dir, on_message=None, on_event=None, *, _client_factory=None):
        self.data_dir = Path(data_dir)
        self.on_message = on_message or (lambda item: None)
        self.on_event = on_event or (lambda item: None)
        self._factory = _client_factory or _WxBotClient
        self._lock = threading.RLock()
        self._lifecycle = threading.RLock()
        self._config = {}
        self._thread = None
        self._loop = None
        self._client = None
        self._task = None
        self._stopping = False
        self._generation = 0
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

    def start(self, config, *, reset_login=False):
        with self._lifecycle:
            config = copy.deepcopy(config or {})
            keys = ("enabled",)
            same = all(config.get(key) == self._config.get(key) for key in keys)
            if same and not reset_login and self._thread and self._thread.is_alive():
                self._config = config
                return self.status()
            self.stop()
            self._config = config
            if not config.get("enabled"):
                return self.status()
            state_path = self.data_dir / "wxbot" / "state.json"
            if reset_login:
                # A fresh scan is requested: drop the persisted session so the
                # next login cannot silently reuse it.
                state_path.unlink(missing_ok=True)
            connection = {"state_path": state_path}
            self._stopping = False
            connection.update(generation=self._generation)
            self._set(state="connecting", error=None)
            self._thread = threading.Thread(target=self._run_thread, args=(connection,), name="agentcall-wechat", daemon=True)
            self._thread.start()
            return self.status()

    def _run_thread(self, connection):
        loop = asyncio.new_event_loop()
        if connection['generation'] != self._generation:
            loop.close()
            return
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
            if self._loop is loop:
                self._loop = None
                self._client = None

    async def _run(self, connection):
        connection = dict(connection)
        Path(connection["state_path"]).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        delay = 1
        generation = connection['generation']
        while not self._stopping and generation == self._generation:
            client = None
            try:
                client = self._factory(connection)
                if generation != self._generation:
                    return
                self._client = client
                def current_event(event, payload, expected_client=client):
                    if generation == self._generation and self._client is expected_client:
                        self._event(event, payload)
                await client.run(current_event)
                if not self._stopping:
                    raise ConnectionError("receiver loop ended")
            except asyncio.CancelledError:
                raise
            except Exception:
                if generation != self._generation:
                    return
                error = _error(ConnectionError())  # never surface exception text
                self._set(state="error", available=False, logged_in=False, account=None,
                          qr_code=None, error=error.as_dict())
                self._report("connection_failed", error)
            finally:
                if self._client is client:
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
            state = str(payload.get("qr_status") or "")
            current = self.status()
            qr = payload.get('qr_code')
            if state in {'Scanned', 'Confirmed'} and (
                    not current.get('qr_code') or qr != current['qr_code']):
                return  # Replayed scan events for an outdated QR.
            # Waiting is the web protocol's long-poll heartbeat (408), not
            # invalidation; only an explicit expiry clears the QR.
            expired = state in {"Expired", "Cancel", "Unknown"}
            self._set(state="awaiting_scan", available=False, logged_in=False, account=None,
                      qr_code=None if expired else qr,
                      qr_status=state or None, qr_updated_at=_now(), error=None)
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
            except Exception as exc:
                storage_error = isinstance(exc, (sqlite3.Error, OSError))
                hint = ('请检查数据目录权限和磁盘空间。' if storage_error else
                        '处理回复数据时发生内部错误，请更新服务或查看诊断记录；这不一定是磁盘或权限问题。')
                # Do not log exception text: it may contain reply text or IDs.
                logging.getLogger(__name__).error('WeChat reply save failed (%s)', type(exc).__name__)
                error = WechatError("WECHAT_REPLY_SAVE_FAILED", "微信回复未能保存。", hint, True)
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
        except Exception:
            error = _error(ConnectionError())  # never surface exception text
            self._set(state="error", available=False, logged_in=False, account=None,
                      error=error.as_dict(), last_checked_at=_now())
        return self.status()

    def _require_login(self):
        status = self.check()
        if not status["available"]:
            detail = status.get("error") or {"code": "WECHAT_DISABLED", "message": "微信配置未启用。", "hint": "请启用并扫码登录。"}
            raise WechatError(detail["code"], detail["message"], detail.get("hint", ""), True)
        return status

    def avatar(self):
        """Best-effort account avatar; never affects login or message routing."""
        client = self._client
        if client is None:
            return {'account_id': None, 'image': None}
        try:
            return self._call(client.avatar(), 8)
        except Exception:
            return {'account_id': None, 'image': None}

    async def _send(self, record, account_id, concurrent_waiting=False):
        client = self._client
        if client is None:
            raise WechatError("WECHAT_UNAVAILABLE", "微信连接已断开。", "将尝试可用的邮箱。", True)
        # Revalidate identity inside the same event loop as send; the recipient
        # is the constant filehelper conversation, so no contact lookup remains.
        async def preflight():
            current = await client.probe()
            if not current or current["id"] != account_id:
                raise WechatError("WECHAT_ACCOUNT_CHANGED", "微信登录账号发生变化。", "请重新扫码登录。", True)
        try:
            await asyncio.wait_for(preflight(), 5)
        except Exception as exc:
            raise _error(exc) from None
        account = self.status().get("account") or {}
        if self._client is not client or account.get("id") != account_id:
            raise WechatError("WECHAT_ACCOUNT_CHANGED", "微信连接或登录账号发生变化。",
                              "请确认登录状态后重试。", True)
        label = "Request" if record["kind"] == "ask" else "Notice"
        text = (record.get("agent_name") or "Agent") + " · " + label + "\n" + record["subject"] + "\n\n" + record["body"].rstrip()
        text += "\n\n[AC:" + record["id"] + "]"
        if record["kind"] == "ask":
            text += "\n回复截止：" + _format_deadline(record.get("deadline_at"))
            if concurrent_waiting:
                text += "\n当前有多条消息等待回复，请使用引用回复"
        try:
            message_id = await asyncio.wait_for(client.send(text), 10)
        except Exception as exc:
            raise _error(exc, sending=True) from None
        return {"message_id": message_id or None}

    def send(self, record, concurrent_waiting=False):
        status = self._require_login()
        account_id = record.get("wechat_account_id") or record.get("account_id")
        if not account_id or account_id != status["account"]["id"]:
            raise WechatError("WECHAT_ACCOUNT_CHANGED", "微信账号与请求绑定的账号不一致。", "请重新扫码登录后创建请求。", True)
        if record.get("target_contact_id") and record["target_contact_id"] != FILEHELPER_ID:
            raise WechatError("WECHAT_ACCOUNT_CHANGED", "请求绑定的微信对话已不存在。", "此请求创建于旧版联系人配置；请重新创建。", True)
        try:
            return self._call(self._send(record, account_id, concurrent_waiting), OPERATION_TIMEOUT)
        except (TimeoutError, concurrent.futures.TimeoutError, concurrent.futures.CancelledError) as exc:
            # A deadline or shutdown cancellation can race with send acceptance.
            # Do not classify cancellation as a safe preflight connection failure.
            raise _error(exc, sending=True) from None
        except Exception as exc:
            raise _error(exc) from None

    def send_note(self, account_id, contact_id, text):
        """Best-effort one-line status note; never creates or mutates records."""
        if threading.current_thread() is self._thread:
            # _call would wait on the very loop this thread is running; a note
            # must only ever be delivered from a service thread.
            raise WechatError("WECHAT_FEEDBACK_THREAD", "状态通知不能在微信事件线程内发送。", "由服务线程投递。", True)
        status = self._require_login()
        client = self._client
        if client is None or not contact_id or account_id != status["account"]["id"]:
            raise WechatError("WECHAT_ACCOUNT_CHANGED", "微信连接或账号与请求绑定的不一致。", "状态通知未发送。", True)

        async def deliver():
            current = await client.probe()
            if not current or current["id"] != account_id:
                raise WechatError("WECHAT_ACCOUNT_CHANGED", "微信登录账号发生变化。", "状态通知未发送。", True)
            return await client.send(text[:MAX_TEXT])

        try:
            return self._call(deliver(), OPERATION_TIMEOUT)
        except Exception as exc:
            raise _error(exc) from None

    def stop(self):
        with self._lifecycle:
            self._generation += 1
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

"""Application validation and background mail processing."""

import hashlib
import json
import logging
import re
import threading
import time

from gateway.store import stamp, utcnow


PROVIDERS = [
    dict(id='icloud', name='iCloud', smtp_host='smtp.mail.me.com', smtp_port=587, smtp_security='starttls', imap_host='imap.mail.me.com', imap_port=993, imap_security='ssl', hint='在 Apple 账户生成 App 专用密码；使用完整 iCloud 邮箱地址登录。'),
    dict(id='gmail', name='Gmail', smtp_host='smtp.gmail.com', smtp_port=465, smtp_security='ssl', imap_host='imap.gmail.com', imap_port=993, imap_security='ssl', hint='开启两步验证并生成应用专用密码。部分 Workspace 组织会禁用此方式。'),
    dict(id='163', name='163 邮箱', smtp_host='smtp.163.com', smtp_port=465, smtp_security='ssl', imap_host='imap.163.com', imap_port=993, imap_security='ssl', hint='在邮箱设置中开启 IMAP/SMTP，并使用客户端授权码。'),
    dict(id='qq', name='QQ 邮箱', smtp_host='smtp.qq.com', smtp_port=465, smtp_security='ssl', imap_host='imap.qq.com', imap_port=993, imap_security='ssl', hint='在邮箱设置中开启 IMAP/SMTP，使用生成的授权码。'),
    dict(id='custom', name='其他邮箱', smtp_host='', smtp_port=465, smtp_security='ssl', imap_host='', imap_port=993, imap_security='ssl', hint='填写服务商提供的 SMTP 和 IMAP 地址，必须使用 TLS 加密连接。'),
]
DEFAULT_CONFIG = {**{k: v for k, v in PROVIDERS[0].items() if k not in ('id', 'name', 'hint')},
                  'provider': 'icloud', 'email': '', 'username': '', 'target_email': '',
                  'imap_folder': 'INBOX', 'poll_interval': 10}
FIELD_LABELS = {'subject': '邮件主题', 'body': '邮件正文', 'agent_name': 'Agent 名称',
                'email': '发件邮箱地址', 'target_email': '目标邮箱地址', 'username': '登录用户名',
                'smtp_host': 'SMTP 服务器地址', 'imap_host': 'IMAP 服务器地址',
                'smtp_port': 'SMTP 端口', 'imap_port': 'IMAP 端口', 'imap_folder': '收件文件夹',
                'poll_interval': '收件检查间隔（秒）', 'timeout_seconds': '回复期限（秒）'}


class APIError(Exception):
    def __init__(self, code, message, hint='', status=422, request_id=None):
        super().__init__(message)
        self.error = {'code': code, 'message': message, 'hint': hint}
        self.status, self.request_id = status, request_id


def text_field(data, name, max_length, required=True, single_line=True):
    value = data.get(name, '')
    label = FIELD_LABELS.get(name, name)
    if not isinstance(value, str) or len(value) > max_length or '\x00' in value:
        raise APIError('INVALID_INPUT', f'{label}格式无效或过长。')
    value = value.strip()
    if required and not value:
        raise APIError('INVALID_INPUT', f'请填写{label}。')
    if single_line and any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise APIError('INVALID_INPUT', f'{label}不允许包含换行或控制字符。')
    return value


def integer(data, name, default, minimum, maximum):
    value = data.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise APIError('INVALID_INPUT', f'{FIELD_LABELS.get(name, name)}必须是 {minimum}–{maximum} 之间的整数。')
    return value


def email_address(data, name):
    value = text_field(data, name, 254)
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', value):
        raise APIError('INVALID_EMAIL', f'{FIELD_LABELS.get(name, name)}不是有效的邮箱地址。', '请填写完整的邮箱地址，不要包含姓名或尖括号。')
    return value


class Gateway:
    def __init__(self, store, transport=None, wechat_transport=None):
        self.store = store
        if transport is None:
            from gateway import mail
            transport = mail
        self.transport = transport
        self.stop_event = threading.Event()
        self.threads = []
        self.poll_error = store.setting('poll_error')
        self.last_poll_at = store.setting('last_poll_at')
        self.diagnostic_lock = threading.Lock()
        self.imap_scan_state = {}
        self.imap_scan_identity = None
        from gateway.channels import Channels
        self.channels = Channels(store, self.receive_wechat, wechat_transport)
        from gateway.live import LiveInbox
        self.live = LiveInbox(store, self.channels)

    def config_view(self):
        config = {**DEFAULT_CONFIG, **self.store.config()}
        config['password_set'] = bool(config.pop('password', ''))
        return {'config': config, 'providers': PROVIDERS, 'wechat': self.wechat_view(),
                'service': {'configured': bool(self.store.config()) or self.channels.config()['enabled'], 'email_configured': bool(self.store.config()), 'poll_error': self.poll_error, 'last_poll_at': self.last_poll_at}}

    def wechat_view(self, probe=False):
        return {**self.channels.view(probe), 'efficient_mode': self.live.view()}

    def save_wechat_config(self, data):
        try:
            return self.channels.save(data)
        except APIError:
            raise
        except Exception as exc:
            from gateway.channels import safe_error
            error = safe_error(exc)
            raise APIError(error['code'], error['message'], error['hint'], 503) from None

    def receive_wechat(self, item):
        # Inbound events arrive on the WeChat event-loop thread; sending a
        # status note from here would block that loop on itself for the whole
        # operation timeout, so feedback is left for the deadlines thread,
        # which drains the queue twice a second.
        return self.live.receive(item)

    def deliver_wechat_feedback(self):
        """Send queued one-line status notes; failures only leave a diagnostic."""
        for note in self.store.drain_feedback():
            try:
                self.channels.wechat.send_note(note['account_id'], note['contact_id'], note['text'])
            except Exception:
                self.store.diagnostic([{'name': '微信状态通知', 'ok': False, 'error': dict(
                    code='WECHAT_FEEDBACK_FAILED', message='状态通知未能发出。', hint='不影响请求与回复本身。')}], channel='wechat')

    def save_config(self, data):
        fields = set(DEFAULT_CONFIG) | {'password', 'password_set'}
        if set(data) - fields:
            raise APIError('INVALID_INPUT', '配置包含不支持的字段。')
        config = {**DEFAULT_CONFIG, **self.store.config(), **data}
        if config['provider'] not in tuple(p['id'] for p in PROVIDERS):
            raise APIError('INVALID_INPUT', '请选择有效的邮箱服务。')
        for field in ('email', 'target_email'):
            config[field] = email_address(config, field)
        config['username'] = text_field(config, 'username', 254, required=False) or config['email']
        for prefix in ('smtp', 'imap'):
            host = text_field(config, prefix + '_host', 253)
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
                raise APIError('INVALID_HOST', '邮件服务器地址无效。', '只填写主机名，不要包含协议、路径或端口。')
            config[prefix + '_host'] = host
            config[prefix + '_port'] = integer(config, prefix + '_port', 465 if prefix == 'smtp' else 993, 1, 65535)
            if config[prefix + '_security'] not in ('ssl', 'starttls'):
                raise APIError('TLS_REQUIRED', '邮件连接必须启用 SSL/TLS 或 STARTTLS。')
        config['imap_folder'] = text_field(config, 'imap_folder', 128)
        config['poll_interval'] = integer(config, 'poll_interval', 10, 10, 300)
        password = data.get('password', '')
        if not isinstance(password, str) or len(password) > 1024 or any(c in password for c in '\r\n\x00'):
            raise APIError('INVALID_INPUT', '邮箱授权码格式无效。')
        old = self.store.config()
        identity = ('provider', 'email', 'username', 'smtp_host', 'imap_host')
        if not password:
            if old and all(config[k] == old[k] for k in identity):
                password = old['password']
            else:
                raise APIError('PASSWORD_REQUIRED', '请填写邮箱授权码或应用专用密码。')
        config['password'] = password
        config.pop('password_set', None)
        with self.store.lock:
            if self.store.has_active() and config != old:
                raise APIError('REQUESTS_ACTIVE', '仍有正在发送或等待回复的请求。', '请等待请求结束后再修改邮箱配置，避免丢失回复。', 409)
            self.store.set_setting('config', config)
        return self.config_view()

    def create(self, kind, data, key=None):
        if key is not None and (not 8 <= len(key) <= 200 or not re.fullmatch(r'[A-Za-z0-9._:-]+', key)):
            raise APIError('INVALID_IDEMPOTENCY_KEY', 'Idempotency-Key 必须是 8–200 位字母、数字或 . _ : -。')
        fingerprint = hashlib.sha256(json.dumps([kind, data], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        # A fresh probe happens before taking the SQLite lock, and again in the
        # sender. A cached login flag cannot select the transport for a request.
        requested = data.get('channel', 'auto')
        selection = self.channels.selection(requested if isinstance(requested, str) else 'auto')
        # Deduplication and creation share one lock, including requests from different HTTP threads.
        with self.store.lock:
            existing = self.store.by_key(key) if key else None
            if existing:
                record, saved_fingerprint = existing
                if fingerprint != saved_fingerprint:
                    raise APIError('IDEMPOTENCY_CONFLICT', '此重试标识已用于不同的请求。', '同一请求重试时应保持内容不变。', 409)
                return record
            payload = {}
            try:
                if set(data) - {'subject', 'body', 'agent_name', 'timeout_seconds', 'channel'}:
                    raise APIError('INVALID_INPUT', '请求包含不支持的字段。', '收件人固定为配置中的目标邮箱。')
                payload['subject'] = text_field(data, 'subject', 200)
                payload['body'] = text_field(data, 'body', 50000, single_line=False)
                payload['agent_name'] = text_field(data, 'agent_name', 80, required=False) or 'Agent'
                if requested not in ('auto', 'wechat', 'email'):
                    raise APIError('INVALID_INPUT', '通道应为 auto、wechat 或 email。')
                payload['channel'] = requested
                wx = self.channels.config()
                payload.update(_channel=selection['selected_channel'] or ('wechat' if requested == 'wechat' else 'email'),
                               _target_contact_id=wx['target_contact_id'], _wechat_account_id=wx['account_id'],
                               _target_contact_name=wx['target_contact_name'],
                               _recipient_label=wx['target_contact_name'] if selection['selected_channel'] == 'wechat' else self.store.config().get('target_email', ''),
                               _fallback_reason=selection['fallback_reason'])
                if kind == 'ask':
                    payload['timeout_seconds'] = integer(data, 'timeout_seconds', 300, 30, 86400)
                if not selection['selected_channel']:
                    reason = selection['wechat'].get('error') if requested == 'wechat' else None
                    if reason:
                        raise APIError(reason['code'], reason['message'], reason.get('hint', ''), 409)
                    raise APIError('NOT_CONFIGURED', '没有可用的通知通道。', '请登录微信并选择联系人，或保存邮箱配置作为备用通道。', 409)
                if self.store.active_count() >= 100:
                    raise APIError('QUEUE_FULL', '同时进行的请求已达到 100 条。', '请等待现有请求结束后，以新的请求标识重试。', 429)
            except APIError as exc:
                safe = {k: str(data.get(k, ''))[:50000 if k == 'body' else 200] for k in ('subject', 'body', 'agent_name')}
                safe['timeout_seconds'] = 300
                safe.update({k: v for k, v in payload.items() if k.startswith('_') or k == 'channel'})
                record = self.store.insert(kind, safe, self.store.config(), key, fingerprint, exc.error)
                exc.request_id = record['id']
                raise
            return self.store.insert(kind, payload, self.store.config(), key, fingerprint)

    def test_config(self, channel='email'):
        if channel not in ('email', 'wechat', 'auto'):
            raise APIError('INVALID_INPUT', '请选择有效的连接检查通道。')
        if channel == 'wechat' or (channel == 'auto' and self.channels.selection()['selected_channel'] == 'wechat'):
            return self.channels.check()
        if not self.diagnostic_lock.acquire(blocking=False):
            raise APIError('TEST_IN_PROGRESS', '邮箱连接检查正在进行，请稍后。', status=409)
        try:
            config = self.store.config()
            if not config:
                error = {'code': 'NOT_CONFIGURED', 'message': '请先保存邮箱配置。', 'hint': '填写发送邮箱、授权码和目标邮箱。'}
                checks = [{'name': 'SMTP', 'ok': False, 'error': error}, {'name': 'IMAP', 'ok': False, 'error': error}]
            else:
                checks = self.transport.test_connection(config)
            record = self.store.diagnostic(checks)
            return {'ok': all(c['ok'] for c in checks), 'checks': checks, 'record_id': record['id']}
        finally:
            self.diagnostic_lock.release()

    def send_once(self):
        record = self.store.claim_next()
        if not record:
            return False
        try:
            selection = self.channels.selection(record.get('requested_channel', 'auto'))
            channel = selection['selected_channel']
            if not channel:
                from gateway.channels import channel_error
                self.store.fail(record['id'], selection['wechat'].get('error') or channel_error('NO_CHANNEL_AVAILABLE', '微信不可用且没有可用的邮箱配置。', '请恢复微信登录或配置备用邮箱。'))
                return True
            record = self.store.set_route(record['id'], channel, selection['fallback_reason'])
            if channel == 'wechat':
                try:
                    result = self.channels.wechat.send(record, concurrent_waiting=(
                        record['kind'] == 'ask' and self.store.concurrent_waiting_asks(record)))
                except Exception as exc:
                    from gateway.channels import safe_error
                    error = safe_error(exc)
                    if getattr(exc, 'safe_to_fallback', False) and record.get('requested_channel', 'auto') == 'auto' and self.store.config():
                        record = self.store.set_route(record['id'], 'email', error)
                    else:
                        self.store.fail(record['id'], error)
                        return True
                else:
                    self.store.mark_sent(record['id'], result.get('message_id') if isinstance(result, dict) else None)
                    return True
            self.transport.send_message(self.store.config(), record)
        except Exception as exc:
            self.store.fail(record['id'], self.transport.explain_error(exc, 'smtp'))
        else:
            self.store.mark_sent(record['id'])
        return True

    def poll_once(self):
        config = self.store.config()
        if not config:
            self.store.expire()
            return
        started_at = utcnow()
        mailbox_checked = False
        try:
            identity = tuple(config.get(key) for key in
                             ('imap_host', 'imap_port', 'imap_security', 'username', 'email', 'imap_folder'))
            if identity != self.imap_scan_identity:
                self.imap_scan_state.clear()
                self.imap_scan_identity = identity
            records = self.store.poll_records()
            if records:
                for reply in self.transport.poll_replies(config, records, scan_state=self.imap_scan_state):
                    if reply.get('ignored_reason'):
                        self.store.add_ignored_reply(reply)
                    else:
                        self.store.add_reply(reply)
                mailbox_checked = True
            if self.poll_error:
                self.store.set_setting('poll_error', None)
            self.poll_error = None
            self.last_poll_at = stamp()
            self.store.set_setting('last_poll_at', self.last_poll_at)
        except Exception as exc:
            # Revisit fetched messages if persisting any result failed after scanning.
            self.imap_scan_state.clear()
            error = self.transport.explain_error(exc, 'imap')
            if error != self.poll_error:
                self.store.diagnostic([{'name': 'IMAP 收件检查', 'ok': False, 'error': error}])
                self.store.set_setting('poll_error', error)
            self.poll_error = error
        finally:
            if mailbox_checked:
                self.store.expire(started_at, mailbox_checked=True)
            self.store.expire()
            self.deliver_wechat_feedback()

    def start(self):
        self.channels.start()
        def sender():
            while not self.stop_event.is_set():
                try:
                    if self.send_once():
                        continue
                except Exception:
                    logging.error('邮件队列处理异常，请检查磁盘空间及数据目录权限。')
                self.stop_event.wait(.5)

        def receiver():
            while not self.stop_event.is_set():
                try:
                    self.poll_once()
                except Exception:
                    logging.error('收件状态保存失败，请检查磁盘空间及数据目录权限。')
                with self.store.changed:
                    delay = self.store.next_poll_delay(self.store.config().get('poll_interval', 10))
                    if not self.stop_event.is_set():
                        self.store.changed.wait(delay)

        def deadlines():
            while not self.stop_event.wait(.5):
                try:
                    self.store.expire()
                    self.deliver_wechat_feedback()
                except Exception:
                    logging.error('等待期限更新失败。')

        for target in (sender, receiver, deadlines):
            thread = threading.Thread(target=target, daemon=True, name=target.__name__)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        self.channels.wechat.stop()
        with self.store.changed:
            self.store.changed.notify_all()
        for thread in self.threads:
            thread.join(timeout=1)

    def wait_request(self, request_id, seconds=0):
        until = time.monotonic() + seconds
        with self.store.changed:
            while True:
                record = self.store.get(request_id)
                if not record:
                    raise APIError('NOT_FOUND', '没有找到这条请求记录。', status=404)
                remaining = until - time.monotonic()
                if record['status'] in ('sent', 'failed', 'replied', 'timed_out') or remaining <= 0:
                    return record
                self.store.changed.wait(min(remaining, 1))

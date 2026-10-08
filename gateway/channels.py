"""WeChat configuration, live routing checks and local QR rendering."""

import base64
import io

from gateway.wechat import FILEHELPER_ID, FILEHELPER_NAME


DEFAULT_WECHAT = dict(enabled=False, target_contact_id='', target_contact_name='', account_id='')


def channel_error(code, message, hint=''):
    return dict(code=code, message=message, hint=hint)


def safe_error(exc):
    if callable(getattr(exc, 'as_dict', None)):
        return {k: v for k, v in exc.as_dict().items() if k in ('code', 'message', 'hint')}
    return channel_error('WECHAT_ERROR', '微信服务暂时不可用。', '请检查微信登录和服务状态；自动模式会使用邮箱备用通道。')


class Channels:
    def __init__(self, store, on_message, transport=None):
        self.store = store
        if transport is None:
            from gateway.wechat import WechatGateway
            transport = WechatGateway(store.directory, on_message=on_message, on_event=self.on_event)
        self.wechat = transport

    def config(self):
        stored = self.store.setting('wechat_config', {})
        stale_binding = stored.get('target_contact_id') not in ('', FILEHELPER_ID)
        if not set(stored) <= set(DEFAULT_WECHAT) or stale_binding:
            # One-time upgrade migration: settings from the contact-based
            # puppet transport (mode/endpoint/token, an old contact binding)
            # are meaningless under the filehelper self-chat transport.
            config = {**DEFAULT_WECHAT, **{k: v for k, v in stored.items() if k in DEFAULT_WECHAT}}
            if stale_binding:
                config.update(target_contact_id='', target_contact_name='', account_id='')
            with self.store.lock:
                self.store.set_setting('wechat_config', config)
            return config
        return {**DEFAULT_WECHAT, **stored}

    def start(self):
        if self.config()['enabled']:
            self.wechat.start(self.config())

    def status(self, probe=False):
        config = self.config()
        if not config['enabled']:
            return dict(state='disabled', logged_in=False, available=False, enabled=False,
                        account=None, qr_code=None, error=None, last_checked_at=None)
        try:
            status = dict(self.wechat.check() if probe else self.wechat.status())
        except Exception as exc:
            status = dict(state='error', logged_in=False, available=False, account=None, error=safe_error(exc))
        status['enabled'] = True
        if status.get('available'):
            account = status.get('account') or {}
            if not config['target_contact_id']:
                status['available'] = False
                status['error'] = channel_error('WECHAT_TARGET_REQUIRED', '微信已登录，正在绑定文件传输助手。')
            elif account.get('id') != config['account_id']:
                status['available'] = False
                status['error'] = channel_error('WECHAT_ACCOUNT_CHANGED', '当前微信账号已改变，请重新扫码登录。')
        return status

    def view(self, probe=False):
        config = self.config()
        status = self.status(probe)
        status['qr_image'] = None
        qr = status.get('qr_code')
        if isinstance(qr, str) and 0 < len(qr) <= 4096:
            try:
                import qrcode
                import qrcode.image.svg
                out = io.BytesIO()
                qrcode.make(qr, image_factory=qrcode.image.svg.SvgPathImage, border=4).save(out)
                status['qr_image'] = 'data:image/svg+xml;base64,' + base64.b64encode(out.getvalue()).decode()
            except (ImportError, ValueError):
                status['error'] = channel_error('WECHAT_QR_UNAVAILABLE', '无法生成登录二维码。', '请使用包含微信依赖的 agentCall Docker 镜像。')
        # UI-only companion field: the account avatar shown from 待确认 until
        # the login state is lost. Never reaches the agent-visible selection.
        status['avatar_image'] = None
        if status.get('logged_in') or status.get('qr_status') in ('Scanned', 'Confirmed'):
            try:
                avatar = self.wechat.avatar()
                image = avatar.get('image')
                if isinstance(image, str) and image.startswith('data:image/') and len(image) < 710000:
                    status['avatar_image'] = image
            except Exception:
                pass
        return {'config': config, 'status': status}

    def _auto_bind(self, account_id):
        """Bind the filehelper conversation to the signed-in account.

        Reply correlation is account-scoped and the target is a constant, so
        binding during live requests cannot re-target an old question; this
        deliberately bypasses the REQUESTS_ACTIVE guard used by save().
        """
        config = self.config()
        config.update(target_contact_id=FILEHELPER_ID, target_contact_name=FILEHELPER_NAME,
                      account_id=account_id)
        with self.store.lock:
            self.store.set_setting('wechat_config', config)
            self.store.changed.notify_all()

    def _try_auto_bind(self):
        try:
            live = self.wechat.check()
        except Exception:
            return
        if live.get('logged_in') and (live.get('account') or {}).get('id'):
            self._auto_bind(live['account']['id'])

    def save(self, data):
        from gateway.service import APIError
        old = self.config()
        if set(data) - {'enabled'}:
            raise APIError('INVALID_INPUT', '微信配置包含不支持的字段。')
        config = {**old, **{k: v for k, v in data.items() if k in DEFAULT_WECHAT}}
        if type(config['enabled']) is not bool:
            raise APIError('INVALID_INPUT', '微信启用开关需要布尔值。')
        with self.store.lock:
            active = self.store.db.execute("""SELECT 1 FROM records WHERE status IN ('queued','sending')
                OR (status='waiting' AND json_extract(data,'$.channel')='wechat') LIMIT 1""").fetchone()
            if active and config != old:
                raise APIError('REQUESTS_ACTIVE', '仍有正在发送或等待回复的请求。', '请等待请求结束后再修改微信配置。', 409)
            self.store.set_setting('wechat_config', config)
            self.store.changed.notify_all()
        if config['enabled'] != old['enabled']:
            self.wechat.stop()
            self.start()
        elif config['enabled'] and not config['account_id']:
            # Enabled unchanged but unbound (e.g. right after the legacy
            # migration cleared an old contact binding): bind if logged in.
            self._try_auto_bind()
        return self.view()

    def login(self):
        from gateway.service import APIError
        if not self.config()['enabled']:
            raise APIError('WECHAT_DISABLED', '请先启用并保存微信配置。', status=409)
        with self.store.lock:
            active = self.store.db.execute("""SELECT 1 FROM records WHERE status IN ('queued','sending')
                OR (status='waiting' AND json_extract(data,'$.channel')='wechat') LIMIT 1""").fetchone()
            if active:
                raise APIError('REQUESTS_ACTIVE', '仍有微信请求正在发送或等待回复，请结束后再刷新二维码。', status=409)
        self.wechat.start(self.config(), reset_login=True)
        return self.view()

    def logout(self):
        # Explicit logout stays disabled across restarts until re-enabled.
        config = self.config()
        config['enabled'] = False
        self.store.set_setting('wechat_config', config)
        self.wechat.stop()
        return self.view()

    def check(self):
        status = self.status(probe=True)
        error = status.get('error') or channel_error('WECHAT_OFFLINE', '微信尚未登录或连接已失效。', '请扫码登录文件传输助手。')
        checks = [{'name': '微信登录与文件传输助手', 'ok': bool(status.get('available')), **({} if status.get('available') else {'error': error})}]
        record = self.store.diagnostic(checks, channel='wechat')
        return dict(ok=all(c['ok'] for c in checks), checks=checks, record_id=record['id'], status=status)

    def selection(self, requested='auto'):
        wx = self.status(probe=True)
        config = self.store.config()
        email = bool(config) and config.get('enabled', True)
        selected = 'wechat' if wx.get('available') else ('email' if email else None)
        reason = None
        if requested == 'email':
            selected = 'email' if email else None
        elif requested == 'wechat':
            selected = 'wechat' if wx.get('available') else None
        elif wx.get('enabled') and not wx.get('available'):
            reason = wx.get('error') or channel_error('WECHAT_OFFLINE', '微信登录或消息连接不可用，已选择邮箱备用通道。', '可以重新扫码登录微信。')
        wx.pop('qr_code', None)
        wx.pop('qr_image', None)
        wx.pop('avatar_image', None)
        return dict(selected_channel=selected, available=bool(selected), wechat=wx,
                    email={'configured': email}, fallback_reason=reason)

    def on_event(self, event):
        # QR and authentication material live only in the transport's memory.
        if isinstance(event, dict) and event.get('type') == 'logged_in':
            account = self.wechat.status().get('account') or {}
            if account.get('id'):
                self._auto_bind(account['id'])
        error = event.get('error') if isinstance(event, dict) else None
        if error and error != self.store.setting('wechat_last_error'):
            self.store.diagnostic([{'name': '微信连接', 'ok': False, 'error': error}], channel='wechat')
        self.store.set_setting('wechat_last_error', error)

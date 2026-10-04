"""WeChat configuration, live routing checks and local QR rendering."""

import base64
import io
import re


DEFAULT_WECHAT = dict(enabled=False, mode='local', service_endpoint='', service_token='',
                      target_contact_id='', target_contact_name='', account_id='')


def channel_error(code, message, hint=''):
    return dict(code=code, message=message, hint=hint)


def safe_error(exc):
    if callable(getattr(exc, 'as_dict', None)):
        return {k: v for k, v in exc.as_dict().items() if k in ('code', 'message', 'hint')}
    return channel_error('WECHAT_ERROR', '微信服务暂时不可用。', '请检查微信登录和容器状态；自动模式会使用邮箱备用通道。')


class Channels:
    def __init__(self, store, on_message, transport=None):
        self.store = store
        if transport is None:
            from gateway.wechat import WechatGateway
            transport = WechatGateway(store.directory, on_message=on_message, on_event=self.on_event)
        self.wechat = transport

    def config(self):
        return {**DEFAULT_WECHAT, **self.store.setting('wechat_config', {})}

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
                status['error'] = channel_error('WECHAT_TARGET_REQUIRED', '微信已登录，请选择目标联系人。')
            elif account.get('id') != config['account_id']:
                status['available'] = False
                status['error'] = channel_error('WECHAT_ACCOUNT_CHANGED', '当前微信账号已改变，请重新选择目标联系人。')
        return status

    def view(self, probe=False):
        config = self.config()
        config['service_token_set'] = bool(config.pop('service_token'))
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
        return {'config': config, 'status': status}

    def save(self, data):
        from gateway.service import APIError
        old = self.config()
        allowed = set(DEFAULT_WECHAT) - {'account_id'}
        if set(data) - allowed - {'service_token_set'}:
            raise APIError('INVALID_INPUT', '微信配置包含不支持的字段。')
        config = {**old, **{k: v for k, v in data.items() if k in allowed}}
        config['target_contact_name'] = old['target_contact_name']
        if type(config['enabled']) is not bool or config['mode'] not in ('local', 'external'):
            raise APIError('INVALID_INPUT', '请选择有效的微信连接模式。')
        for field in ('service_endpoint', 'service_token', 'target_contact_id', 'target_contact_name'):
            value = config[field]
            if not isinstance(value, str) or len(value) > (2048 if field == 'service_token' else 254) or any(ord(c) < 32 for c in value):
                raise APIError('INVALID_INPUT', '微信配置格式无效。')
            config[field] = value.strip()
        if not config['service_token']:
            config['service_token'] = old['service_token'] if config['service_endpoint'] == old['service_endpoint'] else ''
        if config['mode'] == 'external':
            if not re.fullmatch(r'(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]):[0-9]{1,5}', config['service_endpoint']):
                raise APIError('INVALID_INPUT', '请填写微信 Puppet 服务的主机名和端口。', '例如 puppet.example.com:8788；外部服务必须支持受信任的 TLS。')
            if not 1 <= int(config['service_endpoint'].rsplit(':', 1)[1]) <= 65535 or not config['service_token']:
                raise APIError('INVALID_INPUT', '外部微信服务需要有效端口和服务令牌。')
        # Selecting a target binds the choice to this signed-in account. Never
        # authorize an arbitrary contact ID supplied by a client or reused account.
        if config['target_contact_id'] and ('target_contact_id' in data):
            live = self.wechat.check()
            if not live.get('logged_in'):
                raise APIError('WECHAT_LOGIN_REQUIRED', '请先扫码登录微信，再选择目标联系人。', status=409)
            contact = self.wechat.contact(config['target_contact_id'])
            if not contact:
                raise APIError('WECHAT_CONTACT_NOT_FOUND', '目标联系人不在当前微信账号的联系人列表中。')
            config['target_contact_name'] = contact.get('alias') or contact.get('name') or contact['id']
            config['account_id'] = (live.get('account') or {}).get('id', '')
        elif not config['target_contact_id']:
            config['account_id'], config['target_contact_name'] = '', ''
        with self.store.lock:
            active = self.store.db.execute("""SELECT 1 FROM records WHERE status IN ('queued','sending')
                OR (status='waiting' AND json_extract(data,'$.channel')='wechat') LIMIT 1""").fetchone()
            if active and config != old:
                raise APIError('REQUESTS_ACTIVE', '仍有正在发送或等待回复的请求。', '请等待请求结束后再修改微信配置。', 409)
            self.store.set_setting('wechat_config', config)
        connection_changed = any(config[k] != old[k] for k in ('enabled', 'mode', 'service_endpoint', 'service_token'))
        if connection_changed:
            self.wechat.stop()
            self.start()
        return self.view()

    def login(self):
        from gateway.service import APIError
        if not self.config()['enabled']:
            raise APIError('WECHAT_DISABLED', '请先启用并保存微信配置。', status=409)
        self.wechat.stop()
        self.start()
        return self.view()

    def logout(self):
        # Explicit logout stays disabled across restarts until re-enabled.
        config = self.config()
        config['enabled'] = False
        self.store.set_setting('wechat_config', config)
        self.wechat.stop()
        return self.view()

    def contacts(self, query=''):
        from gateway.service import APIError
        if not self.config()['enabled']:
            raise APIError('WECHAT_DISABLED', '请先启用并登录微信。', status=409)
        try:
            return {'items': self.wechat.contacts(query=query[:100], limit=100)}
        except Exception as exc:
            error = safe_error(exc)
            raise APIError(error['code'], error['message'], error['hint'], 503) from None

    def check(self):
        status = self.status(probe=True)
        error = status.get('error') or channel_error('WECHAT_OFFLINE', '微信尚未登录或连接已失效。', '请扫码登录并选择目标联系人。')
        checks = [{'name': '微信登录与目标联系人', 'ok': bool(status.get('available')), **({} if status.get('available') else {'error': error})}]
        if status.get('available'):
            try:
                if not self.wechat.contact(self.config()['target_contact_id']):
                    raise ValueError('missing contact')
            except Exception:
                status['available'] = False
                checks = [{'name': '微信目标联系人', 'ok': False, 'error': channel_error('WECHAT_CONTACT_UNAVAILABLE', '无法访问保存的微信联系人。', '请重新加载联系人并选择。')}]
        record = self.store.diagnostic(checks, channel='wechat')
        return dict(ok=all(c['ok'] for c in checks), checks=checks, record_id=record['id'], status=status)

    def selection(self, requested='auto'):
        wx = self.status(probe=True)
        email = bool(self.store.config())
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
        return dict(selected_channel=selected, available=bool(selected), wechat=wx,
                    email={'configured': email}, fallback_reason=reason)

    def on_event(self, event):
        # QR and authentication material live only in the transport's memory.
        error = event.get('error') if isinstance(event, dict) else None
        if error and error != self.store.setting('wechat_last_error'):
            self.store.diagnostic([{'name': '微信连接', 'ok': False, 'error': error}], channel='wechat')
        self.store.set_setting('wechat_last_error', error)

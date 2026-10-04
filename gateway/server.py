"""Local HTTP API and same-origin frontend, with no third-party runtime."""

import hashlib
import hmac
import io
import json
import logging
import math
import mimetypes
import secrets
import threading
import time
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from gateway import __version__
from gateway.service import APIError

ROOT = Path(__file__).resolve().parent.parent


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app):
        self.app = app
        self.instance_id = secrets.token_hex(12)
        self.session_secret = secrets.token_bytes(32)
        self.slots = threading.BoundedSemaphore(64)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        logging.error('本地 HTTP 请求未能完成。')


class Handler(BaseHTTPRequestHandler):
    server_version = 'EmailCall/1.0'
    sys_version = ''

    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, format, *args):
        # Avoid logging subjects, mailbox addresses, tokens or URL query parameters.
        pass

    def respond(self, status, data, content_type='application/json; charset=utf-8', headers=None):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def check_origin(self):
        port = self.server.server_address[1]
        allowed = {f'localhost:{port}', f'127.0.0.1:{port}', f'[::1]:{port}'}
        host = self.headers.get('Host', '')
        if host not in allowed:
            raise APIError('HOST_REJECTED', '此服务仅接受本机地址访问。', status=403)
        origin = self.headers.get('Origin')
        if origin is not None and origin != 'http://' + host:
            raise APIError('ORIGIN_REJECTED', '已阻止来自其他网站的请求。', status=403)
        if self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            raise APIError('ORIGIN_REJECTED', '请直接打开本机前端页面。', status=403)

    def signature(self, value):
        return hmac.new(self.server.session_secret, value.encode(), hashlib.sha256).hexdigest()

    def session(self):
        seed = f'{int(time.time())}.{secrets.token_urlsafe(24)}'
        cookie = seed + '.' + self.signature(seed)
        return self.respond(200, {'csrf_token': self.signature('csrf:' + cookie)}, headers={
            'Set-Cookie': f'emailcall_session={cookie}; HttpOnly; SameSite=Strict; Path=/api; Max-Age=86400'})

    def authenticate(self, ui_only=False):
        authorization = self.headers.get('Authorization', '')
        if authorization:
            token = authorization.removeprefix('Bearer ')
            if not authorization.startswith('Bearer ') or not secrets.compare_digest(token.encode(), self.server.app.store.token().encode()):
                raise APIError('UNAUTHORIZED', 'API 令牌无效或已更新。', '请重新导出并安装 skill。', 401)
            if ui_only:
                raise APIError('UI_ONLY', '此接口仅供本机前端使用。', status=403)
            return
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
            cookie = cookies['emailcall_session'].value
            seed, signature = cookie.rsplit('.', 1)
            age = time.time() - int(seed.split('.', 1)[0])
            valid = 0 <= age < 86400 and secrets.compare_digest(signature.encode(), self.signature(seed).encode())
            csrf = self.headers.get('X-CSRF-Token', '')
            valid = valid and secrets.compare_digest(csrf.encode(), self.signature('csrf:' + cookie).encode())
        except (KeyError, ValueError):
            valid = False
        if not valid:
            raise APIError('UNAUTHORIZED', '本地会话已过期或缺少 API 令牌。', '请刷新前端页面或检查 skill 配置。', 401)

    def read_json(self):
        if self.headers.get('Transfer-Encoding'):
            raise APIError('INVALID_BODY', '不支持分块请求，请发送普通 JSON。', status=400)
        content_type = self.headers.get('Content-Type', '').split(';')[0].strip()
        if content_type != 'application/json':
            raise APIError('INVALID_BODY', '请求必须使用 application/json。', status=415)
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise APIError('INVALID_BODY', '请求长度无效。', status=400)
        if length < 0 or length > 256000:
            raise APIError('BODY_TOO_LARGE', '请求内容过大，最多允许 256 KB。', status=413)
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            raise APIError('INVALID_JSON', '请求不是有效的 JSON。', status=400)
        if not isinstance(data, dict):
            raise APIError('INVALID_JSON', '请求 JSON 必须是对象。', status=400)
        return data

    def export_skill(self):
        stream = io.BytesIO()
        folder = ROOT / 'skills' / 'emailcall'
        if not (folder / 'SKILL.md').exists():
            raise APIError('SKILL_UNAVAILABLE', 'Skill 模板尚未安装。', status=503)
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc' and path.name != 'config.json':
                    archive.write(path, 'emailcall/' + path.relative_to(folder).as_posix())
            config = {'base_url': f'http://127.0.0.1:{self.server.server_address[1]}', 'token': self.server.app.store.token()}
            info = zipfile.ZipInfo('emailcall/config.json')
            info.external_attr = 0o600 << 16
            archive.writestr(info, json.dumps(config, indent=2))
        self.respond(200, stream.getvalue(), 'application/zip', {'Content-Disposition': 'attachment; filename="emailcall-skill.zip"'})

    def static(self, path):
        if path in ('/', '/frontend'):
            self.respond(302, b'', headers={'Location': '/frontend/'})
            return
        if path == '/favicon.ico':
            self.respond(204, b'')
            return
        if not path.startswith('/frontend/'):
            raise APIError('NOT_FOUND', '页面不存在。', status=404)
        relative = unquote(path[len('/frontend/'):]) or 'index.html'
        root = (ROOT / 'frontend').resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise APIError('NOT_FOUND', '页面不存在。', status=404)
        mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        self.respond(200, target.read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text/') or mime == 'application/javascript' else ''))

    def dispatch(self):
        self.check_origin()
        parsed = urlsplit(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        method, app = self.command, self.server.app
        if method in ('GET', 'HEAD') and not path.startswith('/api/'):
            return self.static(path)
        if method == 'GET' and path == '/api/health':
            return self.respond(200, {'status': 'ok', 'version': __version__, 'instance_id': self.server.instance_id,
                                      'configured': bool(app.store.config())})
        if method == 'GET' and path == '/api/session':
            return self.session()
        agent_route = path in ('/api/notify', '/api/ask') or path.startswith('/api/requests/')
        self.authenticate(ui_only=not agent_route)
        if method == 'GET':
            if path == '/api/config':
                return self.respond(200, app.config_view())
            if path == '/api/token':
                return self.respond(200, {'token': app.store.token()})
            if path == '/api/skill/export':
                return self.export_skill()
            if path == '/api/records':
                try:
                    limit = int(query.get('limit', ['30'])[0])
                    offset = int(query.get('offset', ['0'])[0])
                    if not 1 <= limit <= 100 or offset < 0:
                        raise ValueError()
                except ValueError:
                    raise APIError('INVALID_INPUT', '分页参数无效。')
                return self.respond(200, app.store.list_records(q=query.get('q', [''])[0][:200],
                    status=query.get('status', [''])[0], kind=query.get('kind', [''])[0], limit=limit, offset=offset))
            if path.startswith('/api/records/') or path.startswith('/api/requests/'):
                request_id = path.rsplit('/', 1)[-1]
                try:
                    seconds = float(query.get('wait', ['0'])[0])
                    if not math.isfinite(seconds) or not 0 <= seconds <= 25:
                        raise ValueError()
                except ValueError:
                    raise APIError('INVALID_INPUT', 'wait 必须在 0–25 秒之间。')
                return self.respond(200, app.wait_request(request_id, seconds))
        if method == 'PUT' and path == '/api/config':
            return self.respond(200, app.save_config(self.read_json()))
        if method == 'POST':
            data = self.read_json()
            if path in ('/api/notify', '/api/ask'):
                kind = 'notify' if path.endswith('notify') else 'ask'
                record = app.create(kind, data, self.headers.get('Idempotency-Key'))
                return self.respond(202, record)
            if path == '/api/config/test':
                return self.respond(200, app.test_config())
            if path == '/api/token/rotate':
                return self.respond(200, {'token': app.store.rotate_token()})
        raise APIError('NOT_FOUND', '接口或请求方法不存在。', status=404)

    def handle_request(self):
        try:
            self.dispatch()
        except APIError as exc:
            data = {'error': exc.error}
            if exc.request_id:
                data['request_id'] = exc.request_id
            self.respond(exc.status, data)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            logging.error('HTTP 请求处理异常。')
            self.respond(500, {'error': {'code': 'INTERNAL_ERROR', 'message': '服务暂时无法完成此操作。', 'hint': '请检查容器日志、磁盘空间及数据目录权限。'}})

    do_GET = do_POST = do_PUT = do_HEAD = do_OPTIONS = handle_request


def make_server(app, host='127.0.0.1', port=10086):
    return LocalServer((host, port), app)

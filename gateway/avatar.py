"""Read a Wechaty avatar without exposing its session-bearing URL or headers."""
import base64
import urllib.request
from urllib.parse import urlsplit, urlunsplit

MAX_AVATAR_BYTES = 512 * 1024
HOSTS = {'wx.qq.com', 'wx2.qq.com', 'wx8.qq.com', 'web.wechat.com', 'web2.wechat.com'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def avatar_image(box):
    url = urlsplit(box.get('remoteUrl', ''))
    if (url.scheme not in {'http', 'https'} or url.hostname not in HOSTS
            or url.port not in {None, 443} or url.username or url.password
            or url.path != '/cgi-bin/mmwebwx-bin/webwxgeticon'):
        return None
    # The old Puppet returns HTTP. Always use verified TLS for session cookies.
    address = urlunsplit(('https', url.hostname, url.path, url.query, ''))
    cookie = box.get('headers', {}).get('cookie', '')
    if not isinstance(cookie, str) or len(cookie) > 16384 or '\r' in cookie or '\n' in cookie:
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(urllib.request.Request(address, headers={'Cookie': cookie}), timeout=5) as response:
        data = response.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        return None
    if data.startswith(b'\xff\xd8\xff'):
        mime = 'jpeg'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'png'
    elif data[:6] in (b'GIF87a', b'GIF89a'):
        mime = 'gif'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        mime = 'webp'
    else:
        return None
    return 'data:image/' + mime + ';base64,' + base64.b64encode(data).decode('ascii')

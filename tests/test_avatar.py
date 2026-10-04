import asyncio
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gateway.avatar import avatar_image, MAX_AVATAR_BYTES, NoRedirect
from gateway.wechat import _SDKClient


class AvatarTests(unittest.TestCase):
    def test_verified_https_bounded_image_and_cookie_kept_server_side(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'\xff\xd8\xfftest')
        with patch('gateway.avatar.urllib.request.build_opener', return_value=opener):
            image = avatar_image({'remoteUrl': 'http://wx.qq.com/cgi-bin/mmwebwx-bin/webwxgeticon?ticket=private',
                                  'headers': {'cookie': 'session=private', 'other': 'ignored'}})
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://wx.qq.com/'))
        self.assertEqual(request.get_header('Cookie'), 'session=private')
        self.assertIsNone(request.get_header('Other'))
        self.assertTrue(image.startswith('data:image/jpeg;base64,'))
        self.assertNotIn('private', image)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, None, None, 'http://127.0.0.1'))

    def test_rejects_untrusted_destinations_and_non_image_or_oversized_data(self):
        with patch('gateway.avatar.urllib.request.build_opener') as build:
            for url in ['http://127.0.0.1/', 'https://wx.qq.com.evil.test/cgi-bin/mmwebwx-bin/webwxgeticon',
                        'https://wx.qq.com/other', 'https://user@wx.qq.com/cgi-bin/mmwebwx-bin/webwxgeticon',
                        'https://wx.qq.com:80/cgi-bin/mmwebwx-bin/webwxgeticon']:
                self.assertIsNone(avatar_image({'remoteUrl': url}))
            build.assert_not_called()
        for body in [b'<svg onload="evil()"/>', b'<html>Login required</html>', b'\xff\xd8\xff' + b'0' * MAX_AVATAR_BYTES]:
            with patch('gateway.avatar.urllib.request.build_opener') as build:
                build.return_value.open.return_value = io.BytesIO(body)
                self.assertIsNone(avatar_image({'remoteUrl': 'https://wx.qq.com/cgi-bin/mmwebwx-bin/webwxgeticon'}))


class AvatarCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_account_switch_and_failed_download(self):
        calls = []
        async def contact_avatar(id):
            calls.append(id)
            return SimpleNamespace(filebox=json.dumps({'remoteUrl': 'unused'}))
        client = object.__new__(_SDKClient)
        client._avatar_cache = None
        client.puppet = SimpleNamespace(login_user_id='first', puppet_stub=SimpleNamespace(contact_avatar=contact_avatar))
        with patch('gateway.avatar.avatar_image', return_value='data:image/png;base64,AAAA'):
            self.assertEqual((await client.avatar())['account_id'], 'first')
            await client.avatar()
            self.assertEqual(calls, ['first'])
        client.puppet.login_user_id = 'second'
        with patch('gateway.avatar.avatar_image', side_effect=OSError('private cookie')):
            self.assertIsNone((await client.avatar())['image'])
            await client.avatar()
        self.assertEqual(calls, ['first', 'second'])
        client.puppet.login_user_id = None
        self.assertEqual(await client.avatar(), {'account_id': None, 'image': None})

    async def test_logout_during_download_discards_avatar(self):
        client = object.__new__(_SDKClient)
        client._avatar_cache = None
        async def contact_avatar(id):
            client.puppet.login_user_id = None
            return SimpleNamespace(filebox='{}')
        client.puppet = SimpleNamespace(login_user_id='first', puppet_stub=SimpleNamespace(contact_avatar=contact_avatar))
        with patch('gateway.avatar.avatar_image', return_value='data:image/png;base64,AAAA'):
            self.assertIsNone((await client.avatar())['image'])
        self.assertIsNone(client._avatar_cache)

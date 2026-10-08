"""Vendored wxbot protocol-core tests: patch behaviour without any network."""
import os
import sys
import tempfile
import types
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

# direct_bot imports httpx at module level, but none of the logic under test
# here touches it. Stub the module so these tests also run on a stdlib-only
# checkout (the real client paths are exercised by test_wechat.py whenever
# httpx is installed).
def _real_httpx_installed():
    try:
        return find_spec("httpx") is not None
    except (ValueError, ModuleNotFoundError):
        return False  # a bare stub from a sibling test module is in sys.modules


if not _real_httpx_installed():
    sys.modules.setdefault("httpx", types.ModuleType("httpx"))

from gateway.store import _QUOTE_DIVIDER
from gateway.wxbot.direct_bot import WeChatHelperBot


SELF = 'wxid_agentcall_self'


def text_msg(msg_id='1001', content='好的，收到', from_user=SELF, create_time=1770000000, **extra):
    return {'MsgId': msg_id, 'Content': content, 'FromUserName': from_user,
            'ToUserName': 'filehelper', 'MsgType': 1, 'CreateTime': create_time, **extra}


def refer_msg(title='收到，马上处理', quoted='请确认部署', displayname='Agent',
              svrid='8731822941234567', msg_id='1002', create_time=1770000100,
              app_msg_type=57):
    content = (
        '<appmsg appid="wx_webfilehelper" sdkver="">'
        f'<title>{title}</title><des></des><action></action><type>57</type>'
        '<content></content><url></url>'
        f'<refermsg><type>1</type><svrid>{svrid}</svrid>'
        '<fromusr>filehelper</fromusr><displayname>'
        f'{displayname}</displayname><content>{quoted}</content></refermsg>'
        '</appmsg>'
    )
    return {'MsgId': msg_id, 'Content': content, 'FromUserName': SELF,
            'ToUserName': 'filehelper', 'MsgType': 49, 'CreateTime': create_time,
            'AppMsgType': app_msg_type}


class WxbotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_path = Path(tmp.name) / 'state.json'
        self.bot = self.make_bot(self.state_path)

    @staticmethod
    def make_bot(state_path):
        environ = {k: v for k, v in os.environ.items() if k != 'WECHAT_TRACE_ENABLED'}
        with patch.dict(os.environ, environ, clear=True):
            return WeChatHelperBot(state_path=state_path)

    def normalize(self, items):
        return self.bot._normalize_messages(list(items))


class NormalizationTests(WxbotTests):
    def test_text_message_carries_create_time_and_is_mine(self):
        (message,) = self.normalize([text_msg()])
        self.assertEqual(message['type'], 'text')
        self.assertEqual(message['text'], '好的，收到')
        self.assertTrue(message['is_mine'])
        self.assertEqual(message['create_time'], 1770000000)

    def test_invalid_or_missing_create_time_becomes_zero(self):
        for index, raw in enumerate((None, 0, 'later', '')):
            message, = self.normalize([text_msg(msg_id=f'ct-{index}', create_time=raw)])
            self.assertEqual(message['create_time'], 0, raw)

    def test_image_and_file_messages_carry_create_time(self):
        image, file = self.normalize([
            dict(text_msg(msg_id='i1'), MsgType=3, FileName='a.jpg'),
            dict(text_msg(msg_id='f1'), MsgType=49, AppMsgType=6, FileName='a.zip'),
        ])
        self.assertEqual((image['type'], image['create_time']), ('image', 1770000000))
        self.assertEqual((file['type'], file['create_time']), ('file', 1770000000))

    def test_filehelper_system_message_is_not_mine(self):
        message, = self.normalize([text_msg(content='你已登录网页微信文件传输助手',
                                            from_user='filehelper')])
        self.assertFalse(message['is_mine'])

    def test_non_filehelper_conversation_is_dropped(self):
        self.assertEqual(self.normalize([{'MsgId': 'x1', 'Content': 'hi', 'MsgType': 1,
                                          'FromUserName': 'wxid_a', 'ToUserName': 'wxid_b',
                                          'CreateTime': 1770000000}]), [])

    def test_own_sent_ids_and_duplicates_are_filtered(self):
        self.bot._send_msg_ids.add('2001')
        result = self.normalize([text_msg(msg_id='2001'), text_msg(msg_id='3001'),
                                 text_msg(msg_id='3001')])
        self.assertEqual([m['id'] for m in result], ['3001'])


class RefermsgTests(WxbotTests):
    def test_flattens_to_quote_divider_format_with_reference_id(self):
        message, = self.normalize([refer_msg()])
        self.assertEqual(message['type'], 'text')
        self.assertTrue(message['is_mine'])
        self.assertEqual(message['reference_id'], '8731822941234567')
        head, divider, tail = message['text'].split('\n')
        self.assertEqual(head, '「Agent：请确认部署」')
        self.assertEqual(tail, '收到，马上处理')
        self.assertIsNotNone(_QUOTE_DIVIDER.search(message['text']))

    def test_without_displayname_or_app_msg_type(self):
        message, = self.normalize([refer_msg(displayname='', app_msg_type=0)])
        self.assertTrue(message['text'].startswith('「请确认部署」\n'))
        self.assertEqual(message['reference_id'], '8731822941234567')

    def test_entity_escaped_content_is_unescaped_before_parsing(self):
        import html as html_mod
        raw = refer_msg(title='第一行<br/>第二行', quoted='带 &lt;b&gt; 标记')
        raw['Content'] = html_mod.escape(raw['Content'], quote=False)
        message, = self.normalize([raw])
        lines = message['text'].split('\n')
        self.assertEqual(lines[0], '「Agent：带 <b> 标记」')
        self.assertEqual('\n'.join(lines[2:]), '第一行\n第二行')

    def test_empty_own_words_still_emit_divider(self):
        message, = self.normalize([refer_msg(title='')])
        self.assertTrue(message['text'].endswith('\n' + '- ' * 14 + '-\n'))
        self.assertIsNotNone(_QUOTE_DIVIDER.search(message['text']))

    def test_filehelper_origin_refermsg_is_not_mine(self):
        message, = self.normalize([dict(refer_msg(), FromUserName='filehelper',
                                        ToUserName=SELF)])
        self.assertFalse(message['is_mine'])


class SessionControlTests(WxbotTests):
    def test_trace_defaults_to_off(self):
        self.assertFalse(self.bot.trace_enabled)
        with patch.dict(os.environ, {'WECHAT_TRACE_ENABLED': '1'}):
            self.assertTrue(WeChatHelperBot(state_path=self.state_path).trace_enabled)

    def test_state_path_is_honored(self):
        self.assertEqual(self.bot.state_path, self.state_path)

    def test_reset_session_clears_credentials_and_optionally_the_file(self):
        self.bot.skey, self.bot.sid, self.bot.uin = 's', 'i', '1'
        self.bot.pass_ticket, self.bot.user_name = 'p', SELF
        self.bot.login_avatar = 'data:image/jpeg;base64,QUFB'
        self.bot.is_logged_in = True
        self.state_path.write_text('{}', encoding='utf-8')
        self.bot.reset_session()
        self.assertFalse(self.bot._has_auth())
        self.assertFalse(self.bot.is_logged_in)
        self.assertEqual(self.bot.login_avatar, '')  # 登录状态失效后头像一并清空
        self.assertTrue(self.state_path.exists())
        self.bot.reset_session(delete_file=True)
        self.assertFalse(self.state_path.exists())


class AvatarTests(WxbotTests, unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _client_returning(text, content=b'', content_type='text/html'):
        class Resp:
            def raise_for_status(self):
                pass
        resp = Resp()
        resp.text, resp.content = text, content
        resp.headers = {'content-type': content_type}

        async def get(url):
            return resp
        import types as types_mod
        return types_mod.SimpleNamespace(get=get)

    async def test_scanned_poll_captures_avatar_data_uri(self):
        self.bot.uuid = 'uuid-1'
        self.bot.client = self._client_returning(
            "window.code=201;window.userAvatar='data:image/jpeg;base64,QUJD';")
        self.assertEqual(await self.bot._poll_login_once(), 201)
        self.assertEqual(self.bot.login_avatar, 'data:image/jpeg;base64,QUJD')

    async def test_non_data_uri_or_other_codes_do_not_touch_avatar(self):
        self.bot.uuid = 'uuid-1'
        self.bot.login_avatar = 'data:image/jpeg;base64,KEPT'
        self.bot.client = self._client_returning(
            'window.code=201;window.userAvatar="https://tracker.example/a.jpg";')
        await self.bot._poll_login_once()
        self.assertEqual(self.bot.login_avatar, 'data:image/jpeg;base64,KEPT')
        self.bot.client = self._client_returning('window.code=408;')
        await self.bot._poll_login_once()
        self.assertEqual(self.bot.login_avatar, 'data:image/jpeg;base64,KEPT')

    async def test_fetch_self_avatar_requires_login_image_type_and_size(self):
        self.assertIsNone(await self.bot.fetch_self_avatar())  # 未登录
        self.bot.is_logged_in, self.bot.user_name = True, SELF
        self.bot.client = self._client_returning('', content=b'\xff\xd8img',
                                                 content_type='image/jpeg; charset=binary')
        self.assertEqual(await self.bot.fetch_self_avatar(), (b'\xff\xd8img', 'image/jpeg'))
        self.bot.client = self._client_returning('', content=b'<html/>', content_type='text/html')
        self.assertIsNone(await self.bot.fetch_self_avatar())
        self.bot.client = self._client_returning('', content=b'\x00' * (512 * 1024 + 1),
                                                 content_type='image/png')
        self.assertIsNone(await self.bot.fetch_self_avatar())


if __name__ == '__main__':
    unittest.main()

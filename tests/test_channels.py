import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from gateway.service import Gateway, APIError
from gateway.store import Store
from tests.test_core import CONFIG


class FakeWechat:
    def __init__(self):
        self.online = True
        self.probes = 0
        self.sent = []
        self.account_id = 'bot-1'
        self.failure = None

    def start(self, config, *, reset_login=False):
        pass

    def stop(self):
        pass

    def status(self, probe=False):
        if probe:
            return self.check()
        return dict(state='logged_in' if self.online else 'offline', logged_in=self.online,
                    available=self.online, account={'id': self.account_id, 'name': 'Agent'},
                    qr_code=None, error=None, last_checked_at=None)

    def check(self):
        self.probes += 1
        return self.status()

    def contacts(self, query='', limit=100):
        return [{'id': 'user-1', 'name': '用户', 'alias': '目标'}]

    def contact(self, contact_id):
        return next((c for c in self.contacts() if c['id'] == contact_id), None)

    def send(self, record):
        if self.failure:
            raise self.failure
        self.sent.append(record)
        return {'message_id': 'wx-out-' + record['id']}


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.wx = FakeWechat()
        self.mail = Mock()
        self.app = Gateway(self.store, self.mail, wechat_transport=self.wx)
        self.app.save_config(CONFIG)
        self.app.save_wechat_config({'enabled': True, 'target_contact_id': 'user-1'})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def create(self, **extra):
        return self.app.create('ask', dict(subject='选择下一步', body='A or B?', **extra))

    def incoming(self, record, **extra):
        item = dict(message_id='wx-in-1', from_contact_id='user-1', account_id='bot-1',
                    body='[AC:' + record['id'] + '] 选择 A', received_at=datetime.now(timezone.utc).isoformat())
        item.update(extra)
        return self.app.receive_wechat(item)

    def test_wechat_is_probed_at_creation_and_send_and_preferred(self):
        record = self.create()
        self.assertGreater(self.wx.probes, 0)
        before = self.wx.probes
        self.app.send_once()
        self.assertGreater(self.wx.probes, before)
        self.mail.send_message.assert_not_called()
        stored = self.store.get(record['id'])
        self.assertEqual(stored['channel'], 'wechat')
        self.assertEqual(stored['recipient_label'], '目标')
        self.assertEqual(stored['status'], 'waiting')
        self.assertTrue(stored['transport_message_id'])

    def test_logout_after_enqueue_uses_email_and_persists_fallback(self):
        record = self.create()
        self.wx.online = False
        self.app.send_once()
        stored = self.store.get(record['id'])
        self.assertEqual(stored['channel'], 'email')
        self.assertEqual(stored['status'], 'waiting')
        self.assertIsNotNone(stored['fallback_reason'])
        self.mail.send_message.assert_called_once()
        self.assertEqual(self.wx.sent, [])

    def test_forced_wechat_test_does_not_disguise_failure_as_email_success(self):
        record = self.create(channel='wechat')
        self.wx.online = False
        self.app.send_once()
        self.assertEqual(self.store.get(record['id'])['status'], 'failed')
        self.mail.send_message.assert_not_called()

    def test_uncertain_wechat_send_does_not_duplicate_via_email(self):
        from gateway.wechat import WechatError
        self.wx.failure = WechatError('WECHAT_DELIVERY_UNKNOWN', '发送结果未知', '', safe_to_fallback=False)
        record = self.create()
        self.app.send_once()
        self.assertEqual(self.store.get(record['id'])['error']['code'], 'WECHAT_DELIVERY_UNKNOWN')
        self.mail.send_message.assert_not_called()

    def test_known_failure_before_submission_can_fallback(self):
        from gateway.wechat import WechatError
        self.wx.failure = WechatError('WECHAT_OFFLINE', '离线', '', safe_to_fallback=True)
        record = self.create()
        self.app.send_once()
        self.assertEqual(self.store.get(record['id'])['channel'], 'email')
        self.assertEqual(self.store.get(record['id'])['status'], 'waiting')

    def test_reply_is_deduplicated_and_survives_restart(self):
        record = self.create()
        self.app.send_once()
        self.incoming(record)
        self.incoming(record)
        self.store.close()
        self.store = Store(self.tmp.name)
        stored = self.store.get(record['id'])
        self.assertEqual(stored['status'], 'replied')
        self.assertEqual(stored['reply']['body'], '选择 A')
        self.assertEqual(stored['reply']['channel'], 'wechat')
        self.assertEqual(len(stored['replies']), 1)

    def test_sdk_utc_z_reply_survives_save_and_restart(self):
        record = self.create()
        self.app.send_once()
        received = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        self.assertTrue(self.incoming(record, received_at=received))
        self.assertFalse(self.incoming(record, received_at=received))
        self.store.close()
        self.store = Store(self.tmp.name)
        saved = self.store.get(record['id'])
        self.assertEqual(saved['status'], 'replied')
        self.assertEqual(saved['reply']['body'], '选择 A')
        self.assertEqual(len(saved['replies']), 1)
        self.assertEqual(datetime.fromisoformat(saved['reply']['received_at']),
                         datetime.fromisoformat(received.replace('Z', '+00:00')))

    def test_sdk_utc_z_reply_after_deadline_remains_late(self):
        record = self.create(timeout_seconds=30)
        self.app.send_once()
        deadline = datetime.fromisoformat(self.store.get(record['id'])['deadline_at'])
        received = deadline + timedelta(seconds=1)
        with unittest.mock.patch('gateway.store.utcnow', return_value=received):
            self.assertTrue(self.incoming(record, received_at=received.isoformat().replace('+00:00', 'Z')))
        saved = self.store.get(record['id'])
        self.assertEqual(saved['status'], 'waiting')
        self.assertTrue(saved['reply']['late'])

    def test_wrong_account_or_contact_cannot_complete_request(self):
        record = self.create()
        self.app.send_once()
        self.incoming(record, from_contact_id='other')
        self.incoming(record, message_id='wx-in-2', account_id='other-bot')
        self.assertEqual(self.store.get(record['id'])['status'], 'waiting')
        self.assertIsNone(self.store.get(record['id'])['reply'])

    def test_plain_reply_requires_exactly_one_waiting_request(self):
        first = self.create()
        self.app.send_once()
        second = self.create()
        self.app.send_once()
        self.incoming(first, body='同意')
        self.assertEqual(self.store.get(first['id'])['status'], 'waiting')
        self.assertEqual(self.store.get(second['id'])['status'], 'waiting')
        self.incoming(first, message_id='wx-in-2')
        self.assertEqual(self.store.get(first['id'])['status'], 'replied')
        self.incoming(second, message_id='wx-in-3', body='选择 B')
        self.assertEqual(self.store.get(second['id'])['reply']['body'], '选择 B')

    def test_late_reply_does_not_revive_timeout(self):
        record = self.create(timeout_seconds=30)
        self.app.send_once()
        self.store.expire(datetime.now(timezone.utc) + timedelta(seconds=70))
        self.incoming(record)
        stored = self.store.get(record['id'])
        self.assertEqual(stored['status'], 'timed_out')
        self.assertTrue(stored['reply']['late'])

    def test_plain_late_reply_is_preserved_without_becoming_a_decision(self):
        record = self.create(timeout_seconds=30)
        self.app.send_once()
        self.store.expire(datetime.now(timezone.utc) + timedelta(seconds=70))
        self.incoming(record, body='迟到的回答')
        self.incoming(record, body='迟到的回答')
        self.assertIsNone(self.store.get(record['id'])['reply'])
        diagnostics = self.store.list_records(kind='diagnostic')['items']
        self.assertEqual(len(diagnostics), 1)
        self.assertIn('迟到的回答', diagnostics[0]['body'])

    def test_unanswered_old_question_blocks_guessing_plain_reply_for_new_question(self):
        old = self.create(timeout_seconds=30)
        self.app.send_once()
        self.store.expire(datetime.now(timezone.utc) + timedelta(seconds=70))
        new = self.create()
        self.app.send_once()
        self.incoming(new, body='继续')
        self.assertEqual(self.store.get(new['id'])['status'], 'waiting')
        self.incoming(new, message_id='wx-in-2')
        self.assertEqual(self.store.get(new['id'])['status'], 'replied')

    def test_message_from_queue_waiting_period_cannot_answer_newly_sent_request(self):
        record = self.create()
        record['created_at'] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(record), record['id']))
        self.app.send_once()
        self.incoming(record, received_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
        self.assertEqual(self.store.get(record['id'])['status'], 'waiting')

    def test_unknown_old_delivery_blocks_plain_reply_for_new_question(self):
        old = self.create()
        self.app.send_once()
        self.store.fail(old['id'], {'code': 'WECHAT_SEND_UNCERTAIN', 'message': '发送结果未知', 'hint': ''})
        new = self.create()
        self.app.send_once()
        self.incoming(new, body='同意')
        self.assertEqual(self.store.get(new['id'])['status'], 'waiting')
        self.incoming(new, message_id='wx-in-2')
        self.assertEqual(self.store.get(new['id'])['status'], 'replied')

    def test_known_unsent_failure_does_not_block_plain_reply(self):
        old = self.create()
        self.store.fail(old['id'], {'code': 'WECHAT_OFFLINE', 'message': '尚未发送', 'hint': ''})
        new = self.create()
        self.app.send_once()
        self.incoming(new, body='同意')
        self.assertEqual(self.store.get(new['id'])['status'], 'replied')

    def test_unanswered_question_is_not_hidden_by_many_newer_notifications(self):
        old = self.create(timeout_seconds=30)
        self.app.send_once()
        self.store.expire(datetime.now(timezone.utc) + timedelta(seconds=70))
        for index in range(301):
            self.app.create('notify', {'subject': '任务完成', 'body': str(index)})
            self.app.send_once()
        new = self.create()
        self.app.send_once()
        self.incoming(new, body='同意')
        self.assertEqual(self.store.get(new['id'])['status'], 'waiting')
        self.incoming(old, message_id='wx-in-2')
        self.assertTrue(self.store.get(old['id'])['reply']['late'])

    def test_known_reference_conflicting_with_marker_is_not_accepted(self):
        first, second = self.create(), self.create()
        self.app.send_once()
        self.app.send_once()
        self.incoming(first, reference_id='wx-out-' + second['id'])
        self.assertEqual(self.store.get(first['id'])['status'], 'waiting')
        self.assertEqual(self.store.get(second['id'])['status'], 'waiting')

    def test_explicit_reply_after_uncertain_send_is_archived_without_reviving_request(self):
        record = self.create()
        self.app.send_once()
        self.store.fail(record['id'], {'code': 'WECHAT_SEND_UNCERTAIN', 'message': '发送结果未知', 'hint': ''})
        self.incoming(record)
        stored = self.store.get(record['id'])
        self.assertEqual(stored['status'], 'failed')
        self.assertEqual(stored['reply']['body'], '选择 A')
        self.assertTrue(stored['reply']['unconfirmed'])

    def test_account_switch_requires_reselecting_target(self):
        self.wx.account_id = 'bot-2'
        record = self.create()
        self.app.send_once()
        self.assertEqual(self.store.get(record['id'])['channel'], 'email')
        self.assertEqual(self.store.get(record['id'])['fallback_reason']['code'], 'WECHAT_ACCOUNT_CHANGED')

    def test_token_is_private_and_target_change_is_blocked_while_waiting(self):
        self.app.save_wechat_config({'mode': 'external', 'service_endpoint': 'puppet.example.com:8788', 'service_token': 'secret-value'})
        self.assertNotIn('secret-value', json.dumps(self.app.wechat_view()))
        self.create()
        with self.assertRaises(APIError):
            self.app.save_wechat_config({'target_contact_id': 'other'})

    def test_wechat_only_configuration_can_send_without_email(self):
        self.store.set_setting('config', {})
        record = self.create()
        self.app.send_once()
        self.assertEqual(self.store.get(record['id'])['channel'], 'wechat')
        self.assertEqual(self.store.get(record['id'])['status'], 'waiting')

    def test_waiting_email_does_not_block_wechat_setup(self):
        record = self.create(channel='email')
        self.app.send_once()
        self.app.save_wechat_config({'target_contact_id': ''})
        self.app.save_wechat_config({'target_contact_id': 'user-1'})
        self.assertEqual(self.store.get(record['id'])['status'], 'waiting')

    def test_waiting_wechat_still_protects_its_contact(self):
        self.create()
        self.app.send_once()
        with self.assertRaises(APIError):
            self.app.save_wechat_config({'target_contact_id': ''})

    def test_legacy_record_defaults_to_email_channel(self):
        record = self.create(channel='email')
        record.pop('channel', None)
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(record), record['id']))
        self.assertEqual(self.store.get(record['id'])['channel'], 'email')


if __name__ == '__main__':
    unittest.main()

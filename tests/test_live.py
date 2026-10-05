import sqlite3
import threading
import time
import unittest
from datetime import timedelta

from gateway.service import APIError
from gateway.store import Store, stamp, utcnow
from tests import test_channels


class LiveInboxTests(unittest.TestCase):
    tearDown = test_channels.ChannelTests.tearDown
    create = test_channels.ChannelTests.create

    def setUp(self):
        test_channels.ChannelTests.setUp(self)
        self.app.live.save({'enabled': True})

    def message(self, message_id='message-1', body='随时发来的新要求', **extra):
        return {'message_id': message_id, 'body': body, 'from_contact_id': 'user-1',
                'account_id': 'bot-1', 'received_at': stamp(), **extra}

    def test_unsolicited_message_is_durable_and_read_is_separate_from_claim(self):
        self.assertTrue(self.app.receive_wechat(self.message()))
        result = self.app.live.claim({'consumer_id': 'codex-session-1'})
        event = result['items'][0]
        self.assertEqual(event['kind'], 'message')
        self.assertEqual(event['body'], '随时发来的新要求')
        self.assertEqual(self.store.get(event['record_id'])['status'], 'received')
        self.assertFalse(self.app.receive_wechat(self.message()))
        self.assertEqual(self.app.live.view()['pending'], 1)
        self.assertEqual(self.app.live.claim({'consumer_id': 'codex-session-1'})['items'], result['items'])
        self.store.close()
        self.store = Store(self.tmp.name)
        from gateway.service import Gateway
        self.app = Gateway(self.store, self.mail, self.wx)
        self.assertEqual(self.app.live.peek()['items'][0], event)
        self.app.live.ack({'consumer_id': 'codex-session-1', 'ids': [event['id']]})
        self.app.live.ack({'consumer_id': 'codex-session-1', 'ids': [event['id']]})
        self.assertEqual(self.app.live.view()['pending'], 0)
        self.assertEqual(self.store.get(event['record_id'])['status'], 'read')

    def test_disabled_or_other_sender_does_not_capture_messages(self):
        for fields in ({'from_contact_id': 'other'}, {'account_id': 'other'},
                       {'received_at': stamp(utcnow() + timedelta(seconds=60))},
                       {'received_at': 'invalid'}, {'received_at': '2020-01-01T00:00:00Z'}):
            self.app.receive_wechat(self.message(**fields))
        self.assertEqual(self.app.live.view()['pending'], 0)
        self.app.live.save({'enabled': False})
        self.app.receive_wechat(self.message())
        self.assertEqual(self.app.live.view()['pending'], 0)
        with self.assertRaises(APIError):
            self.app.live.claim({'consumer_id': 'codex-session-1'})

    def test_reply_is_still_correlated_and_not_a_new_task(self):
        record = self.create()
        self.app.send_once()
        self.app.receive_wechat(self.message(body='[AC:' + record['id'] + '] 继续'))
        event = self.app.live.peek()['items'][0]
        self.assertEqual(event['kind'], 'reply')
        self.assertEqual(event['request_id'], record['id'])
        self.assertEqual(self.store.get(record['id'])['status'], 'replied')
        self.assertEqual(self.store.list_records(kind='incoming')['total'], 0)

    def test_unmatched_marker_becomes_message_not_a_decision(self):
        record = self.create()
        self.app.send_once()
        self.app.receive_wechat(self.message(body='[AC:unknown] 继续'))
        self.assertEqual(self.app.live.peek()['items'][0]['kind'], 'message')
        self.assertEqual(self.store.get(record['id'])['status'], 'waiting')

    def test_one_consumer_and_lease_expiry_do_not_drop_pending_messages(self):
        self.app.receive_wechat(self.message())
        event = self.app.live.claim({'consumer_id': 'codex-session-1'})['items'][0]
        with self.assertRaises(APIError) as error:
            self.app.live.claim({'consumer_id': 'claude-session-2'})
        self.assertEqual(error.exception.error['code'], 'INBOX_CONSUMER_BUSY')
        self.store.set_setting('live_consumer', {'id': 'codex-session-1', 'scope': ['bot-1', 'user-1'],
                                               'expires_at': stamp(utcnow() - timedelta(seconds=1))})
        self.assertEqual(self.app.live.claim({'consumer_id': 'claude-session-2'})['items'][0]['id'], event['id'])
        with self.assertRaises(APIError):
            self.app.live.ack({'consumer_id': 'codex-session-1', 'ids': [event['id']]})
        self.app.live.ack({'consumer_id': 'claude-session-2', 'ids': [event['id']]})
        self.assertEqual(self.app.live.view()['pending'], 0)

    def test_long_poll_wakes_on_new_message_or_disabling_mode(self):
        result = []
        def listen():
            try:
                result.append(self.app.live.claim({'consumer_id': 'codex-session-1', 'wait': 2}))
            except APIError as error:
                result.append(error.error['code'])
        thread = threading.Thread(target=listen)
        thread.start()
        time.sleep(.05)
        self.app.receive_wechat(self.message())
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result[0]['items']), 1)
        self.app.live.ack({'consumer_id': 'codex-session-1', 'ids': [result[0]['items'][0]['id']]})
        result.clear()
        thread = threading.Thread(target=listen)
        thread.start()
        time.sleep(.05)
        self.app.live.save({'enabled': False})
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0], 'EFFICIENT_MODE_DISABLED')

    def test_configuration_change_does_not_deliver_old_account_messages(self):
        self.app.receive_wechat(self.message())
        self.app.live.claim({'consumer_id': 'codex-session-1'})
        wx = self.app.channels.config()
        self.store.set_setting('wechat_config', {**wx, 'account_id': 'bot-2'})
        self.assertEqual(self.app.live.peek()['items'], [])
        self.assertFalse(self.app.live.view()['consumer_online'])
        self.assertEqual(self.app.live.claim({'consumer_id': 'claude-session-2'})['items'], [])
        self.assertEqual(self.store.list_records(kind='incoming')['total'], 1)

    def test_capture_and_reply_are_atomic_on_inbox_write_failure(self):
        record = self.create()
        self.app.send_once()
        self.store.db.execute('''CREATE TRIGGER fail_capture BEFORE INSERT ON live_inbox
            BEGIN SELECT RAISE(ABORT,'simulated disk write failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.app.receive_wechat(self.message(body='[AC:' + record['id'] + '] 继续'))
        self.assertEqual(self.store.get(record['id'])['status'], 'waiting')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM replies').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM wechat_inbox').fetchone()[0], 0)
        self.store.db.execute('DROP TRIGGER fail_capture')
        self.assertTrue(self.app.receive_wechat(self.message(body='[AC:' + record['id'] + '] 继续')))

    def test_ack_invalid_batch_keeps_all_items_pending(self):
        self.app.receive_wechat(self.message())
        event = self.app.live.claim({'consumer_id': 'codex-session-1'})['items'][0]
        with self.assertRaises(APIError):
            self.app.live.ack({'consumer_id': 'codex-session-1', 'ids': [event['id'], 99999]})
        self.assertEqual(self.app.live.view()['pending'], 1)

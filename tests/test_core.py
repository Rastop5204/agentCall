import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

from gateway.store import Store
from gateway.service import Gateway, APIError


CONFIG = dict(provider='gmail', email='agent@example.com', username='agent@example.com',
              password='app-password', target_email='user@example.com', smtp_host='smtp.gmail.com',
              smtp_port=465, smtp_security='ssl', imap_host='imap.gmail.com', imap_port=993,
              imap_security='ssl', imap_folder='INBOX', poll_interval=10)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.app = Gateway(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def configure(self):
        self.app.save_config(CONFIG)

    def test_config_survives_restart_without_exposing_password(self):
        self.configure()
        self.assertNotIn('password', self.app.config_view()['config'])
        token = self.store.token()
        self.store.close()
        self.store = Store(self.tmp.name)
        self.assertEqual(self.store.config()['password'], CONFIG['password'])
        self.assertEqual(self.store.token(), token)

    def test_missing_config_failure_is_recorded(self):
        with self.assertRaises(APIError) as ctx:
            self.app.create('notify', {'subject': 'Done', 'body': 'Finished'})
        record = self.store.get(ctx.exception.request_id)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['error']['code'], 'NOT_CONFIGURED')

    def test_idempotency_prevents_duplicates_and_conflicting_content(self):
        self.configure()
        payload = {'subject': 'Decision', 'body': 'Continue?', 'timeout_seconds': 30}
        one = self.app.create('ask', payload, 'unique-key-123')
        two = self.app.create('ask', payload, 'unique-key-123')
        self.assertEqual(one['id'], two['id'])
        with self.assertRaises(APIError) as ctx:
            self.app.create('ask', {**payload, 'body': 'Different'}, 'unique-key-123')
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.store.list_records()['total'], 1)

    def test_reply_deduplication_and_late_reply_does_not_revive_timeout(self):
        self.configure()
        rec = self.app.create('ask', {'subject': 'Decision', 'body': 'Continue?', 'timeout_seconds': 30})
        self.store.claim_next()
        self.store.mark_sent(rec['id'])
        deadline = datetime.now(timezone.utc) + timedelta(seconds=31)
        self.store.expire(deadline, mailbox_checked=True)
        reply = dict(request_id=rec['id'], message_id='<reply@example.com>', from_email='user@example.com',
                     body='Yes', received_at=deadline.isoformat())
        self.store.add_reply(reply)
        self.store.add_reply(reply)
        record = self.store.get(rec['id'])
        self.assertEqual(record['status'], 'timed_out')
        self.assertTrue(record['reply']['late'])
        self.assertEqual(len(record['replies']), 1)

    def test_received_reply_completes_request_and_remains_after_restart(self):
        self.configure()
        rec = self.app.create('ask', {'subject': 'Question', 'body': 'Pick A or B'})
        self.store.claim_next()
        self.store.mark_sent(rec['id'])
        self.store.add_reply(dict(request_id=rec['id'], message_id='<r@example.com>',
                            from_email='user@example.com', body='选 A', received_at=datetime.now(timezone.utc).isoformat()))
        self.store.close()
        self.store = Store(self.tmp.name)
        record = self.store.get(rec['id'])
        self.assertEqual(record['status'], 'replied')
        self.assertEqual(record['reply']['body'], '选 A')

    def test_uncertain_send_after_restart_is_not_resent(self):
        self.configure()
        rec = self.app.create('notify', {'subject': 'Done', 'body': 'Finished'})
        self.store.claim_next()
        self.store.close()
        self.store = Store(self.tmp.name)
        self.assertIsNone(self.store.claim_next())
        self.assertEqual(self.store.get(rec['id'])['error']['code'], 'DELIVERY_UNKNOWN')

    def test_header_injection_is_rejected_and_recorded(self):
        self.configure()
        with self.assertRaises(APIError) as ctx:
            self.app.create('notify', {'subject': 'hi\r\nBcc: stranger@example.com', 'body': 'text'})
        self.assertEqual(self.store.get(ctx.exception.request_id)['status'], 'failed')

    def test_blank_password_preserves_only_same_identity(self):
        self.configure()
        self.app.save_config({**CONFIG, 'password': '', 'target_email': 'new@example.com'})
        self.assertEqual(self.store.config()['password'], 'app-password')
        with self.assertRaises(APIError):
            self.app.save_config({**CONFIG, 'email': 'other@example.com', 'password': ''})

    def test_active_request_blocks_configuration_change(self):
        self.configure()
        self.app.create('ask', {'subject': 'Question', 'body': 'Pick A or B'})
        with self.assertRaises(APIError) as ctx:
            self.app.save_config({**CONFIG, 'target_email': 'other@example.com'})
        self.assertEqual(ctx.exception.error['code'], 'REQUESTS_ACTIVE')

    def test_two_processes_cannot_consume_same_queue(self):
        with self.assertRaises(RuntimeError):
            Store(self.tmp.name)

    def test_send_worker_persists_success_and_sanitized_failure(self):
        self.configure()
        transport = Mock()
        app = Gateway(self.store, transport)
        one = app.create('notify', {'subject': 'Complete', 'body': 'All done'})
        app.send_once()
        self.assertEqual(self.store.get(one['id'])['status'], 'sent')
        transport.send_message.side_effect = RuntimeError('sensitive transport detail')
        transport.explain_error.return_value = dict(code='SMTP_ERROR', message='发送失败', hint='检查连接')
        two = app.create('ask', {'subject': 'Question', 'body': 'Pick one'})
        app.send_once()
        record = self.store.get(two['id'])
        self.assertEqual(record['status'], 'failed')
        self.assertNotIn('sensitive', str(record))

    def test_poll_errors_persist_once_and_timeout_still_progresses(self):
        self.configure()
        transport = Mock()
        app = Gateway(self.store, transport)
        one = app.create('ask', {'subject': 'Question', 'body': 'Pick one', 'timeout_seconds': 30})
        app.send_once()
        transport.poll_replies.side_effect = RuntimeError('network unavailable')
        transport.explain_error.return_value = dict(code='IMAP_ERROR', message='收件失败', hint='检查连接')
        app.poll_once()
        app.poll_once()
        self.assertEqual(self.store.list_records(kind='diagnostic')['total'], 1)
        self.assertEqual(app.config_view()['service']['poll_error']['code'], 'IMAP_ERROR')
        self.store.expire(datetime.now(timezone.utc) + timedelta(seconds=61))
        self.assertEqual(self.store.get(one['id'])['status'], 'timed_out')

    def test_untrusted_reply_sender_cannot_change_request(self):
        self.configure()
        record = self.app.create('ask', {'subject': 'Question', 'body': 'Pick one'})
        self.store.claim_next()
        self.store.mark_sent(record['id'])
        accepted = self.store.add_reply(dict(request_id=record['id'], message_id='<bad@example.com>',
                    from_email='stranger@example.com', body='approve', received_at=datetime.now(timezone.utc).isoformat()))
        self.assertFalse(accepted)
        self.assertEqual(self.store.get(record['id'])['status'], 'waiting')

    def test_poll_records_sender_mismatch_without_accepting_it(self):
        self.configure()
        transport = Mock()
        app = Gateway(self.store, transport)
        record = app.create('ask', {'subject': 'Question', 'body': 'Pick one'})
        app.send_once()
        rejected = dict(request_id=record['id'], message_id='<other@example.com>',
                        from_email='other@example.com', body='选 A',
                        received_at=datetime.now(timezone.utc).isoformat(),
                        ignored_reason={'code': 'REPLY_SENDER_MISMATCH',
                                        'message': '回复的发件地址与目标邮箱不一致。',
                                        'hint': '请使用目标邮箱回复。'})
        transport.poll_replies.return_value = [rejected]
        app.poll_once()
        stored = self.store.get(record['id'])
        self.assertEqual(stored['status'], 'waiting')
        self.assertIsNone(stored['reply'])
        self.assertEqual(stored['replies'], [])
        self.assertEqual(stored['ignored_replies'][0]['ignored_reason']['code'], 'REPLY_SENDER_MISMATCH')
        self.assertEqual(stored['ignored_replies'][0]['body'], '选 A')

        transport.poll_replies.return_value = [dict(request_id=record['id'], message_id='<user@example.com>',
                     from_email='user@example.com', body='选 B', received_at=datetime.now(timezone.utc).isoformat())]
        app.poll_once()
        stored = self.store.get(record['id'])
        self.assertEqual(stored['status'], 'replied')
        self.assertEqual(stored['reply']['body'], '选 B')
        self.assertEqual(len(stored['ignored_replies']), 1)

    def test_ignored_reply_survives_restart_and_is_deduplicated(self):
        self.configure()
        record = self.app.create('ask', {'subject': 'Question', 'body': 'Pick one'})
        self.store.claim_next()
        self.store.mark_sent(record['id'])
        rejected = dict(request_id=record['id'], message_id='<other@example.com>',
                        from_email='other@example.com', body='选 A',
                        received_at=datetime.now(timezone.utc).isoformat(),
                        ignored_reason={'code': 'REPLY_SENDER_MISMATCH',
                                        'message': '回复的发件地址与目标邮箱不一致。',
                                        'hint': '请使用目标邮箱回复。'})
        self.assertTrue(self.store.add_ignored_reply(rejected))
        self.store.close()
        self.store = Store(self.tmp.name)
        self.assertFalse(self.store.add_ignored_reply(rejected))
        stored = self.store.get(record['id'])
        self.assertEqual(stored['status'], 'waiting')
        self.assertEqual(len(stored['ignored_replies']), 1)
        self.assertEqual(len([event for event in stored['events'] if event['type'] == 'ignored_reply']), 1)
        self.assertEqual(self.store.list_records(q='other@example.com')['total'], 1)

    def test_legacy_records_have_empty_ignored_replies(self):
        self.configure()
        record = self.app.create('notify', {'subject': 'Done', 'body': 'Finished'}, 'legacy-record-key')
        record.pop('ignored_replies', None)
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(record), record['id']))
        self.assertEqual(self.store.get(record['id'])['ignored_replies'], [])
        self.assertEqual(self.store.list_records()['items'][0]['ignored_replies'], [])
        self.assertEqual(self.store.by_key('legacy-record-key')[0]['ignored_replies'], [])

    def test_poll_scan_state_survives_success_and_resets_after_storage_error(self):
        self.configure()
        transport = Mock()
        app = Gateway(self.store, transport)
        record = app.create('notify', {'subject': 'Done', 'body': 'Finished'})
        app.send_once()
        seen_states = []

        def poll(config, records, scan_state):
            seen_states.append(dict(scan_state))
            scan_state['last_uid'] = 42
            return []

        transport.poll_replies.side_effect = poll
        app.poll_once()
        app.poll_once()
        self.assertEqual(seen_states, [{}, {'last_uid': 42}])
        app.save_config({**CONFIG, 'imap_folder': 'Archive'})
        app.poll_once()
        self.assertEqual(seen_states[-1], {})

        transport.poll_replies.side_effect = None
        transport.poll_replies.return_value = [dict(request_id=record['id'], message_id='<r@example.com>',
                        from_email='user@example.com', body='Thanks', received_at=datetime.now(timezone.utc).isoformat())]
        transport.explain_error.return_value = dict(code='IMAP_ERROR', message='收件失败', hint='检查连接')
        with patch.object(self.store, 'add_reply', side_effect=OSError('disk full')):
            app.poll_once()
        self.assertEqual(app.imap_scan_state, {})

    def test_successful_connection_diagnostic_is_never_a_sendable_mail(self):
        from unittest.mock import patch
        self.configure()
        original = self.store.insert

        def interleaved(*args, **kwargs):
            record = original(*args, **kwargs)
            self.assertIsNone(self.store.claim_next())
            return record

        with patch.object(self.store, 'insert', side_effect=interleaved):
            record = self.store.diagnostic([{'name': 'SMTP', 'ok': True}])
        self.assertEqual(record['status'], 'sent')

    def test_deadline_allows_final_poll_to_collect_on_time_reply(self):
        self.configure()
        rec = self.app.create('ask', {'subject': 'Question', 'body': 'Continue?', 'timeout_seconds': 30})
        self.store.claim_next()
        self.store.mark_sent(rec['id'])
        deadline = datetime.fromisoformat(self.store.get(rec['id'])['deadline_at'])
        self.store.expire(deadline + timedelta(seconds=1))
        self.assertEqual(self.store.get(rec['id'])['status'], 'waiting')
        self.store.add_reply(dict(request_id=rec['id'], message_id='<timely@example.com>',
                    from_email='user@example.com', body='Continue', received_at=(deadline - timedelta(seconds=1)).isoformat()))
        self.assertEqual(self.store.get(rec['id'])['status'], 'replied')

    def test_successful_final_mailbox_poll_can_finish_timeout_without_grace(self):
        self.configure()
        rec = self.app.create('ask', {'subject': 'Question', 'body': 'Continue?', 'timeout_seconds': 30})
        self.store.claim_next()
        self.store.mark_sent(rec['id'])
        deadline = datetime.fromisoformat(self.store.get(rec['id'])['deadline_at'])
        self.store.expire(deadline + timedelta(seconds=1), mailbox_checked=True)
        self.assertEqual(self.store.get(rec['id'])['status'], 'timed_out')


if __name__ == '__main__':
    unittest.main()

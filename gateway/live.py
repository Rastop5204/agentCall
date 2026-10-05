"""Durable WeChat inbox and a single leased consumer for efficient mode."""
import json
import re
import time
from datetime import datetime, timedelta

from gateway.service import APIError, integer
from gateway.store import stamp, utcnow


class LiveInbox:
    def __init__(self, store, channels):
        self.store, self.channels = store, channels
        with store.lock:
            store.db.executescript('''
              CREATE TABLE IF NOT EXISTS live_inbox (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL, message_id TEXT NOT NULL,
                contact_id TEXT NOT NULL, record_id TEXT NOT NULL,
                data TEXT NOT NULL, acked_by TEXT, acked_at TEXT,
                UNIQUE(account_id,message_id));
              CREATE INDEX IF NOT EXISTS live_pending ON live_inbox(acked_at,seq);
            ''')

    def config(self):
        return self.store.setting('efficient_mode', {'enabled': False, 'enabled_at': None})

    def view(self):
        with self.store.lock:
            wx = self.channels.config()
            scope = (wx['account_id'], wx['target_contact_id'])
            count = self.store.db.execute('''SELECT COUNT(*) FROM live_inbox
                WHERE acked_at IS NULL AND account_id=? AND contact_id=?''', scope).fetchone()[0]
            lease = self.store.setting('live_consumer', {})
            online = self._lease_active(lease, scope)
            return {**self.config(), 'pending': count, 'consumer_online': online,
                    'consumer_id': lease.get('id') if online else None,
                    'consumer_expires_at': lease.get('expires_at') if online else None,
                    'supports': ['text'], 'delivery': 'at_least_once'}

    def save(self, data):
        if set(data) != {'enabled'} or type(data['enabled']) is not bool:
            raise APIError('INVALID_INPUT', '高效模式设置需要 enabled 布尔值。')
        with self.store.lock, self.store.transaction():
            wx = self.channels.config()
            if data['enabled'] and (not wx['enabled'] or not wx['target_contact_id'] or not wx['account_id']):
                raise APIError('WECHAT_TARGET_REQUIRED', '请先配置微信和目标联系人，再开启高效模式。', status=409)
            old = self.config()
            if old['enabled'] != data['enabled']:
                self.store.set_setting('efficient_mode', {'enabled': data['enabled'],
                    'enabled_at': stamp() if data['enabled'] else old.get('enabled_at')})
                self.store.set_setting('live_consumer', {})
                self.store.changed.notify_all()
        return self.view()

    def _scope(self):
        wx = self.channels.config()
        return wx['account_id'], wx['target_contact_id']

    @staticmethod
    def _lease_active(lease, scope):
        return bool(lease.get('scope') == list(scope) and lease.get('expires_at')
                    and datetime.fromisoformat(lease['expires_at']) > utcnow())

    def receive(self, item):
        # Serialize with configuration changes and normal reply processing.
        with self.store.lock, self.store.transaction():
            config, wx = self.config(), self.channels.config()
            enabled = config['enabled'] and wx['enabled']
            eligible = enabled and item.get('account_id') == wx['account_id'] and item.get('from_contact_id') == wx['target_contact_id']
            if not eligible:
                return self.store.add_wechat_message(item)
            for field in ('message_id', 'body', 'received_at'):
                if not isinstance(item.get(field), str) or not item[field]:
                    return False
            try:
                received = datetime.fromisoformat(item['received_at'].replace('Z', '+00:00'))
                if received.tzinfo is None or received > utcnow() + timedelta(seconds=30):
                    return False
                if received < datetime.fromisoformat(config['enabled_at']).replace(microsecond=0):
                    return self.store.add_wechat_message(item)
            except ValueError:
                return False
            if self.store.db.execute('SELECT 1 FROM live_inbox WHERE account_id=? AND message_id=?',
                    (item['account_id'], item['message_id'])).fetchone():
                return False
            saved = self.store.add_wechat_message(item, capture_unmatched=True)
            related = self.store.db.execute('''SELECT request_id,data FROM replies
                WHERE message_id=? AND json_extract(data,'$.account_id')=?''',
                (item['message_id'], item['account_id'])).fetchone()
            # Existing ask replies remain attached to their original request.
            # Only free messages become new incoming history records.
            event = {'message_id': item['message_id'], 'account_id': item['account_id'],
                     'from_contact_id': item['from_contact_id'], 'from_name': wx['target_contact_name'],
                     'body': item['body'][:32000], 'received_at': received.isoformat(),
                     'kind': 'reply' if related else 'message', 'trust': 'untrusted_user_data'}
            with self.store.transaction():
                if related:
                    event['request_id'] = related['request_id']
                    event['late'] = json.loads(related['data']).get('late', False)
                    record_id = related['request_id']
                else:
                    record = self.store.insert('incoming', {'subject': '来自微信的消息',
                        'body': event['body'], 'agent_name': wx['target_contact_name'], '_channel': 'wechat',
                        '_target_contact_id': wx['target_contact_id'], '_wechat_account_id': wx['account_id'],
                        '_target_contact_name': wx['target_contact_name'], '_recipient_label': wx['target_contact_name']}, {})
                    record['received_at'] = event['received_at']
                    self.store._save(record)
                    record_id = record['id']
                cursor = self.store.db.execute('''INSERT INTO live_inbox
                    (account_id,message_id,contact_id,record_id,data) VALUES (?,?,?,?,?)''',
                    (item['account_id'], item['message_id'], item['from_contact_id'], record_id, json.dumps(event, ensure_ascii=False)))
                self.store.changed.notify_all()
            return saved or bool(cursor.lastrowid)

    def _pending(self, limit):
        rows = self.store.db.execute('''SELECT seq,record_id,data FROM live_inbox
            WHERE acked_at IS NULL AND account_id=? AND contact_id=? ORDER BY seq LIMIT ?''',
            (*self._scope(), limit)).fetchall()
        return [{'id': row['seq'], 'record_id': row['record_id'], **json.loads(row['data'])} for row in rows]

    def peek(self, limit=50):
        with self.store.lock:
            return {'items': self._pending(limit), 'mode': self.view()}

    @staticmethod
    def consumer(data):
        value = data.get('consumer_id')
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}', value):
            raise APIError('INVALID_INPUT', 'consumer_id 必须是 8–128 位字母、数字或 . _ : -。')
        return value

    def _lease(self, consumer_id):
        if not self.config()['enabled']:
            raise APIError('EFFICIENT_MODE_DISABLED', '高效模式已关闭。', status=409)
        lease = self.store.setting('live_consumer', {})
        scope = self._scope()
        active = self._lease_active(lease, scope)
        if active and lease['id'] != consumer_id:
            raise APIError('INBOX_CONSUMER_BUSY', '另一位 Agent 正在收取微信消息。', '结束旧监听或等待 60 秒租约到期。', 409)
        self.store.set_setting('live_consumer', {'id': consumer_id, 'scope': list(scope),
            'expires_at': stamp(utcnow() + timedelta(seconds=60))})

    def claim(self, data):
        if set(data) - {'consumer_id', 'wait', 'limit'}:
            raise APIError('INVALID_INPUT', '收件请求包含不支持的字段。')
        consumer = self.consumer(data)
        wait, limit = integer(data, 'wait', 0, 0, 25), integer(data, 'limit', 50, 1, 50)
        stop = time.monotonic() + wait
        with self.store.changed:
            self._lease(consumer)
            while True:
                self._lease(consumer)
                items = self._pending(limit)
                if items or time.monotonic() >= stop:
                    return {'items': items, 'mode': self.view(), 'consumer_id': consumer}
                self.store.changed.wait(max(0, stop - time.monotonic()))

    def ack(self, data):
        if set(data) != {'consumer_id', 'ids'}:
            raise APIError('INVALID_INPUT', '确认收件需要 consumer_id 和 ids。')
        consumer = self.consumer(data)
        ids = data['ids']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 50 or any(type(i) is not int or i < 1 for i in ids):
            raise APIError('INVALID_INPUT', 'ids 需要 1–50 个有效收件编号。')
        with self.store.lock, self.store.transaction():
            self._lease(consumer)
            placeholders = ','.join('?' for _ in ids)
            rows = self.store.db.execute(f'''SELECT seq,record_id,acked_by FROM live_inbox
                WHERE seq IN ({placeholders}) AND account_id=? AND contact_id=?''', (*ids, *self._scope())).fetchall()
            if len(rows) != len(set(ids)) or any(r['acked_by'] not in {None, consumer} for r in rows):
                raise APIError('INBOX_ACK_CONFLICT', '收件编号不属于当前联系人或已由其他 Agent 确认。', status=409)
            for row in rows:
                if row['acked_by'] is not None:
                    continue
                self.store.db.execute('UPDATE live_inbox SET acked_by=?,acked_at=? WHERE seq=?', (consumer, stamp(), row['seq']))
                record = self.store.get(row['record_id'])
                if record and record['kind'] == 'incoming':
                    record['status'] = 'read'
                    self.store._event(record, 'read', 'Agent 已确认读取；不代表任务执行完成。')
                    self.store._save(record)
            return {'acked': sorted(set(ids)), 'consumer_id': consumer}

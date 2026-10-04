"""Durable queue and history. All state transitions are serialized in SQLite."""

import json
import re
import fcntl
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or utcnow()).isoformat(timespec='milliseconds')


def record_data(value):
    record = json.loads(value)
    record.setdefault('ignored_replies', [])
    record.setdefault('channel', 'email')
    record.setdefault('requested_channel', 'auto')
    record.setdefault('recipient_label', record.get('target_email', ''))
    record.setdefault('fallback_reason', None)
    return record


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.process_lock = (self.directory / 'service.lock').open('a')
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.process_lock.close()
            raise RuntimeError('此数据目录已有 agentCall 服务正在运行。') from None
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.db = sqlite3.connect(self.directory / 'emailcall.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        (self.directory / 'emailcall.sqlite3').chmod(0o600)
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, data TEXT NOT NULL,
            idempotency_key TEXT UNIQUE, fingerprint TEXT);
          CREATE INDEX IF NOT EXISTS records_state ON records(status,created_at);
          CREATE TABLE IF NOT EXISTS replies (
            request_id TEXT NOT NULL, message_id TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(request_id,message_id));
          CREATE TABLE IF NOT EXISTS ignored_replies (
            request_id TEXT NOT NULL, message_id TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(request_id,message_id));
          CREATE TABLE IF NOT EXISTS wechat_inbox (
            account_id TEXT NOT NULL, message_id TEXT NOT NULL, received_at TEXT NOT NULL,
            PRIMARY KEY(account_id,message_id));
        ''')
        if not self.setting('token'):
            self.set_setting('token', secrets.token_urlsafe(32))
        # SMTP has no exactly-once delivery protocol. Never retry an uncertain DATA commit.
        for row in self.db.execute("SELECT data FROM records WHERE status='sending'").fetchall():
            record = record_data(row['data'])
            self.fail(record['id'], {'code': 'DELIVERY_UNKNOWN', 'message': '服务在发送过程中停止，消息投递结果未知。',
                                     'hint': '请先检查微信或目标邮箱，再决定是否以新请求重发，避免重复通知。'})

    def close(self):
        with self.lock:
            self.db.close()
            self.process_lock.close()

    def setting(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row['value']) if row else default

    def set_setting(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def config(self):
        return self.setting('config', {})

    def token(self):
        return self.setting('token')

    def rotate_token(self):
        token = secrets.token_urlsafe(32)
        self.set_setting('token', token)
        return token

    def _save(self, record):
        record['updated_at'] = stamp()
        self.db.execute('UPDATE records SET status=?,data=? WHERE id=?',
                        (record['status'], json.dumps(record, ensure_ascii=False), record['id']))
        self.changed.notify_all()

    def _event(self, record, event_type, message):
        record['events'].append({'at': stamp(), 'type': event_type, 'message': message})

    def get(self, request_id):
        with self.lock:
            row = self.db.execute('SELECT data FROM records WHERE id=?', (request_id,)).fetchone()
            return record_data(row['data']) if row else None

    def by_key(self, key):
        with self.lock:
            row = self.db.execute('SELECT data,fingerprint FROM records WHERE idempotency_key=?', (key,)).fetchone()
            return (record_data(row['data']), row['fingerprint']) if row else None

    def insert(self, kind, payload, config, key=None, fingerprint=None, error=None):
        request_id = uuid.uuid4().hex
        now = stamp()
        initial_status = 'failed' if error else ('sent' if kind == 'diagnostic' else 'queued')
        record = dict(id=request_id, kind=kind, subject=payload.get('subject', ''), body=payload.get('body', ''),
                      agent_name=payload.get('agent_name', 'Agent'), target_email=config.get('target_email', ''),
                      sender_email=config.get('email', ''), status=initial_status,
                      created_at=now, updated_at=now, sent_at=None, deadline_at=None,
                      timeout_seconds=payload.get('timeout_seconds', 300) if kind == 'ask' else None,
                      message_id=f'<ac.{request_id}@agentcall.local>', error=error, reply=None, replies=[],
                      requested_channel=payload.get('channel', 'auto'), channel=payload.get('_channel', 'email'),
                      target_contact_id=payload.get('_target_contact_id', ''), target_contact_name=payload.get('_target_contact_name', ''),
                      wechat_account_id=payload.get('_wechat_account_id', ''),
                      recipient_label=payload.get('_recipient_label') or config.get('target_email', ''),
                      fallback_reason=payload.get('_fallback_reason'), transport_message_id=None,
                      ignored_replies=[], events=[])
        self._event(record, record['status'], error['message'] if error else ('邮箱连接检查完成。' if kind == 'diagnostic' else '请求已保存，等待发送。'))
        with self.lock, self.db:
            self.db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?)',
                            (request_id, kind, record['status'], now, json.dumps(record, ensure_ascii=False), key, fingerprint))
            self.changed.notify_all()
        return record

    def claim_next(self):
        with self.lock, self.db:
            row = self.db.execute("SELECT data FROM records WHERE status='queued' AND kind IN ('ask','notify') ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            record = record_data(row['data'])
            record['status'] = 'sending'
            record['sending_at'] = stamp()
            if record['kind'] == 'ask':
                record['deadline_at'] = stamp(utcnow() + timedelta(seconds=record['timeout_seconds']))
            self._event(record, 'sending', '正在检查可用通道并发送消息。')
            self._save(record)
            return record

    def set_route(self, request_id, channel, fallback_reason=None):
        with self.lock, self.db:
            record = self.get(request_id)
            record['channel'], record['fallback_reason'] = channel, fallback_reason
            record['recipient_label'] = record.get('target_contact_name', record['recipient_label']) if channel == 'wechat' else record['target_email']
            label = '微信' if channel == 'wechat' else '邮箱'
            self._event(record, 'fallback' if fallback_reason else 'route',
                        ('微信不可用，改用邮箱：' + fallback_reason['message']) if fallback_reason else ('本次通过' + label + '发送。'))
            self._save(record)
            return record

    def mark_sent(self, request_id, transport_message_id=None):
        with self.lock, self.db:
            record = self.get(request_id)
            timely_reply = record['reply'] and not record['reply'].get('late')
            record['status'] = ('replied' if timely_reply else 'waiting') if record['kind'] == 'ask' else 'sent'
            record['sent_at'] = stamp()
            if transport_message_id:
                record['transport_message_id'] = transport_message_id
            label = '微信服务' if record['channel'] == 'wechat' else '邮件服务器'
            self._event(record, record['status'], label + '已接收消息。' + ('等待用户回复。' if record['status'] == 'waiting' else ''))
            self._save(record)

    def fail(self, request_id, error):
        with self.lock, self.db:
            record = self.get(request_id)
            record['status'], record['error'] = 'failed', error
            self._event(record, 'failed', error['message'])
            self._save(record)

    def diagnostic(self, checks, channel='email'):
        error = next((c['error'] for c in checks if not c['ok']), None)
        body = '\n'.join(f"{c['name']}: " + ('连接成功' if c['ok'] else c['error']['message'] +
                         ('\n' + c['error']['hint'] if c['error'].get('hint') else '')) for c in checks)
        record = self.insert('diagnostic', {'subject': ('微信' if channel == 'wechat' else '邮箱') + '连接检查', 'body': body,
                            'agent_name': 'agentCall', '_channel': channel}, self.config(), error=error)
        return self.get(record['id'])

    def expire(self, now=None, mailbox_checked=False):
        now = now or utcnow()
        with self.lock, self.db:
            for row in self.db.execute("SELECT data FROM records WHERE status='waiting'").fetchall():
                record = record_data(row['data'])
                deadline = datetime.fromisoformat(record['deadline_at'])
                # A final IMAP sweep can observe a timely arrival just after the deadline.
                # During outages the independent clock still ends waiting within 30 seconds.
                grace = 0 if record['channel'] == 'wechat' or mailbox_checked else 30
                if deadline + timedelta(seconds=grace) <= now:
                    record['status'] = 'timed_out'
                    self._event(record, 'timed_out', '等待时间已结束，Agent 将根据任务风险自行决定下一步；后续回复仍会保存。')
                    self._save(record)

    def next_poll_delay(self, interval):
        with self.lock:
            row = self.db.execute("SELECT MIN(json_extract(data,'$.deadline_at')) FROM records WHERE status='waiting'").fetchone()
            if row[0]:
                return min(interval, max(1, (datetime.fromisoformat(row[0]) - utcnow()).total_seconds() + 1))
            return interval

    def add_reply(self, item):
        with self.lock, self.db:
            record = self.get(item['request_id'])
            if not record or item.get('ignored_reason'):
                return False
            if record['channel'] == 'wechat':
                if item.get('channel') != 'wechat' or record.get('target_contact_id') != item.get('from_contact_id') or record.get('wechat_account_id') != item.get('account_id'):
                    return False
            elif item.get('channel', 'email') != 'email' or record['target_email'].casefold() != item.get('from_email', '').casefold():
                return False
            if self.db.execute('SELECT 1 FROM replies WHERE request_id=? AND message_id=?',
                               (record['id'], item['message_id'])).fetchone():
                return False
            late = record['status'] == 'timed_out' or bool(record['deadline_at'] and
                   datetime.fromisoformat(item['received_at']) > datetime.fromisoformat(record['deadline_at']))
            reply = {k: item[k] for k in ('message_id', 'from_email', 'from_contact_id', 'from_name', 'body', 'received_at', 'account_id') if k in item}
            reply['channel'] = record['channel']
            reply['late'] = late
            if record['status'] == 'failed':
                reply['unconfirmed'] = True
            self.db.execute('INSERT INTO replies VALUES (?,?,?)', (record['id'], item['message_id'], json.dumps(reply)))
            record['replies'].append(reply)
            if record['reply'] is None:
                record['reply'] = reply
            if record['kind'] == 'ask' and record['status'] == 'waiting' and not late:
                record['status'], record['reply'] = 'replied', reply
            if reply.get('unconfirmed'):
                self._event(record, 'reply_unconfirmed', '收到明确关联的回复并归档；原发送结果未知，请求状态未自动改变。')
            else:
                self._event(record, 'late_reply' if late else 'reply', '收到超时后的回复，已归档。' if late else '收到用户回复。')
            self._save(record)
            return True

    def add_ignored_reply(self, item):
        """Keep rejected thread matches visible without treating them as user decisions."""
        with self.lock, self.db:
            record = self.get(item['request_id'])
            if not record or not item.get('ignored_reason'):
                return False
            if self.db.execute('SELECT 1 FROM ignored_replies WHERE request_id=? AND message_id=?',
                               (record['id'], item['message_id'])).fetchone():
                return False
            reply = {k: item[k] for k in ('message_id', 'from_email', 'from_contact_id', 'from_name', 'body', 'received_at', 'account_id', 'channel') if k in item}
            reply['ignored_reason'] = item['ignored_reason']
            self.db.execute('INSERT INTO ignored_replies VALUES (?,?,?)',
                            (record['id'], item['message_id'], json.dumps(reply)))
            record['ignored_replies'].append(reply)
            self._event(record, 'ignored_reply',
                        f"收到来自 {reply.get('from_email') or reply.get('from_name') or reply.get('from_contact_id')} 的消息，未采纳为回复：{reply['ignored_reason']['message']}")
            self._save(record)
            return True

    def poll_records(self):
        # Retain a bounded late-reply window, always prioritizing live requests.
        cutoff = stamp(utcnow() - timedelta(days=30))
        with self.lock:
            rows = self.db.execute("""SELECT data FROM records WHERE kind IN ('ask','notify')
                AND status IN ('waiting','sent','timed_out','replied') AND created_at>=?
                AND COALESCE(json_extract(data,'$.channel'),'email')='email'
                ORDER BY (status='waiting') DESC,created_at DESC LIMIT 300""", (cutoff,)).fetchall()
            sender = self.config().get('email', '')
            return [rec for row in rows if (rec := record_data(row['data']))['sender_email'] == sender]

    def add_wechat_message(self, item):
        """Correlate only one-to-one replies to the snapshotted account/contact.

        A plain reply can answer exactly one live question. Parallel questions
        require their explicit request marker; ambiguity is recorded, never guessed.
        """
        if not all(isinstance(item.get(k), str) and item[k] for k in ('message_id', 'from_contact_id', 'account_id', 'body', 'received_at')):
            return False
        item = {**item, 'body': item['body'][:32000], 'channel': 'wechat'}
        try:
            received = datetime.fromisoformat(item['received_at'].replace('Z', '+00:00'))
            if received.tzinfo is None or received > utcnow() + timedelta(seconds=30):
                return False
        except ValueError:
            return False
        with self.lock, self.db:
            if self.db.execute('SELECT 1 FROM wechat_inbox WHERE account_id=? AND message_id=?', (item['account_id'], item['message_id'])).fetchone():
                return False
            tokens = set(re.findall(r'\[(?:AC|EC):([A-Za-z0-9_-]+)\]', item['body']))
            reference = item.get('reference_id')
            cutoff = stamp(utcnow() - timedelta(days=30))
            rows = self.db.execute("""SELECT data FROM records WHERE kind IN ('ask','notify')
                AND json_extract(data,'$.channel')='wechat'
                AND status IN ('sending','waiting','sent','replied','timed_out','failed') AND created_at>=?
                ORDER BY (id=? OR json_extract(data,'$.transport_message_id')=?) DESC,
                (status IN ('waiting','sending')) DESC,created_at DESC LIMIT 300""",
                (cutoff, next(iter(tokens)) if len(tokens) == 1 else '', reference or '')).fetchall()
            records = [record_data(row['data']) for row in rows]
            records = [r for r in records if r['status'] != 'failed' or (r.get('error') or {}).get('code') in
                       ('WECHAT_SEND_UNCERTAIN', 'WECHAT_DELIVERY_UNKNOWN', 'DELIVERY_UNKNOWN')]
            related = [r for r in records if r.get('target_contact_id') == item['from_contact_id'] and r.get('wechat_account_id') == item['account_id']]
            conflicting = False
            if tokens:
                matches = [r for r in records if len(tokens) == 1 and r['id'] in tokens]
                referenced = [r for r in records if reference and r.get('transport_message_id') == reference]
                if referenced and matches and referenced[0]['id'] != matches[0]['id']:
                    matches, conflicting = [], True
            elif reference:
                matches = [r for r in records if r.get('transport_message_id') == reference]
            else:
                matches = [r for r in records if r['kind'] == 'ask' and r['status'] in ('waiting','sending')
                           and r.get('target_contact_id') == item['from_contact_id'] and r.get('wechat_account_id') == item['account_id']]
                # Safety must not depend on the bounded display/matching window.
                unresolved = self.db.execute("""SELECT 1 FROM records WHERE kind='ask' AND created_at>=?
                    AND json_extract(data,'$.channel')='wechat'
                    AND json_extract(data,'$.target_contact_id')=?
                    AND json_extract(data,'$.wechat_account_id')=?
                    AND json_extract(data,'$.reply') IS NULL
                    AND (status='timed_out' OR (status='failed' AND json_extract(data,'$.error.code')
                        IN ('WECHAT_SEND_UNCERTAIN','WECHAT_DELIVERY_UNKNOWN','DELIVERY_UNKNOWN')))
                    LIMIT 1""", (cutoff, item['from_contact_id'], item['account_id'])).fetchone()
                if unresolved:
                    matches, conflicting = [], True
            matches = [r for r in matches if received >= datetime.fromisoformat(r.get('sending_at') or r['created_at']).replace(microsecond=0)]
            if len(matches) == 1:
                record = matches[0]
                if record.get('wechat_account_id') != item['account_id']:
                    return False
                item['request_id'] = record['id']
                if record.get('target_contact_id') != item['from_contact_id']:
                    item['ignored_reason'] = dict(code='WECHAT_SENDER_MISMATCH', message='微信回复并非来自此请求的目标联系人。', hint='只有所选联系人可以回复此请求。')
                    saved = self.add_ignored_reply(item)
                else:
                    item['body'] = re.sub(r'\[(?:AC|EC):' + re.escape(record['id']) + r'\]', '', item['body']).strip()
                    saved = bool(item['body']) and self.add_reply(item)
            elif related:
                self.diagnostic([{'name': '微信回复匹配', 'ok': False, 'error': dict(code='WECHAT_REPLY_AMBIGUOUS' if len(matches) > 1 or conflicting else 'WECHAT_REPLY_UNMATCHED',
                    message='收到目标联系人的微信消息，但无法安全确定回复归属，已保存且未作为决策。',
                    hint='请在回复中保留对应问题的 [AC:请求编号]。回复内容：' + item['body'])}], channel='wechat')
                saved = False
            else:
                return False
            self.db.execute('INSERT INTO wechat_inbox VALUES (?,?,?)', (item['account_id'], item['message_id'], item['received_at']))
            return saved

    def has_active(self):
        with self.lock:
            return bool(self.db.execute("SELECT 1 FROM records WHERE status IN ('queued','sending','waiting') LIMIT 1").fetchone())

    def active_count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM records WHERE status IN ('queued','sending','waiting')").fetchone()[0]

    def list_records(self, q='', status='', kind='', limit=30, offset=0):
        clauses, args = [], []
        if q:
            clauses.append("(json_extract(data,'$.subject') LIKE ? OR json_extract(data,'$.body') LIKE ? OR json_extract(data,'$.agent_name') LIKE ? OR id LIKE ? OR json_extract(data,'$.replies') LIKE ? OR json_extract(data,'$.ignored_replies') LIKE ?)")
            args.extend([f'%{q}%'] * 6)
        for field, value in (('status', status), ('kind', kind)):
            if value:
                clauses.append(f'{field}=?')
                args.append(value)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.lock:
            total = self.db.execute('SELECT COUNT(*) FROM records' + where, args).fetchone()[0]
            rows = self.db.execute('SELECT data FROM records' + where + ' ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?', (*args, limit, offset)).fetchall()
            stats = dict(total=0, sent=0, waiting=0, replied=0, failed=0, timed_out=0, queued=0, sending=0)
            for row in self.db.execute('SELECT status,COUNT(*) AS n FROM records GROUP BY status'):
                stats[row['status']] = row['n']
                stats['total'] += row['n']
            return {'items': [record_data(row['data']) for row in rows], 'total': total, 'stats': stats}

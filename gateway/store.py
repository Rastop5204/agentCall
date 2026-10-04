"""Durable queue and history. All state transitions are serialized in SQLite."""

import json
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
            raise RuntimeError('此数据目录已有 EmailCall 服务正在运行。') from None
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
        ''')
        if not self.setting('token'):
            self.set_setting('token', secrets.token_urlsafe(32))
        # SMTP has no exactly-once delivery protocol. Never retry an uncertain DATA commit.
        for row in self.db.execute("SELECT data FROM records WHERE status='sending'").fetchall():
            record = json.loads(row['data'])
            self.fail(record['id'], {'code': 'DELIVERY_UNKNOWN', 'message': '服务在发送过程中停止，邮件投递结果未知。',
                                     'hint': '请先检查目标邮箱，再决定是否以新请求重发，避免重复邮件。'})

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
            return json.loads(row['data']) if row else None

    def by_key(self, key):
        with self.lock:
            row = self.db.execute('SELECT data,fingerprint FROM records WHERE idempotency_key=?', (key,)).fetchone()
            return (json.loads(row['data']), row['fingerprint']) if row else None

    def insert(self, kind, payload, config, key=None, fingerprint=None, error=None):
        request_id = uuid.uuid4().hex
        now = stamp()
        initial_status = 'failed' if error else ('sent' if kind == 'diagnostic' else 'queued')
        record = dict(id=request_id, kind=kind, subject=payload.get('subject', ''), body=payload.get('body', ''),
                      agent_name=payload.get('agent_name', 'Agent'), target_email=config.get('target_email', ''),
                      sender_email=config.get('email', ''), status=initial_status,
                      created_at=now, updated_at=now, sent_at=None, deadline_at=None,
                      timeout_seconds=payload.get('timeout_seconds', 300) if kind == 'ask' else None,
                      message_id=f'<ec.{request_id}@emailcall.local>', error=error, reply=None, replies=[], events=[])
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
            record = json.loads(row['data'])
            record['status'] = 'sending'
            if record['kind'] == 'ask':
                record['deadline_at'] = stamp(utcnow() + timedelta(seconds=record['timeout_seconds']))
            self._event(record, 'sending', '正在通过加密连接发送邮件。')
            self._save(record)
            return record

    def mark_sent(self, request_id):
        with self.lock, self.db:
            record = self.get(request_id)
            record['status'] = 'waiting' if record['kind'] == 'ask' else 'sent'
            record['sent_at'] = stamp()
            self._event(record, record['status'], '邮件服务器已接收，等待用户回复。' if record['kind'] == 'ask' else '邮件服务器已接收。')
            self._save(record)

    def fail(self, request_id, error):
        with self.lock, self.db:
            record = self.get(request_id)
            record['status'], record['error'] = 'failed', error
            self._event(record, 'failed', error['message'])
            self._save(record)

    def diagnostic(self, checks):
        error = next((c['error'] for c in checks if not c['ok']), None)
        body = '\n'.join(f"{c['name']}: " + ('连接成功' if c['ok'] else c['error']['message']) for c in checks)
        record = self.insert('diagnostic', {'subject': '邮箱连接检查', 'body': body, 'agent_name': 'EmailCall'}, self.config(), error=error)
        return self.get(record['id'])

    def expire(self, now=None, mailbox_checked=False):
        now = now or utcnow()
        with self.lock, self.db:
            for row in self.db.execute("SELECT data FROM records WHERE status='waiting'").fetchall():
                record = json.loads(row['data'])
                deadline = datetime.fromisoformat(record['deadline_at'])
                # A final IMAP sweep can observe a timely arrival just after the deadline.
                # During outages the independent clock still ends waiting within 30 seconds.
                if deadline + timedelta(seconds=0 if mailbox_checked else 30) <= now:
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
            if not record or record['target_email'].casefold() != item['from_email'].casefold():
                return False
            if self.db.execute('SELECT 1 FROM replies WHERE request_id=? AND message_id=?',
                               (record['id'], item['message_id'])).fetchone():
                return False
            late = record['status'] == 'timed_out' or bool(record['deadline_at'] and
                   datetime.fromisoformat(item['received_at']) > datetime.fromisoformat(record['deadline_at']))
            reply = {k: item[k] for k in ('message_id', 'from_email', 'body', 'received_at')}
            reply['late'] = late
            self.db.execute('INSERT INTO replies VALUES (?,?,?)', (record['id'], item['message_id'], json.dumps(reply)))
            record['replies'].append(reply)
            if record['reply'] is None:
                record['reply'] = reply
            if record['kind'] == 'ask' and record['status'] == 'waiting' and not late:
                record['status'], record['reply'] = 'replied', reply
            self._event(record, 'late_reply' if late else 'reply', '收到超时后的回复，已归档。' if late else '收到用户回复。')
            self._save(record)
            return True

    def poll_records(self):
        # Retain a bounded late-reply window, always prioritizing live requests.
        cutoff = stamp(utcnow() - timedelta(days=30))
        with self.lock:
            rows = self.db.execute("""SELECT data FROM records WHERE kind IN ('ask','notify')
                AND status IN ('waiting','sent','timed_out','replied') AND created_at>=?
                ORDER BY (status='waiting') DESC,created_at DESC LIMIT 300""", (cutoff,)).fetchall()
            sender = self.config().get('email', '')
            return [rec for row in rows if (rec := json.loads(row['data']))['sender_email'] == sender]

    def has_active(self):
        with self.lock:
            return bool(self.db.execute("SELECT 1 FROM records WHERE status IN ('queued','sending','waiting') LIMIT 1").fetchone())

    def active_count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM records WHERE status IN ('queued','sending','waiting')").fetchone()[0]

    def list_records(self, q='', status='', kind='', limit=30, offset=0):
        clauses, args = [], []
        if q:
            clauses.append("(json_extract(data,'$.subject') LIKE ? OR json_extract(data,'$.body') LIKE ? OR json_extract(data,'$.agent_name') LIKE ? OR id LIKE ? OR json_extract(data,'$.replies') LIKE ?)")
            args.extend([f'%{q}%'] * 5)
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
            return {'items': [json.loads(row['data']) for row in rows], 'total': total, 'stats': stats}

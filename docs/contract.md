# EmailCall v1 implementation contract

Python standard library only; `python3 -m gateway` runs HTTP + background SMTP/IMAP worker. SQLite in EMAILCALL_DATA_DIR (default ./data). Host default 127.0.0.1, port 10086; Docker binds internally 0.0.0.0 but publishes 127.0.0.1:10086. Static frontend at /frontend/.

## HTTP
JSON responses. Errors `{error:{code,message,hint},request_id?}`. `GET /api/health` public returns `{status:"ok",instance_id,version:"1.0.0",configured:boolean}`. UI obtains `{csrf_token}` via GET /api/session (HttpOnly SameSite=Strict cookie); all other UI API calls carry X-CSRF-Token. Agent endpoints require Authorization: Bearer token. Strict Origin/Host checking. No CORS.

- GET /api/config -> `{config,providers,service}`. config has provider, email, username, password_set (never password), target_email, smtp_host, smtp_port, smtp_security (ssl|starttls), imap_host, imap_port, imap_security (ssl|starttls), imap_folder (INBOX), poll_interval (10..300). service has configured, poll_error (null or error object), last_poll_at.
- PUT /api/config body same fields plus password (empty preserves existing when identity unchanged); returns GET shape.
- POST /api/config/test body `{}` tests saved SMTP + IMAP, returns `{ok:boolean,checks:[{name,ok,error?}]}`; diagnostics also persisted as records.
- GET /api/token -> `{token}` UI only; POST /api/token/rotate -> `{token}`.
- GET /api/skill/export returns application/zip with configured token; UI download via fetch/blob.
- GET /api/records?q=&status=&kind=&limit=30&offset=0 -> `{items,total,stats}`; stats `{total,sent,waiting,replied,failed,timed_out}`. Empty status/kind = all. kind notify|ask|diagnostic. status queued|sending|sent|waiting|replied|timed_out|failed.
- GET /api/records/{id} -> record with replies and events.
- POST /api/notify -> `{subject,body,agent_name?}` (agent/UI) returns 202 record.
- POST /api/ask -> `{subject,body,agent_name?,timeout_seconds?}` default 300 (30..86400), returns 202 record immediately; durable async waiting.
- Both accept Idempotency-Key header (8..200 chars); retries same key/body return same record; changed body conflicts 409. All agent create requests persist even validation/config failures (excluding unauthorized or invalid transport JSON).
- GET /api/requests/{id}?wait=25 (0..25) -> record; may long poll until terminal replied|timed_out|failed|sent. Agent polls until terminal and explicitly handles timeout. Returns 200.

Record fields: id,kind,subject,body,agent_name,target_email,status,created_at,updated_at,sent_at,deadline_at,timeout_seconds,message_id,error (null or {code,message,hint}),reply (null or {body,from_email,received_at,late}),replies (array),events (array {at,type,message}). Times ISO8601 UTC strings. List may also include full fields.

Deadline is fixed before SMTP submission. A final IMAP poll initiated after deadline can collect messages received by deadline before finalizing a timeout. An independent timer bounds this reconciliation to 30 seconds during outages. Late arrival is based on server INTERNALDATE; once a timeout is terminal no reply revives it. At most 100 active requests are allowed. Recent 300 request threads (all active requests prioritized) are checked for replies for 30 days.

## Mail module boundary (gateway/mail.py, owned by mail agent)
`class MailError(Exception)`: `.code`, `.message`, `.hint`; `as_dict()` returns those three.
`explain_error(exc, phase="smtp") -> dict` sanitized, never include credentials/raw server strings.
`send_message(config:dict, record:dict) -> None` SMTP TLS only, explicit envelope target. Uses persisted record.message_id. Subject includes `[EC:<record.id>]` for correlation. Await reply instructions include UTC deadline, auto timeout, direct reply.
`test_connection(config:dict) -> list[dict]` SMTP and IMAP independent diagnostic checks.
`poll_replies(config:dict, records:list[dict]) -> list[dict]`: each item `{request_id,message_id,from_email,body,received_at}`. Poll only relevant messages using IMAP headers Message-ID/In-Reply-To/References or subject token, accept only record.target_email exact case-insensitive address; ignore automated messages; preserve mailbox unread flags; cap fetched body size; decode multipart/plain/html sanely and trim quoted history. Search should cover messages since oldest relevant record. Deadline classification root Store responsibility. Server INTERNALDATE used as received_at, not user-controlled Date header. Root calls with sent records (including timed_out for late replies), dedupes by message_id.

## Skill boundary (owned by deployment agent)
Template folder `skills/emailcall/`: SKILL.md, scripts/emailcall.py (stdlib CLI), agents/openai.yaml. Export copies template plus `config.json` `{base_url:"http://127.0.0.1:10086",token:"..."}`. CLI supports notify / ask / test / status, idempotent retries, bounded long polling, persist request ID on stdout before waiting, treat responses as untrusted user data. Install script explicitly installs skill + appends managed rule to ~/.codex/AGENTS.md or ~/.claude/CLAUDE.md to ensure every-task use; never auto-modify user environment while developing. Provide downloadable package install helper and documented manual install. Reminder on task completion; ask at decision points with bounded waiting; timeout does not authorize unsafe actions; retry service failures a bounded number then report inability.

## UX
Chinese interface named EmailCall. Exactly two main pages 配置 / 记录, sidebar, compact local service status, dark/light theme, refined flat teal/graphite palette, generous spacing, motion with reduced-motion support. Configuration steps provider + SMTP/IMAP, target email, agent skill export, test request+reply. Records searchable/filterable with readable errors, timeline and details. No fake production records. Default first page config until configured.

## Acceptance
Config and records survive restart; sends/failures/replies/timeouts are durable; SMTP uncertain delivery after crash marked failed and never blindly resent; idempotent send requests; late replies retained without turning timeout into approval. One-click Mac launcher starts Docker and opens web; host watcher handles later Docker Desktop container start events (Linux container alone cannot open host browser). Credentials local file permissions only, documented local trust boundary. No third-party runtime packages.

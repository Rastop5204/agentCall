# Vendored: wx-filehelper-api protocol core

`direct_bot.py` is vendored from
[wx-filehelper-api](https://github.com/CjackHwang/wx-filehelper-api) at commit
`cb52d3a6a35dd5769c170c3a5fa0f78fcd017afe` (2026-02-11). The upstream README
declares the project MIT-licensed; no LICENSE file was present in the
repository at vendoring time.

The upstream service is a template app (FastAPI + SQLite + web UI) around a
self-contained protocol core. Only that core — the `WeChatHelperBot` class
driving the 文件传输助手 (filehelper) web WeChat endpoints — is vendored here;
`gateway/wechat.py` embeds it directly in the gateway process.

## Local modifications

All changes are marked with `# agentCall patch:` comments in the file.

- **A** `_normalize_messages` carries each message's raw `CreateTime` (the
  gateway's reply anti-replay check needs real per-message receive times).
- **B** Quoted replies (`MsgType 49` / `AppMsgType 57` refermsg) are flattened
  to the text form `gateway/store.py` correlates: quoted original, a divider
  line of repeated `- `, then the user's own words; the refermsg `svrid`
  surfaces as `reference_id`.
- **C** HTTP traces default to OFF (upstream: on). Traces persist up to 4 KiB
  of every request/response body — including message text — and their default
  directory (cwd) is read-only in the gateway container. Opt back in with
  `WECHAT_TRACE_ENABLED=1` when debugging.
- **D** The ~9 `print()` calls that interpolate exception/response text are
  removed: httpx errors embed full URLs carrying `pass_ticket`/`skey`
  credentials, which must not reach container logs.
- **E** `__init__` takes `state_path` so the session file lives in the
  gateway's private data directory, and `reset_session()` exists so a
  server-side kick can fall back to the QR login path (upstream wedges with
  `_has_auth()` permanently true).
- **F** `send_text()` returns the server-assigned `MsgID` (or `None`) instead
  of a bool, so the gateway can persist it for reference matching.
- **G** A network failure while polling the login endpoint no longer clears
  the login uuid (`-1` instead of `0`): a transient blip must not invalidate
  the QR the user is currently scanning.

Upstream method (`sha256 278234605ced9a83729c2ea5d9a649fcf00424aa1daf360c0a5d1b25987de8bd`):
`curl -fsSL https://raw.githubusercontent.com/CjackHwang/wx-filehelper-api/cb52d3a6a35dd5769c170c3a5fa0f78fcd017afe/direct_bot.py`

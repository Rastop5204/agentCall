"""TLS SMTP/IMAP transport with bounded, read-only reply polling.

Only Python's standard library is required. Configuration/records are owned by
Store; this module never logs credentials or returns server-provided error text.
"""
from __future__ import annotations

import hashlib
import imaplib
import re
import smtplib
import socket
import ssl
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formataddr, format_datetime, getaddresses
from html.parser import HTMLParser

SOCKET_TIMEOUT = 15
MAX_HEADER_BYTES = 32 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_REPLY_CHARS = 32000
MAX_CANDIDATES = 100
MAX_SEARCH_BYTES = 6000
_ID = re.compile(r"<[^<>\s]+>")
_SUBJECT_TOKEN = re.compile(r"\[EC:([A-Za-z0-9_-]+)\]")


class MailError(Exception):
    """A safe, user-readable transport failure."""

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def explain_error(exc: Exception, phase: str = "smtp") -> dict:
    """Translate errors without echoing potentially secret server response text."""
    phase = phase.upper() if phase.lower() in {"smtp", "imap"} else "MAIL"
    if isinstance(exc, MailError):
        return exc.as_dict()
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        result = ("SMTP_AUTH_FAILED", "发件服务器拒绝了登录。", "请检查账号及应用专用密码／客户端授权码，并在邮箱设置中开启 SMTP。普通登录密码通常不能用于此处。")
    elif isinstance(exc, ssl.SSLCertVerificationError):
        result = ("TLS_CERTIFICATE_INVALID", "邮件服务器的安全证书无法验证。", "检查服务器地址、系统时间与网络代理；请勿关闭证书校验。")
    elif isinstance(exc, ssl.SSLError):
        result = ("TLS_HANDSHAKE_FAILED", "无法与邮件服务器建立加密连接。", "请确认端口与 SSL/TLS 或 STARTTLS 模式匹配。")
    elif isinstance(exc, (socket.timeout, TimeoutError)):
        result = (phase + "_TIMEOUT", "连接邮件服务器超时。", "请检查网络、防火墙和服务器地址，稍后重试。")
    elif isinstance(exc, socket.gaierror):
        result = ("DNS_LOOKUP_FAILED", "找不到邮件服务器地址。", "请检查 SMTP／IMAP 域名是否拼写正确，并确认网络可用。")
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        result = ("SMTP_RECIPIENT_REJECTED", "发件服务器拒绝了目标邮箱地址。", "请检查目标邮箱是否存在，或是否被发件服务的收件人策略限制。")
    elif isinstance(exc, smtplib.SMTPSenderRefused):
        result = ("SMTP_SENDER_REJECTED", "发件服务器拒绝了发件人地址。", "发件邮箱应与登录账号或该账号已验证的别名一致。")
    elif isinstance(exc, smtplib.SMTPNotSupportedError):
        result = ("SMTP_FEATURE_UNAVAILABLE", "服务器不支持所需的加密或登录方式。", "请检查 SMTP 端口和加密方式，并确认已开启客户端访问。")
    elif isinstance(exc, smtplib.SMTPDataError):
        if exc.smtp_code in {450, 451, 452, 421}:
            result = ("SMTP_TEMPORARILY_REJECTED", "邮件暂时未被发件服务器接收。", "服务可能限流或临时繁忙，请稍后重试。")
        else:
            result = ("SMTP_MESSAGE_REJECTED", "发件服务器拒绝了这封邮件。", "请检查邮箱配额、发件限制及反垃圾策略，修改邮件内容后重试。")
    elif isinstance(exc, smtplib.SMTPServerDisconnected):
        result = ("SMTP_DISCONNECTED", "发件服务器中断了连接，投递结果可能不确定。", "请先检查已发送邮件与目标收件箱，确认未投递后再创建新请求，避免重复发送。")
    elif isinstance(exc, imaplib.IMAP4.abort):
        result = ("IMAP_DISCONNECTED", "收件服务器中断了连接。", "系统会在下次轮询时重新连接；请检查网络与 IMAP 服务状态。")
    elif isinstance(exc, imaplib.IMAP4.error):
        # Detect only a bounded classification; never expose the source text.
        text = str(exc).lower()
        if any(word in text for word in ("auth", "login", "password", "credential")):
            result = ("IMAP_AUTH_FAILED", "收件服务器拒绝了登录。", "请检查账号及应用专用密码／客户端授权码，并在邮箱设置中开启 IMAP。")
        elif "unsafe" in text or "id command" in text:
            result = ("IMAP_CLIENT_REJECTED", "收件服务器拒绝了客户端连接。", "请在邮箱设置中开启 IMAP，并允许第三方邮件客户端访问。")
        else:
            result = ("IMAP_PROTOCOL_ERROR", "收件服务器无法完成请求。", "请检查 IMAP 服务是否开启、文件夹名称是否正确，并稍后重试。")
    elif isinstance(exc, (ConnectionError, OSError)):
        result = (phase + "_CONNECTION_FAILED", "无法连接邮件服务器。", "请检查网络、服务器地址及端口；确认邮件客户端服务已开启。")
    elif isinstance(exc, (ValueError, KeyError, TypeError)):
        result = ("MAIL_CONFIG_INVALID", "邮件配置或请求格式不正确。", "请重新检查发件账号、目标邮箱、端口及邮件内容。")
    else:
        result = (phase + "_ERROR", "邮件服务发生错误。", "请检查服务配置并重试；原始服务响应已隐藏以保护账号信息。")
    return dict(zip(("code", "message", "hint"), result))


def _raise_safe(exc: Exception, phase: str):
    error = explain_error(exc, phase)
    raise MailError(**error) from None


def _close(client, method: str):
    """A logout failure must not turn an accepted SMTP send into a retry."""
    if client is None:
        return
    try:
        getattr(client, method)()
    except Exception:
        try:
            # SMTP.close and IMAP.shutdown close the socket without protocol IO.
            getattr(client, "close" if method == "quit" else "shutdown")()
        except Exception:
            pass


@contextmanager
def _smtp(config: dict):
    client = None
    try:
        mode = config["smtp_security"]
        context = ssl.create_default_context()
        if mode == "ssl":
            client = smtplib.SMTP_SSL(config["smtp_host"], int(config["smtp_port"]), timeout=SOCKET_TIMEOUT, context=context)
        elif mode == "starttls":
            client = smtplib.SMTP(config["smtp_host"], int(config["smtp_port"]), timeout=SOCKET_TIMEOUT)
            client.ehlo()
            client.starttls(context=context)
        else:
            raise MailError("TLS_REQUIRED", "邮件连接必须使用加密传输。", "请选择 SSL/TLS 或 STARTTLS。")
        client.ehlo()
        client.login(config.get("username") or config["email"], config["password"])
        yield client
    finally:
        _close(client, "quit")


def _imap_id(client):
    """163 requires IMAP ID; RFC 2971 is absent from imaplib's public API."""
    if not any(cap.upper() == b"ID" for cap in client.capabilities):
        return
    # imaplib's command table is module-global; this is a stable RFC extension.
    imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
    result, _ = client._simple_command("ID", '("name" "EmailCall" "version" "1.0.0" "vendor" "EmailCall")')
    if result != "OK":
        raise MailError("IMAP_CLIENT_REJECTED", "收件服务器拒绝了客户端标识。", "请在邮箱设置中允许第三方客户端使用 IMAP。")


@contextmanager
def _imap(config: dict):
    client = None
    try:
        mode = config["imap_security"]
        context = ssl.create_default_context()
        if mode == "ssl":
            client = imaplib.IMAP4_SSL(config["imap_host"], int(config["imap_port"]), ssl_context=context, timeout=SOCKET_TIMEOUT)
        elif mode == "starttls":
            client = imaplib.IMAP4(config["imap_host"], int(config["imap_port"]), timeout=SOCKET_TIMEOUT)
            client.starttls(ssl_context=context)
        else:
            raise MailError("TLS_REQUIRED", "邮件连接必须使用加密传输。", "请选择 SSL/TLS 或 STARTTLS。")
        client.login(config.get("username") or config["email"], config["password"])
        _imap_id(client)
        status, _ = client.select(_imap_folder(config.get("imap_folder") or "INBOX"), readonly=True)
        if status != "OK":
            raise MailError("IMAP_FOLDER_UNAVAILABLE", "无法打开指定的收件文件夹。", "请确认文件夹存在；通常应使用 INBOX。")
        yield client
    finally:
        _close(client, "logout")


def _imap_folder(name: str) -> str:
    # RFC 3501 modified UTF-7 supports user-selected Chinese mailbox names.
    result, run = [], []
    import base64
    def flush():
        if run:
            encoded = base64.b64encode("".join(run).encode("utf-16-be")).decode("ascii")
            result.append("&" + encoded.rstrip("=").replace("/", ",") + "-")
            run.clear()
    for char in name:
        if " " <= char <= "~":
            flush()
            result.append("&-" if char == "&" else char)
        else:
            run.append(char)
    flush()
    return _quote("".join(result))


def _quote(text: str) -> str:
    if any(char in text for char in "\r\n\x00"):
        raise ValueError("invalid IMAP search value")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def send_message(config: dict, record: dict) -> None:
    """Send one persisted request; do not retry an ambiguous SMTP delivery."""
    try:
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = formataddr(("EmailCall · " + (record.get("agent_name") or "Agent"), config["email"]))
        message["To"] = record["target_email"]
        message["Reply-To"] = config["email"]
        message["Message-ID"] = record["message_id"]
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        message["Subject"] = record["subject"] + " [EC:" + record["id"] + "]"
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"
        body = record["body"].rstrip()
        if record["kind"] == "ask":
            deadline = _parse_time(record["deadline_at"]) if record.get("deadline_at") else datetime.now(timezone.utc) + timedelta(seconds=record.get("timeout_seconds", 300))
            body += ("\n\n—— EmailCall · 等待您的回复 ——\n"
                     "请直接回复此邮件，保留邮件主题，以告知 Agent 您的选择。\n"
                     "回复截止：" + deadline.strftime("%Y-%m-%d %H:%M:%S UTC") + "。\n"
                     "截止后本次等待会自动结束，Agent 将依据任务要求自行判断下一步。"
                     "逾期回复仍会被记录，但不会作为本次等待的及时决策。")
        else:
            body += "\n\n—— EmailCall · 来自 Agent 的提醒 ——"
        message.set_content(body)
        with _smtp(config) as client:
            rejected = client.send_message(message, from_addr=config["email"], to_addrs=[record["target_email"]])
            if rejected:
                raise smtplib.SMTPRecipientsRefused(rejected)
    except Exception as exc:
        _raise_safe(exc, "smtp")


def test_connection(config: dict) -> list[dict]:
    """Authenticate both services independently without sending any email."""
    results = []
    for name, connect in (("SMTP", _smtp), ("IMAP", _imap)):
        try:
            with connect(config):
                pass
            results.append({"name": name, "ok": True})
        except Exception as exc:
            results.append({"name": name, "ok": False, "error": explain_error(exc, name)})
    return results


class _HTMLText(HTMLParser):
    """Extract readable text without executing HTML or retaining quoted history."""
    _blocks = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "table", "section"}
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        quote_class = (attributes.get("class", "") + " " + attributes.get("id", "")).lower()
        ignore = tag in {"script", "style", "head", "blockquote"} or any(x in quote_class for x in ("gmail_quote", "yahoo_quoted", "moz-cite", "protonmail_quote", "divrplyfwdmsg"))
        if self.ignored:
            if tag not in self._void:
                self.ignored.append(tag)
            return
        if ignore:
            if tag not in self._void:
                self.ignored.append(tag)
            return
        if tag in self._blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in self.ignored:
                index = len(self.ignored) - 1 - self.ignored[::-1].index(tag)
                del self.ignored[index:]
            return
        if tag in self._blocks:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def _decoded_part(part: Message) -> str:
    raw = part.get_payload(decode=True)
    if raw is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""
    for charset in (part.get_content_charset(), "utf-8", "gb18030"):
        if charset:
            try:
                return raw.decode(charset)
            except (UnicodeError, LookupError):
                pass
    return raw.decode("utf-8", errors="replace")


def _trim_quotes(text: str) -> str:
    lines, output = text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), []
    for index, line in enumerate(lines):
        stripped = line.strip()
        wrapped = " ".join(part.strip() for part in lines[index:index + 3])
        if (re.match(r"^On\s.+wrote\s*:", wrapped, re.I)
                or re.match(r"^在\s?.+(?:写道|寫道)[：:]\s*$", stripped)
                or re.match(r"^-{2,}\s*(?:Original Message|原始邮件|原始郵件|转发邮件|Forwarded message)\s*-*", stripped, re.I)
                or stripped == "—— EmailCall · 等待您的回复 ——"):
            break
        if re.match(r"^(?:From|发件人|寄件者)\s*[：:]", stripped, re.I):
            following = "\n".join(lines[index + 1:index + 5])
            if re.search(r"^(?:Sent|Date|发送时间|日期|To|收件人|Subject|主题)\s*[：:]", following, re.I | re.M):
                break
        if stripped.startswith(">"):
            continue
        output.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


def _text_parts(message: Message):
    if message.get_content_disposition() == "attachment" or message.get_filename():
        return
    if message.get_content_maintype() == "message":
        return  # A forwarded message is its own conversation, even without a filename.
    if message.is_multipart():
        for child in message.get_payload():
            yield from _text_parts(child)
    else:
        yield message


def _body(message: Message) -> str:
    plain, html = [], []
    for part in _text_parts(message):
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(_decoded_part(part))
        elif content_type == "text/html":
            parser = _HTMLText()
            parser.feed(_decoded_part(part))
            html.append("".join(parser.parts))
    text = _trim_quotes("\n".join(plain))
    if not text:
        text = _trim_quotes("\n".join(html))
    # Strip control characters while preserving readable line breaks and tabs.
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    if len(text) > MAX_REPLY_CHARS:
        text = text[:MAX_REPLY_CHARS] + "\n[回复内容过长，后续内容已截断。]"
    return text


def _automated(message: Message) -> bool:
    auto = str(message.get("Auto-Submitted", "no")).strip().lower()
    if auto not in {"", "no"}:
        return True
    if any(message.get(key) for key in ("X-Autoreply", "X-Autorespond", "X-Auto-Reply")):
        return True
    if str(message.get("Precedence", "")).strip().lower() in {"bulk", "junk", "list"}:
        return True
    return message.get_content_type() in {"multipart/report", "message/delivery-status"}


def _match_record(message: Message, records: list[dict]) -> tuple[dict | None, str]:
    if _automated(message):
        return None, ""
    addresses = getaddresses(message.get_all("From", []))
    if len(addresses) != 1 or not addresses[0][1]:
        return None, ""
    sender = addresses[0][1].strip().casefold()
    references = set(_ID.findall(" ".join(str(message.get(key, "")) for key in ("In-Reply-To", "References"))))
    has_thread_header = bool(message.get("In-Reply-To") or message.get("References"))
    tokens = set(_SUBJECT_TOKEN.findall(str(message.get("Subject", ""))))
    own_id = str(message.get("Message-ID", "")).strip()
    matches = []
    for record in records:
        if sender != record["target_email"].casefold() or own_id == record["message_id"]:
            continue
        if record["message_id"] in references or (not has_thread_header and record["id"] in tokens):
            matches.append(record)
    if len(matches) == 1:
        return matches[0], sender
    # Ambiguous reference chains must never decide two waiting requests.
    if len(matches) > 1:
        direct = set(_ID.findall(str(message.get("In-Reply-To", ""))))
        direct_matches = [record for record in matches if record["message_id"] in direct]
        if len(direct_matches) == 1:
            return direct_matches[0], sender
    return None, ""


def _internal_date(metadata: bytes) -> str | None:
    match = re.search(rb'INTERNALDATE "([0-9 ]\d?)-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})"', metadata)
    if not match:
        return None
    day, month, year, hour, minute, second, sign, offset_h, offset_m = [part.decode("ascii") for part in match.groups()]
    months = {name: index + 1 for index, name in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}
    try:
        offset = timedelta(hours=int(offset_h), minutes=int(offset_m)) * (-1 if sign == "-" else 1)
        parsed = datetime(int(year), months[month.title()], int(day), int(hour), int(minute), int(second), tzinfo=timezone(offset))
        return parsed.astimezone(timezone.utc).isoformat()
    except (KeyError, ValueError):
        return None


def _fetch(client, uid: bytes, query: str) -> tuple[bytes, bytes] | None:
    status, data = client.uid("fetch", uid, query)
    if status != "OK":
        raise MailError("IMAP_FETCH_FAILED", "无法读取邮件内容。", "收件服务器可能临时繁忙，系统将在下次轮询时重试。")
    for item in data or []:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
            return item[0], item[1]
    return None  # Message may have been removed between SEARCH and FETCH.


def _or_terms(terms: list[str]) -> str:
    if len(terms) == 1:
        return terms[0]
    return "OR " + terms[0] + " (" + _or_terms(terms[1:]) + ")"


def poll_replies(config: dict, records: list[dict]) -> list[dict]:
    """Read matching replies without marking mail read, moving, or deleting it.

    Search is constrained by thread headers/subject tokens and oldest creation
    date. Fetch at most the latest 100 matching candidates per cycle, each with
    a 32 KiB header and a 1 MiB body cap. No attachment is opened or executed.
    """
    records = [record for record in records if record.get("message_id") and record.get("target_email")]
    if not records:
        return []
    try:
        oldest = min(_parse_time(record["created_at"]) for record in records) - timedelta(days=1)
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        since = f"{oldest.day:02d}-{months[oldest.month - 1]}-{oldest.year}"
        results = []
        with _imap(config) as client:
            candidates = set()
            predicates = []
            for record in records:
                terms = [
                    "HEADER In-Reply-To " + _quote(record["message_id"]),
                    "HEADER References " + _quote(record["message_id"]),
                    "SUBJECT " + _quote("[EC:" + record["id"] + "]"),
                ]
                predicate = _or_terms(terms)
                # Excluding persisted reply IDs prevents old conversations from
                # consuming the fetch budget on every poll. Bound command size.
                known = [record["message_id"]] + [reply.get("message_id", "") for reply in reversed(record.get("replies", []))]
                for identifier in known:
                    if not _ID.fullmatch(identifier) or len(identifier) > 256 or "@emailcall.local>" in identifier and identifier.startswith("<sha256-"):
                        continue
                    exclusion = " NOT HEADER Message-ID " + _quote(identifier)
                    if len(predicate) + len(exclusion) > 4000:
                        break
                    predicate += exclusion
                predicates.append("(" + predicate + ")")
            batches, batch = [], []
            for predicate in predicates:
                if batch and sum(map(len, batch)) + len(predicate) > MAX_SEARCH_BYTES:
                    batches.append(batch)
                    batch = []
                batch.append(predicate)
            if batch:
                batches.append(batch)
            for batch in batches:
                criteria = "(SINCE " + since + " " + _or_terms(batch) + ")"
                status, data = client.uid("search", None, criteria)
                if status != "OK":
                    raise MailError("IMAP_SEARCH_FAILED", "无法搜索回复邮件。", "请检查 IMAP 服务与文件夹设置，系统将在下次轮询时重试。")
                for item in data or []:
                    if isinstance(item, bytes):
                        candidates.update(uid for uid in item.split() if uid.isdigit())
            for uid in sorted(candidates, key=int)[-MAX_CANDIDATES:]:
                fetched = _fetch(client, uid, f"(INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER]<0.{MAX_HEADER_BYTES}>)")
                if not fetched:
                    continue
                metadata, raw_header = fetched
                message = BytesParser(policy=policy.default).parsebytes(raw_header[:MAX_HEADER_BYTES], headersonly=True)
                record, sender = _match_record(message, records)
                if record is None:
                    continue
                existing_ids = {reply.get("message_id") for reply in record.get("replies", [])}
                if str(message.get("Message-ID", "")).strip() in existing_ids:
                    continue
                received_at = _internal_date(metadata)
                if received_at is None:
                    continue  # A sender-controlled Date header cannot stand in for INTERNALDATE.
                # IMAP INTERNALDATE has seconds precision; request timestamps
                # may include milliseconds within the same arrival second.
                if _parse_time(received_at) < _parse_time(record["created_at"]).replace(microsecond=0):
                    continue
                fetched = _fetch(client, uid, f"(INTERNALDATE BODY.PEEK[]<0.{MAX_MESSAGE_BYTES}>)")
                if not fetched:
                    continue
                _, raw = fetched
                # A server must honor partial FETCH; still enforce local limits.
                raw = raw[:MAX_MESSAGE_BYTES]
                message = BytesParser(policy=policy.default).parsebytes(raw)
                verified_record, verified_sender = _match_record(message, records)
                if verified_record is None or verified_record["id"] != record["id"] or verified_sender != sender:
                    continue
                body = _body(message)
                if not body:
                    continue
                if len(raw) >= MAX_MESSAGE_BYTES:
                    body += "\n[邮件超过读取上限，内容可能不完整；请用简短文字重新回复。]"
                message_id = str(message.get("Message-ID", "")).strip()
                if not _ID.fullmatch(message_id):
                    message_id = "<sha256-" + hashlib.sha256(raw + received_at.encode()).hexdigest() + "@emailcall.local>"
                results.append({"request_id": record["id"], "message_id": message_id, "from_email": sender, "body": body, "received_at": received_at})
        return results
    except Exception as exc:
        _raise_safe(exc, "imap")

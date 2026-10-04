"""Full local TLS SMTP/IMAP + HTTP/skill roundtrip; no external mail service.

OpenSSL creates an ephemeral test certificate. The production client retains
certificate verification and trusts only this test CA within the patch scope.
"""
import http.client
import json
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from gateway import mail
from gateway.server import make_server
from gateway.service import Gateway
from gateway.store import Store


class Mailbox:
    def __init__(self):
        self.sent = []
        self.inbox = []
        self.imap_commands = []
        self.smtp_envelopes = []
        self.outage = False

    def inject_reply(self, original, body, sender="user@example.com"):
        reply = EmailMessage(policy=policy.SMTP)
        reply["From"] = sender
        reply["To"] = "agent@example.com"
        reply["Subject"] = "Re: " + str(original["Subject"])
        reply["In-Reply-To"] = original["Message-ID"]
        reply["Message-ID"] = f"<local-reply-{len(self.inbox)}@example.com>"
        reply.set_content(body)
        # IMAP timestamps have one-second resolution; select a receipt safely
        # after request creation while still within the 30-second deadline.
        received = datetime.now(timezone.utc) + timedelta(seconds=1)
        self.inbox.append((reply.as_bytes(), received))


class TLSMailServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, handler, context, mailbox):
        self.context, self.mailbox = context, mailbox
        super().__init__(("127.0.0.1", 0), handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        try:
            return self.context.wrap_socket(connection, server_side=True), address
        except Exception:
            connection.close()
            raise

    def handle_error(self, request, client_address):
        pass  # A deliberately disconnected outage client is expected.


class SMTPHandler(socketserver.StreamRequestHandler):
    def send(self, line):
        self.wfile.write(line.encode("ascii") + b"\r\n")

    def handle(self):
        self.send("220 localhost EmailCall test SMTP")
        while raw := self.rfile.readline(65536):
            command, _, argument = raw.decode("ascii", errors="replace").strip().partition(" ")
            command = command.upper()
            if command in ("EHLO", "HELO"):
                self.send("250-localhost")
                self.send("250-AUTH PLAIN")
                self.send("250 SIZE 2097152")
            elif command == "AUTH":
                self.send("235 2.7.0 authenticated")
            elif command in ("MAIL", "RCPT"):
                self.server.mailbox.smtp_envelopes.append(command + " " + argument)
                self.send("250 OK")
            elif command == "DATA":
                self.send("354 End with dot")
                message = bytearray()
                while (line := self.rfile.readline(65536)) != b".\r\n":
                    if not line:
                        return
                    message.extend(line[1:] if line.startswith(b"..") else line)
                self.server.mailbox.sent.append(bytes(message))
                self.send("250 2.0.0 accepted")
            elif command == "QUIT":
                self.send("221 Bye")
                return
            else:
                self.send("250 OK")


class IMAPHandler(socketserver.StreamRequestHandler):
    def send(self, line):
        self.wfile.write(line.encode("ascii") + b"\r\n")

    def handle(self):
        box = self.server.mailbox
        if box.outage:
            # Hold the connection until the bounded client timeout closes it.
            try:
                while self.rfile.read(1):
                    pass
            except (TimeoutError, OSError):
                pass
            return
        self.send("* OK EmailCall local IMAP test")
        while raw := self.rfile.readline(32768):
            line = raw.decode("ascii").strip()
            box.imap_commands.append(line)
            tag, command, *remainder = line.split(" ", 2)
            argument = remainder[0] if remainder else ""
            if command == "CAPABILITY":
                self.send("* CAPABILITY IMAP4rev1 ID")
            elif command == "ID":
                self.send('* ID ("name" "local-test-server")')
            elif command == "EXAMINE":
                self.send("* FLAGS (\\Seen)")
                self.send(f"* {len(box.inbox)} EXISTS")
                self.send("* OK [UIDVALIDITY 1] UIDs valid")
            elif command == "UID":
                action, _, args = argument.partition(" ")
                if action.upper() == "SEARCH":
                    # Return all mail deliberately: the gateway must revalidate
                    # sender + correlation locally instead of trusting SEARCH.
                    self.send("* SEARCH " + " ".join(str(i + 1) for i in range(len(box.inbox))))
                elif action.upper() == "FETCH":
                    uid, query = args.split(" ", 1)
                    body, received = box.inbox[int(uid) - 1]
                    if "HEADER" in query:
                        body = body.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
                    cap = re.search(r"<0\.(\d+)>", query)
                    if cap:
                        body = body[:int(cap[1])]
                    label = "BODY[HEADER]<0>" if "HEADER" in query else "BODY[]<0>"
                    date = received.strftime("%d-%b-%Y %H:%M:%S %z")
                    self.send(f'* {uid} FETCH (UID {uid} INTERNALDATE "{date}" RFC822.SIZE {len(body)} {label} {{{len(body)}}}')
                    self.wfile.write(body + b")\r\n")
            elif command == "LOGOUT":
                self.send("* BYE logout")
                self.send(tag + " OK LOGOUT completed")
                return
            self.send(tag + " OK completed")


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL executable is required for local TLS integration tests")
class RoundtripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cert_tmp = tempfile.TemporaryDirectory()
        directory = Path(cls.cert_tmp.name)
        cls.cert, cls.key = directory / "cert.pem", directory / "key.pem"
        config = directory / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\n")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(cls.key), "-out", str(cls.cert), "-config", str(config)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_context.load_cert_chain(cls.cert, cls.key)
        cls.original_context = staticmethod(ssl.create_default_context)

    @classmethod
    def tearDownClass(cls):
        cls.cert_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = Mailbox()
        self.smtp = TLSMailServer(SMTPHandler, self.server_context, self.box)
        self.imap = TLSMailServer(IMAPHandler, self.server_context, self.box)
        self.store = Store(self.tmp.name)
        self.app = Gateway(self.store)
        self.http = make_server(self.app, "127.0.0.1", 0)
        self.servers = [self.smtp, self.imap, self.http]
        self.threads = []
        for server in self.servers:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.trust_patch = patch.object(mail.ssl, "create_default_context", side_effect=lambda: self.original_context(cafile=str(self.cert)))
        self.trust_patch.start()
        self.headers = {"Authorization": "Bearer " + self.store.token()}
        self.app.save_config({
            "provider": "custom", "email": "agent@example.com", "username": "agent@example.com",
            "password": "local-test-password", "target_email": "user@example.com",
            "smtp_host": "127.0.0.1", "smtp_port": self.smtp.server_address[1], "smtp_security": "ssl",
            "imap_host": "127.0.0.1", "imap_port": self.imap.server_address[1], "imap_security": "ssl",
            "imap_folder": "INBOX", "poll_interval": 10,
        })
        self.cli_config = Path(self.tmp.name) / "skill-config.json"
        self.cli_config.write_text(json.dumps({"base_url": f"http://127.0.0.1:{self.http.server_address[1]}", "token": self.store.token()}))

    def tearDown(self):
        self.trust_patch.stop()
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.store.close()
        self.tmp.cleanup()

    def request(self, path, method="GET", body=None):
        client = http.client.HTTPConnection("127.0.0.1", self.http.server_address[1], timeout=5)
        headers = dict(self.headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        client.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = client.getresponse()
        value = json.loads(response.read())
        status = response.status
        client.close()
        return status, value

    def cli(self, *args):
        script = Path(__file__).resolve().parents[1] / "skills/emailcall/scripts/emailcall.py"
        process = subprocess.run([sys.executable, str(script), "--config", str(self.cli_config), *args], capture_output=True, text=True, timeout=8)
        events = [json.loads(line) for line in process.stdout.splitlines()]
        return process, events

    def create_ask(self):
        status, record = self.request("/api/ask", "POST", {"subject": "本机端到端测试", "body": "请选择下一步", "timeout_seconds": 30})
        self.assertEqual(status, 202)
        self.assertTrue(self.app.send_once())
        self.assertEqual(self.store.get(record["id"])["status"], "waiting")
        return record, BytesParser(policy=policy.default).parsebytes(self.box.sent[-1])

    def test_skill_cli_to_real_tls_mail_and_back_to_api(self):
        process, events = self.cli("test", "--timeout", "30", "--no-wait")
        self.assertEqual(process.returncode, 4, process.stdout + process.stderr)  # Pending is resumable, not success.
        created = next(event for event in events if event.get("event") == "request_created")
        self.assertTrue(self.app.send_once())
        original = BytesParser(policy=policy.default).parsebytes(self.box.sent[-1])
        self.assertIn("rcpt to:<user@example.com>", [line.lower() for line in self.box.smtp_envelopes])
        self.box.inject_reply(original, "选择 B，明天再继续。\n\n> 旧的邮件内容")
        self.app.poll_once()
        self.assertIsNone(self.app.poll_error)
        status, record = self.request("/api/requests/" + created["id"])
        self.assertEqual(status, 200)
        self.assertEqual(record["status"], "replied")
        self.assertEqual(record["reply"]["body"], "选择 B，明天再继续。")
        self.assertFalse(record["reply"]["late"])
        status_process, status_events = self.cli("status", created["id"])
        self.assertEqual(status_process.returncode, 0, status_process.stdout)
        self.assertIn("选择 B，明天再继续。", status_process.stdout)
        self.assertTrue(any(" EXAMINE " in command for command in self.box.imap_commands))
        self.assertFalse(any(" STORE " in command for command in self.box.imap_commands))
        # Repeated polling must not duplicate the persisted reply.
        self.app.poll_once()
        self.assertEqual(len(self.store.get(created["id"])["replies"]), 1)

    def test_http_notify_uses_real_smtp_and_reaches_sent(self):
        status, record = self.request("/api/notify", "POST", {"subject": "任务完成", "body": "本机测试已通过"})
        self.assertEqual(status, 202)
        self.app.send_once()
        status, stored = self.request("/api/requests/" + record["id"])
        self.assertEqual(stored["status"], "sent")
        self.assertEqual(len(self.box.sent), 1)

    def test_wrong_sender_cannot_finish_request_then_real_sender_can(self):
        record, original = self.create_ask()
        self.box.inject_reply(original, "不要执行", sender="intruder@example.com")
        self.app.poll_once()
        self.assertEqual(self.store.get(record["id"])["status"], "waiting")
        self.box.inject_reply(original, "确认执行")
        self.app.poll_once()
        stored = self.store.get(record["id"])
        self.assertEqual(stored["status"], "replied")
        self.assertEqual(len(stored["replies"]), 1)
        self.assertEqual(stored["reply"]["body"], "确认执行")

    def test_imap_outage_is_recorded_and_wait_expires(self):
        record, _ = self.create_ask()
        self.box.outage = True
        future = datetime.now(timezone.utc) + timedelta(seconds=70)
        with patch.object(mail, "SOCKET_TIMEOUT", .08), patch("gateway.store.utcnow", return_value=future):
            self.app.poll_once()
        self.assertEqual(self.app.poll_error["code"], "IMAP_TIMEOUT")
        status, stored = self.request("/api/requests/" + record["id"])
        self.assertEqual(stored["status"], "timed_out")
        diagnostics = self.store.list_records(kind="diagnostic")["items"]
        self.assertTrue(any(item["error"]["code"] == "IMAP_TIMEOUT" for item in diagnostics))


if __name__ == "__main__":
    unittest.main()

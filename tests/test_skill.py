"""Behavior checks for the exported agent helper and its opt-in installer."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = load_module("emailcall_cli", ROOT / "skills/agentcall/scripts/agentcall.py")

    def test_new_commands_check_channels_before_creating_request(self):
        client = mock.Mock()
        client.request.side_effect = [{"selected_channel": "wechat", "available": True},
                                      {"id": "request-1", "status": "sent"}]
        with mock.patch.object(self.cli, "load_config", return_value={}), \
                mock.patch.object(self.cli, "Client", return_value=client), \
                contextlib.redirect_stdout(io.StringIO()):
            code = self.cli.main(["notify", "--subject", "Done", "--body", "Finished"])
        self.assertEqual(code, 0)
        self.assertEqual(client.request.call_args_list[0].args, ("GET", "/api/channels"))
        self.assertEqual(client.request.call_args_list[1].args[:2], ("POST", "/api/notify"))

    def test_inbox_stops_on_message_without_acknowledging_automatically(self):
        client = mock.Mock()
        client.request.side_effect = [{'selected_channel': 'wechat'},
            {'items': [], 'mode': {'enabled': True}},
            {'items': [{'id': 3, 'kind': 'message', 'body': '新要求'}], 'mode': {'enabled': True}}]
        with mock.patch.object(self.cli, 'load_config', return_value={}), \
                mock.patch.object(self.cli, 'Client', return_value=client), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.cli.main(['inbox', '--consumer-id', 'session-123', '--wait-seconds', '30']), 0)
        calls = client.request.call_args_list
        self.assertEqual(calls[0].args, ('GET', '/api/channels'))
        self.assertEqual(calls[1].args[2]['consumer_id'], 'session-123')
        self.assertFalse(any('/ack' in call.args[1] for call in calls))
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result['items'][0]['body'], '新要求')
        self.assertTrue(result['ack_required'])

    def test_inbox_zero_wait_and_explicit_ack(self):
        for command, replies, expected in [
            (['inbox', '--consumer-id', 'session-123'], [{'selected_channel': 'wechat'}, {'items': []}], '/api/inbox/claim'),
            (['ack', '--consumer-id', 'session-123', '3'], [{'selected_channel': 'wechat'}, {'acked': [3]}], '/api/inbox/ack'),
            (['mode'], [{'selected_channel': 'wechat'}, {'enabled': True}], '/api/efficient-mode')]:
            client = mock.Mock()
            client.request.side_effect = replies
            with mock.patch.object(self.cli, 'load_config', return_value={}), \
                    mock.patch.object(self.cli, 'Client', return_value=client), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.cli.main(command), 0)
            self.assertEqual(client.request.call_args_list[-1].args[1], expected)

    def test_explicit_wechat_test_keeps_channel_choice_in_request(self):
        client = mock.Mock()
        client.request.side_effect = [{"selected_channel": "email", "available": True},
                                      {"id": "request-1", "status": "queued"}]
        with mock.patch.object(self.cli, "load_config", return_value={}), \
                mock.patch.object(self.cli, "Client", return_value=client), \
                contextlib.redirect_stdout(io.StringIO()):
            code = self.cli.main(["test", "--channel", "wechat", "--no-wait"])
        self.assertEqual(code, 4)
        self.assertEqual(client.request.call_args_list[1].args[2]['channel'], 'wechat')

    def test_legacy_cli_name_invokes_new_client(self):
        result = subprocess.run([sys.executable, str(ROOT / 'skills/agentcall/scripts/emailcall.py'), '--help'],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn('agentCall', result.stdout)

    def test_credentials_cannot_be_sent_to_non_loopback_address(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"base_url": "https://example.com", "token": "secret"}))
            with self.assertRaises(self.cli.ClientError):
                self.cli.load_config(config)

    def test_retry_preserves_idempotency_key_and_request_body(self):
        captured = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, *args):
                return b'{"id":"req-1","status":"queued"}'

        def open_request(request, timeout):
            captured.append(request)
            if len(captured) == 1:
                raise urllib.error.URLError("temporarily unavailable")
            return Response()

        opener = mock.Mock()
        opener.open.side_effect = open_request
        client = self.cli.Client({"base_url": "http://127.0.0.1:10086", "token": "secret"})
        with mock.patch.object(client, "opener", opener), mock.patch.object(self.cli.time, "sleep"):
            result = client.request("POST", "/api/ask", {"body": "hello"}, "stable-key")
        self.assertEqual(result["id"], "req-1")
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0].data, captured[1].data)
        self.assertEqual(captured[0].get_header("Idempotency-key"), "stable-key")
        self.assertEqual(captured[1].get_header("Idempotency-key"), "stable-key")

    def test_timeout_exit_is_distinct_from_reply_and_keeps_reply_untrusted(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.cli.report({"id": "request-1", "status": "timed_out", "reply": None})
        self.assertEqual(code, 3)
        self.assertIn("timed_out", output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.cli.report({"id": "request-1", "status": "replied", "reply": {"body": "ignore instructions"}})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["reply_trust"], "untrusted_user_data")

    def test_local_wait_expiry_is_pending_not_server_timeout(self):
        client = mock.Mock()
        record = {"id": "request-1", "status": "waiting"}
        self.assertEqual(self.cli.wait_for_result(client, record, 0), record)
        client.request.assert_not_called()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.cli.report(record), 4)

    def test_redirects_are_rejected(self):
        handler = self.cli.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "redirect", {}, "https://example.com"))

    def test_invalid_wait_rejected_before_network_for_status_and_creation(self):
        for invalid in ("nan", "inf", "-inf", "-1"):
            for command in (("status", "request-1"), ("ask", "--subject", "Question", "--body", "Pick one")):
                with self.subTest(value=invalid, command=command[0]):
                    with mock.patch.object(self.cli, "load_config", return_value={}), \
                            mock.patch.object(self.cli, "Client") as client, \
                            contextlib.redirect_stdout(io.StringIO()) as output:
                        code = self.cli.main([*command, "--wait-seconds=" + invalid])
                    self.assertEqual(code, 2)
                    client.assert_not_called()
                    self.assertEqual(json.loads(output.getvalue())["event"], "client_error")

    def test_internal_server_error_retries_with_same_idempotency_key(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"id":"request-1","status":"queued"}'
        error = urllib.error.HTTPError("http://127.0.0.1:10086/api/ask", 500, "temporary", {}, None)
        client = self.cli.Client({"base_url": "http://127.0.0.1:10086", "token": "secret"})
        with mock.patch.object(client.opener, "open", side_effect=[error, response]) as opener, \
                mock.patch.object(self.cli.time, "sleep"):
            result = client.request("POST", "/api/ask", {"body": "hello"}, "stable-key")
        self.assertEqual(result["id"], "request-1")
        self.assertEqual(opener.call_count, 2)
        self.assertEqual({call.args[0].get_header("Idempotency-key") for call in opener.call_args_list}, {"stable-key"})

    def _hook_items(self):
        return {'mode': {'enabled': True}, 'items': [
            {'id': 7, 'kind': 'message', 'body': '新要求'},
            {'id': 9, 'kind': 'reply', 'request_id': 'req-2', 'late': True, 'body': '晚到回复'}]}

    def test_inbox_hook_surfaces_new_messages_without_claiming_lease(self):
        client = mock.Mock()
        client.request.return_value = self._hook_items()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.cli, 'HOOK_STATE_DIR', Path(directory)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.cli.inbox_hook(client, json.dumps(
                {'session_id': 'sess-1', 'hook_event_name': 'PostToolUse'})), 0)
        client.request.assert_called_once_with('GET', '/api/inbox?limit=50', timeout=5, attempts=1)
        payload = json.loads(output.getvalue())
        context = payload['hookSpecificOutput']['additionalContext']
        self.assertEqual(payload['hookSpecificOutput']['hookEventName'], 'PostToolUse')
        self.assertIn('#7', context)
        self.assertIn('#9', context)
        self.assertIn('req-2', context)
        self.assertIn('untrusted_user_data', context)
        self.assertIn('ack', context)

    def test_inbox_hook_codex_payload_uses_top_level_context(self):
        client = mock.Mock()
        client.request.return_value = self._hook_items()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.cli, 'HOOK_STATE_DIR', Path(directory)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.cli.inbox_hook(client, json.dumps({'session_id': 'codex-1'})), 0)
        payload = json.loads(output.getvalue())
        self.assertIn('additionalContext', payload)
        self.assertNotIn('hookSpecificOutput', payload)

    def test_inbox_hook_debounces_and_never_repeats_a_seq(self):
        client = mock.Mock()
        client.request.return_value = self._hook_items()
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.cli, 'HOOK_STATE_DIR', Path(directory)):
            state = Path(directory) / 'sess-1.json'
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.cli.inbox_hook(client, json.dumps({'session_id': 'sess-1'}))
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            # 10 秒防抖：第二次调用不发起网络请求
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.cli.inbox_hook(client, json.dumps({'session_id': 'sess-1'}))
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(client.request.call_count, 1)
            # 越过防抖后，已浮出的编号不再重复注入，新编号会注入
            state.write_text(json.dumps({'checked_at': 0, 'max_seq': 9}), encoding='utf-8')
            client.request.return_value = {'mode': {'enabled': True}, 'items': self._hook_items()['items'] + [
                {'id': 11, 'kind': 'message', 'body': '补充'}]}
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.cli.inbox_hook(client, json.dumps({'session_id': 'sess-1'}))
            context = output.getvalue()
            self.assertNotIn('#7', context)
            self.assertIn('#11', context)

    def test_inbox_hook_silent_when_mode_disabled_without_losing_history(self):
        client = mock.Mock()
        client.request.return_value = {'mode': {'enabled': False}, 'items': self._hook_items()['items']}
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(self.cli, 'HOOK_STATE_DIR', Path(directory)), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.cli.inbox_hook(client, json.dumps({'session_id': 'sess-1'})), 0)
                self.assertEqual(output.getvalue(), '')
            state = json.loads((Path(directory) / 'sess-1.json').read_text(encoding='utf-8'))
            self.assertNotIn('max_seq', state)  # 重新开启高效模式后仍会浮出历史消息

    def test_inbox_hook_entry_never_fails_loudly(self):
        with mock.patch.object(self.cli, 'load_config', side_effect=self.cli.ClientError('坏配置')), \
                mock.patch('sys.stdin', io.StringIO('{}')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.cli.main(['inbox-hook']), 0)
        self.assertEqual(output.getvalue(), '')
        client = mock.Mock()
        client.request.side_effect = self.cli.ClientError('服务不可达')
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.cli, 'HOOK_STATE_DIR', Path(directory)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.cli.inbox_hook(client, 'not json at all'), 0)
        self.assertEqual(output.getvalue(), '')


class InstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer = load_module("emailcall_installer", ROOT / "skills/agentcall/install.py")

    def test_reinstall_manages_one_rule_and_preserves_existing_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory) / "home"
            source = Path(directory) / "export/agentcall"
            shutil.copytree(ROOT / "skills/agentcall", source)
            (source / "config.json").write_text(json.dumps({"base_url": "http://127.0.0.1:10086", "token": "secret"}))
            root = user_home / ".codex"
            root.mkdir(parents=True)
            rules = root / "AGENTS.md"
            rules.write_text("# Existing rules\nAlways preserve my work.\n")
            self.installer.install("codex", user_home=user_home, source=source)
            self.installer.install("codex", user_home=user_home, source=source)
            text = rules.read_text()
            self.assertEqual(text.count(self.installer.START), 1)
            self.assertIn("Always preserve my work.", text)
            self.assertTrue((root / "skills/agentcall/SKILL.md").exists())
            self.installer.uninstall("codex", user_home=user_home)
            self.assertEqual(rules.read_text(), "# Existing rules\nAlways preserve my work.\n")
            self.assertFalse((root / "skills/agentcall").exists())

    def test_upgrade_replaces_legacy_rule_without_changing_api_token(self):
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory) / "home"
            source = Path(directory) / "export/agentcall"
            shutil.copytree(ROOT / "skills/agentcall", source)
            (source / "config.json").write_text(json.dumps({"base_url": "http://127.0.0.1:10086", "token": "unchanged-token"}))
            rules = user_home / ".codex/AGENTS.md"
            rules.parent.mkdir(parents=True)
            rules.write_text("Preserve my work.\n<!-- EMAILCALL:START -->\nLegacy rule\n<!-- EMAILCALL:END -->\n")
            target, _ = self.installer.install("codex", user_home=user_home, source=source)
            text = rules.read_text()
            self.assertNotIn("EMAILCALL:START", text)
            self.assertEqual(text.count(self.installer.START), 1)
            self.assertIn("Preserve my work.", text)
            self.assertEqual(json.loads((target / "config.json").read_text())["token"], "unchanged-token")
            self.assertTrue((target / "scripts/emailcall.py").exists())

    def test_rule_migration_keeps_separate_user_rules_on_each_side(self):
        original = ('First user rule.\n<!-- EMAILCALL:START -->\nLegacy rule\n'
                    '<!-- EMAILCALL:END -->\nSecond user rule.\n')
        self.assertEqual(self.installer.remove_rule(original), 'First user rule.\nSecond user rule.\n')

    def test_claude_install_uses_claude_rules_and_private_config(self):
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory) / "home"
            source = Path(directory) / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("skill")
            (source / "config.json").write_text('{"base_url":"http://127.0.0.1:10086","token":"secret"}')
            self.installer.install("claude", user_home=user_home, source=source)
            config = user_home / ".claude/skills/agentcall/config.json"
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertIn(self.installer.START, (user_home / ".claude/CLAUDE.md").read_text())

    def test_missing_config_does_not_install_or_change_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory)
            with self.assertRaises(ValueError):
                self.installer.install("codex", user_home=user_home, source=ROOT / "skills/agentcall")
            self.assertFalse((user_home / ".codex").exists())

    def test_install_writes_managed_hook_and_preserves_user_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory) / "home"
            source = Path(directory) / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("skill")
            (source / "config.json").write_text('{"base_url":"http://127.0.0.1:10086","token":"secret"}')
            settings = user_home / ".claude/settings.json"
            settings.parent.mkdir(parents=True)
            user_hook = {"hooks": [{"type": "command", "command": "echo user-hook"}]}
            settings.write_text(json.dumps({"hooks": {"PostToolUse": [user_hook]}, "other": True}))
            self.installer.install("claude", user_home=user_home, source=source)
            self.installer.install("claude", user_home=user_home, source=source)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertTrue(data["other"])
            entries = data["hooks"]["PostToolUse"]
            managed = [e for e in entries if e is not user_hook
                       and "agentcall.py inbox-hook" in str(e["hooks"][0]["command"])]
            self.assertEqual(len(entries), 2)
            self.assertEqual(len(managed), 1)
            command = managed[0]["hooks"][0]["command"]
            self.assertIn(str(user_home / ".claude/skills/agentcall/scripts/agentcall.py"), command)
            self.assertEqual(managed[0]["hooks"][0]["timeout"], 20)
            self.installer.uninstall("claude", user_home=user_home)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["hooks"]["PostToolUse"], [user_hook])
            self.assertTrue(data["other"])

    def test_codex_hooks_json_created_and_cleaned_on_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory) / "home"
            source = Path(directory) / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("skill")
            (source / "config.json").write_text('{"base_url":"http://127.0.0.1:10086","token":"secret"}')
            self.installer.install("codex", user_home=user_home, source=source)
            hooks = user_home / ".codex/hooks.json"
            self.assertIn("agentcall.py inbox-hook", hooks.read_text(encoding="utf-8"))
            self.installer.uninstall("codex", user_home=user_home)
            data = json.loads(hooks.read_text(encoding="utf-8"))
            self.assertNotIn("PostToolUse", data.get("hooks", {}))

    def test_unparseable_hook_config_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory) / "home"
            source = Path(directory) / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("skill")
            (source / "config.json").write_text('{"base_url":"http://127.0.0.1:10086","token":"secret"}')
            settings = user_home / ".claude/settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text("not json {")
            self.installer.install("claude", user_home=user_home, source=source)
            self.assertEqual(settings.read_text(encoding="utf-8"), "not json {")

    def test_http_export_can_be_installed_into_isolated_home(self):
        from gateway.server import make_server
        from gateway.service import Gateway
        from gateway.store import Store
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            store = Store(temporary / "data")
            server = make_server(Gateway(store), "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = "http://127.0.0.1:" + str(server.server_address[1])
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(base + "/api/session") as response:
                    cookie = response.headers["Set-Cookie"].split(";")[0]
                    csrf = json.loads(response.read())["csrf_token"]
                request = urllib.request.Request(base + "/api/skill/export",
                            headers={"Cookie": cookie, "X-CSRF-Token": csrf})
                with opener.open(request) as response:
                    exported = response.read()
                with zipfile.ZipFile(io.BytesIO(exported)) as archive:
                    self.assertTrue(all(name.startswith("agentcall/") for name in archive.namelist()))
                    self.assertIn("agentcall/README.md", archive.namelist())
                    archive.extractall(temporary / "export")
                target, _ = self.installer.install("codex", user_home=temporary / "home",
                                                    source=temporary / "export/agentcall")
                config = json.loads((target / "config.json").read_text())
                self.assertEqual(config["base_url"], base)
                self.assertEqual(config["token"], store.token())
                self.assertTrue((target / "scripts/agentcall.py").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                store.close()


class BrowserWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.watcher = load_module("emailcall_watcher", ROOT / "scripts/watch.py")

    def test_browser_opens_once_per_instance_and_retries_failed_open(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = b'{"status":"ok","instance_id":"first"}'
            with mock.patch.object(self.watcher, "STATE_DIR", state), \
                    mock.patch.object(self.watcher.OPENER, "open", return_value=response), \
                    mock.patch.object(self.watcher.subprocess, "run") as browser:
                browser.return_value.returncode = 1
                self.assertFalse(self.watcher.open_if_new())
                self.assertFalse((state / "last-instance").exists())
                browser.return_value.returncode = 0
                self.assertTrue(self.watcher.open_if_new())
                self.assertTrue(self.watcher.open_if_new())
                self.assertEqual(browser.call_count, 2)
                response.read.return_value = b'{"status":"ok","instance_id":"second"}'
                self.assertTrue(self.watcher.open_if_new())
                self.assertEqual(browser.call_count, 3)

    def test_offline_service_does_not_open_browser(self):
        with mock.patch.object(self.watcher.OPENER, "open", side_effect=OSError("offline")), \
                mock.patch.object(self.watcher.subprocess, "run") as browser:
            self.assertFalse(self.watcher.open_if_new())
            browser.assert_not_called()

    def test_legacy_watcher_is_unloaded_and_instance_marker_preserved(self):
        installer = load_module('agentcall_watcher_install', ROOT / 'scripts/install-watcher.py')
        with tempfile.TemporaryDirectory() as directory:
            user_home = Path(directory)
            legacy_plist = user_home / 'Library/LaunchAgents/local.emailcall.browser.plist'
            legacy_plist.parent.mkdir(parents=True)
            legacy_plist.write_text('legacy')
            old_state = user_home / 'Library/Application Support/EmailCall/last-instance'
            old_state.parent.mkdir(parents=True)
            old_state.write_text('same-running-instance')
            with mock.patch.object(installer.subprocess, 'run') as run:
                installer.migrate_legacy(user_home, 'gui/501')
            self.assertEqual(run.call_args.args[0], ['launchctl', 'bootout', 'gui/501/local.emailcall.browser'])
            self.assertFalse(legacy_plist.exists())
            self.assertEqual((user_home / 'Library/Application Support/agentCall/last-instance').read_text(),
                             'same-running-instance')


class DeploymentMigrationTests(unittest.TestCase):
    def test_container_migration_stops_only_identified_legacy_service(self):
        migration = load_module('agentcall_container_migration', ROOT / 'scripts/migrate-container.py')
        legacy = {'Config': {'Labels': {'com.docker.compose.project': 'emailcall',
                                       'com.docker.compose.service': 'emailcall'}},
                  'Mounts': [{'Name': 'emailcall-data', 'Destination': '/data'}],
                  'State': {'Running': True}}
        with mock.patch.object(migration, 'docker', side_effect=[json.dumps([legacy]), '', '']) as docker:
            self.assertTrue(migration.migrate())
        self.assertEqual(docker.call_args_list[1].args, ('update', '--restart=no', 'emailcall'))
        self.assertEqual(docker.call_args_list[2].args, ('stop', '--time', '25', 'emailcall'))
        with mock.patch.object(migration, 'docker', return_value=json.dumps([{'Config': {}}])) as docker:
            with self.assertRaises(RuntimeError):
                migration.migrate()
            self.assertEqual(docker.call_count, 1)



if __name__ == "__main__":
    unittest.main()

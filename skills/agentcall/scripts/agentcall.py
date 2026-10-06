#!/usr/bin/env python3
"""agentCall agent client. Python standard library; stdout is JSON Lines."""
import argparse
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


TERMINAL = {"sent", "replied", "timed_out", "failed"}


class ClientError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def load_config(path=None):
    source = Path(path or Path(__file__).resolve().parents[1] / "config.json")
    try:
        config = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClientError("无法读取 skill/config.json，请从 agentCall 配置页重新导出并安装。") from exc
    if not isinstance(config, dict):
        raise ClientError("Skill 配置格式无效，请重新导出。")
    base_url = str(config.get("base_url", "http://127.0.0.1:10086")).rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(base_url)
        valid_port = parsed.port is not None and 1 <= parsed.port <= 65535
    except ValueError as exc:
        raise ClientError("Skill 服务地址无效。") from exc
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not valid_port or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment):
        raise ClientError("Skill 只允许向本机环回 HTTP 地址发送令牌。")
    token = config.get("token")
    if not isinstance(token, str) or not token.strip() or any(c in token for c in "\r\n"):
        raise ClientError("Skill 缺少 API 令牌，请从配置页重新导出。")
    return {"base_url": base_url, "token": token}


class Client:
    def __init__(self, config):
        self.config = config
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None, idempotency_key=None, timeout=35):
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.config["token"], "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        for attempt in range(3):
            request = urllib.request.Request(self.config["base_url"] + path, data=payload,
                                             headers=headers, method=method)
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    # A batch can contain 50 messages of 32,000 Unicode characters.
                    data = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ClientError("本机服务响应格式异常。")
                    return data
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                    exc.close()
                    time.sleep(2 ** attempt)
                    continue
                try:
                    problem = json.loads(exc.read(65536)).get("error", {})
                except (ValueError, AttributeError):
                    problem = {}
                code = problem.get("code", "HTTP_" + str(exc.code))
                message = problem.get("message", "服务拒绝请求。请检查配置页与记录。")
                raise ClientError(str(code) + ": " + str(message)) from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as exc:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ClientError("本机 agentCall 服务连续 3 次无法访问；请启动容器或检查端口。") from exc
            except (ValueError, UnicodeError) as exc:
                raise ClientError("本机服务返回了无法解析的响应。") from exc


def wait_for_result(client, record, wait_seconds):
    stop = time.monotonic() + max(0, wait_seconds)
    while record.get("status") not in TERMINAL:
        remaining = stop - time.monotonic()
        if remaining <= 0:
            break
        wait = min(25, max(0, int(remaining)))
        identifier = urllib.parse.quote(str(record["id"]), safe="")
        record = client.request("GET", "/api/requests/" + identifier + "?wait=" + str(wait),
                                timeout=min(35, max(2, remaining + 2)))
        if record.get("status") not in TERMINAL and wait == 0:
            time.sleep(min(0.5, max(0, stop - time.monotonic())))
    return record


def report(record):
    result = dict(record)
    result["event"] = "request_result"
    result["reply_trust"] = "untrusted_user_data"
    if record.get("status") not in TERMINAL:
        result["resume_hint"] = "调用 status <id> --wait-seconds 300 继续等待；本地等待结束不代表邮件超时。"
    emit(result)
    return {"sent": 0, "replied": 0, "failed": 2, "timed_out": 3}.get(record.get("status"), 4)


def parser():
    result = argparse.ArgumentParser(description="agentCall 本机微信／邮件通知与等待回复（JSON Lines 输出）")
    result.add_argument("--config", help="导出的 config.json 路径")
    actions = result.add_subparsers(dest="command", required=True)
    for name in ("notify", "ask", "test"):
        action = actions.add_parser(name)
        action.add_argument("--subject", default="agentCall 连通性测试" if name == "test" else None,
                            required=name != "test")
        body = action.add_mutually_exclusive_group(required=name != "test")
        body.add_argument("--body", help="邮件正文；作为纯文本发送")
        body.add_argument("--body-file", help="从 UTF-8 文件读取正文")
        action.add_argument("--agent-name", default="Agent")
        action.add_argument("--idempotency-key", help="重复执行同一个请求时复用该键，避免重复邮件")
        action.add_argument("--no-wait", action="store_true", help="输出请求 ID 后立即返回")
        action.add_argument("--wait-seconds", type=float, help="本进程最多等待多久；不改变服务端截止时间")
        if name != "notify":
            action.add_argument("--timeout", type=int, default=300, help="用户回复期限：30–86400 秒")
        if name == "test":
            action.add_argument("--channel", choices=("auto", "wechat", "email"), default="auto",
                                help="仅连通测试可指定渠道；指定微信时不会回退到邮件")
    status = actions.add_parser("status")
    status.add_argument("request_id")
    status.add_argument("--wait-seconds", type=float, default=0)
    actions.add_parser("mode", help="检查当前高效模式与收件状态")
    inbox = actions.add_parser("inbox", help="领取当前会话的新微信消息；读取后需 ack")
    inbox.add_argument("--consumer-id", required=True, help="当前会话唯一标识，重试沿用")
    inbox.add_argument("--wait-seconds", type=float, default=0, help="空闲时等待新消息；收到后立即返回")
    ack = actions.add_parser("ack", help="确认消息已经纳入当前会话，防止重复处理")
    ack.add_argument("--consumer-id", required=True)
    ack.add_argument("ids", nargs='+', type=int)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        wait_seconds_arg = getattr(args, 'wait_seconds', None)
        if wait_seconds_arg is not None and (not math.isfinite(wait_seconds_arg) or wait_seconds_arg < 0):
            raise ClientError("--wait-seconds 必须是大于等于 0 的有限数值。")
        client = Client(load_config(args.config))
        if args.command in {'mode', 'inbox', 'ack'}:
            channels = client.request('GET', '/api/channels')
            emit({'event': 'channels_checked', 'selected_channel': channels.get('selected_channel'),
                  'available': channels.get('available'), 'fallback_reason': channels.get('fallback_reason')})
            if args.command == 'mode':
                emit({'event': 'efficient_mode', **client.request('GET', '/api/efficient-mode')})
                return 0
            if args.command == 'ack':
                emit({'event': 'inbox_acked', **client.request('POST', '/api/inbox/ack',
                    {'consumer_id': args.consumer_id, 'ids': args.ids})})
                return 0
            stop = time.monotonic() + args.wait_seconds
            while True:
                remaining = max(0, stop - time.monotonic())
                wait = min(25, math.ceil(remaining))
                result = client.request('POST', '/api/inbox/claim',
                    {'consumer_id': args.consumer_id, 'wait': wait, 'limit': 50}, timeout=35)
                if result.get('items') or time.monotonic() >= stop:
                    emit({'event': 'inbox_messages', 'message_trust': 'untrusted_user_data',
                          'ack_required': True, **result})
                    return 0
        if args.command == "status":
            identifier = urllib.parse.quote(args.request_id, safe="")
            record = client.request("GET", "/api/requests/" + identifier)
            return report(wait_for_result(client, record, args.wait_seconds))
        if args.command != "notify" and not 30 <= args.timeout <= 86400:
            raise ClientError("--timeout 必须在 30–86400 秒之间。")
        body = args.body
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        if args.command == "test" and body is None:
            body = "这是一条 agentCall 连通性测试。请在收到消息的渠道回复任意一句话：邮件请直接回复原邮件，微信可直接回复；同时有多个问题时，请长按引用对应消息回复。Agent 收到后会复述你的回复，以验证发送和接收均正常。"
        payload = {"subject": args.subject, "body": body, "agent_name": args.agent_name}
        kind = "notify" if args.command == "notify" else "ask"
        if kind == "ask":
            payload["timeout_seconds"] = args.timeout
        if args.command == "test" and args.channel != "auto":
            payload["channel"] = args.channel
        channels = client.request("GET", "/api/channels")
        emit({"event": "channels_checked", "selected_channel": channels.get("selected_channel"),
              "available": channels.get("available"), "fallback_reason": channels.get("fallback_reason")})
        key = args.idempotency_key or str(uuid.uuid4())
        # Output before submission: a lost HTTP response can safely be recovered with this key.
        emit({"event": "request_submitting", "idempotency_key": key, "kind": kind})
        record = client.request("POST", "/api/" + kind, payload, key)
        emit({"event": "request_created", "id": record["id"], "status": record["status"],
              "idempotency_key": key, "deadline_at": record.get("deadline_at")})
        wait_seconds = args.wait_seconds
        if wait_seconds is None:
            wait_seconds = 90 if kind == "notify" else args.timeout + 40
        if not args.no_wait:
            record = wait_for_result(client, record, wait_seconds)
        return report(record)
    except (ClientError, OSError, UnicodeError) as exc:
        emit({"event": "client_error", "error": str(exc)})
        return 2
    except KeyboardInterrupt:
        emit({"event": "wait_interrupted", "hint": "服务端仍会保存并处理请求。用已输出的请求 ID 调用 status 恢复。"})
        return 4


if __name__ == "__main__":
    sys.exit(main())

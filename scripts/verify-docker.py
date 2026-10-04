#!/usr/bin/env python3
"""Verify an EmailCall image in an isolated disposable Docker container.

Uses no published ports and --network none. SMTP/IMAP point to the closed local
port 9, so the test cannot send external mail. Only resources carrying this
run's unique ownership label are removed; production resources are untouched.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid


LABEL = "emailcall.verification-owner"

# Executed inside the test container over docker exec stdin. No token is sent to
# host stdout: a SHA-256 fingerprint proves persistence without disclosing it.
PROBE = r'''
import hashlib
import http.client
import json
import os
import time
from pathlib import Path


def request(path, method="GET", payload=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", 10086, timeout=3)
    headers = dict(headers or {})
    if payload is not None:
        headers["Content-Type"] = "application/json"
    connection.request(method, path, json.dumps(payload) if payload is not None else None, headers)
    response = connection.getresponse()
    body = response.read()
    result = response.status, dict(response.getheaders()), json.loads(body) if body else None
    connection.close()
    return result


def wait_ready():
    stop = time.monotonic() + 15
    while time.monotonic() < stop:
        try:
            status, _, health = request("/api/health")
            if status == 200:
                return health
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    raise RuntimeError("Container API did not become ready within 15 seconds")


def run(phase, expected):
    health = wait_ready()
    assert os.geteuid() != 0, "Container application must run as a non-root user"
    status, headers, session = request("/api/session")
    assert status == 200, "UI session unavailable"
    ui = {"Cookie": headers["Set-Cookie"].split(";", 1)[0], "X-CSRF-Token": session["csrf_token"]}
    status, _, token = request("/api/token", headers=ui)
    assert status == 200, "CSRF-authenticated token endpoint failed"
    fingerprint = hashlib.sha256(token["token"].encode()).hexdigest()
    agent = {"Authorization": "Bearer " + token["token"]}
    if phase == "initial":
        assert not health["configured"], "Disposable volume must begin unconfigured"
        config = {
            "provider": "custom", "email": "agent@example.com", "username": "agent@example.com",
            "password": "disposable-verification-credential", "target_email": "user@example.com",
            "smtp_host": "127.0.0.1", "smtp_port": 9, "smtp_security": "ssl",
            "imap_host": "127.0.0.1", "imap_port": 9, "imap_security": "ssl",
            "imap_folder": "INBOX", "poll_interval": 10,
        }
        status, _, result = request("/api/config", "PUT", config, ui)
        assert status == 200 and result["config"]["password_set"], "Configuration could not be saved"
        assert "password" not in result["config"], "UI configuration leaked the stored credential"
        agent["Idempotency-Key"] = "docker-verification-notify-001"
        status, _, record = request("/api/notify", "POST", {
            "subject": "Disposable Docker verification", "body": "Expected local connection failure; no external mail.",
            "agent_name": "Docker verifier",
        }, agent)
        assert status == 202, "Notification request was not queued"
        identifier = record["id"]
        stop = time.monotonic() + 15
        while time.monotonic() < stop:
            status, _, record = request("/api/requests/" + identifier, headers=agent)
            if status == 200 and record["status"] == "failed":
                break
            time.sleep(.2)
        assert record["status"] == "failed", "Closed local SMTP port did not create a failed record"
        assert record["error"]["code"] == "SMTP_CONNECTION_FAILED", "Unexpected SMTP failure classification"
        assert record["error"]["message"] and record["error"]["hint"], "Mail failure must include a readable explanation and hint"
        assert "disposable-verification-credential" not in json.dumps(record), "Credential appeared in the record"
    else:
        assert health["configured"], "Saved configuration disappeared after container lifecycle operation"
        assert fingerprint == expected["token_fingerprint"], "API token changed after container lifecycle operation"
        status, _, config = request("/api/config", headers=ui)
        assert status == 200, "Saved configuration unavailable"
        assert config["config"]["email"] == "agent@example.com", "Sender configuration was not persisted"
        assert config["config"]["target_email"] == "user@example.com", "Target configuration was not persisted"
        assert config["config"]["password_set"], "Stored application credential disappeared"
        assert "password" not in config["config"], "UI configuration leaked a stored credential"
        identifier = expected["record_id"]
        status, _, record = request("/api/requests/" + identifier, headers=agent)
        assert status == 200 and record["status"] == "failed", "Failed request record was lost"
        assert record["error"]["code"] == "SMTP_CONNECTION_FAILED", "Persisted failure details changed"
        assert record["error"]["message"] and record["error"]["hint"], "Persisted readable error details were lost"
    status, _, records = request("/api/records", headers=ui)
    assert status == 200 and records["total"] >= 1, "Record list is not persisted"
    directory, database = Path("/data"), Path("/data/emailcall.sqlite3")
    assert directory.stat().st_mode & 0o777 == 0o700, "Data directory must have private permissions"
    assert database.stat().st_mode & 0o777 == 0o600, "Database must have private permissions"
    print(json.dumps({"phase": phase, "uid": os.geteuid(), "record_id": identifier,
                      "token_fingerprint": fingerprint, "error_code": record["error"]["code"],
                      "record_count": records["total"], "configured": True}))
'''


class VerificationError(Exception):
    pass


def docker(*arguments, stdin=None, timeout=40, required=True):
    try:
        process = subprocess.run(["docker", *arguments], input=stdin, text=True,
                                 capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError("Docker command could not complete: " + arguments[0]) from error
    if process.returncode and required:
        # Probe errors contain only static assertion messages, never token values.
        detail = process.stderr.strip().splitlines()
        raise VerificationError("Docker " + arguments[0] + " failed" + (": " + detail[-1] if detail else ""))
    return process.stdout.strip() if process.returncode == 0 else None


def owned(kind, name, owner):
    raw = docker(kind, "inspect", name, required=False, timeout=15)
    if raw is None:
        return False
    resource = json.loads(raw)[0]
    labels = resource.get("Labels") if kind == "volume" else resource.get("Config", {}).get("Labels")
    return (labels or {}).get(LABEL) == owner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="emailcall-emailcall:latest", help="Already-built local Docker image")
    args = parser.parse_args()
    owner = uuid.uuid4().hex
    name = "emailcall-verify-" + owner[:12]
    volume = name + "-data"
    success = False
    try:
        docker("image", "inspect", args.image)
        docker("volume", "create", "--label", LABEL + "=" + owner, volume)
        if not owned("volume", volume, owner):
            raise VerificationError("Refusing to use a volume not owned by this verification")

        def start():
            docker("run", "--detach", "--name", name, "--label", LABEL + "=" + owner,
                   "--network", "none", "--read-only", "--tmpfs", "/tmp:size=16m,mode=1777",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--init",
                   "--mount", "type=volume,source=" + volume + ",target=/data", args.image)
            details = json.loads(docker("container", "inspect", name))[0]
            if details["HostConfig"]["NetworkMode"] != "none" or details["HostConfig"].get("PortBindings"):
                raise VerificationError("Verification container is not fully network isolated")

        def probe(phase, expected=None):
            source = PROBE + "\nrun(" + repr(phase) + ", " + repr(expected) + ")\n"
            return json.loads(docker("exec", "-i", name, "python", "-", stdin=source, timeout=35))

        start()
        result = probe("initial")
        print("PASS: isolated non-root container; configuration saved; readable SMTP failure persisted.")
        docker("restart", "--time", "25", name, timeout=40)
        probe("restart", result)
        print("PASS: container restart preserved configuration, API token and failed request.")
        docker("stop", "--time", "25", name, timeout=40)
        docker("rm", name)
        start()
        probe("recreated", result)
        print("PASS: deleting/recreating the container with the same volume preserved all tested data.")
        success = True
    except (VerificationError, ValueError, KeyError) as error:
        print("FAIL: " + str(error), file=sys.stderr)
    finally:
        cleanup_ok = True
        for kind, resource in (("container", name), ("volume", volume)):
            try:
                if owned(kind, resource, owner):
                    arguments = ("container", "rm", "--force", resource) if kind == "container" else ("volume", "rm", resource)
                    docker(*arguments, timeout=30)
            except VerificationError as error:
                cleanup_ok = False
                print("CLEANUP FAILED for " + resource + ": " + str(error), file=sys.stderr)
        if cleanup_ok:
            print("CLEANUP: removed only this run's labelled container and volume.")
        success = success and cleanup_ok
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""macOS host watcher: open the browser once per healthy gateway instance."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request


STATE_DIR = Path.home() / "Library/Application Support/EmailCall"
URL = "http://127.0.0.1:10086/frontend/"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_if_new():
    try:
        with OPENER.open("http://127.0.0.1:10086/api/health", timeout=2) as response:
            health = json.loads(response.read(8192))
        instance = health.get("instance_id")
        if health.get("status") != "ok" or not isinstance(instance, str) or not instance:
            return False
    except (OSError, ValueError):
        return False
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE_DIR / "watcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_file = STATE_DIR / "last-instance"
        if state_file.exists() and state_file.read_text(encoding="utf-8").strip() == instance:
            return True
        result = subprocess.run(["/usr/bin/open", URL], check=False)
        if result.returncode:
            return False
        state_file.write_text(instance + "\n", encoding="utf-8")
        state_file.chmod(0o600)
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open-once", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.open_once:
        return 0 if open_if_new() else 1
    while True:
        try:
            open_if_new()
        except OSError:
            pass
        time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())

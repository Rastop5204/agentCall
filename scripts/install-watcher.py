#!/usr/bin/env python3
"""Install/uninstall the optional, per-user browser watcher when explicitly run."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


LABEL = "local.agentcall.browser"
LEGACY_LABEL = "local.emailcall.browser"


def migrate_legacy(user_home, domain):
    user_home = Path(user_home)
    subprocess.run(["launchctl", "bootout", domain + "/" + LEGACY_LABEL],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    (user_home / "Library/LaunchAgents" / (LEGACY_LABEL + ".plist")).unlink(missing_ok=True)
    old_state = user_home / "Library/Application Support/EmailCall/last-instance"
    new_state = user_home / "Library/Application Support/agentCall/last-instance"
    if old_state.exists() and not new_state.exists():
        new_state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(old_state, new_state)
        new_state.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("自动打开浏览器助手仅适用于 macOS。", file=sys.stderr)
        return 1
    support = Path.home() / "Library/Application Support/agentCall"
    plist = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    domain = "gui/" + str(os.getuid())
    migrate_legacy(Path.home(), domain)
    subprocess.run(["launchctl", "bootout", domain + "/" + LABEL],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if args.uninstall:
        plist.unlink(missing_ok=True)
        if support.exists():
            shutil.rmtree(support)
        print("已移除 agentCall 自动打开浏览器助手；数据与容器保持不变。")
        return 0
    support.mkdir(parents=True, exist_ok=True, mode=0o700)
    watcher = support / "watch.py"
    shutil.copy2(Path(__file__).with_name("watch.py"), watcher)
    watcher.chmod(0o700)
    plist.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(Path(sys.executable).resolve()), str(watcher)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    with plist.open("wb") as file:
        plistlib.dump(config, file)
    plist.chmod(0o600)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=False)
    if result.returncode:
        print("浏览器助手安装失败；仍可手动打开 http://127.0.0.1:10086/frontend/。", file=sys.stderr)
        return result.returncode
    # Background LaunchAgents may otherwise remain in launchd's speculative queue.
    result = subprocess.run(["launchctl", "kickstart", domain + "/" + LABEL], check=False)
    if result.returncode:
        print("浏览器助手已注册但未启动，请重新运行启动器。", file=sys.stderr)
        return result.returncode
    print("已启用浏览器助手：以后在 Docker Desktop 启动容器时会自动打开前端。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

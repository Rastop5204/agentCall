#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
cd "$PROJECT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '缺少 Python 3。请安装 macOS 命令行工具（xcode-select --install）后重试。'
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' '请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/'
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  if [[ "$(uname -s)" == 'Darwin' ]]; then
    open -a Docker
  else
    printf '%s\n' 'Docker 引擎未启动，请启动 Docker 后重试。'
    exit 1
  fi
  printf '%s\n' '正在等待 Docker Desktop 启动（最多 120 秒）…'
  for ((attempt=0; attempt<60; attempt++)); do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 2
  done
  if ! docker info >/dev/null 2>&1; then
    printf '%s\n' 'Docker 在 120 秒内未就绪。请打开 Docker Desktop 查看提示，然后重试。'
    exit 1
  fi
fi

docker compose build
legacy_running="$(python3 scripts/migrate-container.py)"
if ! docker compose up --no-build --remove-orphans -d; then
  if [[ "$legacy_running" == 'running' ]]; then
    docker compose stop || true
    python3 scripts/migrate-container.py --restore || true
  fi
  exit 1
fi
printf '%s\n' '正在等待 agentCall 就绪…'
if ! python3 - <<'PY'
import json
import time
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(60):
    try:
        with opener.open("http://127.0.0.1:10086/api/health", timeout=2) as response:
            if json.loads(response.read()).get("status") == "ok":
                break
    except (OSError, ValueError):
        pass
    time.sleep(1)
else:
    raise SystemExit("agentCall 未就绪。运行 docker compose logs --tail=100 查看原因。")
PY
then
  if [[ "$legacy_running" == 'running' ]]; then
    docker compose stop || true
    python3 scripts/migrate-container.py --restore || true
  fi
  exit 1
fi

if [[ "$(uname -s)" == 'Darwin' ]]; then
  python3 scripts/install-watcher.py || true
  python3 scripts/watch.py --open-once || open 'http://127.0.0.1:10086/frontend/'
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open 'http://127.0.0.1:10086/frontend/' >/dev/null 2>&1 || true
fi
printf '%s\n' 'agentCall 已启动：http://127.0.0.1:10086/frontend/'

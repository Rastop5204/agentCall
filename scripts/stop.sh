#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
cd "$PROJECT_DIR"
docker compose stop
printf '%s\n' 'agentCall 已停止，配置、记录和微信会话保留在 Docker 数据卷中。'

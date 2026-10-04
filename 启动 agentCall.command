#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! /bin/bash scripts/start.sh; then
  printf '\n%s\n' '启动失败。按回车关闭窗口。'
  read -r _
  exit 1
fi

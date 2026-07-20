#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 兼容两种命令：优先 docker compose（V2 插件），否则回退 docker-compose（V1 独立命令）
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    echo "错误：未检测到 docker compose 或 docker-compose，请先安装 Docker Compose" >&2
    exit 1
fi

echo "正在停止 SmartClaw 容器..."
$DC down
echo "容器已停止并移除。"


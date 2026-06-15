#!/bin/bash

# 读取公共配置
CONFIG_FILE="./service_config.sh"
if [ ! -f "$CONFIG_FILE" ]; then
  echo "公共配置文件不存在: $CONFIG_FILE"
  exit 1
fi
. "$CONFIG_FILE"

# 停止前端服务
# -sTCP:LISTEN 确保只获取处于监听状态的进程 PID
frontend_pid=$(lsof -t -sTCP:LISTEN -i:$FRONTEND_PORT)
if [ -n "$frontend_pid" ]; then
  echo "前端服务进程详细信息："
  ps -fp $frontend_pid
  echo "正在停止前端服务，PID: $frontend_pid"
  kill -9 $frontend_pid
else
  echo "前端服务未运行，端口: $FRONTEND_PORT"
fi

sleep 3

# 停止后端服务
# 在此处动态获取后端 PID，避免受已被杀死的进程干扰
backend_pid=$(lsof -t -sTCP:LISTEN -i:$BACKEND_PORT)
if [ -n "$backend_pid" ]; then
  echo "后端服务进程详细信息："
  ps -fp $backend_pid
  echo "正在停止后端服务，PID: $backend_pid"
  kill -9 $backend_pid
else
  echo "后端服务未运行，端口: $BACKEND_PORT"
fi

sleep 3

# 再次检查前后端是否已停止
frontend_pid=$(lsof -t -sTCP:LISTEN -i:$FRONTEND_PORT)
backend_pid=$(lsof -t -sTCP:LISTEN -i:$BACKEND_PORT)

if [ -z "$frontend_pid" ] && [ -z "$backend_pid" ]; then
  echo "前后端服务已停止。"
else
  [ -n "$frontend_pid" ] && echo "前端服务仍在运行，可能较慢，请稍后检查。PID: $frontend_pid"
  [ -n "$backend_pid" ] && echo "后端服务仍在运行，可能较慢，请稍后检查。PID: $backend_pid"
fi

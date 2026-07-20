#!/bin/bash
# =============================================================================
# SmartClaw Docker 启动入口（前台运行版）
#
# 该脚本是源码方式 start.sh 的容器化前台版本，行为与源码方式保持一致：
#   - 端口、监听地址、代理目标、HTTPS 逻辑均与 service_config.sh 一致
#   - 配置根目录仍为 ~/.smartclaw（容器内以 root 运行即 /root/.smartclaw）
#   - 后端 uvicorn 加 --no-server-header，前端 serve_webui.py 代理到后端
#
# 与 start.sh 的差异（仅为适配容器，不改变对外行为）：
#   - 去掉 conda 激活：容器内直接使用镜像中 pip 安装的 python
#   - 去掉 lsof 端口探测/nohup/disown：容器内单进程组，前台运行由 Docker 托管
#   - 日志输出到 stdout/stderr（docker logs 可见），同时不再写 ./logs
# =============================================================================

set -e

# -----------------------------------------------------------------------------
# 项目级 .smartclaw（/app/smartclaw/.smartclaw）通过 compose 挂载为宿主目录，
# 首次为空、会遮盖镜像里烤入的 .smartclaw。真正的播种逻辑放在下方
#（usermod 调整 smartclaw uid/gid 之后执行），以保证播种内容属主正确。
# -----------------------------------------------------------------------------

# 读取公共配置（沿用源码 service_config.sh 中的端口/HTTPS 等设置）
CONFIG_FILE="./service_config.sh"
if [ ! -f "$CONFIG_FILE" ]; then
  echo "公共配置文件不存在: $CONFIG_FILE"
  exit 1
fi

# -----------------------------------------------------------------------------
# Docker 专用配置覆盖（不改动原项目 service_config.sh）
#
# service_config.sh 保持源码原版（硬编码 5173/5174/true/./.certs），以便这套代码
# 既能源码部署、也能 Docker 部署，两边都无需改文件。Docker 下如需自定义端口/HTTPS/
# 证书目录，通过 docker-compose.yml 传入同名环境变量即可。
#
# 注意：`. service_config.sh` 会用脚本内硬编码值给这些变量赋值，从而覆盖 compose
# 传入的环境变量。因此这里先把环境变量的值备份到 _OV_* ，source 之后再覆盖回去。
# 未设置对应环境变量时，_OV_* 为空，完全沿用 service_config.sh 的原版默认值。
# -----------------------------------------------------------------------------
_OV_FRONTEND_PORT="$FRONTEND_PORT"
_OV_BACKEND_PORT="$BACKEND_PORT"
_OV_ENABLE_HTTPS="$ENABLE_HTTPS"
_OV_FRONTEND_HOST="$FRONTEND_HOST"
_OV_BACKEND_HOST="$BACKEND_HOST"
_OV_PROXY_TARGET_HOST="$PROXY_TARGET_HOST"
_OV_CERT_DIR="$CERT_DIR"

. "$CONFIG_FILE"

[ -n "$_OV_FRONTEND_PORT" ]     && FRONTEND_PORT="$_OV_FRONTEND_PORT"
[ -n "$_OV_BACKEND_PORT" ]      && BACKEND_PORT="$_OV_BACKEND_PORT"
[ -n "$_OV_ENABLE_HTTPS" ]      && ENABLE_HTTPS="$_OV_ENABLE_HTTPS"
[ -n "$_OV_FRONTEND_HOST" ]     && FRONTEND_HOST="$_OV_FRONTEND_HOST"
[ -n "$_OV_BACKEND_HOST" ]      && BACKEND_HOST="$_OV_BACKEND_HOST"
[ -n "$_OV_PROXY_TARGET_HOST" ] && PROXY_TARGET_HOST="$_OV_PROXY_TARGET_HOST"
[ -n "$_OV_CERT_DIR" ]          && CERT_DIR="$_OV_CERT_DIR"

# HTTPS 证书配置，仅在 ENABLE_HTTPS=true 时生效
HTTPS_CERT_FILE="$CERT_DIR/smartclaw.crt"
HTTPS_KEY_FILE="$CERT_DIR/smartclaw.key"
HTTPS_CERT_CONFIG="$CERT_DIR/smartclaw-openssl.cnf"
FRONTEND_SCHEME="http"
BACKEND_SCHEME="http"

case "$ENABLE_HTTPS" in
  true|TRUE|True|1|yes|YES|Yes|on|ON|On)
    ENABLE_HTTPS="true"
    FRONTEND_SCHEME="https"
    BACKEND_SCHEME="https"
    ;;
  *)
    ENABLE_HTTPS="false"
    ;;
esac

detect_access_host() {
  if [ -n "$ACCESS_HOST" ]; then
    echo "$ACCESS_HOST"
    return 0
  fi

  if [ "$ENABLE_HTTPS" != "true" ]; then
    echo "127.0.0.1"
    return 0
  fi

  if command -v hostname >/dev/null 2>&1 && command -v awk >/dev/null 2>&1; then
    hostname -I 2>/dev/null | awk '{
      for (i = 1; i <= NF; i++) {
        if ($i !~ /^127\./ && $i != "::1") {
          print $i
          exit
        }
      }
    }'
    return 0
  fi

  echo "127.0.0.1"
}

ACCESS_HOST="$(detect_access_host)"
[ -n "$ACCESS_HOST" ] || ACCESS_HOST="127.0.0.1"
HTTPS_CERT_HOST="$ACCESS_HOST"

# 根据环境变量修改smartclaw用户uid和gid
if [ -n "$SMARTCLAW_UID" ] && [ -n "$SMARTCLAW_GID" ]; then
  usermod -u "$SMARTCLAW_UID" smartclaw
  groupmod -g "$SMARTCLAW_GID" smartclaw
fi

write_cert_config() {
  local san_host="$1"

  mkdir -p "$CERT_DIR"
    chown smartclaw:smartclaw "$CERT_DIR"
    chmod 750 "$CERT_DIR"
  cat > "$HTTPS_CERT_CONFIG" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
x509_extensions = v3_req
distinguished_name = dn

[dn]
CN = $san_host

[v3_req]
subjectAltName = @alt_names
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
IP.1 = 127.0.0.1
DNS.1 = localhost
EOF

  if [[ "$san_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "IP.2 = $san_host" >> "$HTTPS_CERT_CONFIG"
  else
    echo "DNS.2 = $san_host" >> "$HTTPS_CERT_CONFIG"
  fi
}

ensure_https_cert() {
  if ! command -v openssl >/dev/null 2>&1; then
    echo "校验或生成 HTTPS 证书需要安装 openssl。"
    echo "请安装 openssl，或者提前将证书文件放到以下位置："
    echo "  $HTTPS_CERT_FILE"
    echo "  $HTTPS_KEY_FILE"
    exit 1
  fi

  if [ -f "$HTTPS_CERT_FILE" ] && [ -f "$HTTPS_KEY_FILE" ]; then
    # 已有证书时也要确保 cert 目录和文件属于 smartclaw 用户（兼容旧版 root 创建）
    chown smartclaw:smartclaw "$CERT_DIR"
    chmod 750 "$CERT_DIR"
    chown smartclaw:smartclaw "$HTTPS_KEY_FILE" "$HTTPS_CERT_FILE"
    chmod 600 "$HTTPS_KEY_FILE"
    chmod 644 "$HTTPS_CERT_FILE"

    cert_san="$(openssl x509 -in "$HTTPS_CERT_FILE" -noout -ext subjectAltName 2>/dev/null || true)"
    if [[ "$HTTPS_CERT_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      if [[ "$cert_san" == *"IP Address:$HTTPS_CERT_HOST"* || "$cert_san" == *"IP:$HTTPS_CERT_HOST"* ]]; then
        return 0
      fi
    else
      if [[ "$cert_san" == *"DNS:$HTTPS_CERT_HOST"* ]]; then
        return 0
      fi
    fi
    echo "现有 HTTPS 证书与 $HTTPS_CERT_HOST 不匹配，正在重新生成..."
  fi

  echo "正在为 $HTTPS_CERT_HOST 生成自签名 HTTPS 证书..."
  write_cert_config "$HTTPS_CERT_HOST"
  openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
    -keyout "$HTTPS_KEY_FILE" \
    -out "$HTTPS_CERT_FILE" \
    -config "$HTTPS_CERT_CONFIG"
  chmod 600 "$HTTPS_KEY_FILE"
  chown smartclaw:smartclaw "$HTTPS_KEY_FILE"
  chmod 644 "$HTTPS_CERT_FILE"
  chown smartclaw:smartclaw "$HTTPS_CERT_FILE"
}

# 在 PATH 上提供 smartclaw 命令（复刻源码 ensure_smartclaw_cli_wrapper 的作用）。
# 源码方式会在 conda 环境 bin 下生成该命令，供 ACP 等以 "command: smartclaw" 方式调用；
# 容器内无 conda，这里生成到 /usr/local/bin 以保持相同行为。
ensure_smartclaw_cli_wrapper() {
  local smartclaw_python
  smartclaw_python="$(python -c 'import sys; print(sys.executable)')" || exit 1
  cat > /usr/local/bin/smartclaw <<EOF
#!/usr/bin/env bash
exec "$smartclaw_python" -m smartclaw.cli.main "\$@"
EOF
  chmod +x /usr/local/bin/smartclaw
}

# 收到停止信号时，优雅终止前后端子进程
BACKEND_PID=""
FRONTEND_PID=""
shutdown() {
  echo "正在停止 SmartClaw 服务..."
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  exit 0
}
trap shutdown SIGTERM SIGINT

ensure_smartclaw_cli_wrapper
chmod o-rwx *.sh
chmod o-rwx docker/*.sh

# 确保挂载卷目录属于 smartclaw 用户，使其可读写配置和数据
mkdir -p /home/smartclaw/.smartclaw
chown -R smartclaw:smartclaw /home/smartclaw

# 首次启动：项目级 .smartclaw 挂载目录首次为空，会遮盖镜像里烤入的内容，
# 需从镜像内副本 .smartclaw.default 播种预置内容（配置模板 + 预置 plugins）。
SMARTCLAW_PROJECT_DIR="/app/smartclaw/.smartclaw"
SMARTCLAW_DEFAULT_DIR="/app/smartclaw/.smartclaw.default"
if [ -d "$SMARTCLAW_DEFAULT_DIR" ] && [ ! -e "$SMARTCLAW_PROJECT_DIR/.seeded" ]; then
  echo "首次启动：正在从镜像播种项目级 .smartclaw 目录..."
  mkdir -p "$SMARTCLAW_PROJECT_DIR"
  # -n 不覆盖已存在文件，保证幂等且不破坏客户已有改动
  cp -rn "$SMARTCLAW_DEFAULT_DIR/." "$SMARTCLAW_PROJECT_DIR/"
  touch "$SMARTCLAW_PROJECT_DIR/.seeded"
fi
# 属主归 smartclaw（在 usermod 之后执行，uid 已正确），保证运行时可读写 plugins 等
chown -R smartclaw:smartclaw "$SMARTCLAW_PROJECT_DIR"

# 启动后端服务
# Uvicorn 默认会在每个 HTTP 响应中加入 Server: uvicorn，会导致被扫描出漏洞，加 --no-server-header 避免
if [ "$ENABLE_HTTPS" = "true" ]; then
  ensure_https_cert
  su - smartclaw -c "cd /app/smartclaw && python -m uvicorn smartclaw.server.app:app \
    --host $BACKEND_HOST \
    --port $BACKEND_PORT \
    --no-server-header \
    --ssl-certfile $HTTPS_CERT_FILE \
    --ssl-keyfile $HTTPS_KEY_FILE" &
  BACKEND_PID=$!
else
  su - smartclaw -c "cd /app/smartclaw && python -m uvicorn smartclaw.server.app:app \
    --host $BACKEND_HOST \
    --port $BACKEND_PORT \
    --no-server-header" &
  BACKEND_PID=$!
fi
echo "正在启动后端服务... PID: $BACKEND_PID"

sleep 3

# 启动前端服务，并将请求代理到后端
if [ "$ENABLE_HTTPS" = "true" ]; then
  ensure_https_cert
  su - smartclaw -c "cd /app/smartclaw && python ./scripts/serve_webui.py \
    --directory ./webui/dist \
    --host $FRONTEND_HOST \
    --port $FRONTEND_PORT \
    --proxy-target $BACKEND_SCHEME://$PROXY_TARGET_HOST:$BACKEND_PORT \
    --proxy-ca-file $HTTPS_CERT_FILE \
    --ssl-certfile $HTTPS_CERT_FILE \
    --ssl-keyfile $HTTPS_KEY_FILE" &
  FRONTEND_PID=$!
else
  su - smartclaw -c "cd /app/smartclaw && python ./scripts/serve_webui.py \
    --directory ./webui/dist \
    --host $FRONTEND_HOST \
    --port $FRONTEND_PORT \
    --proxy-target $BACKEND_SCHEME://$PROXY_TARGET_HOST:$BACKEND_PORT" &
  FRONTEND_PID=$!
fi
echo "正在启动前端服务... PID: $FRONTEND_PID"

echo "前端访问地址: $FRONTEND_SCHEME://$ACCESS_HOST:$FRONTEND_PORT"
echo "后端访问地址: $BACKEND_SCHEME://$ACCESS_HOST:$BACKEND_PORT"

# 任一子进程退出即让容器退出，交由 Docker 的 restart 策略处理
wait -n "$BACKEND_PID" "$FRONTEND_PID"
echo "检测到前端或后端进程退出，容器即将停止。"
shutdown

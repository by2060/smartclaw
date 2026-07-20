#!/bin/bash

# 读取公共配置
CONFIG_FILE="./service_config.sh"
if [ ! -f "$CONFIG_FILE" ]; then
  echo "公共配置文件不存在: $CONFIG_FILE"
  exit 1
fi
. "$CONFIG_FILE"

# 是否启用 HTTPS，在 service_config.sh 中配置

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

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "重启 SmartClaw 服务需要安装 $1。"
    exit 1
  fi
}

write_cert_config() {
  local san_host="$1"

  mkdir -p "$CERT_DIR"
  chmod 700 "$CERT_DIR"
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
  chmod 644 "$HTTPS_CERT_FILE"
}

require_command lsof

# 检查前后端端口是否已被占用（仅匹配 LISTEN 状态的进程）
frontend_pid=$(lsof -t -sTCP:LISTEN -i:$FRONTEND_PORT)
backend_pid=$(lsof -t -sTCP:LISTEN -i:$BACKEND_PORT)

# 停止前端服务
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
if [ -n "$backend_pid" ]; then
  echo "后端服务进程详细信息："
  ps -fp $backend_pid
  echo "正在停止后端服务，PID: $backend_pid"
  kill -9 $backend_pid
else
  echo "后端服务未运行，端口: $BACKEND_PORT"
fi

echo "等待 3 秒后重新启动服务..."
sleep 3

# 激活 conda 环境
eval "$(conda shell.bash hook)"
conda activate "$CONDA_ENV_NAME"
# 确保当前 Conda 环境提供 smartclaw 命令，方便直接使用 smartclaw 命令
ensure_smartclaw_cli_wrapper

# 启动后端服务
if [ "$ENABLE_HTTPS" = "true" ]; then
  ensure_https_cert
  mkdir -p ./logs && nohup python -m uvicorn smartclaw.server.app:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    --no-server-header \
    --ssl-certfile "$HTTPS_CERT_FILE" \
    --ssl-keyfile "$HTTPS_KEY_FILE" \
    >> ./logs/backend.log 2>&1 &
  disown $! 2>/dev/null || true
else
  mkdir -p ./logs && nohup python -m uvicorn smartclaw.server.app:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" --no-server-header >> ./logs/backend.log 2>&1 &
  disown $! 2>/dev/null || true
fi
echo "正在启动后端服务..."

sleep 3

# 启动前端服务，并将请求代理到后端
if [ "$ENABLE_HTTPS" = "true" ]; then
  ensure_https_cert
  mkdir -p ./logs && nohup python ./scripts/serve_webui.py \
    --directory ./webui/dist \
    --host "$FRONTEND_HOST" \
    --port "$FRONTEND_PORT" \
    --proxy-target "$BACKEND_SCHEME://$PROXY_TARGET_HOST:$BACKEND_PORT" \
    --proxy-ca-file "$HTTPS_CERT_FILE" \
    --ssl-certfile "$HTTPS_CERT_FILE" \
    --ssl-keyfile "$HTTPS_KEY_FILE" \
    >> ./logs/webui.log 2>&1 &
  disown $! 2>/dev/null || true
  echo "正在启动前端服务..."
else
  mkdir -p ./logs && nohup python ./scripts/serve_webui.py --directory ./webui/dist --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --proxy-target "$BACKEND_SCHEME://$PROXY_TARGET_HOST:$BACKEND_PORT" >> ./logs/webui.log 2>&1 &
  disown $! 2>/dev/null || true
  echo "正在启动前端服务..."
fi

sleep 3

# 等待服务启动后再次检查进程状态（仅匹配 LISTEN 状态的进程）
frontend_pid=$(lsof -t -sTCP:LISTEN -i:$FRONTEND_PORT)
backend_pid=$(lsof -t -sTCP:LISTEN -i:$BACKEND_PORT)

# 输出前端进程信息与访问地址
if [ -n "$frontend_pid" ]; then
  echo "前端服务进程详细信息："
  ps -fp $frontend_pid
  echo "前端访问地址: $FRONTEND_SCHEME://$ACCESS_HOST:$FRONTEND_PORT"
else
  echo "前端服务未启动成功，可能较慢，请稍后检查。"
fi

# 输出后端进程信息与访问地址
if [ -n "$backend_pid" ]; then
  echo "后端服务进程详细信息："
  ps -fp $backend_pid
  echo "后端访问地址: $BACKEND_SCHEME://$ACCESS_HOST:$BACKEND_PORT"
else
  echo "后端服务未启动成功，可能较慢，请稍后检查。"
fi

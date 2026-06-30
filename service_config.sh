# 前端服务端口
FRONTEND_PORT=5173
# 后端服务端口
BACKEND_PORT=5174
# conda 环境
CONDA_ENV_NAME="smartclaw"
# 是否启用 HTTPS
ENABLE_HTTPS=true

# 前端服务监听所有网卡
FRONTEND_HOST="0.0.0.0"
# 后端服务监听所有网卡
BACKEND_HOST="0.0.0.0"
# 前端服务代理到后端的目标地址
PROXY_TARGET_HOST="127.0.0.1"
# HTTPS 证书
CERT_DIR="./.certs"

# 在当前 Conda 虚拟环境的 bin 目录下，自动生成一个名为 smartclaw 的启动命令
ensure_smartclaw_cli_wrapper() {
  if [ -z "$CONDA_PREFIX" ]; then
    echo "当前Conda虚拟环境的根目录（绝对路径）为空; 环境未激活."
    exit 1
  fi

  local smartclaw_python
  smartclaw_python="$(python -c 'import sys; print(sys.executable)')" || exit 1
  mkdir -p "$CONDA_PREFIX/bin"
  cat > "$CONDA_PREFIX/bin/smartclaw" <<EOF
#!/usr/bin/env bash
exec "$smartclaw_python" -m smartclaw.cli.main "\$@"
EOF
  chmod +x "$CONDA_PREFIX/bin/smartclaw"
}

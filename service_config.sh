# 前端服务端口
FRONTEND_PORT=5173
# 后端服务端口
BACKEND_PORT=5174
# conda 环境
CONDA_ENV_NAME="smartclaw"
# 是否启用 HTTPS
ENABLE_HTTPS=true

# 前端监听所有网卡，方便其他机器访问页面
FRONTEND_HOST="0.0.0.0"
# 后端只监听本机，避免外部机器直接访问 API
BACKEND_HOST="0.0.0.0"
# 前端服务代理到后端的目标地址
PROXY_TARGET_HOST="127.0.0.1"
# HTTPS 证书
CERT_DIR="./.certs"

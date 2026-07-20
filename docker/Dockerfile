# =============================================================================
# SmartClaw Dockerfile
#
# 说明：
#   - 基础镜像 python:3.13-slim，与服务器 smartclaw 环境的 Python 版本一致
#   - 构建时通过 pip 安装 requirements.txt（构建机需联网；现场离线可用
#     docker save/load 导入构建好的镜像）
#   - 运行时行为与源码 start.sh 保持一致：配置根目录仍为 ~/.smartclaw
#     （容器内以 root 运行即 /root/.smartclaw），端口/HTTPS 逻辑不变
# =============================================================================
FROM python:3.13-slim

# 系统依赖：
#   openssl  - 生成/校验 HTTPS 自签名证书（与源码一致）
#   ca-certificates - HTTPS 出站请求所需根证书
#   procps   - 提供 ps 等基础排障命令
# 说明：容器内前台单进程组，不再依赖 lsof 做端口探测，故不安装 lsof。
# 替换 Debian 默认源为阿里云源（兼容新版 Debian 12 的配置路径）
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssl \
        ca-certificates \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/smartclaw



# 先仅复制依赖清单以利用构建缓存
COPY requirements.txt /app/smartclaw/requirements.txt
# RUN pip install --no-cache-dir -r /app/smartclaw/requirements.txt
RUN pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r /app/smartclaw/requirements.txt


RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fonts-wqy-zenhei \
    git \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    libxshmfence1 \
    libxss1 \
    libasound2 \
    locales \
    lsof \
    procps \
    tini \
    && rm -rf /var/lib/apt/lists/*


RUN sed -i 's/^# *zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen zh_CN.UTF-8 \
    && useradd --create-home --shell /bin/bash smartclaw

# 复制项目源码（.dockerignore 已排除 logs/.certs/__pycache__ 等运行态文件）
COPY . /app/smartclaw

# 入口脚本转为可执行（Windows 检出可能丢失可执行位，这里显式赋权）。
# 现在只挂载 .smartclaw 子目录、不再挂载整个项目根，故 docker/ 下脚本不被遮盖，
# 此 chmod 生效；ENTRYPOINT 仍用 bash 显式执行做双保险，不依赖可执行位。
RUN chmod +x /app/smartclaw/docker/docker-entrypoint.sh \
    && chmod o-rwx /app/smartclaw/docker/*.sh

# 播种源备份：compose 仅把宿主目录挂到 /app/smartclaw/.smartclaw（首次为空），
# 会遮盖镜像里烤入的 .smartclaw。故此处保留一份不被遮盖的副本 .smartclaw.default，
# 供 entrypoint 首次启动时把预置内容（配置模板 + 预置 plugins）播种进挂载目录。
RUN cp -r /app/smartclaw/.smartclaw /app/smartclaw/.smartclaw.default

# 与源码 service_config.sh 中的端口保持一致
EXPOSE 5173 5174

# 用 bash 显式执行入口脚本：即便宿主机挂载的脚本没有可执行位（Windows 检出常见），
# 也能正常启动，不依赖文件的 +x 权限。
ENTRYPOINT ["bash", "/app/smartclaw/docker/docker-entrypoint.sh"]

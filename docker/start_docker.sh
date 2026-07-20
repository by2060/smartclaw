#!/bin/bash
# =============================================================================
# SmartClaw Docker 启动脚本（宿主机端）
#
# 功能：
#   1. 从 .env 文件加载环境变量
#   2. 若缺少 SMARTCLAW_UID / SMARTCLAW_GID，引导用户输入运行用户名（禁止 root）
#   3. 检查/创建宿主机用户，获取 uid/gid
#   4. 将 SMARTCLAW_UID / SMARTCLAW_GID 写入 .env（docker compose 自动读取）
#   5. 执行 docker compose up -d 启动容器
#
# 说明：
#   .env 与 docker-compose.yml 同目录，docker compose 会自动读取做变量替换。
#   因此首次执行本脚本生成 .env 后，之后在本目录直接 docker compose
#   up -d / restart / down 均可正确拿到 UID/GID，无需再 source 或 export。
#
# 使用方式：
#   sudo ./startup_docker.sh         # 需要创建用户时用 sudo
# =============================================================================

set -e

# ---------------------------------------------------------------------------
# 脚本位置检测
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 统一切换到脚本所在目录，保证后续相对路径（../smartclaw-home 等）
# 与 docker-compose.yml 的挂载路径一致，无论从哪个目录调用本脚本。
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

# ============================================================================
# 1. 从 .env 文件加载环境变量
# ============================================================================
# 使用与 docker-compose.yml 同目录的绝对路径，保证无论从哪个目录调用脚本，
# 读到/写入的都是同一个 .env（docker compose 变量替换也读取该文件）。
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    echo "[1/6] 从 .env 加载环境变量: $ENV_FILE"
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
else
    echo "[1/6] .env 文件不存在，跳过加载"
fi

# ============================================================================
# 2. 检查 SMARTCLAW_UID / SMARTCLAW_GID 是否已设置
# ============================================================================
if [ -n "$SMARTCLAW_UID" ] && [ -n "$SMARTCLAW_GID" ]; then
    echo "[2/6] 已加载用户配置: UID=$SMARTCLAW_UID  GID=$SMARTCLAW_GID"
    echo "[3/6] 跳过用户创建"
    echo "[4/6] 跳过 uid/gid 获取"
else
    echo "[2/6] 未检测到 SMARTCLAW_UID / SMARTCLAW_GID"

    # ----------------------------------------------------------------------
    # 3. 提示用户输入平台启动用户名（禁止 root）
    # ----------------------------------------------------------------------
    while true; do
        echo -n "[3/6] 请输入平台启动普通用户名（禁用root）: "
        read -r SMARTCLAW_USER

        if [ -z "$SMARTCLAW_USER" ]; then
            echo "      错误：用户名不能为空"
            continue
        fi

        if [ "$SMARTCLAW_USER" = "root" ]; then
            echo "      错误：禁止使用 root 运行此服务，请使用普通用户名"
            continue
        fi

        break
    done

    # ----------------------------------------------------------------------
    # 4. 检查用户是否存在，不存在则创建
    # ----------------------------------------------------------------------
    if id "$SMARTCLAW_USER" &>/dev/null; then
        echo "[4/6] 用户 $SMARTCLAW_USER 已存在"
    else
        echo "[4/6] 用户 $SMARTCLAW_USER 不存在，正在创建..."
        if [ "$EUID" -ne 0 ]; then
            echo ""
            echo "错误：创建用户需要 root 权限"
            echo "请使用: sudo $0"
            exit 1
        fi
        useradd --create-home --shell /bin/bash "$SMARTCLAW_USER"
        echo "      用户 $SMARTCLAW_USER 创建成功"
    fi

    # ----------------------------------------------------------------------
    # 5. 获取 uid / gid，保存到 .env
    # ----------------------------------------------------------------------
    SMARTCLAW_UID=$(id -u "$SMARTCLAW_USER")
    SMARTCLAW_GID=$(id -g "$SMARTCLAW_USER")
    echo "[4/6] UID=$SMARTCLAW_UID  GID=$SMARTCLAW_GID"

    # 写入 .env（docker compose 原生格式 KEY=VALUE，不带 export；
    # 更新已有条目或追加新条目）
    if [ -f "$ENV_FILE" ] && grep -q "^SMARTCLAW_UID=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s/^SMARTCLAW_UID=.*/SMARTCLAW_UID=$SMARTCLAW_UID/" "$ENV_FILE"
        sed -i "s/^SMARTCLAW_GID=.*/SMARTCLAW_GID=$SMARTCLAW_GID/" "$ENV_FILE"
        echo "      已更新 $ENV_FILE 中的用户配置"
    else
        cat >> "$ENV_FILE" <<EOF

# ---------------------------------------------------------------------------
# SMARTCLAW 平台启动用户信息（由 startup_docker.sh 自动生成）
# docker compose 会自动读取本文件做变量替换，无需 export。
# ---------------------------------------------------------------------------
SMARTCLAW_UID=$SMARTCLAW_UID
SMARTCLAW_GID=$SMARTCLAW_GID
EOF
        echo "      已保存用户配置到 $ENV_FILE"
    fi
fi

# ============================================================================
# 6. 导出环境变量并启动容器
# ============================================================================
export SMARTCLAW_UID
export SMARTCLAW_GID

mkdir -p ../smartclaw-home
mkdir -p ../.smartclaw
# 用数字 UID:GID（而非用户名）设置属主：无论 UID/GID 是本次输入创建、
# 还是从 .env 加载得到（此时 SMARTCLAW_USER 变量为空），都能正确执行。
# smartclaw-home：用户级配置/数据；smartclaw-project：项目级 .smartclaw 运行时数据。
chown $SMARTCLAW_UID:$SMARTCLAW_GID -R ../smartclaw-home
chown $SMARTCLAW_UID:$SMARTCLAW_GID -R ../.smartclaw

echo "[5/6] docker compose 将使用: SMARTCLAW_UID=$SMARTCLAW_UID  SMARTCLAW_GID=$SMARTCLAW_GID"
echo "[6/6] 执行 $DC up -d..."

$DC up -d

echo "容器已启动。"
echo "重启: $DC -f $SCRIPT_DIR/docker-compose.yml restart"
echo "停止: $DC -f $SCRIPT_DIR/docker-compose.yml down"
echo "日志: $DC -f $SCRIPT_DIR/docker-compose.yml logs -f"


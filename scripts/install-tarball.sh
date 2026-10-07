#!/usr/bin/env bash
# =============================================================================
# install-tarball.sh —— 离线 tarball包的一键安装脚本
#
# 由 scripts/package-tarball.sh 生成并随包分发，适用于内网/无外网环境。
# 与 deploy/install.sh 的差异：不需要 git / npm / 网络，只用包内已有产物。
#
# 用法：
#   sudo bash install-tarball.sh                # 安装（幂等，重复执行即升级）
#   sudo bash install-tarball.sh --skip-venv    # 跳过 venv 创建（包内已内置）
#   sudo bash install-tarball.sh --no-systemd   # 不注册 systemd 服务
#   sudo bash install-tarball.sh --uninstall    # 卸载（保留数据）
#   sudo bash install-tarball.sh --purge        # 卸载并删除全部数据
# =============================================================================
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="/opt/aiassistant"
GATEWAY_ENV_DIR="/etc/aiassistant"
GATEWAY_ENV_FILE="${GATEWAY_ENV_DIR}/gateway.env"
SERVICE_NAME="aiassistant-gateway"

SKIP_VENV=0
USE_SYSTEMD=1
DO_UNINSTALL=0
PURGE=0

for arg in "$@"; do
  case "$arg" in
    --skip-venv)  SKIP_VENV=1 ;;
    --no-systemd) USE_SYSTEMD=0 ;;
    --uninstall)  DO_UNINSTALL=1 ;;
    --purge)      PURGE=1 ;;
    -h|--help)    sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

C_RED='\033[1;31m'; C_GREEN='\033[1;32m'; C_BLUE='\033[1;34m'; C_YELLOW='\033[1;33m'; C_OFF='\033[0m'
log()  { printf "${C_BLUE}[install]${C_OFF} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[OK]${C_OFF}     %s\n" "$*"; }
warn() { printf "${C_YELLOW}[!]${C_OFF}     %s\n" "$*"; }
err()  { printf "${C_RED}[ERROR]${C_OFF}   %s\n" "$*" >&2; }

if [[ $EUID -ne 0 ]]; then
  err "请以 root 运行：sudo bash install-tarball.sh"
  exit 1
fi

# ---------- 卸载 ----------
if [[ $DO_UNINSTALL -eq 1 ]]; then
  log "卸载 Aurora 网关"
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    ok "systemd 服务已清理"
  fi
  if command -v nginx >/dev/null 2>&1; then
    rm -f /etc/nginx/conf.d/aiassistant.conf
    nginx -t >/dev/null 2>&1 && systemctl reload nginx 2>/dev/null || true
    ok "nginx 配置已移除"
  fi
  id aiassistant >/dev/null 2>&1 && { userdel aiassistant 2>/dev/null || true; ok "系统用户已删除"; }
  if [[ $PURGE -eq 1 ]]; then
    rm -rf "$TARGET" "$GATEWAY_ENV_DIR"
    ok "已删除 $TARGET 与 $GATEWAY_ENV_DIR（不可逆）"
  else
    ok "已保留数据目录 $TARGET（--purge 可彻底删除）"
  fi
  ok "卸载完成"
  exit 0
fi

log "离线包目录: $PKG_DIR"
log "目标目录  : $TARGET"

# ---------- 1) 目录骨架 ----------
mkdir -p "$TARGET"/backend "$TARGET"/frontend/dist "$TARGET"/config
mkdir -p "$TARGET"/data/sessions "$TARGET"/data/uploads "$TARGET"/logs
mkdir -p "$GATEWAY_ENV_DIR"
chmod 700 "$GATEWAY_ENV_DIR"

if ! id aiassistant >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /opt/aiassistant --shell /usr/sbin/nologin aiassistant
  ok "已创建系统用户 aiassistant"
fi

# ---------- 2) 同步代码 ----------
log "---- 同步后端代码与前端产物 ----"
# 保留目标机已有的 *.local.yaml 密钥层
cp -rf "$PKG_DIR/backend/." "$TARGET/backend/"
rm -rf "$TARGET/backend/app/__pycache__" "$TARGET/backend/app/routers/__pycache__" \
       "$TARGET/backend/app/services/__pycache__" "$TARGET/backend/app/core/__pycache__" 2>/dev/null || true

cp -rf "$PKG_DIR/frontend/dist/." "$TARGET/frontend/dist/"

# config：逐文件覆盖，跳过密钥层
cp -rf "$PKG_DIR/config/." "$TARGET/config/"
ok "代码与前端产物已同步"

# ---------- 3) 虚拟环境 ----------
if [[ -d "$PKG_DIR/backend/venv" ]]; then
  log "---- 使用包内预建虚拟环境 ----"
  if [[ -d "$TARGET/backend/venv" ]]; then
    rm -rf "$TARGET/backend/venv"
  fi
  cp -rf "$PKG_DIR/backend/venv" "$TARGET/backend/venv"
  # 预建 venv 内路径与包内目录不一致，需重定位解释器符号
  "$TARGET/backend/venv/bin/python" -c "import sys" 2>/dev/null \
    || err "包内 venv 无法迁移，请改用 --skip-venv 重新创建"
  ok "虚拟环境已就绪"
elif [[ $SKIP_VENV -eq 1 ]]; then
  warn "已跳过 venv（--skip-venv），请自行确保依赖已安装"
else
  log "---- 创建 Python 虚拟环境 ----"
  PY_BIN="python3"
  for cand in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
  done
  command -v "$PY_BIN" >/dev/null 2>&1 || { err "未找到 python3.10+"; exit 1; }
  [[ -x "$TARGET/backend/venv/bin/python" ]] \
    || "$PY_BIN" -m venv "$TARGET/backend/venv" \
    || { err "创建 venv 失败（可能需 apt install python3-venv）"; exit 1; }
  "$TARGET/backend/venv/bin/pip" install --upgrade pip --quiet
  "$TARGET/backend/venv/bin/pip" install -r "$TARGET/backend/requirements.txt" --quiet
  PY_VER="$("$TARGET/backend/venv/bin/python" -V 2>&1)"
  ok "依赖已安装（${PY_VER}）"
fi

# ---------- 4) 权限 ----------
chown -R aiassistant:aiassistant "$TARGET"/config "$TARGET"/data "$TARGET"/logs "$TARGET"/backend "$TARGET"/frontend
chmod 750 "$TARGET"/data "$TARGET"/logs
ok "权限已设置"

# ---------- 5) 密钥环境文件 ----------
if [[ ! -f "$GATEWAY_ENV_FILE" ]]; then
  if [[ -f "$PKG_DIR/deploy/gateway.env.example" ]]; then
    install -m 600 "$PKG_DIR/deploy/gateway.env.example" "$GATEWAY_ENV_FILE"
  else
    cat > "$GATEWAY_ENV_FILE" <<'ENVEOF'
AI_ASSISTANT_API_KEY=
AI_ASSISTANT_HOST=127.0.0.1
AI_ASSISTANT_PORT=8080
AI_ASSISTANT_PRODUCTION=true
AI_ASSISTANT_LOG_LEVEL=INFO
ENVEOF
    chmod 600 "$GATEWAY_ENV_FILE"
  fi
  ok "已创建 $GATEWAY_ENV_FILE（权限 600）"
  warn "请编辑该文件填入 AI_ASSISTANT_API_KEY 后重启服务"
else
  log "密钥环境文件已存在，保留原值"
fi

# ---------- 6) systemd ----------
if [[ $USE_SYSTEMD -eq 1 ]] && command -v systemctl >/dev/null 2>&1; then
  log "---- 注册 systemd 服务 ----"
  UNIT_SRC="$PKG_DIR/deploy/systemd/${SERVICE_NAME}.service"
  if [[ -f "$UNIT_SRC" ]]; then
    cp "$UNIT_SRC" "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    ok "服务已 enable（尚未启动：需先配置模型端点）"
  else
    warn "包内未找到 ${SERVICE_NAME}.service，跳过"
  fi
else
  warn "未注册 systemd（--no-systemd 或系统无 systemctl）"
fi

# ---------- 7) nginx ----------
if command -v nginx >/dev/null 2>&1 && [[ -f "$PKG_DIR/deploy/nginx.conf" ]]; then
  cp "$PKG_DIR/deploy/nginx.conf" /etc/nginx/conf.d/aiassistant.conf
  log "nginx 配置已写入 /etc/nginx/conf.d/aiassistant.conf（请改 server_name 后 reload）"
fi

ok "======================================================"
ok " 离线安装完成"
ok " 1. 配置密钥：sudo vi ${GATEWAY_ENV_FILE}"
ok " 2. 配置模型：编辑 ${TARGET}/config/models.local.yaml"
ok " 3. 启动服务：sudo systemctl restart ${SERVICE_NAME}"
ok " 4. 验证：curl http://127.0.0.1:8080/api/health"
ok " 5. 查看日志：journalctl -u ${SERVICE_NAME} -f"
ok "======================================================"

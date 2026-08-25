#!/usr/bin/env bash
# ============================================================================
# env-setup.sh —— AI 智能助手单机部署环境检测与安装脚本（Ubuntu 24.04）
#
# 用途：
#   检测并（可选）安装 AI 助手网关及其后端依赖的运行环境：
#     - docker         （推理服务容器化 / 沙箱执行器）
#     - nginx          （前端静态资源与反向代理）
#     - python3.11+ 与 venv（FastAPI 网关后端）
#     - nodejs/npm     （前端构建）
#     - git            （源码拉取与会话数据管理）
#
# 用法：
#   sudo bash env-setup.sh            # 检测并安装缺失的依赖（幂等）
#   sudo bash env-setp.sh --dry-run   # 仅打印“将要执行的动作”，不实际安装
#
# 特性：
#   - 幂等：已满足版本要求的组件直接 [SKIP]，不会重复安装
#   - 每个组件由独立函数封装“检测 / 安装”逻辑，便于维护与扩展
#   - --dry-run 模式仅输出 [DRY-RUN] 动作日志，绝不动系统
#   - 日志前缀： [INFO] 执行/说明  [SKIP] 已满足跳过  [DRY-RUN] 仅预览
# ============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# 全局状态与日志工具
# ---------------------------------------------------------------------------
DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY_RUN=1; fi

info()  { printf '\033[1;34m[INFO]\033[0m   %s\n' "$*"; }
ok()    { printf '\033[1;32m[OK]\033[0m     %s\n' "$*"; }
skip()  { printf '\033[1;33m[SKIP]\033[0m   %s\n' "$*"; }
warn()  { printf '\033[1;35m[WARN]\033[0m   %s\n' "$*"; }
dryrun(){ printf '\033[1;36m[DRY-RUN]\033[0m %s\n' "$*"; }

# 执行一条可能修改系统的命令；dry-run 模式只打印不执行
run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    dryrun "将执行: $*"
  else
    info "执行: $*"
    eval "$*"
  fi
}

# apt 更新标记：保证整个脚本只做一次 apt-get update
APT_UPDATED=0
apt_update() {
  if [[ $APT_UPDATED -eq 0 ]]; then
    run "sudo apt-get update -y"
    APT_UPDATED=1
  fi
}

# 幂等安装一组 apt 包（已满足的 SKIP，缺失的安装）
apt_ensure() {
  local pkg
  local need=()
  for pkg in "$@"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
      skip "apt 包已安装: $pkg"
    else
      need+=("$pkg")
    fi
  done
  if [[ ${#need[@]} -gt 0 ]]; then
    apt_update
    run "sudo apt-get install -y ${need[*]}"
    for pkg in "${need[@]}"; do ok "apt 包已安装: $pkg"; done
  fi
}

# ---------------------------------------------------------------------------
# 版本比较工具：返回 0 表示 $1 的版本 >= $2
# ---------------------------------------------------------------------------
ver_ge() {
  local a="$1" b="$2"
  # 提取形如 x.y.z 的主版本号，缺失位按 0 处理
  local a1 aa bb
  a1=$(echo "$a" | gsed -E 's/^[^0-9]*([0-9]+(\.[0-9]+)*).*/\1/' 2>/dev/null \
       || echo "$a" | sed -E 's/^[^0-9]*([0-9]+(\.[0-9]+)*).*/\1/')
  IFS='.' read -r -a aa <<<"$a1"
  IFS='.' read -r -a bb <<<"$b"
  for i in 0 1 2 3; do
    local x="${aa[$i]:-0}" y="${bb[$i]:-0}"
    (( 10#$x > 10#$y )) && return 0
    (( 10#$x < 10#$y )) && return 1
  done
  return 0
}

# ---------------------------------------------------------------------------
# 1) git
# ---------------------------------------------------------------------------
setup_git() {
  info "---- git ----"
  if command -v git >/dev/null 2>&1; then
    ok "git 已安装: $(git --version)"
  else
    apt_ensure git
    ok "git 已安装: $(git --version)"
  fi
}

# ---------------------------------------------------------------------------
# 2) python3.11+ 与 venv
# ---------------------------------------------------------------------------
setup_python() {
  info "---- python3 (>=3.11) 与 venv ----"
  local PY_MIN="3.11.0" PY_VER=""
  if command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null || echo "")
  fi
  if [[ -n "$PY_VER" ]] && ver_ge "$PY_VER" "$PY_MIN"; then
    ok "python3 $PY_VER 满足要求(>=${PY_MIN})"
  else
    warn "python3 缺失或版本过低(${PY_VER:-无})，安装 python3/pip/venv"
    apt_ensure python3 python3-pip python3-venv
    ok "python3 已安装: $(python3 --version)"
  fi
  # 校验 venv 模块可用
  if ! python3 -m venv --help >/dev/null 2>&1; then
    warn "venv 模块不可用，安装 python3.venv"
    apt_ensure python3.11-venv python3-venv
  else
    skip "python3 venv 模块可用"
  fi
}

# ---------------------------------------------------------------------------
# 3) nodejs / npm（前端构建）
# ---------------------------------------------------------------------------
setup_node() {
  info "---- nodejs / npm ----"
  local NODE_MIN="18.0.0" NODE_VER=""
  if command -v node >/dev/null 2>&1; then
    NODE_VER=$(node -v 2>/dev/null | sed 's/^v//')
  fi
  if [[ -n "$NODE_VER" ]] && ver_ge "$NODE_VER" "$NODE_MIN"; then
    ok "nodejs $NODE_VER 满足要求(>=${NODE_MIN})"
  else
    warn "nodejs 缺失或版本过低(${NODE_VER:-无})，通过 NodeSource 安装 LTS"
    apt_update
    run "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
    run "sudo apt-get install -y nodejs"
    ok "nodejs 已安装: $(node -v); npm: $(npm -v)"
  fi
  command -v npm >/dev/null 2>&1 && skip "npm 已可用: $(npm -v)" || apt_ensure npm
}

# ---------------------------------------------------------------------------
# 4) docker（>= 24）
# ---------------------------------------------------------------------------
setup_docker() {
  info "---- docker ----"
  local DOK_MIN="24.0.0" DOK_VER=""
  if command -v docker >/dev/null 2>&1; then
    DOK_VER=$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "")
  fi
  if [[ -n "$DOK_VER" ]] && ver_ge "$DOK_VER" "$DOK_MIN"; then
    ok "docker $DOK_VER 满足要求(>=${DOK_MIN})"
  elif [[ -n "$DOK_VER" ]]; then
    warn "docker $DOK_VER 低于 ${DOK_MIN}，安装 docker-ce 最新版"
    install_docker
  else
    warn "docker 未安装，开始安装 docker-ce"
    install_docker
  fi
  # 非 root 用户加入 docker 组（可选，便于开发调试）
  if command -v docker >/dev/null 2>&1; then
    if ! id -nG "${SUDO_USER:-$USER}" 2>/dev/null | grep -qw docker; then
      run "sudo usermod -aG docker ${SUDO_USER:-$USER}"
      warn "已将 ${SUDO_USER:-$USER} 加入 docker 组，请注销重登生效（当前会话可能仍需 sudo）"
    else
      skip "用户已在 docker 组"
    fi
  fi
}

install_docker() {
  apt_update
  run "sudo install -m 0755 -d /etc/apt/keyrings"
  run "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg"
  run "echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \$(. /etc/os-release && echo \$VERSION_CODENAME) stable\" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null"
  apt_update
  run "sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin"
  ok "docker 已安装: $(docker --version)"
}

# ---------------------------------------------------------------------------
# 5) nginx
# ---------------------------------------------------------------------------
setup_nginx() {
  info "---- nginx ----"
  if command -v nginx >/dev/null 2>&1; then
    ok "nginx 已安装: $(nginx -v 2>&1)"
  else
    warn "nginx 未安装，开始安装"
    apt_ensure nginx
    ok "nginx 已安装: $(nginx -v 2>&1)"
  fi
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
main() {
  if [[ $DRY_RUN -eq 1 ]]; then
    info "=================================================="
    info " dry-run 模式：仅检测并打印动作，不修改系统"
    info "=================================================="
  else
    info "=================================================="
    info " 开始检测与安装环境（Ubuntu 24.04）"
    info "=================================================="
  fi
  info "OS:  $(grep -m1 PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '\"')"
  info "用户: ${SUDO_USER:-$USER}"

  setup_git
  setup_python
  setup_node
  setup_docker
  setup_nginx

  info "=================================================="
  info " 环境检测/安装完成"
  info "=================================================="
  ok "git:       $(git --version 2>/dev/null || echo 未安装)"
  ok "python3:   $(python3 --version 2>/dev/null || echo 未安装)"
  ok "nodejs:    $(node -v 2>/dev/null || echo 未安装)  npm: $(npm -v 2>/dev/null || echo 未安装)"
  ok "docker:    $(docker --version 2>/dev/null || echo 未安装)"
  ok "nginx:     $(nginx -v 2>&1 || echo 未安装)"
  if [[ $DRY_RUN -eq 1 ]]; then
    info "dry-run 结束：以上动作为“预计执行”而非真实修改。"
    info "确认无误后请去掉 --dry-run 执行真实安装。"
  else
    info "全部依赖就绪。下一步请运行 deploy/install.sh 部署应用。"
  fi
}

main "$@"
# 安装与部署指南

Aurora AI 智能助手网关的单机部署手册，覆盖 **Linux**（systemd / Docker）与 **Windows**（PowerShell / NSSM / WSL2）两端。

- 部署方式选择：[快速决策](#快速决策)
- Linux 部署：[Docker](#一linux-部署) · [systemd](#二linux-部署systemd) · [离线 tarball](#二二linux-离线部署tarball-内网分发)
- Windows 部署：[一键脚本](#三windows-部署powershell-一键脚本) · [离线 zip](#三二windows-离线部署zip-分发) · [手动](#四windows-手动部署) · [WSL2](#五windows-部署wsl2)
- 通用：[反代与 HTTPS](#六反向代理与-https) · [配置](#七配置说明) · [升级卸载](#八升级与卸载) · [排错](#九常见问题排查)

---

## 快速决策

| 场景 | 推荐方式 | 耗时 |
|------|----------|------|
| Linux 单机，长期运行 | [systemd](#二linux-部署systemd) | 5-10 分钟 |
| Linux 快速试用 / 容器化 | [Docker](#一linux-部署) | 3 分钟 |
| Linux 内网机房 / 无外网 | [离线 tarball](#二二linux-离线部署tarball-内网分发) | 3 分钟 |
| Windows 单机，长期运行 | [PowerShell 一键脚本](#三windows-部署powershell-一键脚本) | 5 分钟 |
| Windows 内网 / 批量分发 | [离线 zip](#三二windows-离线部署zip-分发) | 3 分钟 |
| Windows 开发者 / 熟悉 Linux | [WSL2](#五windows-部署wsl2) | 5 分钟 |
| 需要 HTTPS 与域名 | 任一 + [反代](#六反向代理与-https) | +10 分钟 |

> **首次部署三步**：① 装依赖 → ② 装程序 → ③ 配密钥并启动。所有脚本均为幂等，重复执行即为**原地升级**，不会覆盖既有配置与会话数据。

---

## 一、Linux 部署（Docker）

最省事的方式：镜像内已内置前端产物，网关直接托管，无需额外 nginx。

### 1. 前置条件

Docker 20.10+ 与 Docker Compose v2：

```bash
docker --version && docker compose version
```

未安装请先运行 `sudo bash scripts/env-setup.sh`，或参考 <https://docs.docker.com/engine/install/>。

### 2. 构建与启动

```bash
git clone https://github.com/plxlaaa/aurora.git
cd aurora

# 复制环境变量模板并填入密钥
cp deploy/.env.example deploy/.env
vi deploy/.env            # 至少设置 AI_ASSISTANT_API_KEY

# 启动
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d
```

访问 <http://服务器IP:8080>。镜像内已托管前端，直接使用即可。

### 3. 验证

```bash
curl http://127.0.0.1:8080/api/health
# {"ok":true,"status":"healthy","ready":true,...}

docker compose -f deploy/docker-compose.yml ps    # 看健康状态
docker compose -f deploy/docker-compose.yml logs -f gateway
```

### 4. 常用运维命令

```bash
C="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"

$C logs -f gateway# 跟踪日志
$C restart gateway     # 重启
$C down                # 停止（保留数据卷）
$C pull && $C up -d -t # 拉取新镜像后滚动更新
$C exec gateway sh    # 进入容器排查
```

数据持久化在命名卷 `aurora_data`（会话、附件）与 `aurora_logs`；配置以**只读**方式挂载自宿主机 `config/`，修改配置后 `$C restart gateway` 生效。

---

## 二、Linux 部署（systemd）

适合生产长期运行，进程守护、日志、开机自启均由 systemd 管理。

### 1. 环境准备

```bash
sudo bash scripts/env-setup.sh          # 幂等；先看状态：--dry-run
sudo bash scripts/env-setup.sh --dry-run # 仅检测，不安装
```

安装内容：`python3.11+`、`nodejs`、`docker`、`nginx`、`git`、`rsync`。

### 2. 安装程序

```bash
sudo bash deploy/install.sh
```

脚本会：

1. 创建目录骨架与系统用户 `aiassistant`
2. 同步后端代码到 `/opt/aiassistant/backend`（排除 `venv`）
3. 创建虚拟环境并安装 `backend/requirements.txt`
4. 同步 `config/`（**保留**已有 `*.local.yaml` 密钥层）
5. 构建前端到 `/opt/aiassistant/frontend/dist`
6. 安装 systemd 服务与密钥环境文件 `/etc/aiassistant/gateway.env`
7. 写入 nginx 模板 `/etc/nginx/conf.d/aiassistant.conf`

常用参数：

```bash
sudo bash deploy/install.sh --skip-frontend   # 跳过前端构建（离线/仅后端）
sudo bash deploy/install.sh --uninstall      # 卸载（保留数据）
sudo bash deploy/install.sh --purge          # 卸载并删除全部数据
```

### 3. 配置密钥

```bash
sudo vi /etc/aiassistant/gateway.env
```

至少设置一项（否则任何人都可访问你的助手）：

```bash
# 生成强随机密钥
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

AI_ASSISTANT_API_KEY=<粘贴生成的密钥>
AI_ASSISTANT_JWT_SECRET=<粘贴另一个随机值>   # 保证重启后 token 仍有效
```

模型端点密钥写在**不入 git**的覆盖层：

```bash
sudo vi /opt/aiassistant/config/models.local.yaml
```

```yaml
endpoints:
  main:
    base_url: http://127.0.0.1:8001/v1
    api_key: sk-xxxxxxxx
    model_name: qwen2.5:7b-instruct
```

### 4. 启动服务

```bash
# 先启动模型服务（需先按规范书配置 deploy/systemd/vllm-*.service 的模型名/端口）
sudo systemctl start vllm-main vllm-vision vllm-compress
# 再启动网关
sudo systemctl restart aiassistant-gateway
```

### 5. 验证与日志

```bash
curl http://127.0.0.1:8080/api/health
sudo systemctl status aiassistant-gateway
sudo journalctl -u aiassistant-gateway -f          # 实时跟踪
sudo journalctl -u aiassistant-gateway --since today -p warning  # 仅看告警
```

### 6. 安全加固

```bash
# 仅放行 HTTP/HTTPS，8080 不对外开放（网关只监听 127.0.0.1）
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable

# 确认网关未暴露到公网
sudo ss -tlnp | grep 8080
# 应看到 127.0.0.1:8080 而非 0.0.0.0:8080
```

unit 文件已启用 systemd 沙箱加固（`ProtectSystem=strict`、`NoNewPrivileges`、内存写入禁用等），可写路径通过 `ReadWritePaths` 白名单限定。

### 7. 防火墙与升级辅助

```bash
# 安装时自动放行端口（仅在确需外部直连时使用）
sudo bash deploy/install.sh --with-firewall

# 仅检查是否有新版本，不改动任何文件
sudo bash deploy/install.sh --check-upgrade

# 安装并立即启动（脚本末尾自动做健康检查）
sudo bash deploy/install.sh --start
```

> `--with-firewall` 会同时识别 **ufw**（Debian/Ubuntu）与 **firewalld**（RHEL/CentOS/Fedora）；
> 卸载时（`--uninstall` / `--purge`）会自动清理对应的放行规则。

---

## 二之二、Linux 离线部署（tarball 内网分发）

适用于**无外网 / 内网机房 / 灰度分发**场景：在一台有网机器上打包，把tarball 拷到目标机后离线安装，目标机**不需要 git / npm / 网络**。

### 1. 在有网机器上打包

```bash
git clone https://github.com/plxlaaa/aurora.git && cd aurora
cd frontend && npm ci && npm run build && cd ..     # 先构建前端产物

# 打包（不含 venv，目标机自动创建）
bash scripts/package-tarball.sh

# 或：预建 venv + 自动构建前端（目标机免编译，适合批量部署）
bash scripts/package-tarball.sh --with-venv --with-frontend --version 1.1.0
```

产物位于 `dist/`：

```
dist/aurora-1.1.0-linux-amd64.tar.gz
dist/aurora-1.1.0-linux-amd64.tar.gz.sha256
```

包内已包含后端源码、前端 `dist`、配置模板、systemd unit、nginx 模板与一键安装脚本。

### 2. 拷贝到目标机并校验

```bash
scp dist/aurora-1.1.0-linux-amd64.tar.gz*  user@server:/tmp/
cd /tmp && sha256sum -c aurora-1.1.0-linux-amd64.tar.gz.sha256
# 必须显示 OK 再继续
```

### 3. 解压并安装

```bash
sudo tar -xzf aurora-1.1.0-linux-amd64.tar.gz -C /opt
sudo bash /opt/aurora-1.1.0/install-tarball.sh
```

安装脚本支持：

| 参数 | 说明 |
|------|------|
| `--skip-venv` | 跳过虚拟环境创建（包内已内置时使用） |
| `--no-systemd` | 不注册 systemd 服务（容器/无 systemctl 环境） |
| `--uninstall` | 卸载（保留数据目录） |
| `--purge` | 卸载并删除数据目录与密钥文件 |

```bash
# 常用组合
sudo bash /opt/aurora-1.1.0/install-tarball.sh --no-systemd   # 纯容器环境
sudo bash /opt/aurora-1.1.0/install-tarball.sh --purge        # 彻底卸载
```

### 4. 安装后配置密钥并启动

```bash
sudo vi /etc/aiassistant/gateway.env     # 填 AI_ASSISTANT_API_KEY
sudo systemctl restart aiassistant-gateway
curl http://127.0.0.1:8080/api/health
```

> **安全提示**：打包脚本会自动排除 `config/*.local.yaml`，确保真实 api_key 不会被打进分发包。

---

## 三、Windows 部署（PowerShell 一键脚本）

### 1. 前置条件

| 依赖 | 版本 | 安装命令 |
|------|------|----------|
| Python | 3.10+ | `winget install --id Python.Python.3.12 -e` |
| Node.js | 18+ | `winget install --id OpenJS.NodeJS.LTS -e` |
| NSSM（可选，注册服务用） | 2.24+ | `winget install NSSM.NSSM` |

> 安装 Python 时请勾选 **Add Python to PATH**，否则脚本无法检测。

### 2. 一键安装

在**管理员 PowerShell** 中执行（脚本会在检测到权限不足时自动弹窗提权）：

```powershell
# 基本安装：自动生成 API Key，并注册 Windows 服务
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -RegisterService
```

脚本完成：检测依赖 → 创建目录 → 建 venv 装依赖 → 构建前端 → 生成 `gateway.env` → 配置防火墙 → 注册服务（NSSM，自动重启策略）→ 冒烟健康检查。

### 3. 安装参数

```powershell
# 自定义端口与密钥，使用低权限账户运行服务
.\deploy\windows\install.ps1 -Port 9090 -ApiKey "your-secret" -RegisterService -ServiceUser ".\aurora_svc"

# 跳过前端构建（已有 dist，或前端由 IIS/nginx 托管）
.\deploy\windows\install.ps1 -SkipFrontend

# 安装但不注册服务（手动运行）
.\deploy\windows\install.ps1
```

完整参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `-InstallDir` | `%ProgramData%\Aurora` | 程序安装目录 |
| `-Port` | `8080` | 网关监听端口 |
| `-ApiKey` | 自动生成 | 鉴权密钥，留空则自动生成随机强密钥 |
| `-RegisterService` | 关闭 | 注册为 Windows 服务（开机自启、崩溃自动重启） |
| `-ServiceUser` | `LocalSystem` | 服务运行账户 |
| `-SkipFrontend` | 关闭 | 跳过前端构建 |
| `-Uninstall` | — | 卸载（保留数据） |
| `-PurgeData` | — | 与 `-Uninstall` 搭配，**删除全部数据** |

### 4. 服务管理

```powershell
Get-Service AuroraGateway                    # 查看状态
Restart-Service AuroraGateway                # 重启
Stop-Service AuroraGateway                   # 停止
Start-Service AuroraGateway                  # 启动
Get-Content "C:\ProgramData\Aurora\logs\service.log" -Tail 100   # 查看日志
```

崩溃后 NSSM 会在 3 秒后自动重启（最多重试 3 次）。

### 5. 验证

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/health
```

---

## 三之二、Windows 离线部署（zip 分发）

与 Linux tarball 对应：在有网机器上打包 zip，拷贝到内网机器离线安装，**目标机无需 git / npm**。

### 1. 在有网机器上打包

```powershell
cd aurora\frontend
npm ci; npm run build
cd ..

# 打包（复用已有 frontend\dist）
powershell -ExecutionPolicy Bypass -File scripts\package-windows.ps1

# 自动构建前端 + 指定版本与架构
powershell -ExecutionPolicy Bypass -File scripts\package-windows.ps1 -Version 1.1.0 -Arch x64 -WithFrontend
```

产物：

```
dist\aurora-1.1.0-windows-x64.zip
dist\aurora-1.1.0-windows-x64.zip.sha256
```

### 2. 目标机离线安装

```powershell
# 校验（PowerShell）
(Get-FileHash .\aurora-1.1.0-windows-x64.zip -Algorithm SHA256).Hash

# 解压后以管理员身份安装
Expand-Archive .\aurora-1.1.0-windows-x64.zip -DestinationPath C:\Temp\aurora
powershell -ExecutionPolicy Bypass -File C:\Temp\aurora\aurora-1.1.0-windows-x64\install.ps1 -RegisterService
```

### 3. winget / scoop 分发

仓库已内置分发清单，可直接提交到对应仓库或自建源：

| 包管理器 | 清单位置 | 包标识 |
|---------|---------|--------|
| winget | `deploy/windows/winget/plxlaaa.aurora.yaml` | `plxlaaa.aurora` |
| scoop | `deploy/windows/scoop/plxlaaa-aurora.json` | `aurora` |

```powershell
# scoop（自建bucket 后）
scoop bucket add aurora https://github.com/plxlaaa/aurora
scoop install aurora

# winget（发布到 Releases 后）
winget install plxlaaa.aurora
```

> 清单中的 `DownloadUrl` 需指向 GitHub Release 资产；使用前请把版本号与下载地址替换为实际发布值。

---

## 四、Windows 手动部署

适合需要精细控制的场景。

```powershell
# 1. 目录
$root = "C:\ProgramData\Aurora"
New-Item -ItemType Directory -Force -Path "$root\backend","$root\config","$root\data\sessions","$root\data\uploads","$root\logs" | Out-Null

# 2. 后端依赖
git clone https://github.com/plxlaaa/aurora.git C:\src\aurora
Copy-Item -Recurse C:\src\aurora\backend\app "$root\backend"
Copy-Item C:\src\aurora\backend\requirements.txt "$root\backend\"
cd "$root\backend"
python -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt

# 3. 前端构建
cd C:\src\aurora\frontend
npm ci
npm run build
Copy-Item -Recurse dist "$root\frontend"

# 4. 配置与密钥
Copy-Item -Recurse C:\src\aurora\config\* "$root\config"
# 编辑 "$root\gateway.env"：至少设置 AI_ASSISTANT_API_KEY

# 5. 前台运行（调试用）
cd "$root\backend"
$env:AI_ASSISTANT_CONFIG_DIR="$root\config"
$env:AI_ASSISTANT_DATA_DIR="$root\data\sessions"
$env:AI_ASSISTANT_UPLOAD_DIR="$root\data\uploads"
$env:AI_ASSISTANT_API_KEY="<你的密钥>"
.\venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8080
```

生产环境请用 [install.ps1 -RegisterService](#三windows-部署powershell-一键脚本) 注册为服务。

### 用 sc.exe 注册服务（不依赖 NSSM）

NSSM 不可用时，可用Windows 自带的 `sc.exe create`直接注册（注意：不具备崩溃自动重启能力）：

```powershell
# 生成包装脚本加载环境变量
$launcher = @"
@echo off
for /f "usebackq tokens=1,* delims==" %%a in ("C:\ProgramData\Aurora\gateway.env") do (
    if not "%%a"=="" set "%%a=%%b"
)
cd /d C:\ProgramData\Aurora\backend
C:\ProgramData\Aurora\backend\venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8080
"@
Set-Content -Path "C:\ProgramData\Aurora\run-gateway.cmd" -Value $launcher -Encoding ASCII

sc.exe create AuroraGateway binPath= "C:\ProgramData\Aurora\run-gateway.cmd" start= auto
sc.exe description AuroraGateway "Aurora AI 助手网关"
sc.exe failure AuroraGateway reset= 86400 actions= restart/5000/restart/15000/restart/30000
sc.exe start AuroraGateway
```

> `binPath=` 与 `start=` 后**必须有一个空格**，这是 `sc.exe` 的语法要求。

---

## 五、Windows 部署（WSL2）

适合 Windows 上的开发者，或需与 Linux 生态（Docker、vLLM）无缝协作的场景。

### 1. 启用 WSL2

```powershell
wsl --install -d Ubuntu-22.04
wsl --set-default-version 2
```

### 2. 在 WSL 内按Linux 方式部署

**方式 A：一键脚本（推荐）**

仓库提供 WSL 专用安装脚本，会自动配置 `0.0.0.0` 监听以便 Windows 宿主机访问。入口有两个，按你的调用位置选择：

```powershell
# 入口 1：在 Windows PowerShell 中执行（自动检查发行版并转调 WSL）
#         需先把仓库克隆/解压到 Windows 可访问的路径
wsl -d Ubuntu-22.04 -- bash -lc "cd /mnt/c/path/to/aurora && sudo bash deploy/wsl2/install-wsl2-linux.sh"
```

```bash
# 入口 2：直接进入 WSL 终端执行（路径为 WSL 内的真实路径）
cd ~/aurora
sudo bash deploy/wsl2/install-wsl2-linux.sh

# 跳过前端构建 / 自定义端口
sudo bash deploy/wsl2/install-wsl2-linux.sh --skip-frontend --port 9090

# 卸载（--purge 一并删除数据）
sudo bash deploy/wsl2/install-wsl2-linux.sh --uninstall
```

> Windows 侧还提供了 PowerShell 包装器 `deploy/wsl2/install-wsl2.ps1`，它会自动检测发行版、
> 校验 WSL 是否可用并拼接参数，适合从 Windows 直接调用：
> ```powershell
> powershell -ExecutionPolicy Bypass -File deploy\wsl2\install-wsl2.ps1 -Distro Ubuntu-22.04
> ```

**方式 B：完全按 Linux 方式**

```bash
# WSL 内执行（等同 Linux systemd 部署）
sudo apt update && sudo apt install -y python3-venv python3-pip nodejs npm git
git clone https://github.com/plxlaaa/aurora.git && cd aurora
sudo bash deploy/install.sh
sudo systemctl restart aiassistant-gateway
curl http://127.0.0.1:8080/api/health
```

> WSL 脚本相比通用 `install.sh` 的差异：不强制创建 `aiassistant` 系统用户，并把 `AI_ASSISTANT_HOST` 设为 `0.0.0.0`，否则 Windows 侧无法通过 localhost 访问。

### 3. 从 Windows 访问

WSL2 有独立网络栈，需端口转发或用 WSL 的 localhost 透传：

```powershell
# WSL2 默认支持 localhost 透传，直接访问即可
Start-Process http://localhost:8080
```

若需局域网访问，在 WSL 内设置端口转发规则，或改用 Docker 方案后用 Windows 侧反代。

> **GPU 注意事项**：Windows 上跑 vLLM 建议使用 **WSL2 + CUDA on Windows**，而非原生 Windows。驱动需为 CUDA 兼容版本（NVIDIA 官方提供 WSL 支持）。不使用 GPU 时（接入云端 API），WSL2 完全够用。

### 4. 开机自启（可选）

在 WSL 内用 `systemd` + Windows 任务计划程序：

```powershell
# Windows 侧：创建开机任务执行 wsl 中的 systemd 服务
$action  = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu-22.04 -u root /usr/bin/systemctl start aiassistant-gateway"
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName "AuroraGateway" -Action $action -Trigger $trigger
```

---

## 六、反向代理与 HTTPS

网关默认只监听 `127.0.0.1:8080`，**必须**经反向代理对外提供服务。

### Linux（nginx）

仓库已提供模板：

```bash
sudo cp deploy/nginx.conf /etc/nginx/conf.d/aiassistant.conf
sudo vi /etc/nginx/conf.d/aiassistant.conf   # 改server_name 为真实域名
sudo nginx -t && sudo systemctl reload nginx
```

HTTPS（certbot 自动签发并配置）：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d ai.example.com
```

模板已包含 WebSocket（`/api/chat`）升级头与 3600s 长超时，**无需**手工补充。

### Windows（IIS）

需先安装 [URL Rewrite](https://www.iis.net/downloads/microsoft/url-rewrite) 与 [ARR](https://www.iis.net/downloads/microsoft/application-request-routing)：

1. IIS 管理器 → 新建网站 → 物理路径留空（或指向 `C:\ProgramData\Aurora\frontend\dist`）
2. **Application Request Routing** → Server Proxy Settings → 填 `127.0.0.1:8080`
3. 为站点添加 `web.config` 启用 WebSocket 代理：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <httpProtocol>
      <customHeaders>
        <add name="X-Forwarded-Proto" value="https" />
      </customHeaders>
    </httpProtocol>
    <rewrite>
      <rules>
        <rule name="WebSocket proxy" stopProcessing="true">
          <match url="(.*)" />
          <conditions>
            <add input="{HTTP_X_UPGRADE}" pattern="websocket" ignoreCase="true" />
          </conditions>
          <action type="Rewrite"
                  url="http://127.0.0.1:8080/{R:1}"
                  appendQueryString="true" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
```

> **跨域提醒**：若前端与网关不同源，需在 `gateway.env` / `.env` 中设置 `AI_ASSISTANT_CORS_ORIGINS=https://你的域名`，否则浏览器会拦截 `/api` 请求。

---

## 七、配置说明

### 环境变量（两端通用）

| 变量 | 默认 | 说明 |
|------|------|------|
| `AI_ASSISTANT_API_KEY` | 空 | 设为非空即启用鉴权，**生产必填** |
| `AI_ASSISTANT_JWT_SECRET` | 由 API Key 派生 | 显式设置可保证重启后 token 有效 |
| `AI_ASSISTANT_HOST` / `_PORT` | `127.0.0.1` / `8080` | 监听地址与端口 |
| `AI_ASSISTANT_CONFIG_DIR` | `/opt/aiassistant/config` | 配置目录 |
| `AI_ASSISTANT_DATA_DIR` | `.../data/sessions` | 会话存储目录 |
| `AI_ASSISTANT_UPLOAD_DIR` | `.../data/uploads` | 附件目录 |
| `AI_ASSISTANT_FRONTEND_DIR` | 空 | 设置后网关直接托管前端（免 nginx 静态站点） |
| `AI_ASSISTANT_PRODUCTION` | `false` | `true` 时关闭 `/docs` 与 `/openapi.json` |
| `AI_ASSISTANT_CORS_ORIGINS` | `http://localhost:5173` | 允许的跨域来源，逗号分隔；同源部署留空 |
| `AI_ASSISTANT_MAX_CONCURRENT_GENERATIONS` | `3` | 全局并发生成上限 |
| `AI_ASSISTANT_MAX_BODY_MB` | `24` | 单请求体上限（MB） |
| `AI_ASSISTANT_TRUST_PROXY` | `true` | 是否采信 `X-Forwarded-For`（反代在前时保持 true） |
| `AI_ASSISTANT_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `AI_ASSISTANT_LOG_JSON` | `false` | `true` 时输出 JSON 结构化日志（含自动注入的 `request_id`），便于 Loki / ES 采集 |
| `AI_ASSISTANT_SLOW_REQUEST_MS` | `30000` | 慢请求告警阈值（毫秒），超过即打 WARNING |

> `AI_ASSISTANT_SLOW_REQUEST_MS` 与 `AI_ASSISTANT_LOG_JSON` 从 **v1.2.0** 起真正生效
> （此前已定义但未下发到中间件 / 未实现），升级后可通过日志确认配置已应用。

开启 JSON 日志后的输出示例：

```json
{"ts":"2026-10-07T02:04:17.725813+00:00","level":"INFO","logger":"aiassistant.http","msg":"GET /api/health -> 200 3ms rid=9c54d883e27c4a5c ip=testclient","request_id":"9c54d883e27c4a5c"}
```

完整清单见 [`deploy/.env.example`](deploy/.env.example)；systemd 专用模板见 [`deploy/gateway.env.example`](deploy/gateway.env.example)。

### 配置文件分层

```
config/
├── models.yaml           # 入库模板：端点结构、视觉路由、压缩参数
├── models.local.yaml     # 本机私有：真实 api_key / base_url（不入 git）
├── sandbox.yaml          # 沙箱 / local 执行环境
└── mcp.yaml              # MCP server 声明
```

`.local.yaml` 会**深合并**覆盖同名 `.yaml` 的字段，因此真实密钥无需写进模板文件，也不会随代码分发。

### 防火墙与端口

| 端口 | 用途 | 是否需对外开放 |
|------|------|----------------|
| 8080 | 网关（只监听 127.0.0.1） | 否 |
| 80 / 443 | nginx / IIS | 是 |
| 8001 / 8002 / 8003 | vLLM 主/视觉/压缩（只监听 127.0.0.1） | 否 |

---

## 八、升级与卸载

### 升级

```bash
# Linux systemd
cd /path/to/aurora && git pull
sudo bash deploy/install.sh          # 幂等：同步代码、装依赖、重编前端、重装 unit
sudo systemctl restart aiassistant-gateway
```

```powershell
# Windows
cd C:\src\aurora; git pull
.\deploy\windows\install.ps1 -RegisterService     # 幂等升级
Restart-Service AuroraGateway
```

升级不会覆盖：`config/*.local.yaml`、`gateway.env`、会话与附件数据。

### 卸载

```bash
sudo bash deploy/install.sh --uninstall   # 保留 /opt/aiassistant 数据
sudo bash deploy/install.sh --purge       # 连同数据一并删除
```

```powershell
.\deploy\windows\install.ps1 -Uninstall           # 保留 %ProgramData%\Aurora
.\deploy\windows\install.ps1 -Uninstall -PurgeData # 连同数据删除
```

---

## 九、常见问题排查

### 网关起不来 / 502

```bash
sudo systemctl status aiassistant-gateway
sudo journalctl -u aiassistant-gateway -n 100 --no-pager
curl -v http://127.0.0.1:8080/api/health     # 端口是否在监听
```

- `Address already in use` → 端口被占用：`sudo ss -tlnp | grep 8080`
- `Permission denied` 写配置 → `sudo chown -R aiassistant:aiassistant /opt/aiassistant`

### 对话报「模型端点不可用」

```bash
curl http://127.0.0.1:8080/api/ready    # 查看 endpoints_available
```

- `ready: false` → 模型服务未起或 `base_url` / `api_key` 配错
- 检查 `config/models.local.yaml` 的 `base_url` 是否与 vLLM 实际监听地址端口一致（默认 8001/8002/8003）
- 手动验证端点：`curl -H "Authorization: Bearer <key>" http://127.0.0.1:8001/v1/models`

### 前端空白 / 404

- 确认前端已构建：`ls /opt/aiassistant/frontend/dist/index.html`
- 未走 nginx 静态站点时，设置 `AI_ASSISTANT_FRONTEND_DIR=/opt/aiassistant/frontend/dist` 让网关直接托管
- nginx 场景检查 `root` 指向与 `try_files` 回退规则

### WebSocket 聊天频繁断开

nginx 需保证升级头与长超时，仓库模板已配置。若自行修改过nginx，请确认：

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 3600s;
```

### 上传大图失败（413）

默认请求体上限 24MB（覆盖 10MB 图片的 base64 膨胀）。调整：

```bash
AI_ASSISTANT_MAX_BODY_MB=48   # 写入 gateway.env 后重启
```

### 登录提示「尝试过于频繁」

登录失败 5 次触发 5 分钟锁定（按来源 IP）。等待或清除失败计数后重启网关。

### Windows 服务无法启动

```powershell
Get-Content "C:\ProgramData\Aurora\logs\service.log" -Tail 100
# 检查环境变量文件
Get-Content C:\ProgramData\Aurora\gateway.env
```

- `uvicorn.exe not found` → venv 未创建，重跑 `install.ps1`
- `Access denied` → 服务账户对安装目录无权限，用 `-ServiceUser` 指定有权限的账户

### 端口冲突

```bash
# Linux
sudo ss -tlnp | grep 8080
# Windows
Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue
```

### 开启调试日志

```bash
# 临时
sudo systemctl edit aiassistant-gateway   # 追加 Environment=AI_ASSISTANT_LOG_LEVEL=DEBUG
sudo systemctl restart aiassistant-gateway
```

---

## 附：环境自检清单

部署完成后逐项确认：

- [ ] `curl /api/health` 返回 `ok: true`
- [ ] `curl /api/ready` 中 `ready: true`（模型端点已连通）
- [ ] `AI_ASSISTANT_API_KEY` 已设置且**未**使用默认值
- [ ] `ss -tlnp | grep 8080` 显示绑定在 `127.0.0.1`
- [ ] 防火墙未对公网放行 8080
- [ ] 生产环境 `AI_ASSISTANT_PRODUCTION=true`（`/docs` 不可访问）
- [ ] `config/*.local.yaml` 与 `gateway.env` 权限为 600且未被提交到 git
- [ ] 服务已设为开机自启（`systemctl is-enabled` / `Get-Service`）
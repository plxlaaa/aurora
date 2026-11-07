# AI 智能助手（浏览器 Web UI）

依据《[docs/AI助手部署与实现规范书.md](docs/AI助手部署与实现规范书.md)》实现的单机部署版浏览器Web UI 智能助手。

前后端分离：FastAPI 网关 + Vue 3 单页应用，支持多轮对话、视觉理解、上下文压缩、Skills、MCP、沙箱/local 可切换执行环境。**Linux与 Windows 均可一键部署**。

## 功能特性

| 能力 | 说明 |
|------|------|
| 多轮对话 | WebSocket 流式输出，支持中断（cancel）与重新生成 |
| 视觉理解 | 图片上传 + 魔数校验，可配置视觉路由策略 |
| 上下文压缩 | 超过阈值自动 map_reduce 压缩，保留系统提示与最近 K 轮 |
| Skills | 目录摘要 + 按需读取 + 执行，支持 zip 导入 |
| MCP 集成 | 支持 stdio / http / sse 三种传输的 server 管理 |
| 自定义工具 | HTTP 与 Python 两类工具，可在设置页维护与测试 |
| 执行环境 |沙箱（Docker 隔离）/ local 可切换 |
| 会话管理 | 持久化、重命名、归档、导出（Markdown / JSON） |
| 用量统计 | 内置 stats 插件 + 生命周期钩子扩展点 |
| 应用内自更新 | 设置页一键升级：多渠道下载（加速镜像优先、失败自动切换、官方兜底）、自动备份与失败回滚 |
| 统一鉴权 | JWT / API Key，登录失败按 IP 限流 |
| 可观测性 | 结构化 JSON 日志（自动注入 request_id）、慢请求告警、健康/就绪探针 |
| 数据可靠性 | 会话原子写入（断电不损坏历史）、孤儿索引回收、WebSocket 心跳保活 |
| 部署友好 | systemd / Docker / Windows 服务 / WSL2 / 离线包一键安装 |

## 快速开始

### 方式一：Docker（最省事）

```bash
git clone https://github.com/plxlaaa/aurora.git && cd aurora
cp deploy/.env.example deploy/.env && vi deploy/.env      # 填 AI_ASSISTANT_API_KEY
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d
```

访问 <http://服务器IP:8080>，镜像内已托管前端，无需额外 nginx。

### 方式二：Linux systemd

```bash
sudo bash scripts/env-setup.sh     # 依赖准备（幂等，可先 --dry-run）
sudo bash deploy/install.sh        # 安装（幂等，重复执行即升级）
sudo vi /etc/aiassistant/gateway.env   # 填入 AI_ASSISTANT_API_KEY
sudo systemctl restart aiassistant-gateway
```

### 方式三：Windows

在管理员 PowerShell 中：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -RegisterService
```

### 方式四：离线包（内网 / 无外网）

在一台有网机器上打包，拷贝到目标机离线安装（目标机**无需 git / npm / 网络**）：

```bash
# Linux：产出 dist/aurora-<版本>-linux-<arch>.tar.gz
cd frontend && npm ci && npm run build && cd ..
bash scripts/package-tarball.sh
sudo tar -xzf aurora-1.3.0-linux-amd64.tar.gz -C /opt
sudo bash /opt/aurora-1.3.0/install-tarball.sh
```

```powershell
# Windows：产出 dist\aurora-<版本>-windows-x64.zip
powershell -ExecutionPolicy Bypass -File scripts\package-windows.ps1
```

### 方式五：Windows WSL2

```powershell
# 在 WSL 内执行（自动配置 0.0.0.0 监听，便于 Windows 侧 localhost 访问）
wsl -d Ubuntu-22.04 -- bash -lc "cd ~/aurora && sudo bash deploy/wsl2/install-wsl2-linux.sh"
```

> 完整部署手册（含反向代理、HTTPS、WSL2、升级卸载与排错）见 **[INSTALL.md](INSTALL.md)**。

## 目录结构

```
├── README.md                      # 项目说明（本文件）
├── INSTALL.md                     # 安装部署手册（Linux / Windows）
├── CHANGELOG.md                   # 版本变更记录
├── CONTRIBUTING.md                # 贡献指南
├── dev.sh                         # 本地一键启动后端脚本
├── docs/
│   ├── AI助手部署与实现规范书.md    # 部署与实现规范
│   └── API.md                     # 接口参考（REST / WebSocket）
├── scripts/
│   ├── env-setup.sh               # 环境自动检测与安装（幂等，支持 --dry-run）
│   ├── package-tarball.sh         # 构建 Linux 离线安装包（tar.gz + SHA256）
│   ├── install-tarball.sh         # 离线包内的一键安装脚本（随包分发）
│   └── package-windows.ps1        # 构建 Windows 离线安装包（zip + SHA256）
├── config/                        # 运行时配置（可热刷新）
│   ├── models.yaml                # 主/视觉/压缩端点 + 视觉路由策略 + 压缩参数
│   ├── sandbox.yaml               # sandbox/local 执行环境
│   ├── mcp.yaml                   # MCP server 声明
│   └── skills/                    # Skill 清单（weather / web_fetch）
├── backend/                       # FastAPI 网关
│   ├── app/
│   │   ├── main.py                # ASGI 入口（中间件、异常处理、静态托管）
│   │   ├── core/                  # 基础设施层
│   │   │   ├── config.py          #   配置加载与热刷新
│   │   │   ├── runtime.py         #   环境变量运行参数
│   │   │   ├── errors.py          #   统一异常与错误码
│   │   │   ├── middleware.py      #   请求 ID / 安全头 / 限额 / gzip
│   │   │   ├── logsetup.py        #   日志初始化（文本 / JSON 双格式）
│   │   │   ├── auth.py            #   JWT/API Key 鉴权
│   │   │   └── hooks.py           #   生命周期事件总线
│   │   ├── services/              # 业务服务层
│   │   │   ├── llm.py             #   OpenAI 兼容客户端 + 端点探测
│   │   │   ├── vision.py          #   视觉路由决策
│   │   │   ├── compress.py        #   上下文压缩（map_reduce）
│   │   │   ├── chat.py            #   对话编排（视觉路由/压缩/工具循环）
│   │   │   ├── skills.py          #   Skills 框架
│   │   │   ├── mcp_client.py      #   MCP 客户端集成
│   │   │   ├── custom_tools.py    #   自定义工具（HTTP / Python）
│   │   │   ├── sandbox.py         #   沙箱/local 执行器
│   │   │   ├── sessions.py        #   会话持久化（增量缓存 + 分页）
│   │   │   ├── upload.py          #   附件上传
│   │   │   └── tool_registry.py   #   工具注册与聚合
│   │   └── routers/               # API 路由层
│   │       ├── deps.py            #   共享单例装配 + 启停逻辑
│   │       └── api.py             #   REST + WebSocket 端点
│   ├── tests/                     # 单元与集成测试（pytest，321 项）
│   │   ├── test_middleware_gzip.py #  gzip 多块响应压缩回归（原始 ASGI 层验证）
│   │   ├── test_static_mount.py    #  静态托管与路径穿越回归
│   │   ├── test_sessions_durability.py #  会话原子写与孤儿回收
│   │   └── test_ws_and_login_guard.py  #  WebSocket 心跳与登录限流清理
│   └── requirements.txt
├── frontend/                      # Vue 3 单页应用（Vite）
│   ├── package.json / vite.config.js / index.html
│   └── src/{main.js, App.vue, api/, router/, views/, components/}
└── deploy/
    ├── install.sh                 # Linux 安装 / 升级 / 卸载（防火墙、健康检查、journald/logrotate）
    ├── windows/install.ps1        # Windows 一键安装（NSSM 服务、-HealthRetries、-PublicPort）
    ├── windows/winget/            # winget 分发清单
    ├── windows/scoop/             # scoop 分发清单
    ├── wsl2/                      # WSL2 部署（PowerShell 包装器 + Linux 侧脚本）
    ├── docker/Dockerfile          # 多阶段构建
    ├── docker-compose.yml         # 容器编排
    ├── .env.example               # 跨平台环境变量模板
    ├── gateway.env.example        # systemd 密钥文件模板
    ├── systemd/                   # 网关 + vLLM 主/视觉/压缩 unit
    └── nginx.conf                 # 反代 + WebSocket + HTTPS 模板
```

运行时数据目录 `data/`（sessions / uploads / archive）由 `.gitignore` 忽略，不随仓库提交。

## 本地开发

后端（默认监听 127.0.0.1:8080）：

```bash
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
bash ../dev.sh        # 已注入 config/data/skills 环境变量，等价于：
# AI_ASSISTANT_CONFIG_DIR=<repo>/config \
# AI_ASSISTANT_DATA_DIR=<repo>/data/sessions \
# AI_ASSISTANT_SKILLS_DIR=<repo>/config/skills \
# AI_ASSISTANT_UPLOAD_DIR=<repo>/data/uploads \
# venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
```

前端（Vite dev server 端口 5173，`/api` 反向代理到后端）：

```bash
cd frontend
npm install
npm run dev
```

测试：

```bash
cd backend && venv/bin/python -m pytest tests/ -q         # 321 项
```

## 配置说明

### 模型端点

均OpenAI 兼容。`models.endpoints.*.base_url / model_name / api_key` 可在设置页修改并热刷新。

真实密钥建议只写在**本机覆盖层** `config/models.local.yaml`（深合并、不入 git）：

```yaml
endpoints:
  main:
    base_url: http://127.0.0.1:8001/v1
    api_key: sk-xxxxxxxx
    model_name: qwen2.5:7b-instruct
```

### 视觉路由策略（`vision_routing.policy`）

| 策略 | 行为 |
|------|------|
| `prefer_vision_model` | 优先独立视觉模型（默认） |
| `prefer_main_if_multimodal` | 主模型多模态则复用主模型 |
| `vision_model_only` | 始终使用独立视觉模型 |

### 上下文压缩

token 数超过 `threshold * context_window` 时自动触发，也可在设置页或 `POST /api/compress` 手动触发；保留系统提示与最近 K 轮原文。

### 执行环境

`sandbox`（Docker 隔离，默认）或 `local`（需显式 `allow=true`）。

### 鉴权

设置 `AI_ASSISTANT_API_KEY` 后启用。除 `/api/health` 与 `/api/ready` 外，所有 REST 端点（含会话管理）与 WebSocket 均要求 `Authorization: Bearer <api_key | JWT>`；WebSocket 亦可用 `?token=` 查询参数。登录失败按来源 IP 限流（5 次/ 60 秒 → 锁定 5 分钟）。

> **生产环境务必设置 `AI_ASSISTANT_API_KEY` 与 `AI_ASSISTANT_JWT_SECRET`**，并保持 `AI_ASSISTANT_PRODUCTION=true`（关闭 `/docs`）。

### 关键环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `AI_ASSISTANT_API_KEY` | 空 | 设为非空即启用鉴权 |
| `AI_ASSISTANT_JWT_SECRET` | 由 API Key 派生 | 显式设置可保证重启后 token 有效 |
| `AI_ASSISTANT_HOST` / `_PORT` | `127.0.0.1` / `8080` | 监听地址与端口 |
| `AI_ASSISTANT_FRONTEND_DIR` | 空 | 设置后网关直接托管前端（免 nginx 静态站点） |
| `AI_ASSISTANT_PRODUCTION` | `false` | `true` 关闭 API 文档 |
| `AI_ASSISTANT_MAX_CONCURRENT_GENERATIONS` | `3` | 全局并发生成上限 |
| `AI_ASSISTANT_MAX_BODY_MB` | `24` | 单请求体上限 |
| `AI_ASSISTANT_CORS_ORIGINS` | `http://localhost:5173` | 跨域白名单（逗号分隔） |
| `AI_ASSISTANT_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `AI_ASSISTANT_LOG_JSON` | `false` | `true` 输出 JSON 结构化日志（自动注入 `request_id`） |
| `AI_ASSISTANT_SLOW_REQUEST_MS` | `30000` | 慢请求告警阈值（毫秒） |
| `AI_ASSISTANT_TRUST_PROXY` | `true` | 是否采信 `X-Forwarded-For`（反代在前时保持 true） |

完整清单见 [`deploy/.env.example`](deploy/.env.example)。

### 可观测性

```bash
# 结构化日志（每行一条 JSON，含 ts/level/logger/msg/request_id）
export AI_ASSISTANT_LOG_JSON=true

# 只看慢请求与告警
sudo journalctl -u aiassistant-gateway --since today -p warning
```

`request_id` 由中间件统一分配（透传上游 `X-Request-ID`），并通过上下文自动附加到该请求内的
所有业务日志，访问日志与业务日志可按同一 trace id 串联排查。

### 数据可靠性与长连接

- **会话原子写入**：会话 JSONL 与状态 / 标题索引均采用「临时文件 → `fsync` → `os.replace`」原子替换，
  写入中断或磁盘写满时**保留原文件**，不会损坏已有会话历史。
- **孤儿索引回收**：会话删除后，`_status` / `_titles` 中的残留条目会随下一次列表查询自动回收，
  长时间运行不会内存与索引文件无界增长；运行中的会话不受影响。
- **WebSocket 心跳保活**：`/api/chat` 空闲超过 **300 秒**（`WS_IDLE_TIMEOUT`）服务端会以
  `close(1001)` 主动回收连接，避免对端异常断网导致的推理资源泄漏。客户端可周期性发送
  `{"type":"ping"}` 刷新计时器（服务端回 `pong`）。
- **响应压缩安全**：gzip 中间件对分块响应**整体缓冲后统一决策**，超过 8 MiB 的响应直接放弃压缩，
  既避免内存放大，也不会出现「头声明 gzip 但首块未压缩」的损坏响应。

### 日志治理

安装脚本会自动配置日志容量上限，避免长期运行后磁盘被日志占满：

| 平台 | 配置 | 效果 |
|------|------|------|
| Linux（systemd） | `/etc/systemd/journald.conf.d/99-aiassistant.conf` | `SystemMaxUse=500M`、保留 2 周 |
| Linux（文件日志） | `/etc/logrotate.d/aiassistant` | 每日轮转、保留 14 份、`copytruncate` |
| Windows | NSSM stdout/stderr → `logs\gateway.log` | 建议配合计划任务清理 |

卸载时（`--uninstall` / `--purge`）上述配置会一并清理。手动调整方式：

```bash
sudo vi /etc/systemd/journald.conf.d/99-aiassistant.conf && sudo systemctl restart systemd-journald
sudo vi /etc/logrotate.d/aiassistant && sudo logrotate -d /etc/logrotate.d/aiassistant
```

## API 摘要

完整接口参考（含参数、错误码、WebSocket 协议）见 **[docs/API.md](docs/API.md)**。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/health | 健康检查（公开） |
| GET | /api/ready | 就绪详情（公开） |
| GET/PUT | /api/models | 模型端点配置与探测 |
| GET/PUT | /api/models/catalog | 模型目录 |
| GET/PUT | /api/sandbox | 执行环境配置 |
| GET | /api/tools | 工具中心快照 |
| GET/POST/PUT/DELETE | /api/tools/custom[/{name}] | 自定义工具管理 |
| GET | /api/skills | Skill 列表 |
| PUT/DELETE | /api/skills/{name} | 启停 / 删除 Skill |
| POST | /api/skills/upload | zip 导入 Skill |
| GET/PUT | /api/mcp/servers[/{name}] | MCP server 管理 |
| GET | /api/mcp | MCP 连接状态 |
| POST | /api/upload | 附件上传 |
| GET | /api/files/{object_id} | 附件读取 |
| POST | /api/run-code | 执行代码（沙箱 / local） |
| POST | /api/compress | 手动触发压缩 |
| GET/POST | /api/sessions | 会话列表（支持 limit/offset/q 分页）/ 新建 |
| GET/DELETE | /api/sessions/{id} | 读取 / 删除会话 |
| POST | /api/sessions/{id}/clear | 清空会话消息 |
| POST | /api/sessions/{id}/archive | 归档会话 |
| POST | /api/sessions/{id}/rename | 重命名会话 |
| GET | /api/sessions/{id}/export | 导出 Markdown / JSON |
| POST | /api/auth/login | 凭据换取 JWT |
| GET | /api/stats | 用量统计与钩子清单 |
| WS | /api/chat | 对话（流式） |

### 错误响应格式

所有错误统一返回 `detail` + 稳定错误码 `code`，并附带 `request_id`（与响应头 `X-Request-ID` 一致，便于日志检索）：

```json
{
  "detail": "会话不存在",
  "code": "not_found",
  "request_id": "795ef0a9061b4925"
}
```

错误码：`validation_error`(400/422)、`unauthorized`(401)、`forbidden`(403)、`not_found`(404)、`conflict`(409)、`session_busy`(409)、`payload_too_large`(413)、`rate_limited`(429)、`internal_error`(500)、`upstream_error`(502)、`service_unavailable`(503)。

## 运维要点

```bash
# 健康与就绪
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/ready

# 实时日志（含 request_id 与耗时）
sudo journalctl -u aiassistant-gateway -f

# 常见运维命令
sudo systemctl restart aiassistant-gateway     # 重启
sudo systemctl status aiassistant-gateway      # 状态
docker compose -f deploy/docker-compose.yml logs -f gateway   # 容器日志
```

## 安全建议

1. **务必配置** `AI_ASSISTANT_API_KEY`（生产环境）；
2. 设置 `AI_ASSISTANT_PRODUCTION=true` 关闭 `/docs` 与 `/openapi.json`；
3. 网关只监听 `127.0.0.1`，外部流量统一经 nginx / IIS 反代（模板已含 WebSocket 与长超时配置）；
4. 防火墙仅放行 80/443，模型端点（8001/8002/8003）不对外暴露；
5. 密钥写在 `*.local.yaml` 或 `/etc/aiassistant/gateway.env`（权限 600），**勿提交进仓库**；
6. systemd unit 已启用 `ProtectSystem=strict`、`NoNewPrivileges` 等沙箱加固。

## 贡献

欢迎提交 Issue 与 PR。开发环境、代码风格、测试与提交规范见 **[CONTRIBUTING.md](CONTRIBUTING.md)**。

## 许可证

按仓库实际许可证执行。
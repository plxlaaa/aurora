# AI 助手部署与实现规范书

本文档描述 AI 智能助手（浏览器 Web UI）的部署方式与后端实现规范。项目为前后端分离的单机部署版，
端口、路径、配置字段以本仓库实际代码为准。

## 1. 系统架构

```
浏览器 (Vue 3 SPA)
   │  /api（REST）+ /api/chat（WebSocket）
   ▼
Nginx (80/443，可选 HTTPS) ──► FastAPI 网关 :8080（127.0.0.1）
                                   │
                                   ├──► 主模型 / 视觉模型 / 压缩模型（vLLM/Ollama，OpenAI 兼容）
                                   ├──► Skills（config/skills/*，子进程执行）
                                   ├──► MCP Server（stdio / sse / http）
                                   └──► 执行环境（sandbox=Docker | local）
```

外部连接：仅 Nginx 监听 80/443；网关与模型端点默认仅监听 127.0.0.1。

## 2. 目录结构

```
workspace/
├── README.md                       # 项目说明
├── dev.sh                          # 本地一键启动脚本（后端）
├── .gitignore
├── config/                         # 运行时配置（可热刷新）
│   ├── models.yaml                 # 主/视觉/压缩端点 + 视觉路由策略 + 压缩参数 + 模型目录
│   ├── sandbox.yaml                # sandbox(Docker)/local 执行环境
│   ├── mcp.yaml                    # MCP server 声明
│   └── skills/                     # Skill 清单
│       ├── weather/                #   weather：查询天气
│       └── web_fetch/              #   web_fetch：抓取网页
├── backend/                        # FastAPI 网关
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                 # ASGI 入口：创建 app、CORS、挂载路由、启停钩子
│   │   ├── core/                   # 基础设施层
│   │   │   ├── config.py           #   配置加载与热刷新
│   │   │   └── auth.py             #   JWT/API Key 鉴权
│   │   ├── services/               # 业务服务层
│   │   │   ├── llm.py              #   OpenAI 兼容客户端 + 端点能力探测
│   │   │   ├── vision.py           #   视觉路由决策
│   │   │   ├── compress.py         #   上下文压缩（map_reduce）
│   │   │   ├── chat.py             #   对话编排 + 工具循环
│   │   │   ├── skills.py           #   Skills 框架
│   │   │   ├── mcp_client.py       #   MCP 客户端
│   │   │   ├── sandbox.py          #   沙箱/local 执行器
│   │   │   ├── sessions.py         #   会话持久化（JSONL）
│   │   │   └── upload.py           #   附件上传
│   │   └── routers/                # API 路由层
│   │       ├── deps.py             #   进程级共享单例 + 启停逻辑
│   │       └── api.py              #   REST + WebSocket 端点
│   └── tests/                      # 单元测试（unittest，无需外部依赖即可运行）
├── frontend/                       # Vue 3 单页应用（Vite）
│   ├── package.json / vite.config.js / index.html
│   └── src/{main.js, App.vue, api.js, style.css, router/, views/ChatView.vue, views/SettingsView.vue}
├── scripts/
│   └── env-setup.sh                # 环境检测与安装（幂等，--dry-run）
├── deploy/
│   ├── nginx.conf                  # 反代 + HTTPS 模板
│   ├── install.sh                  # 服务器安装脚本
│   └── systemd/                    # systemd unit
│       ├── aiassistant-gateway.service
│       ├── vllm-main.service
│       ├── vllm-vision.service
│       └── vllm-compress.service
└── data/                           # 运行时数据（gitignore）
    ├── sessions/                   # 会话 JSONL
    ├── uploads/                    # 上传附件
    └── archive/                    # 归档会话
```

## 3. 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AI_ASSISTANT_CONFIG_DIR` | `/opt/aiassistant/config` | 配置目录（models/sandbox/mcp） |
| `AI_ASSISTANT_SKILLS_DIR` | `<config>/skills` | Skills 目录 |
| `AI_ASSISTANT_DATA_DIR` | `/opt/aiassistant/data/sessions` | 会话数据目录 |
| `AI_ASSISTANT_UPLOAD_DIR` | `/opt/aiassistant/data/uploads` | 上传附件目录 |
| `AI_ASSISTANT_SANDBOX_LOCAL_DIR` | — | 覆盖 sandbox.local.workdir 与 docker.mount 宿主路径 |
| `AI_ASSISTANT_API_KEY` | 空 | 设置后启用鉴权（Bearer Token） |
| `AI_ASSISTANT_JWT_SECRET` | 随机 | JWT 签名密钥 |

## 4. 模型服务

模型端点均 OpenAI 兼容（`/v1/chat/completions`、`/v1/models`）。推荐：
- 主对话（main）：能力较强的对话模型；
- 视觉（vision）：多模态模型（用于图片理解），可复用主模型；
- 压缩（compressor）：轻量模型（用于历史摘要压缩）。

按第 2 章 systemd unit 部署 vLLM（或 Ollama）后，在 `config/models.yaml` 的 `endpoints` 配置
`base_url` / `model_name` / `api_key`。

## 5. 关键实现机制

- **分层结构**：`core`（配置/鉴权）、`services`（业务）、`routers`（HTTP/WS），依赖从外向内单向。
- **配置热刷新**：`config.py` 的 `Settings.update()` 将内存变更写回 YAML 并 `reload()`；
  端点变更后 `ModelRegistry.reload()` 重建客户端并重新探测。
- **视觉路由**：`vision_routing.policy` 三选一（见 `config/models.yaml` 注释），依据探测到的模态选择端点。
- **上下文压缩**：会话 token 超 `compress.threshold * context_window` 自动触发；`map_reduce` 分块摘要→合并，
  保留系统提示与最近 `keep_recent_turns` 轮原文；结果为 `[context_summary]` 回注。
- **工具循环**：`chat.py` 将 Skills 与 MCP 工具合并注入主模型，处理 `tool_call` 直至无调用（上限 `MAX_TOOL_ROUNDS`）。
- **执行环境**：`sandbox`（Docker，隔离默认）与 `local`（需 `local.allow=true`）。
- **鉴权**：设置 `AI_ASSISTANT_API_KEY` 后，REST 需 `Authorization: Bearer <api-key|JWT>`，
  WebSocket 握手支持 `Authorization` 头或 `?token=`，JWT 经 `/api/auth/login` 换取。
- **会话**：以 JSONL 按会话持久化，含 idle/running/completed 状态。

## 6. API 摘要

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET/PUT | `/api/models` | 模型端点配置与探测 |
| GET/PUT | `/api/models/catalog` | 模型目录 |
| GET/PUT | `/api/sandbox` | 执行环境配置 |
| GET | `/api/skills` | Skill 列表 |
| PUT | `/api/skills/{name}` | 启停 Skill |
| GET | `/api/mcp` | MCP server 状态 |
| POST | `/api/upload` | 附件上传 |
| GET | `/api/files/{object_id}` | 附件读取 |
| POST | `/api/run-code` | 执行代码（沙箱/local） |
| POST | `/api/compress` | 手动触发压缩 |
| GET/POST | `/api/sessions` | 会话列表 / 新建 |
| GET/DELETE | `/api/sessions/{id}` | 读取 / 清空会话 |
| POST | `/api/sessions/{id}/archive` | 归档会话 |
| POST | `/api/auth/login` | 换取 JWT |
| WS | `/api/chat` | 对话（流式） |

## 7. 部署步骤（Ubuntu 24.04）

1. 环境准备：`sudo bash scripts/env-setup.sh`（可先 `--dry-run` 查看状态）。
2. 模型服务：按第 4 章部署并配置 vLLM/Ollama。
3. 安装：`sudo bash deploy/install.sh`（安装依赖、构建前端、装 systemd unit）。
4. 网关托管：`sudo systemctl enable --now aiassistant-gateway`。
5. Nginx：套用 `deploy/nginx.conf`，certbot 申请 HTTPS。
6. 安全加固：UFW 仅放行 80/443；网关与模型端点仅监听 127.0.0.1。

## 8. 本地开发

```bash
# 后端（默认 127.0.0.1:8080）
cd backend
python3 -m venv venv && venv/bin/pip install -r requirements.txt
bash ../dev.sh            # 或手动设环境变量后 venv/bin/uvicorn app.main:app --port 8080

# 前端（Vite dev，5173，/api 代理到后端）
cd frontend && npm install && npm run dev
```

测试：`cd backend && venv/bin/python -m unittest discover -s tests -v`（22 项）。
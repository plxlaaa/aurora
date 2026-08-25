# AI 智能助手（浏览器 Web UI）

依据《[docs/AI助手部署与实现规范书.md](docs/AI助手部署与实现规范书.md)》实现的单机部署版浏览器 Web UI 智能助手。
前后端分离：FastAPI 网关 + Vue 3 单页应用，支持多轮对话、视觉理解、上下文压缩、Skills、MCP、沙箱/local 可切换执行环境。

## 目录结构

```
├── README.md                      # 项目说明（本文件）
├── dev.sh                         # 本地一键启动后端脚本
├── docs/
│   └── AI助手部署与实现规范书.md    # 部署与实现规范
├── scripts/
│   └── env-setup.sh               # 环境自动检测与安装（幂等，支持 --dry-run）
├── config/                        # 运行时配置（可热刷新）
│   ├── models.yaml                # 主/视觉/压缩端点 + 视觉路由策略 + 压缩参数
│   ├── sandbox.yaml               # sandbox/local 执行环境
│   ├── mcp.yaml                   # MCP server 声明
│   └── skills/                    # Skill 清单（weather / web_fetch）
├── backend/                       # FastAPI 网关
│   ├── app/
│   │   ├── main.py                # ASGI 入口（创建 app、CORS、挂载路由、启停钩子）
│   │   ├── core/                  # 基础设施层
│   │   │   ├── config.py          #   配置加载与热刷新
│   │   │   └── auth.py            #   JWT/API Key 鉴权
│   │   ├── services/              # 业务服务层
│   │   │   ├── llm.py             #   OpenAI 兼容客户端 + 端点探测
│   │   │   ├── vision.py          #   视觉路由决策
│   │   │   ├── compress.py        #   上下文压缩（map_reduce）
│   │   │   ├── chat.py            #   对话编排（视觉路由/压缩/工具循环）
│   │   │   ├── skills.py          #   Skills 框架
│   │   │   ├── mcp_client.py      #   MCP 客户端集成
│   │   │   ├── sandbox.py         #   沙箱/local 执行器
│   │   │   ├── sessions.py        #   会话持久化
│   │   │   └── upload.py          #   附件上传
│   │   └── routers/               # API 路由层
│   │       ├── deps.py            #   共享单例装配 + 启停逻辑
│   │       └── api.py             #   REST + WebSocket 端点
│   ├── tests/                     # 单元测试（unittest）
│   └── requirements.txt
├── frontend/                      # Vue 3 单页应用（Vite）
│   ├── package.json / vite.config.js / index.html
│   └── src/{main.js, App.vue, api.js, style.css, router/, views/ChatView.vue, views/SettingsView.vue}
└── deploy/
    ├── systemd/                   # 网关 + vLLM 主/视觉/压缩 unit
    ├── nginx.conf                 # 反代 + HTTPS 模板
    └── install.sh                 # 服务器安装脚本
```

运行时数据目录 `data/`（sessions / uploads / archive）由 `.gitignore` 忽略，不随仓库提交。

## 快速启动（开发环境）

后端（默认监听 127.0.0.1:8080）：

```
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
bash ../dev.sh        # 已为你注入 config/data/skills 等环境变量，等价于下：
# AI_ASSISTANT_CONFIG_DIR=/path/to/config \
# AI_ASSISTANT_DATA_DIR=/path/to/data/sessions \
# AI_ASSISTANT_SKILLS_DIR=/path/to/config/skills \
# AI_ASSISTANT_UPLOAD_DIR=/path/to/data/uploads \
# venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
```

前端（Vite dev server 端口 5173，`/api` 反向代理到后端）：

```
cd frontend
npm install
npm run dev
```

测试：

```
cd backend && venv/bin/python -m unittest discover -s tests -v   # 22 项
```

## 生产部署（Ubuntu 24.04）

1. 环境准备：`sudo bash scripts/env-setup.sh`（或先 `--dry-run` 查看状态）。
2. 安装代码：`sudo bash deploy/install.sh`，按提示安装 Python 依赖、构建前端。
3. 模型服务：按规范书第 4 章部署 vLLM/Ollama，`deploy/systemd/` 提供主/视觉/压缩三个 unit。
4. 网关托管：`sudo systemctl enable --now aiassistant-gateway`。
5. Nginx：套用 `deploy/nginx.conf`，用 certbot 申请 HTTPS 证书。
6. 安全加固：UFW 仅放行 80/443；网关与模型端点仅监听 127.0.0.1。

## 配置说明

- 模型端点均 OpenAI 兼容；`models.endpoints.*.base_url/model_name/api_key` 可在设置页修改并热刷新。
- 视觉路由策略（`vision_routing.policy`）：
  - `prefer_vision_model`：优先独立视觉模型（默认）
  - `prefer_main_if_multimodal`：主模型多模态则复用主模型
  - `vision_model_only`：始终使用独立视觉模型
- 上下文压缩：token 超过 `threshold * context_window` 自动触发，或设置页/API 手动触发；保留系统提示与最近 K 轮原文。
- 执行环境：`sandbox`（Docker 隔离）默认，`local` 需显式 `allow=true`。
- 鉴权：设置 `AI_ASSISTANT_API_KEY` 后启用 JWT/API Key 鉴权，除 `/api/health` 与 `/api/auth/login` 外，所有 REST 端点（含会话管理）与 WebSocket 均要求 `Authorization: Bearer <api-key | JWT>`。

## API 摘要

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/health | 健康检查 |
| GET/PUT | /api/models | 模型端点配置与探测 |
| GET/PUT | /api/models/catalog | 模型目录 |
| GET/PUT | /api/sandbox | 执行环境配置 |
| GET | /api/skills | Skill 列表 |
| PUT | /api/skills/{name} | 启停 Skill |
| GET | /api/mcp | MCP server 状态 |
| POST | /api/upload | 附件上传 |
| GET | /api/files/{object_id} | 附件读取 |
| POST | /api/run-code | 执行代码 |
| POST | /api/compress | 手动触发压缩 |
| GET/POST | /api/sessions | 会话列表 / 新建 |
| GET/DELETE | /api/sessions/{id} | 读取 / 清空会话 |
| POST | /api/sessions/{id}/archive | 归档会话 |
| POST | /api/auth/login | 换取 JWT |
| WS | /api/chat | 对话（流式） |
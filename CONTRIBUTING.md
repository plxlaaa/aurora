# 贡献指南

感谢参与 Aurora AI 智能助手的开发。本文档说明开发环境、代码风格、测试与提交流程。

- 环境搭建：[开发环境](#开发环境) · [目录结构](#目录结构)
- 编码规范：[后端](#后端规范) · [前端](#前端规范) · [错误处理](#错误处理规范)
- 质量要求：[测试](#测试) · [提交](#提交规范)
- 流程：[提交流程](#提交流程)

---

## 开发环境

### 后端

要求 Python 3.10+（推荐 3.11）。

```bash
git clone https://github.com/plxlaaa/aurora.git
cd aurora

cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 前端

要求 Node.js 18+。

```bash
cd frontend
npm install
```

### 本地启动

后端（监听 127.0.0.1:8080）：

```bash
# dev.sh 已注入 config / data / skills 等环境变量
bash dev.sh
```

前端（Vite dev server，5173 端口，`/api` 代理到后端）：

```bash
cd frontend && npm run dev
```

打开 <http://localhost:5173>。

> **提示**：`dev.sh` 以仓库内的 `config/` 与 `data/` 为运行目录，无需root 权限，也不会污染生产配置。

### 代码检查与测试

```bash
# 后端测试（254 项）
cd backend && venv/bin/python -m unittest discover -s tests -v

# 前端构建验证
cd frontend && npm run build
```

---

## 目录结构

```
├── backend/app/
│   ├── main.py# ASGI 入口：中间件、异常处理、静态托管
│   ├── core/                # 基础设施层（不依赖业务）
│   │   ├── config.py        #   YAML 配置加载与热刷新
│   │   ├── runtime.py       #   环境变量运行参数
│   │   ├── errors.py        #   统一异常与错误码
│   │   ├── middleware.py    #   请求 ID / 安全头 / 限额 / gzip
│   │   ├── auth.py          #   JWT / API Key 鉴权
│   │   └── hooks.py         #   生命周期事件总线
│   ├── services/            # 业务服务层（无 HTTP 依赖，可单测）
│   │   ├── chat.py          #   对话编排
│   │   ├── llm.py           #   OpenAI 兼容客户端与端点探测
│   │   ├── sessions.py      #   会话持久化
│   │   └── ...
│   └── routers/
│       ├── deps.py          #   共享单例装配与生命周期
│       └── api.py           #   REST + WebSocket 端点
├── deploy/                  # 部署脚本与模板
│   ├── install.sh           #   Linux 安装/升级/卸载
│   ├── windows/install.ps1  #   Windows 一键安装
│   ├── docker/              #   Dockerfile
│   ├── docker-compose.yml
│   └── systemd/             #   unit 文件
└── docs/API.md              # 接口参考
```

分层原则：`routers` 只做协议转换与参数校验，业务逻辑一律下沉到 `services`；`core` 不依赖 `services` 与 `routers`。

---

## 后端规范

### 类型注解

所有函数签名必须有完整注解，包括返回值：

```python
def list_sessions_page(
    self, *, limit: int | None = None, offset: int = 0, keyword: str = ""
) -> tuple[list[dict[str, Any]], int]:
    ...
```

### 文档字符串

模块、公开类与函数均需中文docstring，说明「做什么」与「为什么」，而非复述代码：

```python
def collapse_deltas(self, session_id: str) -> None:
    """一轮生成结束后，将连续的 assistant_delta 合并为单条 assistant 消息。"""
```

### 日志

使用 `logging.getLogger("aiassistant.<模块>")` 获取模块级 logger。慢请求、失败降级等关键路径需记录，但**不要**打印密钥、token 或完整用户输入。

### 并发安全

共享状态用锁保护；会话写入必须经`SessionStore` 的锁，避免与流式落盘交错写坏文件。涉及「生成中」会话的写操作，调用 `_reject_busy_session()`。

---

## 错误处理规范

**业务代码抛 `AppError` 子类，不要直接 `raise HTTPException`**——前者携带稳定错误码，前端与文档可依赖：

```python
from ..core.errors import NotFoundError, ValidationError, SessionBusyError

if not skills_mgr.get(name):
    raise NotFoundError(f"skill 不存在: {name}")

if "enabled" not in payload:
    raise ValidationError("缺少 enabled 字段")
```

新增错误场景时：

1. 复用 `ErrorCode` 中的既有码；确需新增则同时更新 `docs/API.md` 的错误码表；
2. 信息中不要暴露内部细节（路径、SQL、堆栈），这些只进日志。

---

## 前端规范

- Vue 3 `<script setup>` 组合式 API；
- 所有 API 调用经 `src/api/http.js`，它统一附加 token 并处理 401；
- 错误处理读取 `detail` 字段展示给用户；需要分支判断时用 `code`；
- 新增视图需在 `src/router/index.js` 注册；
- 保持响应式布局（`src/style.css` 中已有基础断点）。

---

## 测试

新增或修改业务逻辑时必须附带测试。测试放在 `backend/tests/`，命名 `test_<模块>.py`。

```bash
cd backend && venv/bin/python -m unittest discover -s tests -v
cd backend && venv/bin/python -m unittest tests.test_sessions_page  # 单文件
```

覆盖要点：

|改动类型 | 必须覆盖 |
|---------|---------|
| 新增接口 | 正常路径 + 参数校验失败 + 鉴权失败 |
| 新增异常码 | 状态码与 `code` 字段断言 |
| 缓存 / 状态逻辑 | 失效条件、新增、删除、重命名 |
| 中间件 | 命中与未命中两条路径 |
| 修复缺陷 | **先写能复现缺陷的测试**，确认它在修复前失败 |

测试需使用 `tempfile` 创建隔离目录，**不得**写入真实 `/opt/aiassistant` 或仓库目录。

---

## 提交规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-CN/)：

```
<type>(<scope>): <subject>

<body>
```

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `perf` | 性能优化 |
| `refactor` | 重构（无行为变化） |
| `test` | 测试 |
| `docs` | 文档 |
| `build` | 构建与部署脚本 |
| `chore` | 杂项 |

示例：

```
feat(middleware): 新增请求体限额与 gzip 中间件

统一错误响应结构（detail/code/request_id），并对超大请求体返回 413。
实测会话列表接口响应体积由 5584B 降至 889B。
```

要求：

- 标题行不超 72 字符，使用祈使句；
- 正文说明「为什么」与「影响」，破坏性变更需明确列出迁移步骤；
- 一次提交聚焦一件事，勿混入无关格式化。

---

## 提交流程

1. **同步最新代码**：`git fetch origin && git rebase origin/main`
2. **建分支**：`git checkout -b feat/your-feature`
3. **开发 + 自测**：本地跑通测试与前端构建
4. **更新文档**：接口变更同步 `docs/API.md`，行为变更同步 `CHANGELOG.md`
5. **提交**：`git commit`（遵循上文规范）
6. **推送并开PR**：描述改动动机、方案与验证方式

提交前确认清单：

- [ ] `python -m unittest discover -s tests` 全部通过
- [ ] `npm run build` 成功
- [ ] 未将密钥、token、`*.local.yaml` 提交进仓库
- [ ] 新增接口已在 `docs/API.md` 记录
- [ ] 用户可见的行为变更已记入 `CHANGELOG.md`
- [ ] 部署脚本通过语法检查（`bash -n deploy/install.sh`）

---

## 安全须知

发现安全问题请**不要**公开提交 Issue，请通过 GitHub 私密漏洞报告渠道私下反馈。

涉及鉴权、密钥处理、上传与路径处理的改动，请特别补充负向测试（如路径穿越、越权访问、超大请求体）。
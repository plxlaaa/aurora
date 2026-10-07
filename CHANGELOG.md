# 更新日志

本文件记录 Aurora AI 智能助手的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] — 2026-10-07

后端健壮性与可观测性增强，补齐 Linux / Windows 双端安装部署能力。

### 后端增强

- **统一异常体系**（`app/core/errors.py`）：新增 `AppError` 及11 个语义化子类（`ValidationError`、`NotFoundError`、`SessionBusyError`、`RateLimitedError`、`UpstreamError` 等），配套稳定错误码常量 `ErrorCode`。`AppError` 继承 FastAPI `HTTPException`，对既有调用方与测试完全向后兼容。
- **全局异常处理**（`app/main.py`）：统一序列化 `detail` / `code` / `request_id`；新增 `RequestValidationError` 与未捕获异常的兜底处理，未捕获异常对外不暴露内部细节，仅在日志中记录完整堆栈。
- **中间件链**（`app/core/middleware.py`）：纯 ASGI 实现，包含请求 ID 透传与结构化访问日志、安全响应头（nosniff / SAMEORIGIN / Referrer-Policy）、请求体限额（413）、gzip 压缩（实测会话列表 5584B → 889B，减少约 84%）。
- **运行时配置**（`app/core/runtime.py`）：新增 `RuntimeConfig`，将监听地址、CORS 白名单、请求体上限、并发上限、日志级别、前端目录等收敛为环境变量解析，含非法值回退与布尔多格式兼容。
- **CORS 白名单化**：由原先 `allow_origins=["*"]` 改为环境变量驱动的显式白名单（默认仅本机开发端口），并通过 `expose_headers` 暴露 `X-Request-ID`。
- **健康与就绪探针分离**：`/api/health` 保持轻量存活检查（公开），新增 `/api/ready` 返回端点可用性、MCP 连接、插件、鉴权状态等就绪详情，便于 systemd / Docker / 监控接入。
- **连接池与资源回收**（`app/services/llm.py`）：HTTP 客户端启用连接池（`max_connections=16`），探测请求使用独立 15s 短超时避免拖住启动；新增 `ModelRegistry.close_all()`，进程退出时关闭全部端点连接池，修复原先退出时的连接泄漏。
- **URL 凭据脱敏**：端点快照新增 `base_url_masked`，对 `user:pass@host` 与 `key` 类查询参数做掩码，避免中转站凭据随 `/api/models` 外泄。
- **会话列表增量缓存**（`app/services/sessions.py`）：改为按 `(mtime_ns, size)` 签名增量刷新，仅重新解析变更过的文件；每次读取与目录实际内容对账，修复删除 / 归档会话后列表残留的缺陷。
- **会话列表分页**：新增 `limit` / `offset` / `q` 查询参数，返回 `total` / `has_more` 字段，同时保留原有 `sessions` 数组结构以兼容前端。
- **登录防护加固**：来源 IP 优先取 `X-Forwarded-For`（受 `AI_ASSISTANT_TRUST_PROXY` 控制，避免伪造绕过限流）；失败记录表上限 1 万条，防止伪造 IP 造成内存无界增长；失败日志记录 IP。
- **生产模式**：新增 `AI_ASSISTANT_PRODUCTION`，开启后关闭 `/docs` 与 `/openapi.json`。
- **前端一体化托管**：设置 `AI_ASSISTANT_FRONTEND_DIR` 后网关直接托管前端构建产物并支持 SPA 深链回退（含路径穿越防护），实现单进程部署，免去单独配置 nginx 静态站点。
- **优雅关闭**：`lifespan` 记录启动耗时，关闭时按启动相反顺序释放 MCP 与模型端点连接。

### 新增功能

- `/api/ready` 就绪探针端点。
- 静态前端托管与 SPA history 模式回退。
- 会话列表分页与关键字过滤。

### 部署与安装

- **Linux systemd**：`deploy/install.sh` 支持 `--skip-frontend` / `--no-start` / `--uninstall` / `--purge`，全程幂等（重复执行即原地升级）；同步代码时排除 `venv`、同步 `config` 时保留 `*.local.yaml` 密钥层；新增密钥环境文件 `/etc/aiassistant/gateway.env`（权限 600）。
- **systemd unit 加固**：`aiassistant-gateway.service` 启用 `ProtectSystem=strict`、`ProtectHome`、`NoNewPrivileges`、`MemoryDenyWriteExecute`、`RestrictAddressFamilies` 等加固项，以 `ReadWritePaths` 限定可写路径；改用 journald 统一日志、增加优雅停止时长与文件句柄上限。
- **Windows 一键安装**（`deploy/windows/install.ps1`）：非管理员运行时自动提权；检测 Python / Node.js 并给出 winget 安装指引；创建 venv、构建前端、生成 `gateway.env`、配置目录 ACL 与防火墙；`-RegisterService` 经 NSSM 注册开机自启服务（3秒重启退避、stdout/stderr 落盘），并生成加载环境变量的 `run-gateway.cmd` 包装脚本。
- **Windows 卸载**：`-Uninstall` 停止并删除服务，`-PurgeData` 一并清除数据目录。
- **Docker**：新增多阶段 `deploy/docker/Dockerfile`（非 root 运行、tini 转发信号、健康检查）与 `deploy/docker-compose.yml`（命名卷持久化、配置只读挂载、可选内置 nginx 段）。
- **环境变量模板**：新增 `deploy/.env.example`（跨平台）与 `deploy/gateway.env.example`（systemd 专用），集中列全部可配置项。

### 文档

- 新增 `INSTALL.md`：覆盖 Linux（Docker / systemd）、Windows（PowerShell / 手动 / WSL2）部署，反向代理与 HTTPS、配置说明、升级卸载、九类常见问题排查，以及部署后自检清单。
- 新增 `docs/API.md`：完整接口参考，含鉴权方式、统一错误码表、会话分页说明与 WebSocket 协议。
- 新增 `CONTRIBUTING.md`：开发环境、目录结构、代码风格、测试与提交规范。
- `README.md` 更新目录结构、测试数量（220 项）、API 摘要与部署指引。

### 测试

- 新增 `tests/test_middleware.py`（33 项）：请求 ID 透传、安全响应头、请求体限额、gzip 条件与往返、错误体构造、异常分类与状态码、运行时配置解析（含非法值回退）、健康/就绪端点。
- 新增 `tests/test_sessions_page.py`（18 项）：分页切片、关键字过滤、排序、增量缓存失效、新建/删除/归档同步、损坏行跳过。
- 新增 `tests/test_gateway_http.py`（23 项）：健康与就绪探针、错误响应契约、分页参数边界校验、安全响应头、请求体限额、OpenAPI 端点与参数文档。
- 测试总数由 146 项增至 220 项，全部通过。

### 修复

- 修复进程退出时模型端点 HTTP 连接池未关闭的问题。
- 修复会话删除 / 归档后会话列表仍残留该会话的问题。
- 修复 `CORS` 全开（`allow_origins=["*"]`）带来的越权访问风险。
- 修复 `X-Forwarded-For` 未被采信导致登录限流对所有用户共用同一计数的问题。

---

## [1.0.0] — 2026-10-06

首个功能完整版本：对话体验专项（错误重试、打字指示、工具结果表格化、响应式修复）。

- FastAPI 网关：多轮对话 WebSocket、视觉理解、上下文压缩。
- Skills 框架、MCP 客户端集成、自定义工具（HTTP / Python）、工具中心。
- 沙箱 / local 可切换执行环境。
- 会话持久化、附件上传与图像路由。
- 内置插件（自动命名、用量统计）与生命周期钩子。
- Vue 3 单页应用：对话页、设置页、工具中心、登录页。
- Linux 部署脚本与 systemd / nginx 配置模板。
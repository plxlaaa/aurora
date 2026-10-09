# 更新日志

本文件记录 Aurora AI 智能助手的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.3.0] — 2026-10-09

修复 gzip 多块响应体损坏与静态托管路径穿越两个真实生产缺陷，强化会话持久化 durability 与长连接健壮性，Linux / Windows 安装脚本的健康检查与日志治理能力补齐。

### 缺陷修复

- **gzip 多块响应体损坏（影响所有分块响应）**：`GZipMiddleware` 原实现只在 `not more_body and len(body) >= min_size` 时压缩**最后一个**分块，却已在响应头声明 `Content-Encoding: gzip`。由于该头一旦发出即代表**整个响应体**都是 gzip 流，未压缩的前序分块与被压缩的末块拼接后成为非法 gzip 序列，客户端必然解码失败（`incorrect header check` / `Not a gzipped file`）。实测前端 40KB 级静态资源（`FileResponse` 分块输出）100% 触发。现改为**整体缓冲后统一决策**：先累积全部分块，再根据总量与 `min_size` 决定整体压缩或原样发出；压缩时同步重算 `Content-Length` 并剔除失效的 `content-encoding`，未压缩路径下响应体与头部严格自洽。
- **超大响应内存放大**：整体缓冲策略若无上限会把大文件整个读进内存。现引入 `MAX_BUFFER_BYTES = 8 MiB` 阈值，超过即**放弃压缩**并原样flush 已缓冲分块（不丢数据），既避免内存放大也避免对已压缩内容二次压缩。
- **静态托管路径穿越（安全漏洞）**：SPA 深链回退原先用 `str(candidate).startswith(str(root.resolve()))` 做前缀匹配，而字符串前缀无法区分路径分量边界——请求 `/..%2Fdist-secrets%2Fsecret.txt` 会把同级兄弟目录 `dist-secrets/` 误判为在 `dist` 内并**返回其文件内容（200）**。现改用 `Path.relative_to()` 做分量级判定，越界请求记warning 并回退到 `index.html`；`api/`、`ws` 前缀仍直接返回 404 JSON。

### 后端增强

- **会话持久化原子写**（`app/services/sessions.py`）：`_write_unlocked` 原先直接覆写目标 `.jsonl`，进程崩溃 / 磁盘写满时会使整个会话历史损坏。现改为「`mkstemp` 临时文件 → `write` → `fsync` → `os.replace`」原子替换（同目录保证同一文件系统），失败时清理临时文件并保留原文件不动。状态 / 标题索引表同样改用原子写，且写失败仅记warning 不再向上抛出，避免一次磁盘抖动导致整个请求 500。
- **孤儿会话状态回收**：`SessionStore` 的 `_status` / `_titles` 只增不减，会话删除后残留条目永久堆积。新增 `_get_cached_session_list` 孤儿清理：以实际存活的会话 id 为基准做差集，回收并持久化，避免长时间运行后内存与索引文件无界增长；运行中的会话不会被误清理。
- **WebSocket 心跳保活**（`app/routers/api.py`）：`/api/chat` 原先阻塞在 `receive_json()` 上，对端异常断开（网络中断、客户端进程被杀）时服务端无法感知，连接与推理资源泄漏。新增 `WS_IDLE_TIMEOUT = 300s` 空闲超时，超时以 `close(1001)` 主动回收；正常客户端的 `ping` 同样会刷新计时器。
- **登录失败表定期清理**：按 IP 限流表只增不减，攻击者轮换来源 IP 会导致无界增长。新增 `_sweep_login_failures()` 以 60 秒为周期（限频，避免每请求都扫全表）清理超出锁定窗口的失败记录。
- **健康 / 就绪探针分离复核**：`/api/health` 保持轻量存活语义，`/api/ready` 返回端点、 MCP、插件、鉴权等就绪详情，安装脚本的健康检查优先探测 `ok`，并附带就绪探针二次确认。

### 测试

- 新增 `tests/test_middleware_gzip.py`（22 项 + 4 subtests）：**在原始 ASGI 层驱动中间件**（绕过 httpx 自动解压，直接校验响应头与响应体字节的自洽性），覆盖多块压缩可解压、首块极小、阈值边界、自定义 `min_size`、流式类型跳过、HTTP 500、已带 `content-encoding` 的透传、`Content-Length` 预判、`vary` 头、超大响应内存保护、空 body、异常中断、非 http scope 放行。
- 新增 `tests/test_static_mount.py`（17 项 + 2 subtests）：目录内资源正常服务、SPA 深链回退、`api/` 与 `ws` 前缀 404、**兄弟目录前缀绕过**、URL 编码 / 双编码 / 四点 / 反斜杠穿越、拒绝时回退index。
- 新增 `tests/test_sessions_durability.py`（13 项）：原子写无临时文件残留、写入失败保留原内容、状态 / 标题表容错、孤儿条目清理、运行中会话不被误清理。
- 新增 `tests/test_ws_and_login_guard.py`（12 项）：`ping`/`pong`、欢迎语、`switch_model`/`set_thinking` 确认、空消息拒绝、空闲超时结构性校验；登录失败表过期移除、近期保留、清理限频、空表安全、guard 触发。
- 测试总数 **256 → 321 项**（+65），全部通过。
- **回归有效性验证**：临时将实现还原为缺陷版本后复跑——gzip 测试 13 项失败、路径穿越测试 4 项失败，确认新增用例确实覆盖缺陷而非空转。

### 部署与安装

- **Linux 健康检查**（`deploy/install.sh`）：新增 `--health-check` 参数与带重试轮询的健康 / 就绪探针（`read_gateway_port()` 统一读取端口，修复原脚本 `GATEWAY_PORT_UMASK` 未定义变量），避免服务刚启动时的单次探测误判失败；移除无意义的 `DO_START=${DO_START}`。
- **Linux 日志治理**：安装时写入 `/etc/systemd/journald.conf.d/99-aiassistant.conf`（`SystemMaxUse=500M`、`MaxRetentionSec=2week`）限制 journald 磁盘占用，并生成 `/etc/logrotate.d/aiassistant`（`daily` / `rotate 14` / `copytruncate`）；卸载时同步清理这两份配置。
- **Windows 健康检查**（`deploy/windows/install.ps1`）：新增 `-HealthRetries`（默认 10次 × 2 秒 ≈ 20s）带重试轮询 + 就绪探针；修复原实现引用**不存在字段** `$health.content` 的缺陷（改为输出 `ok` / `ready` / `version`）。
- **Windows 防火墙语义修正**：原先按「端口是否等于 8080」判断是否放行，且引用了**未定义参数** `-PublicPort`。现改为显式 `-PublicPort`（`0` 表示不暴露，默认仅监听 `127.0.0.1` 不创建规则），并修正 `param()` 块补齐该参数定义；对外暴露时提示优先走 IIS / nginx 反代。
- **双端脚本语法校验**：`bash -n deploy/install.sh` 与 PowerShell AST 解析器（`Parser::ParseFile`）双双通过，并静态扫描确认无未定义变量引用。

### 文档

- `README.md`：功能表新增「数据可靠性」条目；目录结构补充 4 个新增测试文件与部署脚本能力；环境变量表补充日志治理相关说明；测试数 256 → 321；新增「数据可靠性与长连接」小节（原子写、孤儿回收、WS 空闲回收）。
- `INSTALL.md`：Linux 章节补充 `--health-check` 与 journald / logrotate 日志治理说明；Windows 章节补充 `-HealthRetries` 与 `-PublicPort` 的语义与用法；排错章节补充「安装脚本健康检查失败如何排查」。
- `docs/API.md`：版本标注更新至 1.3.0，WebSocket 章节补充**空闲超时（300s）与 `close(1001)`** 的协议说明与客户端心跳建议。
- `CONTRIBUTING.md`：补充新增测试文件的职责划分与本地验证命令。
- 版本号统一提升至 **1.3.0**（`app/main.py`、`routers/deps.py`、winget / scoop 清单、docker-compose 默认镜像标签同步）。

## [1.2.0] — 2026-10-07

修复「死配置」与gzip 阈值缺陷，新增结构化日志与离线分发能力，Linux / Windows 安装部署再增强。

### 缺陷修复

- **`AI_ASSISTANT_SLOW_REQUEST_MS` 此前为死配置**：`middleware.install()` 未把 `runtime.slow_request_ms` 下发给 `RequestContextMiddleware`，无论环境变量如何设置，慢请求阈值恒为硬编码的 30s。现已在 `main.py` 接线，配置真正生效。
- **gzip 阈值参数失效**：`GZipMiddleware._should_compress()` 为`staticmethod` 且内部直接引用模块级 `GZIP_MIN_SIZE`，导致构造参数 `min_size` 完全无效。现改为接收 `min_size` 并由实例传入，实例化阈值可正确控制压缩判定。
- **会话导出 O(n) 扫描**：`GET /api/sessions/{id}/export` 原先调用 `list_sessions()` 遍历并解析**全部**会话文件才能取到单个会话的标题，会话量大时开销随总数线性增长。新增 `SessionStore.get_meta()` 定点查询（只读目标文件），并在导出前显式校验会话存在性。
- **退化会话 id 命中共享文件**：`_path()` 过滤非法字符后可能得到空串，进而落到共享的 `.jsonl` 文件（传入 `..` / `///` 即可命中），存在跨会话误读风险。新增 `_safe_name()` 并在 `exists()` / `load()` / `_load_unlocked()` / `get_meta()` 中拒绝空结果。
- **导出格式参数未校验**：`format` 原先任意取值都会被静默当作 `md` 处理，现非法值返回 400 `validation_error`。

### 后端增强

- **结构化 JSON 日志**（新增 `app/core/logsetup.py`）：`AI_ASSISTANT_LOG_JSON=true` 时每行输出一条 JSON（`ts` / `level` / `logger` / `msg`），业务自定义字段通过 `extra=` 透传，异常自动带 `exc`。`setup_logging()` 幂等，重复调用不叠加handler，并接管 uvicorn 的重复输出。
- **request_id 全链路透传**：新增 `request_id_var` 上下文变量，由 `RequestContextMiddleware` 绑定/复位，使同一请求内的访问日志与业务日志自动携带同一 trace id，无需逐处传参。
- **`SessionStore.get_meta()`**：单会话元信息定点查询（含标题、消息数、状态、时间戳），对外不泄漏内部排序键 `mtime`。
- **打包脚本安全性**：新增的 tarball / zip 打包流程均显式排除 `config/*.local.yaml`，确保真实 api_key 不会进入分发包。

### 测试

- 新增 `tests/test_logging.py`（12 项）：JSON 字段完整性、request_id 注入与缺席、extra 字段、Unicode、异常堆栈、文本模式、幂等初始化、非法级别回退，以及 `slow_request_ms` 接线断言。
- 新增 `tests/test_sessions_meta.py`（12 项 + 12 subtests）：`get_meta` 正确性、只读目标文件（以 spy 断言不扫描其它会话）、自定义标题优先，以及退化 id 的各类拒绝行为（含「不得读到 `.jsonl`」回归用例）。
- 新增 `tests/test_sessions_export.py`（7 项）：md / json 导出、默认格式、自定义标题、非法 format 返回 400、缺失会话 404、中文标题 RFC 5987 编码。
- 扩充 `tests/test_middleware.py`：新增 3 项 gzip 自定义阈值用例。
- 测试总数 **220 → 254 项**，全部通过。

### 部署与安装

- **Linux 离线分发**（新增 `scripts/package-tarball.sh` + `scripts/install-tarball.sh`）：在有网机器打包 `dist/aurora-<版本>-linux-<arch>.tar.gz` 与 `.sha256`，目标机解压即装，**无需 git / npm / 网络**。支持 `--with-venv`（预建虚拟环境）、`--with-frontend`（自动构建）、`--version`、`--outdir`；安装脚本支持 `--skip-venv` / `--no-systemd` / `--uninstall` / `--purge`。
- **Windows 离线分发**（新增 `scripts/package-windows.ps1`）：产出 `dist\aurora-<版本>-windows-<arch>.zip` 与 SHA256，参数含 `-Version` / `-Arch` / `-OutDir` / `-WithFrontend`；zip 内已随包分发 `install.ps1`。
- **WSL2 部署**（新增 `deploy/wsl2/`）：`install-wsl2-linux.sh` 自动识别 WSL 环境并把 `AI_ASSISTANT_HOST` 设为 `0.0.0.0`（否则 Windows 宿主机无法通过 localhost 访问），支持 `--install-dir` / `--port` / `--skip-frontend` / `--uninstall` / `--purge`；`install-wsl2.ps1` 为 Windows 侧包装器，负责发行版检测与参数转调。
- **Linux 安装脚本增强**（`deploy/install.sh`）：新增 `--with-firewall`（自动识别 ufw / firewalld 并放行端口）、`--check-upgrade`（仅比对本地与远端 commit，不改动文件）、`--start`（安装后启动并做健康检查）；卸载时同步清理防火墙放行规则。
- **包管理器分发清单**：新增 `deploy/windows/winget/plxlaaa.aurora.yaml` 与 `deploy/windows/scoop/plxlaaa-aurora.json`，支持 `winget install` / `scoop install`。
- **.gitignore**：新增 `dist/`，避免打包产物误入版本库。

### 文档

- `README.md`：功能表新增「可观测性」与「离线包」条目；快速开始新增方式四（离线包）、方式五（WSL2）；目录结构补充打包脚本与 `deploy/wsl2`、winget/scoop；新增「可观测性」小节与 JSON 日志示例；环境变量表补充 `AI_ASSISTANT_LOG_JSON` / `_SLOW_REQUEST_MS` / `_TRUST_PROXY` / `_LOG_LEVEL`；测试数220 → 254。
- `INSTALL.md`：新增「二之二 Linux 离线部署（tarball 内网分发）」「三之二 Windows 离线部署（zip 分发）」两章；systemd 章节补充防火墙与升级辅助；WSL2 章节补充一键脚本两种入口；配置章节补充 JSON 日志与慢请求阈值说明并标注「v1.2.0 起真正生效」；快速决策表补充离线场景。
- `deploy/.env.example` / `deploy/gateway.env.example`：新增 `AI_ASSISTANT_LOG_JSON`。
- 版本号统一提升至 **1.2.0**（`app/main.py`、`routers/deps.py`、winget / scoop 清单同步）。

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
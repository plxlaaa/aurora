# API 参考

Aurora 网关 HTTP / WebSocket 接口参考，覆盖 1.1.0 版本。

- 基础信息：[服务信息](#服务信息) · [鉴权](#鉴权) · [错误格式](#错误格式) · [错误码](#错误码表)
- 接口：[健康检查](#健康与就绪) · [模型](#模型配置) · [沙箱](#沙箱) · [工具](#工具中心) · [Skills](#skills) · [MCP](#mcp-管理) · [文件](#文件) · [会话](#会话) · [统计](#统计) · [对话](#对话websocket)
- 附录：[环境变量](#环境变量) · [curl 示例](#curl-示例)

交互式文档：开发模式启动后访问 <http://127.0.0.1:8080/docs>（生产模式 `AI_ASSISTANT_PRODUCTION=true` 时关闭）。

---

## 服务信息

| 项 | 值 |
|----|-----|
| 网关标题 | AI Assistant Gateway |
| 版本 | 1.1.0 |
| 协议 | HTTP/1.1（WebSocket 走 `/api/chat`） |
| 编码 | UTF-8 |
| 数据格式 | JSON（文件下载除外） |

所有响应均携带 `X-Request-ID`（16 位十六进制）。客户端传入该头时会被沿用，便于跨服务链路追踪；排查问题时可直接凭此 ID 在网关日志中检索。

---

## 鉴权

设置 `AI_ASSISTANT_API_KEY` 后启用鉴权，**除 `/api/health` 与 `/api/ready` 外的所有接口**（含 WebSocket）均需携带凭据。

### REST

```http
Authorization: Bearer <api_key | JWT>
```

`api_key` 即配置中的 `AI_ASSISTANT_API_KEY`；Web UI 登录后换取的 JWT 同样可用该头传递。缺失或无效返回 `401`。

### WebSocket

浏览器无法为 WS 设置请求头，故支持两种方式（任选其一）：

```
ws://host/api/chat?token=<api_key|JWT>
ws://host/api/chat?session_id=<id>&token=<api_key|JWT>
```

鉴权失败时服务端先发送 `{"type":"error","message":"未授权"}`，随后以关闭码 `4401` 断开。

### 登录与限流

`POST /api/auth/login` 用凭据换取 JWT（有效期 24 小时）。

按来源 IP 限流：**60 秒窗口内失败 5 次锁定 5 分钟**，超限返回 `429`。来源 IP 优先取 `X-Forwarded-For` 首段（仅当 `AI_ASSISTANT_TRUST_PROXY=true` 时采信）。

未配置 `AI_ASSISTANT_API_KEY` 时不启用鉴权，登录接口直接返回 token 且 `auth_enabled: false`。

---

## 错误格式

所有错误响应结构一致：

```json
{
  "detail": "会话不存在",
  "code": "not_found",
  "request_id": "795ef0a9061b4925"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `detail` | string | 人类可读的错误描述 |
| `code` | string | 机器可读错误码，见下表 |
| `request_id` | string | 请求追踪 ID，与响应头一致 |
| `errors` | array | 仅参数校验失败（422）时出现，逐字段列出 |

参数校验失败额外返回 `errors`：

```json
{
  "detail": "limit: Input should be greater than or equal to 1",
  "code": "validation_error",
  "request_id": "983de5d8205a41b9",
  "errors": [{"loc": ["query", "limit"], "msg": "Input should be greater than or equal to 1"}]
}
```

---

## 错误码表

| 码 | HTTP | 含义 |
|----|------|------|
| `validation_error` | 400 / 422 | 参数或请求体不合法 |
| `unauthorized` | 401 | 凭据缺失或无效 |
| `forbidden` | 403 | 无权限 |
| `not_found` | 404 | 资源不存在 |
| `conflict` | 409 | 与资源当前状态冲突 |
| `session_busy` | 409 | 会话正在生成中，禁止并发写操作 |
| `payload_too_large` | 413 | 请求体超过上限（默认 24MB） |
| `unprocessable` | 422 | 语义合法但业务上无法处理 |
| `rate_limited` | 429 | 触发限流 |
| `internal_error` | 500 | 服务器内部错误（细节仅见日志） |
| `upstream_error` | 502 | 上游模型端点异常 |
| `service_unavailable` | 503 | 依赖不可用 / 系统繁忙 |

---

## 健康与就绪

无需鉴权。

### `GET /api/health`

存活探针，适合负载均衡与容器健康检查。

```json
{
  "ok": true,
  "status": "healthy",
  "ready": true,
  "mode": "sandbox",
  "version": "1.2.0",
  "endpoints_available": ["main"],
  "endpoints_total": 3,
  "uptime_hint": "see /api/stats"
}
```

### `GET /api/ready`

就绪详情，适合监控与运维诊断。

```json
{
  "ok": true,
  "ready": true,
  "endpoints_total": 3,
  "endpoints_available": ["main", "vision"],
  "mcp_servers": [],
  "auth_enabled": true,
  "sandbox_mode": "sandbox",
  "plugins": ["auto_title", "stats"],
  "version": "1.1.0"
}
```

`ready` 为 `false` 表示所有模型端点均探测失败，此时对话请求会失败，但进程本身存活。

### 请求追踪与日志

所有 HTTP 请求都会返回 `X-Request-ID` 响应头（透传上游同名头，否则自动生成 16 位十六进制值）。
该id 同时写入响应体错误结构的 `request_id` 字段，并作为上下文绑定到该请求内的全部日志。

开启 `AI_ASSISTANT_LOG_JSON=true` 后日志为每行一条 JSON，便于 Loki / Elasticsearch 采集：

```json
{"ts":"2026-10-07T02:04:17.725813+00:00","level":"INFO","logger":"aiassistant.http","msg":"GET /api/health -> 200 3ms rid=9c54d883e27c4a5c ip=testclient","request_id":"9c54d883e27c4a5c"}
```

超过 `AI_ASSISTANT_SLOW_REQUEST_MS`（默认 30000）的请求会额外记录一条 `WARNING` 慢请求日志。

> **v1.2.0 修复**：`AI_ASSISTANT_SLOW_REQUEST_MS` 与 `AI_ASSISTANT_LOG_JSON` 此前虽已定义但未真正生效
> （前者未下发到中间件、后者未实现），本版本起两项配置均已接线。

---

## 模型配置

### `GET /api/models`

返回端点状态快照、视觉路由与压缩配置。

```json
{
  "endpoints": {
    "main": {
      "name": "main",
      "base_url": "http://127.0.0.1:8001/v1",
      "base_url_masked": "http://127.0.0.1:8001/v1",
      "model_name": "qwen2.5:7b-instruct",
      "modality": "auto",
      "detected_modality": "text",
      "available": true,
      "enabled": true,
      "models": ["qwen2.5:7b-instruct"],
      "error": ""
    }
  },
  "vision_routing": {"policy": "prefer_vision_model"},
  "compress": {"threshold": 0.8, "keep_recent": 6}
}
```

> `base_url` 若内嵌凭据（如 `https://user:pass@host` 或 `?key=xxx`），请使用 `base_url_masked` 展示；掩码字段会替换为 `***`。

### `PUT /api/models`

保存模型配置并热刷新（自动重新探测端点）。采用**字段级深合并**：未提供的字段（如 `api_key`）保留原值。

```json
{
  "endpoints": {
    "main": {"base_url": "http://127.0.0.1:8001/v1", "api_key": "sk-xxx", "model_name": "qwen2.5:7b-instruct"}
  }
}
```

响应：`{"ok": true, "endpoints": {...}}`

### `GET /api/models/catalog` · `PUT /api/models/catalog`

模型目录（可在设置页维护可选模型列表）。

---

## 沙箱

### `GET /api/sandbox` · `PUT /api/sandbox`

执行环境配置。`mode` 取值：

| 值 | 说明 |
|----|------|
| `sandbox` | Docker 隔离执行（默认） |
| `local` | 本机执行，需显式 `allow: true` |

---

## 工具中心

### `GET /api/tools`

工具快照，按来源分组。

```json
{
  "builtin": ["read_skill", "run_skill", "run_code"],
  "tools": [
    {"name": "read_skill", "source": "builtin", "description": "...", "enabled": true},
    {"name": "weather", "source": "skill", "description": "查询天气", "enabled": true},
    {"name": "my_tool", "source": "custom", "type": "http", "enabled": false}
  ]
}
```

### 自定义工具

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tools/custom` | 列出自定义工具 |
| `GET` | `/api/tools/custom/{name}` | 工具详情 |
| `POST` | `/api/tools/custom` | 创建（`name` 不得与内置/MCP 冲突） |
| `PUT` | `/api/tools/custom/{name}` | 更新 |
| `DELETE` | `/api/tools/custom/{name}` | 删除 |
| `PUT` | `/api/tools/custom/{name}/enabled` | 启停，body `{"enabled": bool}` |
| `POST` | `/api/tools/custom/{name}/test` | 测试运行，body `{"arguments": {...}}` |

---

## Skills

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/skills` | 列出 Skills |
| `PUT` | `/api/skills/{name}` | 启停，body `{"enabled": bool}` |
| `DELETE` | `/api/skills/{name}` | 删除 |
| `POST` | `/api/skills/upload` | 上传 zip 导入（`multipart/form-data`，字段名 `file`） |
| `GET` | `/api/skills/{name}/files` | 文件树 |
| `GET` | `/api/skills/{name}/files/{path}` | 读取文本文件 |
| `PUT` | `/api/skills/{name}/files/{path}` | 保存文本文件，body `{"content": "..."}` |

---

## MCP 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/mcp` | MCP 连接状态 |
| `GET` | `/api/mcp/servers` | server 配置列表 |
| `POST` | `/api/mcp/servers` | 新增（自动连接） |
| `PUT` | `/api/mcp/servers/{name}` | 更新（自动重连） |
| `DELETE` | `/api/mcp/servers/{name}` | 删除 |
| `POST` | `/api/mcp/servers/{name}/reconnect` | 手动重连 |

创建/更新时的校验规则：`name` 非空；`transport` 为 `stdio` / `http` / `sse` 之一；`stdio` 必须提供 `command`，`http` / `sse` 必须提供 `url`。

---

## 文件

### `POST /api/upload`

上传附件（`multipart/form-data`，字段名 `file`）。仅接受图片，单文件上限 10MB。

```json
{
  "object_id": "a1b2c3d4e5f6",
  "filename": "screenshot.png",
  "mime_type": "image/png",
  "size": 245678,
  "url": "/api/files/a1b2c3d4e5f6",
  "base64": "iVBORw0KGgo..."
}
```

服务以文件内容魔数判定真实类型，不轻信客户端 `Content-Type`。

### `GET /api/files/{object_id}`

读取附件，响应为二进制流，`Cache-Control: private, max-age=86400`。

---

## 会话

### `GET /api/sessions`

列出会话，按最后更新时间倒序。

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `limit` | int | 50 | 返回条数，取值范围 1–200 |
| `offset` | int | 0 | 跳过条数 |
| `q` | string | "" | 标题关键字（不区分大小写，最长 100 字符） |

```json
{
  "sessions": [
    {"id": "a1b2c3", "title": "查询天气", "message_count": 4, "status": "idle",
     "created_at": "2026-10-07T01:00:00+00:00", "updated_at": "2026-10-07T01:05:00+00:00"}
  ],
  "total": 42,
  "limit": 50,
  "offset": 0,
  "has_more": false
}
```

超出范围的 `limit` / 负数 `offset` / 超长 `q` 返回 `422`。

### 其他会话接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/sessions` | 新建，返回 `{"id": "..."}` |
| `GET` | `/api/sessions/{id}` | 读取（`messages` + `status`） |
| `DELETE` | `/api/sessions/{id}` | 删除 |
| `POST` | `/api/sessions/{id}/clear` | 清空消息，保留会话 |
| `POST` | `/api/sessions/{id}/archive` | 归档，返回 `{"archived": "..."}` |
| `POST` | `/api/sessions/{id}/rename` | 重命名，body `{"title": "..."}` |
| `GET` | `/api/sessions/{id}/export?format=md\|json` | 导出（见下） |
| `POST` | `/api/compress` | 手动触发压缩，body `{"session_id": "..."}` |

会话 `status` 取值：`idle` / `running` / `completed`。**`running` 期间**压缩、清空、删除、归档会返回 `409 session_busy`。

导出的 Markdown 按角色排版：用户 / 助手小节，工具调用与结果以引用块呈现，思考过程折叠在 `<details>` 中。

> **`format` 参数校验（v1.2.0 起）**：`format` 仅接受 `md`（默认）与 `json`，
> 其它取值返回 `400 validation_error`（此前任意值都会被静默当作 `md` 处理）。
> 会话不存在时返回 `404 not_found`。

> **导出性能（v1.2.0 起）**：导出时的标题等元信息通过 `SessionStore.get_meta()`
> 定点读取目标会话文件，开销与会话总数无关；此前需解析全部会话文件才能定位单个会话。

---

## 统计

### `GET /api/stats`

内置 stats 插件的用量快照 + 钩子订阅清单。

```json
{
  "stats": {"rounds": 12, "tokens": {"input": 8432, "output": 2150}, "tools": {"run_code": 3}},
  "hooks": {"generate.start": ["auto_title"], "generate.end": ["stats"], "tool.after": ["stats"]}
}
```

---

## 对话（WebSocket）

### `WS /api/chat`

| 查询参数 | 说明 |
|----------|------|
| `session_id` | 指定会话；省略则新建并推送欢迎语 |
| `token` | 鉴权凭据（见[鉴权](#鉴权)） |

### 客户端 → 服务端

| `type` | 字段 | 说明 |
|--------|------|------|
| `message` | `content`、`attachments`、`force_compress` | 发送消息（默认类型） |
| `regenerate` | — | 重新生成上一条回复 |
| `cancel` / `stop` | — | 中断当前生成 |
| `switch_model` | `model_name` | 切换当前连接所用模型 |
| `set_thinking` | `level`：`low`/`normal`/`high` | 调整思考强度（映射为采样温度 0.3/0.7/1.0） |
| `save_session` | — | 立即落盘当前会话 |
| `assistant_message` | `content` | 后端主动推送的消息 |
| `ping` | — | 心跳，服务端回 `pong` |

### 服务端 → 客户端

| `type` | 字段 | 说明 |
|--------|------|------|
| `delta` | `text` | 增量文本 |
| `thinking` | `text` | 思考过程增量 |
| `assistant_message` | `content` | 完整消息 |
| `tool_call` | `name`、`arguments`、`result`、`status` | 工具调用（`status`: `running`/`done`/`error`） |
| `tool` | `tool_call_id`、`name`、`content` | 工具执行结果 |
| `tool_result` | `name`、`result_text` | 文本协议工具结果 |
| `done` | — | 本轮结束 |
| `error` | `message` | 出错 |
| `info` | `text` | 操作确认（如已切换模型） |
| `cancelled` | — | 已中断 |
| `pong` | — | 心跳响应 |

### 并发限制

网关全局限制并发生成数（默认 3，`AI_ASSISTANT_MAX_CONCURRENT_GENERATIONS`）。槽位占满时返回 `{"type":"error","message":"系统生成任务已达上限，请稍后再试"}`。

同一连接在上一轮未结束时收到 `message` 会返回 `{"type":"error","message":"上一轮生成尚未结束"}`。

### 执行代码

### `POST /api/run-code`

body `{"code": "print(2**100)"}`，响应：

```json
{
  "stdout": "1267650600228229401496703205376\n",
  "stderr": "",
  "exit_code": 0,
  "timed_out": false,
  "mode": "sandbox"
}
```

---

## 环境变量

影响接口行为的关键变量：

| 变量 | 默认 | 影响 |
|------|------|------|
| `AI_ASSISTANT_API_KEY` | 空 | 设置即启用鉴权 |
| `AI_ASSISTANT_PRODUCTION` | `false` | `true` 时关闭 `/docs`、`/openapi.json` |
| `AI_ASSISTANT_MAX_BODY_MB` | `24` | 请求体上限，超限返回 413 |
| `AI_ASSISTANT_MAX_CONCURRENT_GENERATIONS` | `3` | 并发生成上限 |
| `AI_ASSISTANT_CORS_ORIGINS` | `http://localhost:5173` | 允许的跨域来源（逗号分隔） |
| `AI_ASSISTANT_TRUST_PROXY` | `true` | 是否采信 `X-Forwarded-For` |
| `AI_ASSISTANT_FRONTEND_DIR` | 空 | 设置后托管前端静态产物 |
| `AI_ASSISTANT_LOG_LEVEL` | `INFO` | 日志级别 |

完整清单见 [`deploy/.env.example`](../deploy/.env.example)。

---

## curl 示例

```bash
BASE=http://127.0.0.1:8080
TOKEN=<你的 api_key 或 JWT>

# 健康检查
curl -s $BASE/api/health

# 就绪详情
curl -s $BASE/api/ready

# 登录换JWT
curl -s -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"<你的密钥>"}'

# 列出模型端点
curl -s $BASE/api/models -H "Authorization: Bearer $TOKEN"

# 新建会话并重命名
SID=$(curl -s -X POST $BASE/api/sessions -H "Authorization: Bearer $TOKEN" | jq -r .id)
curl -s -X POST $BASE/api/sessions/$SID/rename -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"title":"我的会话"}'

# 分页查询会话
curl -s "$BASE/api/sessions?limit=10&offset=0&q=天气" -H "Authorization: Bearer $TOKEN"

# 导出会话为 Markdown
curl -s "$BASE/api/sessions/$SID/export?format=md" -H "Authorization: Bearer $TOKEN" -o session.md

# 执行代码
curl -s -X POST $BASE/api/run-code -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"code":"print(sum(range(101)))"}'

# 上传图片
curl -s -X POST $BASE/api/upload -H "Authorization: Bearer $TOKEN" \
  -F 'file=@screenshot.png'
```

WebSocket 对话（需 `websocat` 或 `wscat`）：

```bash
# 发送消息
echo '{"type":"message","content":"你好"}' | wscat -c "ws://127.0.0.1:8080/api/chat?token=$TOKEN"

# 保持连接交互
wscat -c "ws://127.0.0.1:8080/api/chat?session_id=$SID&token=$TOKEN"
```
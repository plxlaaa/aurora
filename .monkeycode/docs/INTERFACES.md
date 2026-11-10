# 接口定义

## CLI

| 命令 | 说明 |
|------|------|
| `aurora` | 交互 REPL |
| `aurora <task>` | 单次 Agent 运行 |
| `aurora init` | 写出默认配置 |
| `aurora setup` | 交互式首次配置向导（provider/base_url/model/API key） |
| `aurora config` | 打印配置路径与内容 |
| `aurora config set k v` | 写入配置字段 |
| `aurora update` | 本地自更新 |
| `aurora update --check` | 只检查远程版本 |
| `aurora doctor` | 诊断安装与渠道 |
| `aurora version` | 版本 |

## 环境变量

| 变量 | 用途 |
|------|------|
| `AURORA_API_KEY` | OpenAI 兼容密钥（默认） |
| `AURORA_BASE_URL` | 覆盖 `llm.base_url` |
| `AURORA_MODEL` | 覆盖模型名 |

密钥只从用户环境读取，不写进仓库。

## 配置文件

路径：Windows `%APPDATA%\aurora\config.json`，其他系统 `~/.config/aurora/config.json`

```json
{
  "llm": {
    "provider": "openai",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "api_key_env": "AURORA_API_KEY",
    "context_window": 128000
  },
  "update": {
    "enabled": true,
    "channel_race": true,
    "timeout_ms": 60000,
    "repo": "plxlaaa/aurora",
    "mirrors": ["github", "gh_proxy_com", "ghfast_top", "ghproxy_net", "gh_ddlc"]
  },
  "agent": {
    "max_steps": 32,
    "shell": "auto",
    "confirm_shell": true
  }
}
```

`llm.context_window` 用于回合末尾的上下文水位预警（≥60% 黄色、≥80% 红色），默认 128000。

## Agent 工具

| 工具 | 作用 |
|------|------|
| `read_file` | 读文本，支持 offset/limit |
| `write_file` | 覆盖写 |
| `edit_file` | 精确替换 |
| `list_dir` | 列目录 |
| `glob` | 按模式找文件 |
| `grep` | 正则搜索 |
| `run_shell` | 工作区内命令；Windows 走 PowerShell |

路径必须落在工作区根目录内。`*.go` 只匹配当前层，`**/*.go` 才递归。

REPL 会话在 `/reset` 之前保留对话记忆。`/yolo` 跳过 shell 确认。Windows 更新若锁住 `aurora.exe`，下次启动会从 `aurora.exe.new` 完成替换。

## 内部接口（v0.2/v0.3）

### agent

```go
type Event struct {
    Kind  string    // step | delta | tool | tool_result | usage | done | turn_end
    Name  string    // 工具名（tool/tool_result）
    Text  string    // 流式增量 / 工具参数 / 工具结果
    OK    bool      // tool_result 成败
    Diff  string    // tool_result 附带的行级 diff（write/edit）
    Usage llm.Usage // usage 事件的单步用量
}

Runner.Run(ctx, input) (final string, turnUsage llm.Usage, err error)
Runner.SessionUsage() / LastUsage()  // 会话累计与单步用量
Runner.Messages()  // 当前消息条数
```

### llm

```go
type Usage struct { Prompt, Completion, Total int64 }
ChatStream(ctx, msgs, tools, onDelta) (Message, Usage, error)  // 请求带 stream_options.include_usage
Chat(ctx, msgs, tools) (Message, Usage, error)
```

### tools

```go
type Result struct {
    OK      bool   `json:"ok"`
    Content string `json:"content"`
    Diff    string `json:"-"` // 仅 UI 展示，不进 LLM 上下文
}
tools.Diff(old, new string) string  // 行级 LCS diff，±行 + 2 行上下文，60 行截断
```

### tui

| 文件 | 职责 |
|------|------|
| `output.go` | printer：全局输出互斥 + ClearLine |
| `spinner.go` | spinner：`· ✢ ✳ ∗ ✻ ✽` 正反回放 + 动词池轮换 + 计时 + Ctrl+C 提示，仅 TTY |
| `markdown.go` | 行缓冲 markdown-lite：标题/加粗/行内代码/列表/围栏 |
| `cards.go` | 工具卡片 `⏺ Tool(arg)`（成功绿/失败红/执行中青）、结果行 `⎿`、diff 着色（40 行截断） |
| `banner.go` | 圆角欢迎框（模型/provider/git 分支/工作区 + 轮换提示），管道降级纯文本 |
| `term_unix.go` `term_windows.go` | 终端宽度探测，失败回退 80 |
| `repl.go` | REPL、事件接线（工具卡片单行重绘）、回合汇总行（tokens/耗时/上下文水位）、Ctrl+C 语义 |

回合事件流（v0.3）：`tool` 事件先打青色卡片并暂存参数，`tool_result` 到达后光标上移擦行重绘着色卡片，再打 `⎿` 结果行与 diff；回合成功结束打汇总行 `↑prompt ↓completion tokens · elapsed · context NN% used`。

## 更新 Manifest

发布资产 `manifest.json`：

```json
{
  "version": "0.1.0",
  "notes": "Aurora terminal agent",
  "assets": {
    "windows-amd64": {
      "name": "aurora-windows-amd64.zip",
      "url": "https://github.com/plxlaaa/aurora/releases/download/v0.1.0/aurora-windows-amd64.zip",
      "sha256": "<hex>"
    }
  }
}
```

下载渠道对 GitHub URL 加镜像前缀，并行请求，先完成且 HTTP 成功者胜出。

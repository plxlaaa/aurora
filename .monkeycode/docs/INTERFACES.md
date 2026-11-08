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
    "api_key_env": "AURORA_API_KEY"
  },
  "update": {
    "enabled": true,
    "channel_race": true,
    "timeout_ms": 20000,
    "repo": "plxlaaa/aurora",
    "mirrors": ["github", "ghproxy", "ghproxy_net", "gitdl", "gh_ddlc"]
  },
  "agent": {
    "max_steps": 32,
    "shell": "auto",
    "confirm_shell": true
  }
}
```

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

# Aurora

当前版本 **v0.1.1**

高性能终端 AI 编程助手。Windows 优先、单文件分发、多渠道本地更新。

High-performance terminal coding agent. Windows-first, single binary, local multi-channel updates.

Aurora lives in your terminal the way Codex and Claude Code do: it reads the workspace, edits files, runs commands, and talks to an OpenAI-compatible model.

## Install on Windows

PowerShell (recommended):

```powershell
irm https://github.com/plxlaaa/aurora/releases/latest/download/install.ps1 | iex
```

一行安装会弹出文件夹选择框。取消则落到 `%LOCALAPPDATA%\Programs\Aurora`。

在终端里直接跑脚本时，回车用默认路径，输入目录，或输入 `b` 再开选择框。

指定目录：

```powershell
$env:AURORA_INSTALL_DIR = "D:\Apps\Aurora"
irm https://github.com/plxlaaa/aurora/releases/latest/download/install.ps1 | iex
```

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDir "D:\Apps\Aurora"
powershell -ExecutionPolicy Bypass -File install.ps1 -Browse
```

CMD:

```cmd
curl -fsSL https://github.com/plxlaaa/aurora/releases/latest/download/install.cmd -o install.cmd && install.cmd D:\Apps\Aurora
```

The installer races several GitHub download mirrors and keeps the first complete payload. Chosen folder is added to the user PATH.

## First run

```powershell
$env:AURORA_API_KEY = "sk-..."
aurora init
aurora
```

Compatible with any OpenAI Chat Completions endpoint:

```powershell
$env:AURORA_BASE_URL = "https://api.openai.com/v1"
$env:AURORA_MODEL = "gpt-4o-mini"
```

Config file: `%APPDATA%\aurora\config.json`

## Update locally

```powershell
aurora update
aurora update --check
```

`aurora update` fetches `manifest.json` from multiple mirrors in parallel, then races asset download across GitHub + ghproxy + gitdl. The fastest healthy channel wins. SHA-256 is verified when the manifest includes it. On Windows the running exe is swapped via `aurora.exe.old` / `.pending` so a locked file still finishes on the next launch.

## Commands

```
aurora                 interactive REPL
aurora <task>          one-shot agent run
aurora update          local self-update
aurora doctor          install / PATH / channel diagnostics
aurora version
```

REPL extras: `/help` `/status` `/model` `/cd` `/reset` `/compact` `/yolo` `/update` `/quit`

Conversation memory is kept across turns until `/reset`. `aurora config set model <name>` writes `%APPDATA%\aurora\config.json`.

## Build

```bash
make build
make windows
make test
make pack
```

Windows binaries produced by `make windows`:

- `bin/aurora-windows-amd64.exe`
- `bin/aurora-windows-arm64.exe`

## Layout

```
cmd/aurora          CLI entry
internal/agent      tool-calling loop
internal/llm        OpenAI-compatible client
internal/tools      read/write/edit/grep/shell
internal/updater    racing multi-channel self-update
internal/tui        terminal REPL
scripts/            Windows / Unix installers
```

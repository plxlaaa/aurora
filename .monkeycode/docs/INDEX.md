# Aurora 文档

## 项目概述

Aurora 是一个 Windows 优先的终端 AI 编程助手（CLI Agent），目标是安装简单、启动快、本地可更新，体验接近 Codex / Claude Code。

**目标用户**: 在 Windows 终端（PowerShell / Windows Terminal）里写代码的开发者。

**核心价值**:
- 单文件分发，PowerShell 一行安装
- 原生 Go 二进制，启动开销低
- 多镜像竞速下载，本地自更新

## 技术选型

| 类别 | 选择 | 理由 |
|------|------|------|
| 语言 | Go 1.22 | 交叉编译 Windows 简单，单二进制，启动快 |
| LLM | OpenAI Chat Completions | 兼容大量中转 / 本地网关 |
| 更新 | 多渠道竞速 + SHA256 | 国内直连 GitHub 不稳定时仍能更新 |
| 安装 | PowerShell 脚本 | 与 Claude Code 同类体验 |

## 核心功能

### Agent REPL
- **目的**: 在终端里用自然语言改代码
- **描述**: 读取工作区、编辑文件、按需跑命令，流式输出

### Windows 安装器
- **目的**: 降低安装门槛
- **描述**: `irm ... \| iex`，写入 `%LOCALAPPDATA%\Programs\Aurora` 并加入用户 PATH

### 本地多渠道更新
- **目的**: 不依赖商店，直接替换本地二进制
- **描述**: 并行拉取 manifest 与 zip，最快成功渠道胜出

## 文档导航

- 当前工作区 `/ .monkeycode/docs/ARCHITECTURE.md` - 架构设计
- 当前工作区 `/ .monkeycode/docs/INTERFACES.md` - 接口定义
- 当前工作区 `/ .monkeycode/docs/DEVELOPER_GUIDE.md` - 开发指南

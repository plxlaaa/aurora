# User Instruction Memory

This file records user instructions, preferences, and teachings for reference in future interactions.

## Format

### User Instruction Entry
User instruction entries should follow this format:

[User Instruction Summary]
- Date: [YYYY-MM-DD]
- Context: [Mentioned scenario or time]
- Instructions:
  - [Content of user teaching or instruction, described line by line]

### Project Knowledge Entry
Entries discovered by the Agent during task execution should follow this format:

[Project Knowledge Summary]
- Date: [YYYY-MM-DD]
- Context: Discovered by Agent while performing [specific task description]
- Category: [Operations & Deployment|Build Methods|Testing Methods|Troubleshooting & Debugging|Workflow & Collaboration|Environment Configuration]
- Instructions:
  - [Specific knowledge points, described line by line]

## Deduplication Strategy
- Before adding a new entry, check for similar or identical instructions.
- If a duplicate is found, skip the new entry or merge it with the existing one.
- When merging, update the context or date information.
- This helps avoid redundant entries and keeps the memory file tidy.

## Entries

[Aurora 发版与验证流程]
- Date: 2026-11-09
- Context: Agent 在执行 v0.1.3 / v0.1.4 / v0.2.0 三次发版验证时发现
- Category: Operations & Deployment
- Instructions:
  - 发版机制：push tag `v*` → `.github/workflows/release.yml`（名称 "release"）自动构建并发布 manifest.json + 6 平台 zip + installers
  - 版本号需同步三处：`internal/version/version.go`、`Makefile`（VERSION ?=）、`scripts/pack.sh`
  - `gh` CLI 本环境未认证会失败；查询 workflow 状态用公开 API：`curl -s https://api.github.com/repos/plxlaaa/aurora/actions/runs?per_page=3`
  - 发布验证用镜像直链：`https://gh-proxy.com/https://github.com/plxlaaa/aurora/releases/latest/download/manifest.json`，下载 zip 后比对 manifest 中的 sha256
  - GitHub API 直连对本环境偶发 403 限流，asset raw 媒体类型响应不稳定，验证一律走 mirror 直链

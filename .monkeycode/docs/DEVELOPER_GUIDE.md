# 开发指南

## 环境要求

- Go 1.22+
- make、zip（打包用）
- 交叉编译 Windows 不需要本机 Windows 工具链

## 快速开始

```bash
go test ./...
make build
make windows
./bin/aurora version
```

## 项目结构说明

- `cmd/aurora` 入口
- `internal/*` 业务包，不对外发模块
- `scripts/install.ps1` Windows 安装与多渠道下载
- `scripts/pack.sh` 多平台 zip + manifest

## 开发规范

- 包名小写，导出标识用明确名词
- 不把密钥写入配置默认值
- 更新路径必须可在无网络单测里用 httptest 覆盖竞速逻辑

## 常见任务

跑测试：

```bash
go test ./...
```

只编 Windows：

```bash
make windows
```

## 构建与发布

```bash
VERSION=0.1.0 make pack
```

产物在 `dist/`：各平台 zip、`manifest.json`、安装脚本。打 `v*` tag 后 GitHub Actions 上传 Release。

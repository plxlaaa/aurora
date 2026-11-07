"""应用版本统一来源。

打包脚本（scripts/package-tarball.sh）、更新器与 /api/health 均引用此处，
发版时只改这一个常量。
"""
APP_VERSION = "1.5.0"
UPDATE_REPO = "plxlaaa/aurora"  # GitHub 仓库，release 与源码均从此获取

Aurora AI 助手（Windows 版）
============================

启动：开始菜单「Aurora」——打开**桌面原生窗口**（无需浏览器）
备用：开始菜单「Aurora（浏览器模式）」——浏览器访问 http://127.0.0.1:8080
停止：开始菜单「停止 Aurora」（或 aurora-stop.bat）
卸载：Windows「设置 → 应用 → 安装的应用」→ Aurora → 卸载
      卸载时会先确认，再询问是否保留用户数据（默认保留）

目录说明
--------
backend\        后端程序（app\ 源码 + site-packages\ 依赖）
frontend\dist\  前端静态产物
runtime\        内置 Python 3.11（免安装）
config\         配置（*.local.yaml 为本地覆盖，卸载/升级均不覆盖）
data\           会话与附件数据（含 data\backups\ 在线更新备份）

在线更新
--------
设置 → 关于与更新 → 检查更新/一键更新。更新只覆盖 backend\app、frontend\dist、
config 模板与 requirements.txt，永不触碰 data\、*.local.yaml、gateway.env。
更新完成后请关闭并重新启动 Aurora（开始菜单「停止 Aurora」→「Aurora」）。

首次使用
--------
默认未启用鉴权（仅本机 127.0.0.1 可访问）。如需开启：编辑 aurora.bat，
在 setlocal 之后加一行 set AI_ASSISTANT_API_KEY=<你的密钥>，保存后重启 Aurora。

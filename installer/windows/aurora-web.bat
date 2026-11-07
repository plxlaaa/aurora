@echo off
rem 备用入口：浏览器模式（固定端口 8080，排查桌面模式问题时用）
setlocal
cd /d "%~dp0"
if not exist data mkdir data

set "PYTHONPATH=%~dp0backend\site-packages"
set "AI_ASSISTANT_CONFIG_DIR=%~dp0config"
set "AI_ASSISTANT_DATA_DIR=%~dp0data\sessions"
set "AI_ASSISTANT_UPLOAD_DIR=%~dp0data\uploads"
set "AI_ASSISTANT_SKILLS_DIR=%~dp0config\skills"
set "AI_ASSISTANT_FRONTEND_DIR=%~dp0frontend\dist"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-aurora.ps1"
if errorlevel 1 (
  echo 启动失败，请查看 start-aurora.ps1 输出。
  pause
  exit /b 1
)
start "" http://127.0.0.1:8080
echo Aurora 已启动：http://127.0.0.1:8080
echo 停止请运行 aurora-stop.bat。
timeout /t 3 >nul

@echo off
rem Aurora 主入口：桌面模式（原生窗口，不开浏览器）
rem 数据与配置全部锚定安装目录；PID 记录供精确停止
setlocal
cd /d "%~dp0"
if not exist data mkdir data

set "PYTHONPATH=%~dp0backend\site-packages"
set "AI_ASSISTANT_CONFIG_DIR=%~dp0config"
set "AI_ASSISTANT_DATA_DIR=%~dp0data\sessions"
set "AI_ASSISTANT_UPLOAD_DIR=%~dp0data\uploads"
set "AI_ASSISTANT_SKILLS_DIR=%~dp0config\skills"
set "AI_ASSISTANT_FRONTEND_DIR=%~dp0frontend\dist"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Start-Process -FilePath '%~dp0runtime\python.exe' -ArgumentList '-m','app.desktop' -WorkingDirectory '%~dp0backend' -WindowStyle Minimized -PassThru; Set-Content -Path '%~dp0data\aurora.pid' -Value $p.Id -Encoding ascii"
if errorlevel 1 (
  echo 启动失败，请尝试"aurora-web.bat"（浏览器模式）排查。
  pause
  exit /b 1
)
echo Aurora 桌面窗口已启动（停止请运行 aurora-stop.bat）。
timeout /t 2 >nul

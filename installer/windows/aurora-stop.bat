@echo off
rem 停止 Aurora：按 data\aurora.pid 精确终止（含子进程），不误伤其他 python 进程
setlocal
cd /d "%~dp0"
if not exist data\aurora.pid (
  echo 未找到运行中的 Aurora（无 data\aurora.pid）。
  pause
  exit /b 0
)
for /f %%i in (data\aurora.pid) do taskkill /F /T /PID %%i >nul 2>&1
del data\aurora.pid
echo Aurora 已停止。
timeout /t 2 >nul

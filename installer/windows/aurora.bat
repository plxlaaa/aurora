@echo off
rem 排障入口：带控制台启动桌面模式（主入口是开始菜单「Aurora」，无黑框直起窗口）
rem 若桌面窗口起不来，看本窗口报错或 data\desktop.log
setlocal
cd /d "%~dp0backend"

"%~dp0runtime\python.exe" -m app.desktop
echo.
echo Aurora 已退出（退出码 %ERRORLEVEL%）。日志：data\desktop.log
pause

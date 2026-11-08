@echo off
setlocal
if not "%~1"=="" set AURORA_INSTALL_DIR=%~1
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/plxlaaa/aurora/releases/latest/download/install.ps1 | iex"
endlocal

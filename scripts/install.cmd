@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/plxlaaa/aurora/releases/latest/download/install.ps1 | iex"
endlocal

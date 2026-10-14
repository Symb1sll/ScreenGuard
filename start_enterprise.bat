@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\start_enterprise.ps1" %*
set "SG_EXIT=%ERRORLEVEL%"
if not "%SG_EXIT%"=="0" pause
exit /b %SG_EXIT%

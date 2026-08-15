@echo off
setlocal
cd /d "%~dp0"

rem Make Python UTF-8 independent of PowerShell/cmd console code page.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8:replace"
chcp 65001 >nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0giaodien.ps1"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo GUI stopped with exit code %EXITCODE%.
  echo Check logs\gui-startup.log or send it to technical support.
  echo.
  pause
)
exit /b %EXITCODE%

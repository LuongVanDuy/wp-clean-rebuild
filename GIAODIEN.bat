@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0giaodien.ps1"
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo Giao dien da dung voi ma loi %EXITCODE%.
  echo Vui long xem thong bao phia tren hoac bao ky thuat.
  echo.
  pause
)
exit /b %EXITCODE%

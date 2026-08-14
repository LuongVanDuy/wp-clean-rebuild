@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo        WP CLEAN REBUILD SETUP
echo ========================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set EXITCODE=%ERRORLEVEL%

echo.
if not "%EXITCODE%"=="0" (
  echo Setup failed with exit code %EXITCODE%.
) else (
  echo Setup completed successfully.
)

echo.
pause
exit /b %EXITCODE%

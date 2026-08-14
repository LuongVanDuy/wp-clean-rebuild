@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

call "%~dp0wpclean.bat" xoa-du-an
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo Xoa du an da dung voi ma loi %EXITCODE%.
  echo Khong co thao tac nao duoc thuc hien tren hosting.
  echo.
  pause
)
exit /b %EXITCODE%

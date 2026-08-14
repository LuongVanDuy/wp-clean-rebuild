@echo off
setlocal
cd /d "%~dp0"

set "UV_EXE="

for /f "delims=" %%I in ('where uv 2^>nul') do (
  if not defined UV_EXE set "UV_EXE=%%I"
)

if not defined UV_EXE if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_EXE=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV_EXE if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV_EXE=%LOCALAPPDATA%\Programs\uv\uv.exe"
if not defined UV_EXE if exist "%LOCALAPPDATA%\uv\uv.exe" set "UV_EXE=%LOCALAPPDATA%\uv\uv.exe"

if not defined UV_EXE (
  echo uv runtime was not found.
  echo Run START.bat first.
  echo.
  echo Checked:
  echo   PATH
  echo   %USERPROFILE%\.local\bin\uv.exe
  echo   %LOCALAPPDATA%\Programs\uv\uv.exe
  echo   %LOCALAPPDATA%\uv\uv.exe
  exit /b 1
)

"%UV_EXE%" run wpclean %*

@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed yet.
  echo Run START.bat first.
  exit /b 1
)

uv run wpclean %*

@echo off
title LeadGraph gate runner - close this window to stop
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found. Installing uv from https://astral.sh/uv ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
if not exist outputs\gates mkdir outputs\gates
echo Installing dependencies (uv sync) ...
uv sync --all-extras > outputs\gates\bootstrap.log 2>&1
type outputs\gates\bootstrap.log
uv run python scripts\gate_runner.py
pause

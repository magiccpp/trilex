@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe trilex.py %*
) else (
  python trilex.py %*
)

@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0backend"
"%~dp0.venv\Scripts\python.exe" -m ganttier.cli %*
exit /b %errorlevel%

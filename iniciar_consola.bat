@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Ganttier - Diagnostico
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Ejecute instalar.bat primero.
  goto error
)
set "PYTHONPATH=%~dp0backend"
set "PYTHONDONTWRITEBYTECODE=1"
"%~dp0.venv\Scripts\python.exe" -m ganttier.gui
if errorlevel 1 goto error
exit /b 0
:error
echo ERROR: No se pudo iniciar la GUI. Revise los mensajes anteriores.
pause
exit /b 1

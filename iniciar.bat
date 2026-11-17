@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Ganttier
if not exist ".venv\Scripts\pythonw.exe" goto falta
set "PYTHONPATH=%~dp0backend"
set "PYTHONDONTWRITEBYTECODE=1"
".venv\Scripts\python.exe" -c "import PySide6.QtWidgets, fastapi, uvicorn, holidays"
if errorlevel 1 goto error
rem El launcher prepara stdout/stderr y ejecuta el equivalente a python -m ganttier.gui.
start "" /b "%~dp0.venv\Scripts\pythonw.exe" -m ganttier.gui.launcher
if errorlevel 1 goto error
exit /b 0
:falta
echo ERROR: Falta el entorno de escritorio. Ejecute instalar.bat primero.
goto error
:error
echo ERROR: No se pudo iniciar Ganttier. Use iniciar_consola.bat para diagnosticar.
pause
exit /b 1

@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Ganttier - Construir ejecutable
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Ejecute instalar.bat primero para crear .venv.
  goto error
)
set "PYTHONPATH=%~dp0backend"
set "PYTHONDONTWRITEBYTECODE=1"
set "PIP_CACHE_DIR=%~dp0.venv\pip-cache"
set "PYINSTALLER_CONFIG_DIR=%~dp0build\pyinstaller-cache"
set "TEMP=%~dp0.venv\tmp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%"
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check "pyinstaller>=6.6,<7"
if errorlevel 1 goto error
".venv\Scripts\python.exe" -c "import PySide6.QtPdf, fastapi, uvicorn, holidays, matplotlib, openpyxl, reportlab, docx"
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m PyInstaller --noconfirm ganttier_gui.spec
if errorlevel 1 goto error
if not exist "dist\Ganttier\Ganttier.exe" goto error
echo.
echo Construccion terminada: dist\Ganttier\Ganttier.exe
echo Distribuya la carpeta dist\Ganttier completa, incluido _internal.
pause
exit /b 0
:error
echo.
echo ERROR: No se pudo construir Ganttier. Revise los mensajes anteriores.
pause
exit /b 1

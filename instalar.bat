@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Ganttier - Instalacion de escritorio
echo ============================================
echo   Ganttier - instalacion (solo la primera vez)
echo ============================================
set "PY_CMD="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if defined PY_CMD goto python_ok
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"
if not defined PY_CMD (
  echo ERROR: No se encontro Python 3.10 o superior.
  echo Instalelo desde https://www.python.org/downloads/ marcando "Add python.exe to PATH".
  goto error
)
:python_ok
%PY_CMD% --version
if exist ".venv\Scripts\python.exe" goto venv_ok
echo Creando el entorno local .venv ...
%PY_CMD% -m venv ".venv"
if errorlevel 1 goto error
:venv_ok
set "PIP_CACHE_DIR=%~dp0.venv\pip-cache"
set "TEMP=%~dp0.venv\tmp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%"
if errorlevel 1 goto error
echo Actualizando pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
echo Instalando dependencias de escritorio (PySide6 incluido) ...
".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 goto error
echo Instalando el optimizador exacto OR-Tools (opcional) ...
".venv\Scripts\python.exe" -m pip install -r "requirements-opcional.txt"
if errorlevel 1 (
  echo ADVERTENCIA: OR-Tools no se pudo instalar para esta version de Python.
  echo Ganttier funcionara igual con el optimizador heuristico; CP-SAT quedara deshabilitado.
)
set "PYTHONPATH=%~dp0backend"
".venv\Scripts\python.exe" -c "import PySide6, pytestqt"
if errorlevel 1 goto error
echo Generando plantillas Excel ...
".venv\Scripts\python.exe" -m ganttier.excel_io.templates
if errorlevel 1 echo ADVERTENCIA: no se pudieron regenerar las plantillas; se usaran las incluidas en la carpeta plantillas.
echo.
echo Instalacion terminada. Ejecute iniciar.bat para abrir la GUI o iniciar_consola.bat para diagnostico.
pause
exit /b 0
:error
echo.
echo ERROR: No se pudo completar la instalacion. Revise el mensaje anterior y la conexion a Internet.
pause
exit /b 1

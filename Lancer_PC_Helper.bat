@echo off
cd /d "%~dp0"
set "PYTHON=py"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"
if not exist ".venv\Scripts\python.exe" if exist "python.exe" set "PYTHON=python.exe"

echo Installation / verification des dependances...
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Echec de l'installation des dependances.
  pause
  exit /b 1
)
"%PYTHON%" "main.py"
pause

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
echo Installation de PyInstaller...
"%PYTHON%" -m pip install pyinstaller
if errorlevel 1 (
  echo.
  echo Echec de l'installation de PyInstaller.
  pause
  exit /b 1
)
echo Creation de PC Helper.exe...
"%PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed --name "PC Helper" --add-data "pc_background.png;." main.py
if errorlevel 1 (
  echo.
  echo Echec de la creation de l'EXE.
  pause
  exit /b 1
)
echo.
echo ========================================
echo PC Helper.exe a ete cree dans le dossier dist
echo ========================================
echo.
pause

@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm "PC Helper.spec"
) else (
  python -m PyInstaller --clean --noconfirm "PC Helper.spec"
)
echo.
echo ========================================
echo EXE corrige cree dans le dossier dist
 echo ========================================
pause

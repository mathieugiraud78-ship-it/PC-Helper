@echo off
cd /d "%~dp0"
echo ==========================================
echo       PC HELPER - CREATION DE L'EXE
echo ==========================================
echo.
if not exist "pc_background.png" (
    echo ERREUR : pc_background.png est introuvable.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo ERREUR : environnement .venv introuvable.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean "PC Helper.spec"
echo.
echo Build termine. Ton EXE est dans le dossier dist.
echo.
pause

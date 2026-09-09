@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo PC Helper V4.1.0 - Installation des dependances
echo ================================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERREUR] Environnement .venv introuvable.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pystray pyinstaller
if errorlevel 1 (
    echo [ERREUR] Installation des dependances impossible.
    pause
    exit /b 1
)

echo.
echo ================================================
echo Compilation de PC Helper V4.1.0
 echo ================================================
pyinstaller --noconfirm --clean "PC_Helper_V4.1.0.spec"
if errorlevel 1 (
    echo [ERREUR] Compilation echouee.
    pause
    exit /b 1
)

echo.
echo ================================================
echo BUILD TERMINE
 echo ================================================
echo EXE : dist\PC Helper.exe
pause

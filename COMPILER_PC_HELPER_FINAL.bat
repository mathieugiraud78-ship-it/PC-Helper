@echo off
setlocal
cd /d "%~dp0"
title Compilation finale - PC Helper

echo ================================================
echo        PC HELPER - COMPILATION FINALE
echo ================================================
echo.

if not exist "main.py" (
    echo ERREUR : main.py est introuvable.
    pause
    exit /b 1
)

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

set "PYTHON=.venv\Scripts\python.exe"

echo Verification de PyInstaller...
"%PYTHON%" -m PyInstaller --version
if errorlevel 1 (
    echo Installation de PyInstaller...
    "%PYTHON%" -m pip install pyinstaller
    if errorlevel 1 (
        echo ERREUR : impossible d'installer PyInstaller.
        pause
        exit /b 1
    )
)

echo.
echo Nettoyage de l'ancienne compilation...
if exist "build" rmdir /s /q "build"
if exist "dist\PC Helper.exe" del /q "dist\PC Helper.exe"

echo.
echo Compilation de PC Helper...
"%PYTHON%" -m PyInstaller --noconfirm --clean "PC_Helper_FINAL.spec"

if errorlevel 1 (
    echo.
    echo ================================================
    echo              COMPILATION ECHOUEE
    echo ================================================
    pause
    exit /b 1
)

if not exist "dist\PC Helper.exe" (
    echo.
    echo ERREUR : l'EXE n'a pas ete cree.
    pause
    exit /b 1
)

echo.
echo ================================================
echo             COMPILATION TERMINEE
echo ================================================
echo.
echo EXE cree dans :
echo %CD%\dist\PC Helper.exe
echo.
echo Le decor pc_background.png est integre.
echo.
pause

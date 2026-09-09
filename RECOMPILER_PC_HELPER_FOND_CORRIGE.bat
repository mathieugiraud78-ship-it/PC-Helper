@echo off
cd /d "%~dp0"
echo ================================================
echo   PC HELPER - RECOMPILATION AVEC FOND D'ECRAN
echo ================================================
echo.
if not exist "pc_background.png" if not exist "pc_background.jpg.png" (
 echo ERREUR : image de fond introuvable.
 echo Place pc_background.png dans ce dossier.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
 echo ERREUR : environnement .venv introuvable.
  pause
  exit /b 1
)
if exist "build" rmdir /s /q "build"
if exist "dist\PC Helper.exe" del /f /q "dist\PC Helper.exe"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean "PC Helper.spec"
echo.
echo ================================================
echo   COMPILATION TERMINEE
echo   EXE : dist\PC Helper.exe
echo ================================================
pause

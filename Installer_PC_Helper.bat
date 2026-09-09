@echo off
setlocal
cd /d "%~dp0"
title Installation de PC Helper V3

echo ================================================
echo        PC HELPER V3 - INSTALLATION
echo        Nouvelle interface + nouveau fond
echo ================================================
echo.

set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY where python >nul 2>&1 && set "PY=python"

if not defined PY (
    echo Python n'est pas installe.
    echo Tentative d'installation via WinGet...
    winget install --id Python.Python.3.13 -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo ECHEC : impossible d'installer Python automatiquement.
        echo Installe Python puis relance ce fichier.
        pause
        exit /b 1
    )
    set "PY=py"
)

echo.
echo Installation des dependances...
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ECHEC pendant l'installation des dependances.
    pause
    exit /b 1
)

if not exist "%LOCALAPPDATA%\PC Helper V3" mkdir "%LOCALAPPDATA%\PC Helper V3"
copy /Y "main.py" "%LOCALAPPDATA%\PC Helper V3\main.py" >nul
copy /Y "pc_background.png" "%LOCALAPPDATA%\PC Helper V3\pc_background.png" >nul
copy /Y "requirements.txt" "%LOCALAPPDATA%\PC Helper V3\requirements.txt" >nul

set "TARGET=%LOCALAPPDATA%\PC Helper V3\Lancer_PC_Helper.bat"
(
 echo @echo off
 echo cd /d "%%~dp0"
 echo %PY% main.py
) > "%TARGET%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\PC Helper V3.lnk'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%LOCALAPPDATA%\PC Helper V3'; $s.Description='PC Helper V3'; $s.Save()"

echo.
echo ================================================
echo Installation terminee !
echo Raccourci cree sur le Bureau : PC Helper V3
echo ================================================
echo.
choice /C ON /N /M "Lancer PC Helper maintenant ? [O/N] "
if errorlevel 2 exit /b 0
if errorlevel 1 start "PC Helper V3" "%TARGET%"
exit /b 0

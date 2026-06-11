@echo off
rem === ETC meisai downloader - build distributable folder & zip ===
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found. Run setup.bat first.
    pause & exit /b 1
)

echo [1/4] Installing build tools...
%PYCMD% -m pip install --upgrade pip pyinstaller >nul
%PYCMD% -m pip install -r requirements.txt >nul

echo [2/4] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/4] Building with PyInstaller (this takes a few minutes)...
%PYCMD% -m PyInstaller app.spec --noconfirm
if errorlevel 1 ( echo [ERROR] PyInstaller failed & pause & exit /b 1 )

echo [4/4] Creating zip for distribution...
set "DISTDIR=dist\ETC明細ダウンローダー"
if not exist "%DISTDIR%" (
    echo [ERROR] Build output not found at %DISTDIR%
    pause & exit /b 1
)
set "ZIPNAME=ETC明細ダウンローダー.zip"
if exist "%ZIPNAME%" del "%ZIPNAME%"
powershell -NoProfile -Command "Compress-Archive -Path '%DISTDIR%' -DestinationPath '%ZIPNAME%' -Force"

echo.
echo ============================================================
echo  Build complete.
echo    Folder : %DISTDIR%
echo    Zip    : %CD%\%ZIPNAME%
echo  Upload the zip to your shared folder for distribution.
echo ============================================================
pause

@echo off
rem === 作業員稼働率ツール 初回セットアップ (ASCII only in this file) ===
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found. Install Python and check "Add python.exe to PATH".
    pause
    exit /b 1
)

echo Installing required libraries...
%PYCMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

echo.
echo Done. You can now run utilization_app.bat (GUI) or utilization.bat (CLI).
pause

@echo off
rem === Hks schedule reader - standalone test (ASCII only) ===
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

%PYCMD% -m pip install pywinauto >nul

echo.
echo Make sure the Hks schedule window (Banwari Yoteihyo) is open,
echo then press any key.
pause

%PYCMD% hks_reader.py
echo.
echo Done. See hks_records.txt in this folder.
pause

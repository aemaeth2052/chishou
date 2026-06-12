@echo off
rem === Hks UIA structure inspector (ASCII only in this file) ===
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

echo Installing pywinauto if needed...
%PYCMD% -m pip install pywinauto
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

echo.
echo --- IMPORTANT ---
echo Make sure the Hks system is running and the schedule list
echo is visible on screen. Then press any key to start.
pause

%PYCMD% inspect_hks.py
echo.
echo Done. See inspect_result.txt in this folder.
pause

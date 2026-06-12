@echo off
rem === Hks UIA structure inspector ===
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found.
    pause & exit /b 1
)

echo Installing pywinauto if needed...
%PYCMD% -m pip install pywinauto >nul

echo.
echo --- IMPORTANT ---
echo Please make sure Hks原価システム is running and the schedule list
echo is visible on screen, then press any key to start.
pause

%PYCMD% inspect_hks.py
pause

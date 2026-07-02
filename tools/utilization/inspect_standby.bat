@echo off
rem === Hks standby/off-duty block structure dump (ASCII only) ===
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
echo Make sure the Hks schedule list is visible on screen (not minimized).
echo Then press any key.
pause

%PYCMD% inspect_standby.py
echo.
echo Done. Share standby_dump.txt in this folder with the developer.
pause

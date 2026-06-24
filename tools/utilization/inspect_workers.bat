@echo off
rem === Hks worker inspector for utilization survey (ASCII only) ===
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

echo Installing pywinauto and Pillow if needed...
%PYCMD% -m pip install pywinauto Pillow
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

echo.
echo --- IMPORTANT ---
echo Make sure the Hks system is running and the schedule list
echo is visible on screen (not minimized). Then press any key.
pause

%PYCMD% inspect_workers.py
echo.
echo Done. See workers_inspect.txt and workers_colors.txt in this folder.
pause

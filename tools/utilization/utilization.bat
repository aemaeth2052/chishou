@echo off
rem === Worker utilization calculator (ASCII only in this file) ===
rem Classification uses name-cell background colors (worker_colors.json)
rem plus manual overrides (name_aliases.json). No roster CSV needed.
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
echo Just open the Hks schedule window. No need to maximize or full-screen it.
echo Press any key.
pause

%PYCMD% utilization.py
echo.
echo Done. See utilization_report.txt and utilization_detail.csv in this folder.
pause

@echo off
rem === Worker utilization calculator (ASCII only in this file) ===
rem Usage: drag the roster CSV onto this .bat, or place it as roster.csv here.
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

set "ROSTER=%~1"
if "%ROSTER%"=="" set "ROSTER=%~dp0roster.csv"
if not exist "%ROSTER%" (
    echo [ERROR] Roster CSV not found: %ROSTER%
    echo Drag the roster CSV onto this .bat, or save it as roster.csv here.
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
echo Just open the Hks schedule window. No need to maximize or full-screen it
echo (names and office badges are read as UIA text, not from pixels). Press any key.
pause

%PYCMD% utilization.py --roster "%ROSTER%"
echo.
echo Done. See utilization_report.txt and utilization_detail.csv in this folder.
pause

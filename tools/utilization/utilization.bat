@echo off
rem === Worker utilization calculator (ASCII only in this file) ===
rem Roster: put each office's CSV into a "roster" folder next to this .bat
rem (the tool reads every *.csv there), or drop a single CSV onto this .bat.
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

rem Pick roster source: dragged file > roster\ folder > roster.csv
set "ROSTER=%~1"
if "%ROSTER%"=="" if exist "%~dp0roster\" set "ROSTER=%~dp0roster"
if "%ROSTER%"=="" set "ROSTER=%~dp0roster.csv"
if not exist "%ROSTER%" (
    echo [ERROR] Roster not found: %ROSTER%
    echo Put each office CSV into a "roster" folder here, or save one as roster.csv,
    echo or drag a CSV onto this .bat.
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

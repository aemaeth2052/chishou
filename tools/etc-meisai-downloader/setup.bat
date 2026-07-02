@echo off
rem === ETC meisai downloader - first-time setup ===
cd /d "%~dp0"

rem Detect Python launcher (py -3 preferred, then python)
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found.
    echo Install from https://www.python.org/downloads/
    echo and check "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)
echo Using Python: %PYCMD%

echo [1/2] Installing libraries...
%PYCMD% -m pip install --upgrade pip
%PYCMD% -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] Library install failed & pause & exit /b 1 )

echo [2/2] Downloading browser (Chromium)...
%PYCMD% -m playwright install chromium
if errorlevel 1 ( echo [ERROR] Browser install failed & pause & exit /b 1 )

echo.
echo Setup complete. Double-click run.bat to start.
pause

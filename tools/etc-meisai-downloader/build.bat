@echo off
rem === build distributable (logic lives in build.py) ===
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found. Run setup.bat first.
    pause
    exit /b 1
)

%PYCMD% build.py
pause

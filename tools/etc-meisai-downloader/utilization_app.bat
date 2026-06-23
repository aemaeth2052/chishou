@echo off
rem === Worker utilization GUI launcher (ASCII only in this file) ===
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

rem pywinauto: required to read the Hks schedule. ttkbootstrap: optional theme.
%PYCMD% -m pip install pywinauto ttkbootstrap >nul 2>&1

%PYCMD% utilization_app.py
if errorlevel 1 pause

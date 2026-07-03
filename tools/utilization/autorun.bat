@echo off
rem === Daily unattended aggregation (ASCII only in this file) ===
rem Called by Task Scheduler (see install_autorun.bat).
rem Requires: Hks schedule boards open on this PC.
rem Output goes to autorun.log; history/snapshots/Sheets update as usual.
cd /d "%~dp0"

set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    echo [ERROR] Python not found. >> autorun.log
    exit /b 1
)

echo ---- %date% %time% ---- >> autorun.log
%PYCMD% utilization.py --gsheet >> autorun.log 2>&1

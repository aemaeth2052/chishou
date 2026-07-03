@echo off
rem === Register daily auto-aggregation in Task Scheduler (ASCII only) ===
rem Runs autorun.bat every day at the given time (default 08:00).
rem The Hks schedule boards must be open on this PC at that time.
cd /d "%~dp0"

set "AT=08:00"
set /p AT=Daily run time (HH:MM, Enter = 08:00):
if "%AT%"=="" set "AT=08:00"

schtasks /Create /TN "UtilizationAutoRun" /TR "\"%~dp0autorun.bat\"" /SC DAILY /ST %AT% /F
if errorlevel 1 (
    echo [ERROR] Failed to register. Try running as administrator.
) else (
    echo Registered: runs daily at %AT%. Log: autorun.log
    echo To remove:  schtasks /Delete /TN "UtilizationAutoRun" /F
)
pause

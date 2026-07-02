@echo off
rem === ETC meisai downloader - start ===
cd /d "%~dp0"

rem Launch GUI without a console window. Try launchers in order.
pyw -3 app.py 2>nul
if not errorlevel 1 goto :eof
pythonw app.py 2>nul
if not errorlevel 1 goto :eof

rem Fallback: run with a visible console so errors are readable.
py -3 app.py 2>nul
if not errorlevel 1 goto :eof
python app.py

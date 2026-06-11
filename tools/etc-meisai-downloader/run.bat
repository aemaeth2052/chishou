@echo off
rem === ETC利用明細ダウンローダー 起動 ===
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo 先に setup.bat を実行してください。
    pause
    exit /b 1
)
start "" .venv\Scripts\pythonw.exe app.py

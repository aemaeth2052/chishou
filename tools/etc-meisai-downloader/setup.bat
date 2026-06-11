@echo off
rem === ETC利用明細ダウンローダー 初回セットアップ ===
cd /d "%~dp0"
chcp 65001 >nul

where py >nul 2>&1
if errorlevel 1 (
    echo Python が見つかりません。https://www.python.org/downloads/ からインストールしてください。
    echo インストール時に「Add python.exe to PATH」にチェックを入れてください。
    pause
    exit /b 1
)

echo [1/3] 仮想環境を作成しています...
py -3 -m venv .venv
if errorlevel 1 ( echo 仮想環境の作成に失敗しました & pause & exit /b 1 )

echo [2/3] ライブラリをインストールしています...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 ( echo ライブラリのインストールに失敗しました & pause & exit /b 1 )

echo [3/3] ブラウザ(Chromium)をダウンロードしています...
.venv\Scripts\python -m playwright install chromium
if errorlevel 1 ( echo ブラウザのインストールに失敗しました & pause & exit /b 1 )

echo.
echo セットアップ完了です。run.bat をダブルクリックして起動してください。
pause

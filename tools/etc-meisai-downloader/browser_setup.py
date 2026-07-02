# -*- coding: utf-8 -*-
"""Chromium ブラウザの初回ダウンロード処理

Playwright が要求する Chromium が未インストールなら、初回起動時に
`python -m playwright install chromium` を実行してダウンロードする。
配布版(exe)では同梱しないため、初回のみネット接続が必要。
"""

import os
import subprocess
import sys
import threading

# paths を先にimportして PLAYWRIGHT_BROWSERS_PATH を設定
import paths  # noqa: F401 -- side effect: sets env var

from playwright.sync_api import sync_playwright


def chromium_installed() -> bool:
    """Chromium が利用可能なら True。

    起動のたびにブラウザを立ち上げて閉じると毎回数秒待たされるので、
    まず実行ファイルがディスク上にあるかだけを高速に確認する。
    パスが取れない/見つからないときだけ、従来どおり実際に起動して確かめる。
    """
    try:
        with sync_playwright() as p:
            try:
                exe = p.chromium.executable_path
            except Exception:
                exe = None
            if exe and os.path.exists(exe):
                return True
            # 実行ファイルが見つからないときだけ、実際に起動して最終確認する
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


def install_chromium(log=print) -> bool:
    """Chromium をダウンロード。成功すれば True"""
    log("ブラウザ(Chromium)をダウンロードしています...")
    log("初回のみ約170MBのダウンロードが発生します。完了まで数分かかります。")

    # 同梱Pythonでなく、playwright モジュール経由で実行する
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    # PyInstaller の onedir では sys.executable は app.exe を指す。
    # その場合は subprocess ではうまくいかないので、playwright CLI を直接呼ぶ。
    if getattr(sys, "frozen", False):
        # PyInstaller環境: playwright を Python API 経由でインストール
        try:
            from playwright._impl._driver import compute_driver_executable, get_driver_env
            driver_executable, driver_cli = compute_driver_executable()
            env = get_driver_env()
            cmd = [driver_executable, driver_cli, "install", "chromium"]
        except Exception as e:
            log(f"ダウンローダの初期化に失敗しました: {e}")
            return False

    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        # 現在の環境変数 (PLAYWRIGHT_BROWSERS_PATH を含む) を子プロセスに引き継ぐ
        env = os.environ.copy()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags,
            env=env,
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(line)
        proc.wait()
        if proc.returncode != 0:
            log(f"ダウンロードに失敗しました (returncode={proc.returncode})")
            return False
        log("ブラウザのダウンロードが完了しました")
        return True
    except Exception as e:
        log(f"ダウンロード中にエラー: {e}")
        return False


def ensure_chromium_async(log, on_ready, on_fail):
    """別スレッドで Chromium をチェック&必要ならインストール"""
    def worker():
        if chromium_installed():
            on_ready()
            return
        if install_chromium(log=log):
            on_ready()
        else:
            on_fail()
    threading.Thread(target=worker, daemon=True).start()

# -*- coding: utf-8 -*-
"""ユーザーデータ(設定・履歴)の保存先を解決するヘルパー

開発時(スクリプト実行)と配布版(PyInstaller exe)で同じパスを返す。
旧バージョン(スクリプトと同じフォルダにconfig.jsonがあった)からは
初回起動時に自動移行する。
"""

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "ETC明細ダウンローダー"


def user_data_dir() -> Path:
    """Windowsなら %APPDATA%\\<APP_NAME>、それ以外は ~/.config/<APP_NAME>"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def browsers_dir() -> Path:
    d = user_data_dir() / "browsers"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Playwright が Chromium を探す/インストールする場所を固定する。
# これは playwright をimportする前に環境変数で指定する必要があるため、
# このモジュールが最初にimportされる前提で先頭で設定する。
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir())


def config_path() -> Path:
    return user_data_dir() / "config.json"


def log_dir() -> Path:
    d = user_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def migrate_old_data(old_dir: Path):
    """旧来 (スクリプトと同じフォルダ) の config.json / logs/ をユーザーデータへ移す"""
    old_cfg = old_dir / "config.json"
    new_cfg = config_path()
    if old_cfg.exists() and not new_cfg.exists():
        try:
            shutil.copy2(old_cfg, new_cfg)
        except Exception:
            pass
    old_logs = old_dir / "logs"
    new_logs = log_dir()
    if old_logs.is_dir():
        for child in old_logs.iterdir():
            target = new_logs / child.name
            if target.exists():
                continue
            try:
                shutil.move(str(child), str(target))
            except Exception:
                pass

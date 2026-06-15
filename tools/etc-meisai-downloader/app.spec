# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: ETC明細ダウンローダー (onedir形式)
# 使い方: pyinstaller app.spec
#
# Chromium本体は配布物に含めない (サイズ削減)。
# 初回起動時に browser_setup.py がダウンロードする。

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

# アプリアイコン: assets/icon.ico があれば exe に埋め込む。
# assets/icon.png は実行中ウインドウ用に同梱する (どちらも無ければアイコンなし)。
_icon_ico = os.path.join("assets", "icon.ico")
_icon_png = os.path.join("assets", "icon.png")
app_icon = _icon_ico if os.path.exists(_icon_ico) else None
icon_datas = [(_icon_png, "assets")] if os.path.exists(_icon_png) else []

# playwright と greenlet は collect_all で「Python・データ・C拡張/バイナリ」を丸ごと集める。
# - greenlet: 本体がC拡張 greenlet._greenlet (.pyd)。未同梱だと frozen exe が
#   「No module named 'greenlet._greenlet'」で起動失敗する。
# - playwright: sync_api が greenlet に依存し、実行時に driver(node) も要るため完全収集する。
greenlet_datas, greenlet_binaries, greenlet_hidden = collect_all("greenlet")
playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")

hiddenimports = (
    collect_submodules("ttkbootstrap")
    + collect_submodules("pywinauto")
    + collect_submodules("comtypes")
    + collect_submodules("pypdf")
    + collect_submodules("reportlab")
    + collect_submodules("tkcalendar")
    + collect_submodules("babel")
    + collect_submodules("PIL")
    + playwright_hidden
    + greenlet_hidden
    + ["greenlet", "greenlet._greenlet"]
)

binaries = greenlet_binaries + playwright_binaries

datas = (
    collect_data_files("ttkbootstrap")
    + collect_data_files("reportlab")
    + collect_data_files("tkcalendar")
    + collect_data_files("babel")
    + playwright_datas
    + greenlet_datas
    + icon_datas
)

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ETC明細ダウンローダー",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,    # コンソール窓を出さない
    icon=app_icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ETC明細ダウンローダー",
)

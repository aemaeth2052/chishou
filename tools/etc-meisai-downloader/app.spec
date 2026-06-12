# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: ETC明細ダウンローダー (onedir形式)
# 使い方: pyinstaller app.spec
#
# Chromium本体は配布物に含めない (サイズ削減)。
# 初回起動時に browser_setup.py がダウンロードする。

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

hiddenimports = (
    collect_submodules("ttkbootstrap")
    + collect_submodules("playwright")
    + collect_submodules("pywinauto")
    + collect_submodules("comtypes")
)

datas = collect_data_files("ttkbootstrap") + collect_data_files("playwright")

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
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
    icon=None,
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

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

# playwright/greenlet/pywinauto は collect_all で「Python・データ・C拡張/バイナリ」を丸ごと集める。
# - greenlet: 本体がC拡張 greenlet._greenlet (.pyd)。未同梱だと frozen exe が
#   「No module named 'greenlet._greenlet'」で起動失敗する。
# - playwright: sync_api が greenlet に依存し、実行時に driver(node) も要るため完全収集する。
# - pywinauto/comtypes: 番割取込(UIA)で使う。pywinauto は遅延importのため静的解析で
#   取りこぼしやすく、依存の pywin32(win32api 等)も明示しないと同梱されない。
greenlet_datas, greenlet_binaries, greenlet_hidden = collect_all("greenlet")
playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")
pywinauto_datas, pywinauto_binaries, pywinauto_hidden = collect_all("pywinauto")
comtypes_datas, comtypes_binaries, comtypes_hidden = collect_all("comtypes")

# pywin32: pywinauto の依存。PyInstaller同梱フックを発火させるため明示的に挙げる。
# win32ui / win32clipboard は pywinauto が実行時に import する (win32ui が抜けると
# 番割取込で「DLL load failed while importing win32ui」になる)。
pywin32_hidden = [
    "pywintypes", "pythoncom",
    "win32api", "win32gui", "win32con", "win32process", "win32event",
    "win32ui", "win32clipboard",
    "win32com", "win32com.client",
]

# win32ui.pyd は pywin32 の Pythonwin/ フォルダにある mfc140u.dll に依存する。
# PyInstaller の依存解析が取りこぼすことがあり、その場合ビルド機では動くのに
# 配布先で「DLL load failed while importing win32ui: 指定されたモジュールが
# 見つかりません」で落ちる。ここで明示的に exe と同じ階層へ同梱する。
def _pywin32_mfc_binaries():
    import glob
    import sysconfig
    dlls = []
    for key in ("purelib", "platlib"):
        pythonwin = os.path.join(sysconfig.get_paths()[key], "pythonwin")
        dlls += glob.glob(os.path.join(pythonwin, "mfc*.dll"))
    # 重複除去して exe 直下 (win32ui.pyd と同じ場所) に置く
    return [(p, ".") for p in sorted(set(dlls))]

pywin32_mfc_binaries = _pywin32_mfc_binaries()

hiddenimports = (
    collect_submodules("ttkbootstrap")
    + collect_submodules("pypdf")
    + collect_submodules("reportlab")
    + collect_submodules("tkcalendar")
    + collect_submodules("babel")
    + collect_submodules("PIL")
    + playwright_hidden
    + greenlet_hidden
    + pywinauto_hidden
    + comtypes_hidden
    + pywin32_hidden
    # comtypes.gen: ビルド前に事前生成した UIA ラッパ。frozen exe で実行時生成が
    # できないため、生成済みモジュールを同梱して番割取込(UIA)を動かす。
    + ["greenlet", "greenlet._greenlet", "comtypes.gen"]
)

binaries = (
    greenlet_binaries + playwright_binaries
    + pywinauto_binaries + comtypes_binaries
    + pywin32_mfc_binaries
)

datas = (
    collect_data_files("ttkbootstrap")
    + collect_data_files("reportlab")
    + collect_data_files("tkcalendar")
    + collect_data_files("babel")
    + playwright_datas + greenlet_datas
    + pywinauto_datas + comtypes_datas
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

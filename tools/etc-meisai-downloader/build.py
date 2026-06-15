# -*- coding: utf-8 -*-
"""配布物ビルドスクリプト

build.bat から呼ばれる。PyInstaller で onedir ビルドし、zip に固める。
日本語(アプリ名など)を扱うため、バッチではなくPythonで処理する。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = "ETC明細ダウンローダー"


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd))
    res = subprocess.run(cmd, **kw)
    if res.returncode != 0:
        print(f"[ERROR] command failed (returncode={res.returncode})")
        sys.exit(1)


def make_icon(py):
    """assets/icon.png から Windows用 icon.ico を生成する。
    PNG が無ければアイコンなしでビルドを続行する。
    """
    png = BASE_DIR / "assets" / "icon.png"
    ico = BASE_DIR / "assets" / "icon.ico"
    if not png.exists():
        print("  assets/icon.png が無いため、アイコンなしでビルドします")
        return
    try:
        from PIL import Image
    except ImportError:
        run([py, "-m", "pip", "install", "pillow"], stdout=subprocess.DEVNULL)
        from PIL import Image
    img = Image.open(png).convert("RGBA")
    # Windowsの各表示サイズ分をまとめた .ico を書き出す
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico, format="ICO", sizes=sizes)
    print(f"  アイコンを生成しました: {ico.name}")


def check_deps(py):
    """ビルド機に必要なモジュール (特にC拡張の greenlet._greenlet) が
    importできるか先に確認する。揃っていなければ同梱漏れの配布物になるので、
    ビルド前に止めて原因を明確にする。"""
    code = ("import greenlet._greenlet, playwright, pywinauto, comtypes, "
            "pywintypes, pythoncom, win32api, "
            "PIL, reportlab, tkcalendar, ttkbootstrap")
    res = subprocess.run([py, "-c", code],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if res.returncode != 0:
        print("[ERROR] ビルド機に必要なモジュールが揃っていません:")
        print((res.stdout or b"").decode("utf-8", "replace").strip())
        print("        → pip install -r requirements.txt を実行してから再度ビルドしてください")
        sys.exit(1)


def smoke_test(dist_dir):
    """ビルドした exe を --smoke-test で起動し、依存モジュールの同梱漏れが
    ないか (起動時importが通るか) を確認する。GUIは開かず終了コードだけ見る。
    壊れた配布物を zip 化して配ってしまわないための最終チェック。"""
    exe = dist_dir / (APP_NAME + (".exe" if sys.platform == "win32" else ""))
    if not exe.exists():
        print(f"[ERROR] 実行ファイルが見つかりません: {exe}")
        sys.exit(1)
    result_file = dist_dir / "smoke_test_result.txt"
    if result_file.exists():
        result_file.unlink()
    env = dict(os.environ, ETC_SMOKE_OUT=str(result_file))
    try:
        res = subprocess.run([str(exe), "--smoke-test"], timeout=120, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        print("[ERROR] 起動チェックがタイムアウトしました。")
        print("        exe を直接実行し『Unhandled exception in script』等の")
        print("        エラーダイアログが出ていないか確認してください (同梱漏れの可能性)。")
        sys.exit(1)
    detail = ""
    if result_file.exists():
        detail = result_file.read_text(encoding="utf-8", errors="replace").strip()
        result_file.unlink()  # 配布zipに混ぜない
    if res.returncode != 0 or detail.startswith("FAILED"):
        print("[ERROR] 配布物が正常に起動しません (依存モジュールの同梱漏れの可能性):")
        print(detail or (res.stdout or b"").decode("utf-8", "replace").strip()[-2000:]
              or "(詳細不明。exe を直接実行して確認してください)")
        sys.exit(1)


def main():
    py = sys.executable

    print("[1/5] ビルドツールと依存モジュールを準備しています...")
    run([py, "-m", "pip", "install", "--upgrade", "pip", "pyinstaller"],
        stdout=subprocess.DEVNULL)
    run([py, "-m", "pip", "install", "-r", str(BASE_DIR / "requirements.txt")],
        stdout=subprocess.DEVNULL)
    check_deps(py)
    make_icon(py)

    print("[2/5] 前回のビルドを削除しています...")
    for d in (BASE_DIR / "build", BASE_DIR / "dist"):
        if d.exists():
            shutil.rmtree(d)

    print("[3/5] PyInstaller でビルドしています (数分かかります)...")
    run([py, "-m", "PyInstaller", str(BASE_DIR / "app.spec"), "--noconfirm"],
        cwd=str(BASE_DIR))

    dist_dir = BASE_DIR / "dist" / APP_NAME
    if not dist_dir.exists():
        print(f"[ERROR] ビルド結果が見つかりません: {dist_dir}")
        sys.exit(1)

    print("[4/5] 配布物の起動チェックをしています...")
    smoke_test(dist_dir)
    print("  起動チェック OK (依存モジュールの同梱を確認)")

    print("[5/5] 配布用zipを作成しています...")
    zip_base = BASE_DIR / APP_NAME  # → ETC明細ダウンローダー.zip
    zip_path = Path(shutil.make_archive(str(zip_base), "zip",
                                        root_dir=str(dist_dir.parent),
                                        base_dir=APP_NAME))

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print()
    print("=" * 60)
    print("ビルド完了")
    print(f"  フォルダ : {dist_dir}")
    print(f"  zip      : {zip_path}  ({size_mb:.0f} MB)")
    print("  このzipを社内共有フォルダにアップロードしてください")
    print("=" * 60)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""配布物ビルドスクリプト

build.bat から呼ばれる。PyInstaller で onedir ビルドし、zip に固める。
日本語(アプリ名など)を扱うため、バッチではなくPythonで処理する。
"""

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


def main():
    py = sys.executable

    print("[1/4] ビルドツールを準備しています...")
    run([py, "-m", "pip", "install", "--upgrade", "pip", "pyinstaller"],
        stdout=subprocess.DEVNULL)
    run([py, "-m", "pip", "install", "-r", str(BASE_DIR / "requirements.txt")],
        stdout=subprocess.DEVNULL)
    make_icon(py)

    print("[2/4] 前回のビルドを削除しています...")
    for d in (BASE_DIR / "build", BASE_DIR / "dist"):
        if d.exists():
            shutil.rmtree(d)

    print("[3/4] PyInstaller でビルドしています (数分かかります)...")
    run([py, "-m", "PyInstaller", str(BASE_DIR / "app.spec"), "--noconfirm"],
        cwd=str(BASE_DIR))

    dist_dir = BASE_DIR / "dist" / APP_NAME
    if not dist_dir.exists():
        print(f"[ERROR] ビルド結果が見つかりません: {dist_dir}")
        sys.exit(1)

    print("[4/4] 配布用zipを作成しています...")
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

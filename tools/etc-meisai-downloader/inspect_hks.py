# -*- coding: utf-8 -*-
"""Hks原価システム UIA構造調査ツール

Hks原価システムの一覧画面が UI Automation 経由でセル単位に
読めるかどうかを調査する。読めるなら本格実装に進める。

使い方:
  1. Hks原価システムを起動し、調査したい「番割一覧」画面を開いておく
  2. python inspect_hks.py を実行
  3. 出力 (inspect_result.txt) を共有してもらう
"""

import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "inspect_result.txt"


def main():
    lines = []

    def log(msg=""):
        print(msg)
        lines.append(str(msg))

    try:
        from pywinauto import Desktop
        from pywinauto.controls.uia_controls import (
            ListItemWrapper, ListViewWrapper,
        )  # noqa: F401
    except ImportError:
        print("[ERROR] pywinauto がインストールされていません")
        print("        py -m pip install pywinauto を実行してください")
        sys.exit(1)

    log("=" * 70)
    log(f"調査日時: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 70)

    # Hks の窓を探す (タイトルに「原価システム」を含む)
    desktop = Desktop(backend="uia")
    candidates = []
    for w in desktop.windows():
        try:
            title = w.window_text()
        except Exception:
            continue
        if "原価システム" in title or "Hks" in title or "HKS" in title:
            candidates.append((title, w))

    if not candidates:
        log("[ERROR] Hks原価システムの窓が見つかりませんでした。")
        log("        起動した状態で再度実行してください。")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    log(f"\n候補ウィンドウ {len(candidates)} 件:")
    for i, (title, _) in enumerate(candidates):
        log(f"  [{i}] {title}")
    target = candidates[0][1]
    log(f"\n→ [0] を対象に調査します\n")

    # ウィンドウ直下の階層を浅く出す
    log("-" * 70)
    log("コントロール階層 (浅め、最大3階層)")
    log("-" * 70)
    try:
        target.print_control_identifiers(depth=3)
    except Exception as e:
        log(f"print_control_identifiers でエラー: {e}")

    # グリッドらしきものを再帰検索
    log("\n" + "-" * 70)
    log("グリッド/テーブル候補のコントロールを検出")
    log("-" * 70)

    GRID_TYPES = {"DataGrid", "Table", "List", "Custom", "Pane"}
    grid_candidates = []

    def walk(ctrl, depth=0):
        try:
            ct = ctrl.element_info.control_type
            rect = ctrl.rectangle()
            cls = ctrl.element_info.class_name or ""
        except Exception:
            return
        if depth > 8:
            return
        # サイズが画面の半分以上 & グリッドっぽい control_type
        try:
            w = rect.right - rect.left
            h = rect.bottom - rect.top
        except Exception:
            w = h = 0
        if ct in GRID_TYPES and w > 400 and h > 200:
            grid_candidates.append((ct, cls, rect, ctrl))
        try:
            children = ctrl.children()
        except Exception:
            children = []
        for c in children:
            walk(c, depth + 1)

    walk(target)
    log(f"検出: {len(grid_candidates)} 件")
    for i, (ct, cls, rect, _) in enumerate(grid_candidates[:5]):
        log(f"  [{i}] control_type={ct!r} class={cls!r} rect={rect}")

    if not grid_candidates:
        log("\n[判定] グリッド候補が見つかりません。")
        log("        UI Automationでセル読み取りは難しい可能性が高い (独自描画の疑い)")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    grid = grid_candidates[0][3]
    log(f"\n→ 候補 [0] を詳細調査します")

    # 子要素の特徴を集計
    log("\n" + "-" * 70)
    log("グリッド配下の子要素の特徴")
    log("-" * 70)
    try:
        children = grid.children()
        log(f"直下の子要素数: {len(children)}")
        type_counts = {}
        sample_texts = []
        for c in children[:200]:
            try:
                ct = c.element_info.control_type
            except Exception:
                ct = "?"
            type_counts[ct] = type_counts.get(ct, 0) + 1
            try:
                txt = c.window_text() or ""
                if txt.strip():
                    sample_texts.append(f"[{ct}] {txt[:60]}")
            except Exception:
                pass
        log("control_type の内訳:")
        for ct, n in sorted(type_counts.items(), key=lambda x: -x[1]):
            log(f"  {ct}: {n}")
        log(f"\nテキストが取れたサンプル (最大20件):")
        for s in sample_texts[:20]:
            log(f"  {s}")
        if not sample_texts:
            log("  (テキストが1つも取れませんでした → 独自描画グリッドの可能性が高い)")
    except Exception as e:
        log(f"子要素の取得でエラー: {e}")

    # グリッドが Table / DataGrid なら GridPattern を試す
    log("\n" + "-" * 70)
    log("GridPattern (テーブルAPI) を試す")
    log("-" * 70)
    try:
        from pywinauto.uia_defines import IUIA
        iuia = IUIA()
        el = grid.element_info.element
        gp = el.GetCurrentPattern(iuia.known_patterns["Grid"].UIA_pattern_id)
        if gp:
            log("Grid パターン対応!  → セルアクセス可能")
        else:
            log("Grid パターン非対応 → セル単位の読み取りは不可")
    except Exception as e:
        log(f"パターン取得で例外: {e}")

    log("\n" + "=" * 70)
    log("調査完了。inspect_result.txt を共有してください。")
    log("=" * 70)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n結果ファイル: {OUT}")


if __name__ == "__main__":
    main()

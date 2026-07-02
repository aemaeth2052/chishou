# -*- coding: utf-8 -*-
"""Hks UIA調査 v6 - 番割予定表の構造ダンプ

v5で番割予定表(WPF)がUIAで完全に読めると判明した。
v6はカードの入れ子構造を把握するため、ツリーを階層付きでダンプする。
これを元に「顧客→現場→車両→作業員」を組み立てるパーサーを書く。

出力:
  inspect_result.txt  : 概要 (ここに貼ってもらう用、先頭120行程度)
  hks_tree.txt        : 完全なツリー (全ノード、必要なら添付)
"""

import sys
import time
import traceback
from pathlib import Path

OUT = Path(__file__).resolve().parent / "inspect_result.txt"
TREE = Path(__file__).resolve().parent / "hks_tree.txt"


def main():
    try:
        from pywinauto import Desktop
    except ImportError:
        print("[ERROR] pywinauto 未インストール")
        sys.exit(1)

    # 番割予定表ウィンドウ (UIA)
    target = None
    for w in Desktop(backend="uia").windows():
        try:
            if "予定表" in w.window_text():
                target = w
                break
        except Exception:
            pass
    if not target:
        OUT.write_text("番割予定表ウィンドウが見つかりません。表示して再実行してください。",
                       encoding="utf-8")
        print("番割予定表が見つかりません")
        return

    # 全ノードを階層付きで収集
    tree_lines = []
    summary = []

    def emit_tree(line):
        tree_lines.append(line)

    def walk(c, depth=0):
        try:
            ct = c.element_info.control_type
        except Exception:
            ct = "?"
        try:
            name = (c.element_info.name or "").replace("\r\n", "\\n").replace("\n", "\\n")
        except Exception:
            name = ""
        try:
            wt = (c.window_text() or "").replace("\r\n", "\\n").replace("\n", "\\n")
        except Exception:
            wt = ""
        text = name or wt
        try:
            r = c.rectangle()
            rect = f"({r.left},{r.top},{r.right},{r.bottom})"
        except Exception:
            rect = "?"
        indent = "  " * depth
        emit_tree(f"{indent}[{ct}] {rect} {text[:80]!r}")
        try:
            children = c.children()
        except Exception:
            children = []
        for ch in children:
            walk(ch, depth + 1)

    walk(target)

    # ツリー全体をファイルに
    header = [
        "=" * 70,
        f"番割予定表 構造ダンプ  {time.strftime('%Y-%m-%d %H:%M:%S')}  (v6)",
        f"総ノード数: {len(tree_lines)}",
        "=" * 70,
    ]
    TREE.write_text("\n".join(header + tree_lines), encoding="utf-8")

    # 概要: Custom(カード/ブロックの器)に絞って構造を見せる
    summary += header
    summary.append("")
    summary.append("【ツリー先頭150行 (最初の数カード分の構造)】")
    summary.append("-" * 70)
    # テキストが空のノードは構造把握に有用なので残すが、見やすさのため
    # 先頭150行を概要に出す
    summary += tree_lines[:150]
    summary.append("")
    summary.append("-" * 70)
    summary.append(f"完全なツリーは hks_tree.txt に保存しました ({len(tree_lines)} 行)。")
    summary.append("構造が複雑な場合は hks_tree.txt の中身も共有してください。")

    OUT.write_text("\n".join(summary), encoding="utf-8")
    print(f"概要: {OUT}")
    print(f"完全ツリー: {TREE}")
    print(f"総ノード数: {len(tree_lines)}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.write_text("FATAL:\n" + traceback.format_exc(), encoding="utf-8")
        print("FATAL. See inspect_result.txt")
        raise

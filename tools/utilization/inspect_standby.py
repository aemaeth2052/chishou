# -*- coding: utf-8 -*-
"""番割「待機/休み」枠の構造ダンプ(稼働率の待機/休み取りこぼし調査用)

read_all_assignments が待機/休み枠の人を 0 名としか拾えないとき、その枠が画面上で
どんな構造(見出しテキスト・ブロックの入れ子・氏名セルの位置)になっているかを
そのまま吐き出して、修正の手がかりにする。

inspect_workers.py は待機/休み枠を意図的にスキップするので、調査にはこちらを使う。

実行: Hks で番割予定表を出した状態(最小化しない)で、同じ PC で
      inspect_standby.bat をダブルクリック。
出力: standby_dump.txt をそのまま開発者(Claude)に共有。
"""

import sys
import traceback
from pathlib import Path

import hks_reader as hr

HERE = Path(__file__).resolve().parent
OUT = HERE / "standby_dump.txt"

# 待機/休み枠の見出しに出そうな語(表記ゆれ込みで広めに)
KEYS = ("待機", "休み", "休", "待", "留守", "欠", "有給", "代休", "公休", "明け")


def _txt(node):
    return (node.get("text") or "")


def _has_key(node):
    """このノードか子孫のテキストに待機/休み系の語が含まれるか。"""
    if any(k in _txt(node) for k in KEYS):
        return True
    return any(_has_key(c) for c in node.get("children", []))


def _leaf_texts(node, out):
    for c in node.get("children", []):
        t = _txt(c).strip()
        if t:
            out.append(t)
        _leaf_texts(c, out)


def dump_tree(node, depth, lines, max_depth=7):
    ind = "  " * depth
    txt = _txt(node).strip().replace("\n", "\\n")
    if len(txt) > 44:
        txt = txt[:44] + "…"
    flag = "   <<<< 待機/休み候補" if any(k in _txt(node) for k in KEYS) else ""
    lines.append(f"{ind}[{node.get('ct', '')}] {txt!r} rect={node.get('rect')}{flag}")
    kids = node.get("children", [])
    if depth >= max_depth:
        if kids:
            lines.append(f"{ind}  …(子 {len(kids)} 省略)")
        return
    for c in kids:
        dump_tree(c, depth + 1, lines, max_depth)


def main():
    metas = hr.enumerate_windows()
    lines = []

    def w(s=""):
        print(s)
        lines.append(str(s))

    if not metas:
        w("番割予定表ウィンドウが見つかりません。Hksで番割予定表を表示してください。")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    w("=" * 78)
    w(f"番割「待機/休み」枠 構造ダンプ  (予定表 {len(metas)} 画面)")
    w("=" * 78)

    for m in metas:
        office = m.get("office", "") or "営業所不明"
        date = m.get("date", "") or "日付不明"
        win = m["win"]

        pane = None
        for c in win.children():
            try:
                if c.element_info.control_type == "Pane":
                    pane = c
                    break
            except Exception:
                continue

        w("\n" + "#" * 78)
        w(f"# {office}  {date}")
        w("#" * 78)
        if pane is None:
            w("  Pane が見つからずスキップ")
            continue
        try:
            root = hr._snap_cached(pane)
        except Exception as e:
            w(f"  高速読取失敗→通常方式: {e}")
            root = hr._snap(pane)

        kids = root.get("children", [])

        # (1) pane 直下の子を順番どおりに一覧(見出しと枠の並びを把握)
        w("\n--- pane直下の子(順番どおり / Text=見出し候補, Custom=現場・枠ブロック) ---")
        for i, child in enumerate(kids):
            ct = child.get("ct", "")
            t = _txt(child).strip().replace("\n", "\\n")
            if len(t) > 46:
                t = t[:46] + "…"
            n_kids = len(child.get("children", []))
            flag = "  <<<< 待機/休み候補" if _has_key(child) else ""
            leafs = []
            if ct != "Text":
                _leaf_texts(child, leafs)
            sample = ("  中身:" + " / ".join(leafs[:6])) if leafs else ""
            w(f"  [{i:>3}] {ct:<7} kids={n_kids:<3} {t!r}{flag}{sample}")

        # (2) 待機/休み を含むノードの周辺を、フル subtree でダンプ
        w("\n--- 待機/休み を含むノードとその前後の subtree(全文) ---")
        found = False
        for i, child in enumerate(kids):
            if not _has_key(child):
                continue
            found = True
            w(f"\n=== child[{i}] とその前後 ===")
            for j in range(max(0, i - 1), min(len(kids), i + 2)):
                sub = []
                dump_tree(kids[j], 0, sub)
                w(f"-- child[{j}] --")
                w("\n".join(sub))
        if not found:
            w("  (待機/休み を含むノードが pane 直下に見つかりませんでした。"
              "枠の見出しが別の語か、別の入れ子にある可能性。"
              "上の (1) で休みの人が並んでいそうな見出しがあれば、その番号を教えてください。)")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print()
    print(f"出力: {OUT}")
    print("このファイルを開発者(Claude)に共有してください。")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.write_text("FATAL:\n" + traceback.format_exc(), encoding="utf-8")
        print("FATAL. standby_dump.txt を確認してください。")
        raise

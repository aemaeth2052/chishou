# -*- coding: utf-8 -*-
"""Hks UIA構造調査ツール v4 (実データ逆引き)

v3で win32 backend なら SysListView32 等が見えると判明した。
v4 は「実際の顧客名・車両番号がどのコントロールに入っているか」を
逆引きして、実装の足がかり(クラス・位置・列構造)を確定させる。
"""

import sys
import time
import traceback
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent / "inspect_result.txt"

# 画面に写っていた実データ (逆引きキーワード)
DATA_KEYWORDS = [
    "建設", "工務", "株式会社", "有限会社", "Caravar", "造園",
    "コウホウ", "エバーブレス", "三共", "共和", "小出",
    "川口", "渋谷", "品川", "船橋", "流山", "袖ヶ浦", "目黒", "池袋",
    "1499", "1500", "1606", "7772", "6031", "4345",
    "軽27", "軽17", "軽23",
]


def main():
    lines = []

    def log(msg=""):
        s = str(msg)
        print(s)
        lines.append(s)

    try:
        from pywinauto import Desktop
    except ImportError:
        print("[ERROR] pywinauto 未インストール")
        sys.exit(1)

    log("=" * 70)
    log(f"調査日時: {time.strftime('%Y-%m-%d %H:%M:%S')}  (v4 実データ逆引き)")
    log("=" * 70)

    # Hksプロセス特定
    pid = None
    for w in Desktop(backend="uia").windows():
        try:
            if "原価システム" in w.window_text():
                pid = w.process_id()
                break
        except Exception:
            pass
    if not pid:
        log("Hksが見つかりません")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return
    log(f"pid={pid}\n")

    # win32 backend で全ウィンドウ → 一番大きい本体ウィンドウを対象に
    desktop = Desktop(backend="win32")
    procwins = []
    for w in desktop.windows():
        try:
            if w.process_id() == pid:
                procwins.append(w)
        except Exception:
            pass
    # 「番割」を含むタイトル or 最大面積のウィンドウ
    main_win = None
    for w in procwins:
        try:
            if "番割" in w.window_text():
                main_win = w
                break
        except Exception:
            pass
    if not main_win:
        main_win = max(procwins, key=lambda w: _area(w), default=None)
    if not main_win:
        log("対象ウィンドウなし")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    log(f"対象ウィンドウ: {main_win.window_text()[:80]}")
    log(f"  class={main_win.class_name()}\n")

    # 全コントロールを集める (class, rect, text, depth)
    all_ctrls = []

    def walk(ctrl, depth=0):
        if depth > 30:
            return
        try:
            cls = ctrl.class_name()
        except Exception:
            cls = "?"
        try:
            text = ctrl.window_text() or ""
        except Exception:
            text = ""
        try:
            r = ctrl.rectangle()
            rect = (r.left, r.top, r.right, r.bottom)
        except Exception:
            rect = (0, 0, 0, 0)
        all_ctrls.append({"depth": depth, "cls": cls, "text": text, "rect": rect, "ctrl": ctrl})
        try:
            for c in ctrl.children():
                walk(c, depth + 1)
        except Exception:
            pass

    log("全コントロール収集中 (時間がかかります)...")
    walk(main_win)
    log(f"  収集数: {len(all_ctrls)}\n")

    # ============ 実データを含むコントロールを逆引き ============
    log("-" * 70)
    log("実データ(顧客名・車両番号)を含むコントロール")
    log("-" * 70)
    hits = []
    for c in all_ctrls:
        for kw in DATA_KEYWORDS:
            if kw in c["text"]:
                hits.append(c)
                break
    log(f"ヒット: {len(hits)} 件")
    # クラス別集計
    hit_cls = Counter(c["cls"] for c in hits)
    log("ヒットしたコントロールのクラス内訳:")
    for cls, n in hit_cls.most_common():
        log(f"  {cls} : {n}")
    log("")
    log("ヒット詳細 (最大40件、画面上の位置つき):")
    for c in sorted(hits, key=lambda x: (x["rect"][1], x["rect"][0]))[:40]:
        x, y = c["rect"][0], c["rect"][1]
        log(f"  ({x:>5},{y:>4}) [{_short_cls(c['cls'])}] {c['text'][:50]!r}")
    log("")

    # ============ SysListView32 を詳しく ============
    log("-" * 70)
    log("SysListView32 コントロールの詳細 (グリッド本体候補)")
    log("-" * 70)
    listviews = [c for c in all_ctrls if "SysListView32" in c["cls"]]
    log(f"SysListView32 総数: {len(listviews)}")
    # 大きいものを表示
    big_lv = sorted(listviews, key=lambda c: -_rect_area(c["rect"]))[:5]
    for c in big_lv:
        r = c["rect"]
        w, h = r[2] - r[0], r[3] - r[1]
        log(f"  size={w}x{h} pos=({r[0]},{r[1]}) text={c['text'][:30]!r}")
        # この ListView の item を読めるか試す
        try:
            lv = c["ctrl"]
            item_count = lv.item_count() if hasattr(lv, "item_count") else "?"
            col_count = lv.column_count() if hasattr(lv, "column_count") else "?"
            log(f"    → item_count={item_count}  column_count={col_count}")
            if hasattr(lv, "item_count") and lv.item_count() and lv.item_count() > 0:
                # 先頭数行を読む
                texts = lv.texts()
                log(f"    → texts()先頭: {texts[:8]}")
        except Exception as e:
            log(f"    → item読み取り試行でエラー: {e}")
    log("")

    # ============ EDIT / STATIC で実データに近いもの ============
    log("-" * 70)
    log("EDIT/STATIC のテキストサンプル (画面中央付近=グリッド領域)")
    log("-" * 70)
    edits = [c for c in all_ctrls
             if ("EDIT" in c["cls"] or "STATIC" in c["cls"]) and c["text"].strip()]
    log(f"テキストありEDIT/STATIC: {len(edits)} 件")
    for c in sorted(edits, key=lambda x: (x["rect"][1], x["rect"][0]))[:40]:
        x, y = c["rect"][0], c["rect"][1]
        log(f"  ({x:>5},{y:>4}) [{_short_cls(c['cls'])}] {c['text'][:50]!r}")

    log("\n" + "=" * 70)
    if hits:
        log("⭕ 実データがコントロールから取得できました → 実装可能")
        log(f"   主にこのクラスに入っています: {hit_cls.most_common(1)[0][0] if hit_cls else '?'}")
    else:
        log("△ キーワード逆引きでは実データが取れませんでした。")
        log("   (日付が変わって画面の値が変わった可能性。下の EDIT/STATIC 一覧を確認)")
    log("=" * 70)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n結果: {OUT}")


def _area(w):
    try:
        r = w.rectangle()
        return (r.right - r.left) * (r.bottom - r.top)
    except Exception:
        return 0


def _rect_area(r):
    return (r[2] - r[0]) * (r[3] - r[1])


def _short_cls(cls):
    # WindowsForms10.SysListView32.app.0.xxx → SysListView32
    if "." in cls:
        parts = cls.split(".")
        for p in parts:
            if p and p not in ("WindowsForms10", "app", "0") and not p.isdigit():
                return p
    return cls


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.write_text("FATAL:\n" + traceback.format_exc(), encoding="utf-8")
        print("FATAL. See inspect_result.txt")
        raise

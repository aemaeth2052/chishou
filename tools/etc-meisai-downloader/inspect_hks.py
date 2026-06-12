# -*- coding: utf-8 -*-
"""Hks UIA構造調査ツール v3 (最終)

v2でメニューバーしか取れなかったので、
- 同じプロセスの全ウィンドウを列挙 (MDI子ウィンドウ含む)
- UIA backend と win32 backend の両方を試す
- "番割" を含むウィンドウを優先的に深掘り
を実施する。
"""

import sys
import time
import traceback
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent / "inspect_result.txt"
MAX_DEPTH = 25


def main():
    lines = []

    def log(msg=""):
        s = str(msg)
        print(s)
        lines.append(s)

    try:
        from pywinauto import Desktop
    except ImportError:
        print("[ERROR] pywinauto がインストールされていません")
        sys.exit(1)

    log("=" * 70)
    log(f"調査日時: {time.strftime('%Y-%m-%d %H:%M:%S')}  (v3)")
    log("=" * 70)

    # =========================================================
    # 同じプロセスの全ウィンドウを列挙 (MDI子も含めて)
    # =========================================================
    log("\n[1] Hks プロセスを特定")
    log("-" * 70)
    target_pid = None
    desktop_uia = Desktop(backend="uia")
    for w in desktop_uia.windows():
        try:
            title = w.window_text()
        except Exception:
            continue
        if "原価システム" in title:
            try:
                target_pid = w.process_id()
                log(f"見つかりました: pid={target_pid}  title={title[:80]}")
                break
            except Exception:
                pass
    if not target_pid:
        log("Hks原価システムが見つかりません。起動して再実行してください。")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    # =========================================================
    # UIA backend で同プロセスのウィンドウを全部列挙
    # =========================================================
    log("\n[2] 同プロセスのウィンドウを列挙 (UIA backend)")
    log("-" * 70)
    uia_windows = []
    for w in desktop_uia.windows():
        try:
            if w.process_id() == target_pid:
                uia_windows.append(w)
        except Exception:
            pass
    log(f"UIA で見えるウィンドウ数: {len(uia_windows)}")
    for i, w in enumerate(uia_windows):
        try:
            t = w.window_text()
            r = w.rectangle()
            log(f"  [{i}] size={r.right - r.left}x{r.bottom - r.top}  title={t[:80]}")
        except Exception:
            pass

    # =========================================================
    # win32 backend でも列挙 (古いコントロールが見えることがある)
    # =========================================================
    log("\n[3] 同プロセスのウィンドウを列挙 (win32 backend)")
    log("-" * 70)
    win32_windows = []
    try:
        desktop_w32 = Desktop(backend="win32")
        for w in desktop_w32.windows():
            try:
                if w.process_id() == target_pid:
                    win32_windows.append(w)
            except Exception:
                pass
        log(f"win32 で見えるウィンドウ数: {len(win32_windows)}")
        for i, w in enumerate(win32_windows):
            try:
                t = w.window_text()
                cls = w.class_name()
                r = w.rectangle()
                log(f"  [{i}] cls={cls!r:<30} "
                    f"size={r.right - r.left}x{r.bottom - r.top}  title={t[:80]}")
            except Exception:
                pass
    except Exception as e:
        log(f"win32 backend 列挙でエラー: {e}")

    # =========================================================
    # 番割一覧ウィンドウを深掘り (UIA)
    # =========================================================
    log("\n[4] 「番割」を含むウィンドウを UIA で深掘り")
    log("-" * 70)
    targets = []
    for w in uia_windows:
        try:
            t = w.window_text()
            if "番割" in t or "一覧" in t:
                targets.append(w)
        except Exception:
            pass
    if not targets:
        # 番割が見つからない場合は親ウィンドウ自体を対象に
        targets = uia_windows[:1]
    for ti, target in enumerate(targets):
        try:
            log(f"\n対象 [{ti}] {target.window_text()[:80]}")
        except Exception:
            continue
        deep_walk(target, log)

    # =========================================================
    # 番割一覧ウィンドウを深掘り (win32)
    # =========================================================
    log("\n[5] 「番割」を含むウィンドウを win32 で深掘り")
    log("-" * 70)
    w32_targets = []
    for w in win32_windows:
        try:
            t = w.window_text()
            if "番割" in t or "一覧" in t:
                w32_targets.append(w)
        except Exception:
            pass
    if not w32_targets and win32_windows:
        w32_targets = win32_windows[:3]
    for ti, target in enumerate(w32_targets):
        try:
            log(f"\nwin32対象 [{ti}] cls={target.class_name()} "
                f"title={target.window_text()[:80]}")
        except Exception:
            continue
        deep_walk_w32(target, log)

    log("\n" + "=" * 70)
    log("調査完了。inspect_result.txt を共有してください。")
    log("=" * 70)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n結果ファイル: {OUT}")


def deep_walk(target, log):
    """UIA backend の深堀り"""
    nodes = []
    err = [0]

    def safe(getter, d=""):
        try:
            v = getter()
            return v if v is not None else d
        except Exception:
            err[0] += 1
            return d

    def walk(ctrl, depth=0):
        if depth > MAX_DEPTH:
            return
        ct = safe(lambda: ctrl.element_info.control_type)
        name = safe(lambda: ctrl.element_info.name)
        wtext = safe(lambda: ctrl.window_text())
        try:
            children = ctrl.children()
        except Exception:
            children = []
            err[0] += 1
        nodes.append({"depth": depth, "ct": ct, "name": name,
                      "wtext": wtext, "nch": len(children)})
        for c in children:
            walk(c, depth + 1)
    try:
        walk(target)
    except RecursionError:
        log("再帰深度オーバー")
    log(f"  走査ノード数: {len(nodes)} / エラー: {err[0]}")
    ctcnt = Counter(n["ct"] for n in nodes)
    log("  control_type の内訳: " + str(dict(ctcnt.most_common(10))))
    text_nodes = [n for n in nodes if (n["name"] or n["wtext"]).strip()]
    log(f"  テキストが取れたノード: {len(text_nodes)}")
    for n in text_nodes[:30]:
        t = (n["name"] or n["wtext"])[:60]
        log(f"    [{n['ct']}] {t!r}")
    # 大きなコントロール
    bignch = sorted(nodes, key=lambda n: -n["nch"])[:5]
    log("  子要素数の多いコントロール:")
    for n in bignch:
        log(f"    depth={n['depth']} ct={n['ct']} children={n['nch']}")


def deep_walk_w32(target, log):
    """win32 backend の深堀り (Windows メッセージ経由)"""
    nodes = []
    err = [0]

    def walk(ctrl, depth=0):
        if depth > MAX_DEPTH:
            return
        try:
            cls = ctrl.class_name()
        except Exception:
            cls = "?"
            err[0] += 1
        try:
            text = ctrl.window_text()
        except Exception:
            text = ""
            err[0] += 1
        try:
            children = ctrl.children()
        except Exception:
            children = []
            err[0] += 1
        nodes.append({"depth": depth, "cls": cls, "text": text, "nch": len(children)})
        for c in children:
            walk(c, depth + 1)
    try:
        walk(target)
    except RecursionError:
        log("再帰深度オーバー")
    log(f"  走査ノード数: {len(nodes)} / エラー: {err[0]}")
    clscnt = Counter(n["cls"] for n in nodes)
    log("  class_name の内訳: " + str(dict(clscnt.most_common(15))))
    text_nodes = [n for n in nodes if n["text"].strip()]
    log(f"  テキストが取れたノード: {len(text_nodes)}")
    for n in text_nodes[:30]:
        log(f"    [{n['cls']}] {n['text'][:60]!r}")
    log("  特徴的なクラスを持つコントロール (上位):")
    for n in sorted(nodes, key=lambda x: -x["nch"])[:5]:
        log(f"    depth={n['depth']} cls={n['cls']} children={n['nch']} "
            f"text={n['text'][:40]!r}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.write_text("FATAL:\n" + traceback.format_exc(), encoding="utf-8")
        print("FATAL ERROR. See inspect_result.txt")
        raise

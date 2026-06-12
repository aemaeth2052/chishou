# -*- coding: utf-8 -*-
"""Hks UIA調査 v5 - 「番割予定表」ウィンドウ専用

カード型表示の予定表ウィンドウから顧客名・現場名・車両番号を取れるか確認。
こちらの画面は別エンジンで描画されている可能性があり、もし読めれば
ETC連携の入力源として使える。
"""

import sys
import time
import traceback
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent / "inspect_result.txt"

# 6/11 の予定表に写っていた実データ
KEYWORDS = [
    # 顧客名
    "あい造園", "大木建設", "エバーブレス", "共和建設", "三共",
    # 現場名
    "川口土地", "渋谷インフォ", "海老名", "足立区谷中", "品川区東品川",
    "大和ハウス", "船橋市本町", "流山", "西鉄", "三浦",
    # 車両
    "軽27", "軽23", "軽17", "1606", "4345", "7772", "Caravar",
    # 作業員
    "北島", "高橋", "吉田峰和", "渡辺学", "石井正弘", "藤原正三",
    # 住所
    "東京都", "千葉県", "上川町",
    # 担当者
    "高橋さん", "石渡さん", "平山さん",
]


def main():
    lines = []
    def log(msg=""):
        s = str(msg)
        print(s); lines.append(s)

    try:
        from pywinauto import Desktop
    except ImportError:
        print("[ERROR] pywinauto 未インストール")
        sys.exit(1)

    log("=" * 70)
    log(f"調査日時: {time.strftime('%Y-%m-%d %H:%M:%S')}  (v5 番割予定表)")
    log("=" * 70)

    # 「番割予定表」を含むウィンドウをUIAで探す
    log("\n[1] UIA backend で「番割予定表」を含むウィンドウを探す")
    log("-" * 70)
    uia_target = None
    for w in Desktop(backend="uia").windows():
        try:
            t = w.window_text()
        except Exception:
            continue
        if "予定表" in t or "番割予定" in t:
            log(f"見つかりました: {t[:80]}")
            uia_target = w
            break
    if not uia_target:
        log("UIAでは「番割予定表」が見つかりません")

    # win32 でも探す
    log("\n[2] win32 backend で「番割予定表」を含むウィンドウを探す")
    log("-" * 70)
    w32_target = None
    try:
        for w in Desktop(backend="win32").windows():
            try:
                t = w.window_text()
                if "予定表" in t or "番割予定" in t:
                    log(f"見つかりました: cls={w.class_name()} title={t[:80]}")
                    w32_target = w
                    break
            except Exception:
                pass
    except Exception as e:
        log(f"win32 列挙でエラー: {e}")
    if not w32_target:
        log("win32でも「番割予定表」が見つかりません")

    if not uia_target and not w32_target:
        log("\n[ERROR] 番割予定表ウィンドウが見つかりません")
        log("        画面を表示した状態で再実行してください")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    # ============ UIA で深掘り ============
    if uia_target:
        log("\n[3] UIA で番割予定表を深掘り")
        log("-" * 70)
        deep_walk_uia(uia_target, log)

    # ============ win32 で深掘り ============
    if w32_target:
        log("\n[4] win32 で番割予定表を深掘り")
        log("-" * 70)
        deep_walk_w32(w32_target, log)

    log("\n" + "=" * 70)
    log("調査完了")
    log("=" * 70)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n結果: {OUT}")


def deep_walk_uia(target, log):
    nodes = []
    def walk(c, depth=0):
        if depth > 30:
            return
        try:
            ct = c.element_info.control_type
        except Exception:
            ct = "?"
        try:
            name = c.element_info.name or ""
        except Exception:
            name = ""
        try:
            wt = c.window_text() or ""
        except Exception:
            wt = ""
        try:
            r = c.rectangle()
            rect = (r.left, r.top, r.right, r.bottom)
        except Exception:
            rect = (0, 0, 0, 0)
        nodes.append({"depth": depth, "ct": ct, "name": name, "wt": wt, "rect": rect})
        try:
            for ch in c.children():
                walk(ch, depth + 1)
        except Exception:
            pass
    walk(target)
    log(f"  ノード数: {len(nodes)}")
    cnt = Counter(n["ct"] for n in nodes)
    log(f"  control_type: {dict(cnt.most_common(10))}")
    # 実データ検索
    hits = []
    for n in nodes:
        text = n["name"] + " " + n["wt"]
        for kw in KEYWORDS:
            if kw in text:
                hits.append((kw, n))
                break
    log(f"  実データヒット: {len(hits)} 件")
    for kw, n in hits[:30]:
        t = (n["name"] or n["wt"])[:50]
        log(f"    [{kw}] ct={n['ct']} text={t!r}")
    text_nodes = [n for n in nodes if (n["name"] or n["wt"]).strip()]
    if not hits and text_nodes:
        log(f"  実データはヒットしませんでしたが、テキストはあります ({len(text_nodes)}件):")
        for n in text_nodes[:20]:
            t = (n["name"] or n["wt"])[:60]
            log(f"    [{n['ct']}] {t!r}")


def deep_walk_w32(target, log):
    nodes = []
    def walk(c, depth=0):
        if depth > 30:
            return
        try:
            cls = c.class_name()
        except Exception:
            cls = "?"
        try:
            text = c.window_text() or ""
        except Exception:
            text = ""
        try:
            r = c.rectangle()
            rect = (r.left, r.top, r.right, r.bottom)
        except Exception:
            rect = (0, 0, 0, 0)
        nodes.append({"depth": depth, "cls": cls, "text": text, "rect": rect})
        try:
            for ch in c.children():
                walk(ch, depth + 1)
        except Exception:
            pass
    walk(target)
    log(f"  ノード数: {len(nodes)}")
    cnt = Counter(n["cls"] for n in nodes)
    log(f"  クラス内訳: {dict(cnt.most_common(10))}")
    # 実データ検索
    hits = []
    for n in nodes:
        for kw in KEYWORDS:
            if kw in n["text"]:
                hits.append((kw, n))
                break
    log(f"  実データヒット: {len(hits)} 件")
    log("  ヒット詳細 (画面上の位置とクラス):")
    for kw, n in sorted(hits, key=lambda x: (x[1]["rect"][1], x[1]["rect"][0]))[:40]:
        r = n["rect"]
        log(f"    ({r[0]:>5},{r[1]:>4}) cls={_short(n['cls'])} kw={kw} text={n['text'][:50]!r}")
    text_nodes = [n for n in nodes if n["text"].strip()]
    if not hits and text_nodes:
        log(f"  実データはヒットしませんでしたが、テキストはあります ({len(text_nodes)}件):")
        for n in text_nodes[:30]:
            r = n["rect"]
            log(f"    ({r[0]:>5},{r[1]:>4}) cls={_short(n['cls'])} text={n['text'][:60]!r}")


def _short(cls):
    if "." in cls:
        for p in cls.split("."):
            if p and p not in ("WindowsForms10", "app", "0") and not p.isdigit():
                return p
    return cls


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.write_text("FATAL:\n" + traceback.format_exc(), encoding="utf-8")
        print("FATAL. See inspect_result.txt"); raise

# -*- coding: utf-8 -*-
"""Hks UIA構造調査ツール v2

v1で「グリッド候補0件」となった原因の切り分け。
コントロールを control_type で絞らず、全要素を走査して
特徴を吐き出す。テキストが1つでも取れれば UIA経由で読める可能性あり。
"""

import sys
import time
import traceback
from pathlib import Path

OUT = Path(__file__).resolve().parent / "inspect_result.txt"
MAX_DEPTH = 25
MAX_NODES = 50000


def main():
    lines = []

    def log(msg=""):
        s = str(msg)
        print(s)
        lines.append(s)

    try:
        from pywinauto import Desktop, Application
    except ImportError:
        print("[ERROR] pywinauto がインストールされていません")
        print("        py -m pip install pywinauto を実行してください")
        sys.exit(1)

    log("=" * 70)
    log(f"調査日時: {time.strftime('%Y-%m-%d %H:%M:%S')}  (v2)")
    log("=" * 70)

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
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    log(f"\n候補ウィンドウ: {len(candidates)} 件")
    for i, (title, _) in enumerate(candidates):
        log(f"  [{i}] {title}")
    target = candidates[0][1]
    log(f"\n→ [0] を対象に調査します\n")

    # ============= 全要素を走査 (control_type で絞らない) =============
    all_nodes = []  # (depth, ct, cls, name, rect, value, n_children)
    err_count = [0]
    node_count = [0]

    def safe(getter, default=""):
        try:
            v = getter()
            return v if v is not None else default
        except Exception:
            err_count[0] += 1
            return default

    def walk(ctrl, depth=0):
        node_count[0] += 1
        if node_count[0] > MAX_NODES:
            return
        ct = safe(lambda: ctrl.element_info.control_type)
        cls = safe(lambda: ctrl.element_info.class_name)
        name = safe(lambda: ctrl.element_info.name)
        value = safe(lambda: ctrl.get_value()) if hasattr(ctrl, "get_value") else ""
        wtext = safe(lambda: ctrl.window_text())
        try:
            rect = ctrl.rectangle()
            rect_str = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
            w = rect.right - rect.left
            h = rect.bottom - rect.top
        except Exception:
            rect_str = "?"
            w = h = 0
            err_count[0] += 1
        try:
            children = ctrl.children()
        except Exception:
            children = []
            err_count[0] += 1
        all_nodes.append({
            "depth": depth, "ct": ct, "cls": cls, "name": name,
            "value": value, "wtext": wtext,
            "rect": rect_str, "w": w, "h": h, "nch": len(children),
        })
        if depth < MAX_DEPTH:
            for c in children:
                walk(c, depth + 1)

    log("コントロール全走査中...")
    try:
        walk(target)
    except RecursionError:
        log("再帰深度オーバー（途中で打ち切り）")
    log(f"  走査ノード数: {len(all_nodes)}")
    log(f"  途中エラー数: {err_count[0]}")
    log("")

    if not all_nodes:
        log("[判定] 1ノードも取得できません。UIA非対応の可能性が高い。")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return

    # ============= control_type のヒストグラム =============
    log("-" * 70)
    log("control_type の出現回数 (上位)")
    log("-" * 70)
    from collections import Counter
    ctcnt = Counter(n["ct"] for n in all_nodes)
    for ct, cnt in ctcnt.most_common(30):
        log(f"  {ct or '(空)':<20} : {cnt}")
    log("")

    # ============= 大きな矩形を持つコントロール (グリッド本体候補) =============
    log("-" * 70)
    log("大きな領域を持つコントロール (面積 上位10)")
    log("-" * 70)
    big = sorted(all_nodes, key=lambda n: -(n["w"] * n["h"]))[:10]
    for n in big:
        log(f"  depth={n['depth']:<2} ct={n['ct']!r:<14} cls={n['cls']!r:<25} "
            f"size={n['w']}x{n['h']} children={n['nch']} name={n['name'][:30]!r}")
    log("")

    # ============= 子要素数が多いコントロール (グリッド/リスト疑い) =============
    log("-" * 70)
    log("子要素数が多いコントロール (上位10)")
    log("-" * 70)
    many = sorted(all_nodes, key=lambda n: -n["nch"])[:10]
    for n in many:
        log(f"  depth={n['depth']:<2} ct={n['ct']!r:<14} cls={n['cls']!r:<25} "
            f"children={n['nch']} size={n['w']}x{n['h']}")
    log("")

    # ============= 何らかのテキストが取れたコントロール =============
    log("-" * 70)
    log("テキストが取れたコントロール (上位30、name/wtext/valueのいずれか非空)")
    log("-" * 70)
    text_nodes = [n for n in all_nodes
                  if (n["name"] or n["wtext"] or n["value"]).strip()]
    log(f"該当: {len(text_nodes)} / {len(all_nodes)}")
    for n in text_nodes[:30]:
        text = n["name"] or n["wtext"] or n["value"]
        text = str(text)[:60]
        log(f"  [{n['ct']}] {text!r}")
    log("")

    # ============= 期待する値が見つかるかキーワード検索 =============
    log("-" * 70)
    log("一覧画面のキーワード検索 (顧客名・現場名・車両番号らしき文字)")
    log("-" * 70)
    keywords = ["建設", "工務", "株式会社", "営業所", "本線", "Caravar",
                "宮崎", "あい造", "コウホウ", "1499", "1500", "1606", "7772", "6031"]
    hits = []
    for n in all_nodes:
        text = (n["name"] or "") + " " + (n["wtext"] or "") + " " + str(n["value"] or "")
        for kw in keywords:
            if kw in text:
                hits.append((kw, n))
                break
    log(f"キーワードを含むコントロール: {len(hits)} 件")
    for kw, n in hits[:20]:
        text = (n["name"] or n["wtext"] or n["value"])[:60]
        log(f"  [{kw}] ct={n['ct']} text={text!r}")
    log("")

    # ============= 判定 =============
    log("=" * 70)
    log("自動判定")
    log("=" * 70)
    if hits:
        log("⭕ 顧客名や車両番号らしき文字がUIA経由で取得できました。")
        log("   → UI Automation での読み取りは実装可能です。")
    elif text_nodes:
        log("△ メニュー名などのテキストは取れますが、")
        log("   一覧の中身(顧客名・車両番号)は取れていない可能性があります。")
        log("   独自描画の疑いが濃いですが、もう一段詳しく調査する余地があります。")
    else:
        log("❌ テキストが1つも取れません。UI Automation での読み取りは不可。")
        log("   → 別手段(OCR/DB直接/ベンダー照会)を検討してください。")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n結果ファイル: {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.write_text("FATAL:\n" + traceback.format_exc(), encoding="utf-8")
        print("FATAL ERROR. See inspect_result.txt")
        raise

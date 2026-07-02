# -*- coding: utf-8 -*-
"""番割予定表 作業員インスペクタ (稼働率の色判定の下調べ用)

目的:
  作業員の自社/他社は「氏名の背景色」で表示される。その背景色が機械的に拾えるかを
  作業員1人ごとに調べて吐き出し、worker_colors.json(色→区分マップ)に何を書けば
  よいかの当たりを付ける。あわせて現場名も一覧化し、外貨除外条件の手がかりにする。

  番割の列挙・採色器の選択は worker_color(GUIの色判定タブと同じ実装)を共有する。
  hks_reader 本体は「車両が割り当たった現場」しか残さないが、稼働率では全作業員が
  要るので、ここは車両の有無を問わず全ブロック・全作業員(待機/休み枠も)を出力する。

実行:
  Hks の「番割予定表」を画面に出した状態で、同じ PC で実行する。
  inspect_workers.bat をダブルクリックしてもよい。

出力(このスクリプトと同じフォルダ):
  workers_inspect.txt : 人が読む用の一覧(作業員・現場・背景色・採色域)。
  workers_colors.txt  : 検出された背景色クラスタ一覧(色ごとの人数+設定の雛形)。
"""

import json
import sys
import time
import traceback
from pathlib import Path

# worker_color は import 時にプロセスを DPI 認識にする(採色座標系の一致に必須)。
# hks_reader より先に import しておく。
import worker_color as wc
import hks_reader as hr  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "workers_inspect.txt"
COLORS = HERE / "workers_colors.txt"


def main():
    lines = []
    clusters = {}   # cluster_key -> {"hex","count","badges","names"} (GUIスキャンと同形)
    got = miss = 0

    def w(s=""):
        print(s)
        lines.append(str(s))

    w("=" * 78)
    w(f"番割 作業員インスペクタ(氏名の背景色＋営業所バッジ)"
      f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w("採色: ウィンドウを前面化して画面キャプチャ(取れなければ PrintWindow に切替)")
    w("※ 自社/他社は氏名の背景色で決まる。背景色が取れない行は ----- になる。")
    w("=" * 78)

    total_workers = 0
    try:
        boards = list(wc._iter_selected_boards(log=w))
    except RuntimeError:
        OUT.write_text(
            "番割予定表ウィンドウが見つかりません。\n"
            "Hks で番割予定表を表示してから再実行してください。",
            encoding="utf-8",
        )
        print("番割予定表が見つかりません")
        return

    for office, date_iso, root, sampler in boards:
        w("")
        w("#" * 78)
        w(f"# {office}  {date_iso}   (採色方式: {sampler.mode})")
        w("#" * 78)
        w(f"{'顧客':<16}{'現場':<16}{'氏名':<9}{'バッジ':<7}{'背景色':<9}"
          f"{'採色域(L,T,R,B)':<22}車")
        w("-" * 78)

        for cust, site, name, badge, rect, is_sb, has_v in hr.iter_board_workers(root):
            total_workers += 1
            vflag = "待" if is_sb else ("○" if has_v else "×")
            bg_hex = sampler.bg(rect)
            if bg_hex:
                got += 1
            else:
                miss += 1
            rstr = ",".join(str(int(v)) for v in rect)
            w(f"{cust[:14]:<16}{site[:14]:<16}{name[:7]:<9}"
              f"{badge[:5]:<7}{(bg_hex or '-----'):<9}{rstr:<22}{vflag}")
            rgb = wc.to_rgb(bg_hex)
            if rgb is not None:
                key = wc.cluster_key(rgb)
                c = clusters.setdefault(key, {"hex": bg_hex, "count": 0,
                                              "badges": set(), "names": []})
                c["count"] += 1
                if badge:
                    c["badges"].add(badge)
                if len(c["names"]) < 12:
                    c["names"].append(name)

    w("")
    w("=" * 78)
    w(f"作業員 総数(のべ): {total_workers} 人  "
      f"(背景色 取得 {got} / 取得不能 {miss})")
    if miss:
        w("※ 取得不能が多い場合は番割を最前面・全表示にして再実行してください。")
    w("=" * 78)
    OUT.write_text("\n".join(lines), encoding="utf-8")

    # --------- 背景色クラスタの集計 + worker_colors.json の雛形
    # GUIの色スキャンと同じ「オレンジ度(G−B)」集約を使い、両ツールの色数を揃える
    merged = wc._merge_clusters(list(clusters.values()), wc.DEFAULT_TOLERANCE)
    merged.sort(key=lambda x: -x["count"])

    clines = []

    def cw(s=""):
        print(s)
        clines.append(str(s))

    cw("=" * 78)
    cw("氏名の背景色クラスタ(人数の多い順・オレンジ度G−Bで集約)")
    cw("自社/他営業所応援/対象外 はこの色で分かれる。各色に区分を割り当てて")
    cw("worker_colors.json に書く(下に雛形を出力)。GUIの『色判定』タブからも設定可。")
    cw("(バッジ 若/蘇/八/都/宮 等が併記されていれば、色と営業所の対応の手がかり)")
    cw("=" * 78)
    suggest_colors = []
    for c in merged:
        badge_set = sorted(c["badges"])
        names = "、".join(c["names"][:8])
        cw("")
        cw(f"■ 背景色 {c['hex']}  ({c['count']} 人)"
           f"{'  バッジ: ' + ' '.join(badge_set) if badge_set else ''}")
        cw(f"   例: {names}")
        suggest_colors.append({"hex": c["hex"], "kind": "?",
                               "label": f"{c['count']}人"
                               f"{'/' + ' '.join(badge_set) if badge_set else ''}"})

    cw("")
    cw("-" * 78)
    cw("【worker_colors.json の雛形】kind を home(自社)/other(他営業所応援)/ignore(対象外)")
    cw("に書き換えて worker_colors.json として保存(? のままの色の人は対象外+要確認になる)。")
    cw("-" * 78)
    cw(json.dumps({"tolerance": wc.DEFAULT_TOLERANCE, "colors": suggest_colors},
                  ensure_ascii=False, indent=2))
    COLORS.write_text("\n".join(clines), encoding="utf-8")

    print()
    print(f"一覧:           {OUT}")
    print(f"背景色クラスタ:   {COLORS}")
    print("この2ファイルを見て、各色に区分を割り当ててください(GUIの『色判定』タブが楽)。")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.write_text("FATAL:\n" + traceback.format_exc(), encoding="utf-8")
        print("FATAL. workers_inspect.txt を確認してください。")
        raise

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
番割（Excel）確認スクリプト  ── まずは「何の情報が入っているか」を見るための偵察ツール

このスクリプトは読み取り専用です。元ファイルは一切変更しません。
稼働率の計算はまだ行いません。先に構造（どこに日付・作業員・現場・色があるか）を
把握するためのものです。ここで分かったことを元に、次に稼働率の集計ロジックを作ります。

確認したいこと（このツールが出力するもの）:
  1. シート一覧とサイズ（行数・列数）
  2. 各シートの先頭プレビュー（値のグリッド）── 日付が横並びか縦並びか、作業員がどこかを掴む
  3. 結合セルの状況 ── 番割はヘッダや現場名が結合されがち
  4. 塗りつぶし色の一覧と出現数・サンプルセル
     → 「外注/応援の作業員は色が違う」とのことなので、どの色がそれに当たるかを特定する材料
  5. 「管理費」など特定キーワードがどのセルに出てくるか（除外現場の当たりをつける）

使い方:
    pip install openpyxl
    python inspect_banwari.py 番割ファイル.xlsx

オプション:
    --rows N     プレビュー行数（既定 25）
    --cols N     プレビュー列数（既定 20）
    --keyword K  探したい語（既定「管理費」）。複数回指定可。
"""

import sys
import argparse
from collections import Counter, defaultdict

try:
    import openpyxl
    from openpyxl.utils import get_column_letter
except ImportError:
    print("openpyxl が見つかりません。先に `pip install openpyxl` を実行してください。")
    sys.exit(1)


def cell_fill_key(cell):
    """セルの塗りつぶし色を識別できる文字列にして返す。色なしは None。"""
    fill = cell.fill
    if fill is None or fill.patternType is None:
        return None
    fg = fill.fgColor
    if fg is None:
        return None
    # RGB 指定（例: FFFF0000）
    if fg.type == "rgb" and fg.rgb and fg.rgb not in ("00000000",):
        return f"rgb:{fg.rgb}"
    # テーマ色 + 濃淡(tint)
    if fg.type == "theme":
        tint = round(fg.tint, 3) if fg.tint else 0
        return f"theme:{fg.theme}/tint:{tint}"
    # インデックス色
    if fg.type == "indexed":
        return f"indexed:{fg.indexed}"
    return None


def preview_sheet(ws, max_rows, max_cols):
    print(f"\n{'='*70}")
    print(f"■ シート: 「{ws.title}」  最大 {ws.max_row} 行 × {ws.max_column} 列")
    print(f"{'='*70}")

    # 結合セル
    merged = list(ws.merged_cells.ranges)
    print(f"\n[結合セル] {len(merged)} 箇所" + (f"（例: {', '.join(str(m) for m in merged[:8])} ...）" if merged else ""))

    # 値プレビュー
    print(f"\n[値プレビュー] 先頭 {min(max_rows, ws.max_row)} 行 × {min(max_cols, ws.max_column)} 列")
    header = "      " + "".join(f"{get_column_letter(c):>10}" for c in range(1, min(max_cols, ws.max_column) + 1))
    print(header)
    for r in range(1, min(max_rows, ws.max_row) + 1):
        cells = []
        for c in range(1, min(max_cols, ws.max_column) + 1):
            v = ws.cell(row=r, column=c).value
            if v is None:
                s = ""
            else:
                s = str(v).replace("\n", " ")
            if len(s) > 9:
                s = s[:8] + "…"
            cells.append(f"{s:>10}")
        print(f"{r:>4}: " + "".join(cells))


def analyze_colors(ws, max_scan_rows, max_scan_cols):
    """塗りつぶし色を集計。出現数の多い順に色とサンプルセルを返す。"""
    color_count = Counter()
    color_samples = defaultdict(list)
    rows = min(max_scan_rows, ws.max_row)
    cols = min(max_scan_cols, ws.max_column)
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            cell = ws.cell(row=r, column=c)
            key = cell_fill_key(cell)
            if key is None:
                continue
            color_count[key] += 1
            if len(color_samples[key]) < 5:
                addr = f"{get_column_letter(c)}{r}"
                val = cell.value
                val = (str(val).replace("\n", " ")[:12]) if val is not None else "(空)"
                color_samples[key].append(f"{addr}={val}")
    if not color_count:
        print("\n[塗りつぶし色] 検出された塗り色はありません（色情報が無いか、別の表現方法かもしれません）。")
        return
    print(f"\n[塗りつぶし色] {len(color_count)} 種類を検出（出現数の多い順）")
    print("  ※「外注/応援」の色がどれかは、サンプルの作業員名を見て判断してください。")
    for key, n in color_count.most_common():
        print(f"  - {key:28} : {n:>5} セル  例) {', '.join(color_samples[key])}")


def find_keyword(ws, keywords, max_scan_rows, max_scan_cols):
    rows = min(max_scan_rows, ws.max_row)
    cols = min(max_scan_cols, ws.max_column)
    hits = defaultdict(list)
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            v = ws.cell(row=r, column=c).value
            if v is None:
                continue
            s = str(v)
            for kw in keywords:
                if kw in s:
                    if len(hits[kw]) < 12:
                        hits[kw].append(f"{get_column_letter(c)}{r}={s.replace(chr(10),' ')[:16]}")
    for kw in keywords:
        if hits[kw]:
            print(f"\n[キーワード「{kw}」] {len(hits[kw])} 箇所（先頭のみ）: " + ", ".join(hits[kw]))
        else:
            print(f"\n[キーワード「{kw}」] 見つかりませんでした。")


def main():
    parser = argparse.ArgumentParser(description="番割Excelの中身を確認する（読み取り専用）")
    parser.add_argument("path", help="番割の .xlsx ファイルパス")
    parser.add_argument("--rows", type=int, default=25, help="プレビュー行数（既定25）")
    parser.add_argument("--cols", type=int, default=20, help="プレビュー列数（既定20）")
    parser.add_argument("--keyword", action="append", default=None, help="探す語（既定『管理費』『応援』『外注』）")
    args = parser.parse_args()

    keywords = args.keyword if args.keyword else ["管理費", "応援", "外注"]

    print(f"ファイルを開いています: {args.path}")
    try:
        wb = openpyxl.load_workbook(args.path, data_only=True)
    except Exception as e:
        print(f"読み込みに失敗しました: {e}")
        print("・.xlsx 形式ですか？（.xls の古い形式は未対応。Excelで .xlsx 保存し直してください）")
        sys.exit(1)

    print(f"\nシート一覧（{len(wb.sheetnames)}枚）: {wb.sheetnames}")

    # 色の走査範囲はプレビューより広めに取る（全体傾向を見るため）
    scan_rows = max(args.rows, 200)
    scan_cols = max(args.cols, 60)

    for name in wb.sheetnames:
        ws = wb[name]
        if ws.max_row == 0 or ws.max_column == 0:
            print(f"\n（シート「{name}」は空のためスキップ）")
            continue
        preview_sheet(ws, args.rows, args.cols)
        analyze_colors(ws, scan_rows, scan_cols)
        find_keyword(ws, keywords, scan_rows, scan_cols)

    print(f"\n{'='*70}")
    print("確認は以上です。この出力（特に『値プレビュー』『塗りつぶし色』『キーワード』）を")
    print("そのまま貼ってもらえれば、稼働率の集計ロジックを次に組みます。")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()

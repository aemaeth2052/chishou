# -*- coding: utf-8 -*-
"""稼働率履歴を Google スプレッドシートへ upsert する。

サービスアカウント方式(gspread)。同じ(日付, 営業所)の行は上書き、無ければ追記。
列は utilization.HISTORY_HEADER と同じ。

事前準備(一度だけ):
  1. Google Cloud でプロジェクト作成 → Google Sheets API を有効化
  2. サービスアカウントを作成 → JSONキーをダウンロード
  3. 対象スプレッドシートを、そのサービスアカウントのメール(xxx@xxx.iam.gserviceaccount.com)に
     「編集者」で共有
  4. アプリの「Google連携」タブに JSONキーのパスとシートURLを設定

gspread 未インストールでも他機能は動くよう、import は関数内で行う。
"""

from pathlib import Path

import utilization as U


def _col_letter(n):
    """1-based 列番号 → A1記法の列文字(A, B, ... , Z, AA ...)。"""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _rows_from_reports(reports):
    """reports → 履歴行(HISTORY_HEADER と同じ並び)。"""
    out = []
    for r in reports:
        out.append([r["date"], r["office"], r["roster_size"], r["denominator"],
                    r["present"], r["revenue"], r["overhead_only"], r["idle"],
                    r.get("staff_excluded", 0), round(r["rate"] * 100, 1),
                    r["other_total"], r["ignored"]])
    return out


def _open_worksheet(sa_json, spreadsheet, worksheet):
    import gspread
    if not Path(sa_json).exists():
        raise RuntimeError(f"サービスアカウントJSONが見つかりません: {sa_json}")
    gc = gspread.service_account(filename=str(sa_json))
    s = str(spreadsheet).strip()
    sh = gc.open_by_url(s) if s.startswith("http") else gc.open_by_key(s)
    try:
        ws = sh.worksheet(worksheet)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet, rows=1000,
                              cols=len(U.HISTORY_HEADER) + 2)
    return sh, ws


def sync_history(reports, sa_json, spreadsheet, worksheet="稼働率履歴", log=print):
    """reports をスプレッドシートへ upsert。 (更新数, 追記数) を返す。"""
    header = U.HISTORY_HEADER
    _, ws = _open_worksheet(sa_json, spreadsheet, worksheet)
    values = ws.get_all_values()
    end_col = _col_letter(len(header))

    if not values:
        ws.update("A1", [header])
        values = [header]
    elif values[0][:len(header)] != header:
        ws.update(f"A1:{end_col}1", [header])
        if values:
            values[0] = header

    # 既存行を (日付, 営業所) → 行番号(1始まり) で索引
    idx = {}
    for i, row in enumerate(values[1:], start=2):
        if len(row) >= 2:
            idx[(row[0], row[1])] = i

    updates, new_rows = [], []
    for r in _rows_from_reports(reports):
        key = (str(r[0]), str(r[1]))
        if key in idx:
            n = idx[key]
            updates.append({"range": f"A{n}:{end_col}{n}", "values": [r]})
        else:
            new_rows.append(r)

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
    log(f"Googleシート更新: 上書き {len(updates)} 行 / 追記 {len(new_rows)} 行")
    return len(updates), len(new_rows)


def test_connection(sa_json, spreadsheet, worksheet="稼働率履歴"):
    """接続確認。成功なら説明文字列、失敗なら例外。"""
    sh, ws = _open_worksheet(sa_json, spreadsheet, worksheet)
    return f"接続OK: 『{sh.title}』のシート『{ws.title}』に書き込めます"

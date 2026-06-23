# -*- coding: utf-8 -*-
"""作業員稼働率の集計

番割(全作業員)と社員名簿CSVを突き合わせ、自社作業員の稼働率を出す。

定義(ユーザー確認済み 2026-06):
  ・番割に載る作業員は全員、第一元商が差配した人(自社＋借りた応援)。
  ・自社作業員       = 名簿(在籍)にマッチした人。
  ・応援(外注)作業員 = 名簿に無い人。別集計する(=借りた人工)。
  ・外貨を産む現場    = 顧客名に除外キーワード(既定: 第一元商 / 宮崎興業)を
                       含まない現場。管理費(送迎応援・寮清掃など)は除外する。

  稼働率 = 外貨を産む現場に出た自社作業員数 ÷ 在籍自社作業員数

突合の注意:
  日本人はフルネーム(姓名)で一致する。外国人は番割が短縮ニックネーム表示の
  ことがあり、名簿(フルネーム)と機械一致しない場合がある。取りこぼしは
  name_aliases.json(番割表示名 → 名簿コード or "応援")で個別に補正する。

使い方:
  # Hks の番割予定表を表示した状態で(同じPCで)、名簿CSVを指定して実行
  python utilization.py --roster 名簿.csv

  # 番割を読まずに、インスペクタ出力(workers_inspect.txt)から再計算(検証用)
  python utilization.py --roster 名簿.csv --inspect workers_inspect.txt
"""

import argparse
import csv
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALIASES_PATH = HERE / "name_aliases.json"
DEFAULT_EXCLUDE = ("第一元商", "宮崎興業")

# 名簿CSVの列位置(Hks 出力。ヘッダ: コード,名称,フリガナ,営業所,区分,備考,Bk,在,…)
COL_CODE, COL_NAME, COL_FURI, COL_NOTE, COL_ACTIVE = 0, 1, 2, 5, 7


# ------------------------------------------------------------------ 文字正規化
def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _norm(s: str) -> str:
    """空白(全角・半角)を除いた比較用キー。"""
    return _nfkc(s).replace("　", "").replace(" ", "").strip()


def _is_kana(s: str) -> bool:
    """全角カタカナ(+長音・中黒)だけで構成されるか(外国人の短縮名判定)。"""
    t = _nfkc(s).replace("　", "").replace(" ", "")
    return bool(t) and bool(re.fullmatch(r"[ァ-ヶー・]+", t))


# ----------------------------------------------------------------------- 名簿
class Employee:
    __slots__ = ("code", "name", "furi", "note")

    def __init__(self, code, name, furi, note):
        self.code, self.name, self.furi, self.note = code, name, furi, note


def load_roster(csv_path, active_only=True):
    """名簿CSV(Shift-JIS)を読み、在籍社員のリストを返す。"""
    raw = Path(csv_path).read_bytes()
    for enc in ("cp932", "shift_jis", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"名簿CSVの文字コードを判別できません: {csv_path}")

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise RuntimeError("名簿CSVが空です")
    out = []
    for r in rows[1:]:  # 1行目はヘッダ
        if len(r) <= COL_ACTIVE or not r[COL_CODE].strip():
            continue
        if active_only and r[COL_ACTIVE].strip() != "True":
            continue
        out.append(Employee(r[COL_CODE].strip(), r[COL_NAME],
                            r[COL_FURI], r[COL_NOTE]))
    return out


class Matcher:
    """番割の表示名 → 自社(コード) / 応援 を判定する。"""

    def __init__(self, roster, aliases=None):
        self.aliases = aliases or {}
        self.full = {}       # 正規化フルネーム -> code
        self.kana_tok = {}   # 外国人カナトークン -> code
        for e in roster:
            self.full.setdefault(_norm(e.name), e.code)
            toks = [t for t in re.split(r"[　 ]+", e.name.strip()) if len(t) >= 2]
            toks += [t for t in e.furi.split() if len(t) >= 2]
            for t in toks:
                if _is_kana(t):
                    self.kana_tok.setdefault(_norm(t), e.code)

    def classify(self, name):
        """(kind, code) を返す。kind は 'own' か 'support'。code は分かれば名簿コード。"""
        # 手動補正が最優先
        if name in self.aliases:
            v = self.aliases[name]
            if v in ("応援", "support", ""):
                return "support", None
            return "own", v
        n = _norm(name)
        if n in self.full:
            return "own", self.full[n]
        if _is_kana(name) and n in self.kana_tok:
            return "own", self.kana_tok[n]
        return "support", None


# --------------------------------------------------------------------- 集計
def is_excluded(customer, keywords):
    return any(k in customer for k in keywords)


def compute(assignments, matcher, exclude_keywords, roster_size):
    """稼働率レポート(dict)を返す。

    assignments: [{customer, site, worker, ...}]
    """
    own_rev, own_ovh = {}, {}      # code or name -> 代表エントリ(重複排除)
    sup_rev, sup_ovh = [], []
    own_on_board = set()
    details = []

    for a in assignments:
        cust, site, worker = a["customer"], a["site"], a["worker"]
        kind, code = matcher.classify(worker)
        excluded = is_excluded(cust, exclude_keywords)
        key = code or worker  # 同一人物の重複は名簿コードで排除(無ければ名前)
        if kind == "own":
            own_on_board.add(key)
            (own_ovh if excluded else own_rev)[key] = worker
        else:
            (sup_ovh if excluded else sup_rev).append((worker, cust, site))
        details.append({
            "worker": worker, "customer": cust, "site": site,
            "kind": "自社" if kind == "own" else "応援",
            "field": "管理費" if excluded else "外貨",
        })

    own_rev_n = len(own_rev)
    own_ovh_only = len([k for k in own_ovh if k not in own_rev])
    idle = roster_size - len(own_on_board)
    util = (own_rev_n / roster_size) if roster_size else 0.0

    return {
        "roster_size": roster_size,
        "assignments": len(assignments),
        "own_total": len(own_on_board),
        "own_revenue": own_rev_n,
        "own_overhead": own_ovh_only,
        "own_idle": idle,
        "support_revenue": len(sup_rev),
        "support_overhead": len(sup_ovh),
        "utilization": util,
        "own_overhead_list": sorted({own_ovh[k] for k in own_ovh}),
        "support_revenue_list": sup_rev,
        "support_overhead_list": sup_ovh,
        "details": details,
    }


def render(report, exclude_keywords):
    L = []
    L.append("=" * 66)
    L.append("作業員稼働率レポート")
    L.append("=" * 66)
    L.append(f"除外(外貨を産まない)顧客キーワード: {' / '.join(exclude_keywords)}")
    L.append("")
    L.append(f"在籍自社作業員(分母)      : {report['roster_size']:>4} 名")
    L.append(f"番割の作業員(自社＋応援)  : {report['assignments']:>4} 名")
    L.append("-" * 66)
    util = report["utilization"] * 100
    L.append(f"★ 稼働率 = 外貨現場に出た自社 {report['own_revenue']} "
             f"÷ 在籍 {report['roster_size']} = {util:.1f}%")
    L.append("-" * 66)
    L.append("【自社作業員の内訳】")
    L.append(f"  外貨を産む現場に配置 : {report['own_revenue']:>4} 名  ← 稼働")
    L.append(f"  管理費現場に配置     : {report['own_overhead']:>4} 名  "
             f"{report['own_overhead_list']}")
    L.append(f"  番割に無し(休/待機)  : {report['own_idle']:>4} 名")
    L.append("")
    L.append("【応援(借りた人工)・別集計】")
    L.append(f"  外貨を産む現場       : {report['support_revenue']:>4} 名")
    L.append(f"  管理費現場           : {report['support_overhead']:>4} 名")
    L.append("=" * 66)
    return "\n".join(L)


# ---------------------------------------------------- 番割の取得(2系統)
def assignments_from_hks():
    import hks_reader as hr
    return hr.read_all_assignments(log=print)


def assignments_from_inspect(path):
    """workers_inspect.txt から (customer, site, worker) を復元(検証用)。"""
    out = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if ln.startswith(("#", "=", "-", "番割", "顧客", "色取得", "作業員 総数")):
            continue
        parts = re.split(r" {2,}", ln.rstrip())
        if len(parts) < 3 or not parts[2].strip():
            continue
        out.append({"customer": parts[0].strip(),
                    "site": parts[1].strip(),
                    "worker": parts[2].strip()})
    return out


def main():
    ap = argparse.ArgumentParser(description="作業員稼働率の集計")
    ap.add_argument("--roster", required=True, help="社員名簿CSV(Shift-JIS)")
    ap.add_argument("--inspect", help="workers_inspect.txt から計算(指定時は番割を読まない)")
    ap.add_argument("--exclude", nargs="*", default=list(DEFAULT_EXCLUDE),
                    help="外貨を産まない顧客キーワード(既定: 第一元商 宮崎興業)")
    ap.add_argument("--out", default=str(HERE / "utilization_report.txt"))
    ap.add_argument("--csv", default=str(HERE / "utilization_detail.csv"),
                    help="作業員ごとの判定明細CSVの出力先")
    args = ap.parse_args()

    roster = load_roster(args.roster, active_only=True)
    aliases = {}
    if ALIASES_PATH.exists():
        raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
        # 先頭が _ のキーは説明用コメントとして無視する
        aliases = {k: v for k, v in raw.items() if not k.startswith("_")}
    matcher = Matcher(roster, aliases)

    if args.inspect:
        assignments = assignments_from_inspect(args.inspect)
    else:
        assignments = assignments_from_hks()

    report = compute(assignments, matcher, args.exclude, len(roster))
    text = render(report, args.exclude)
    print()
    print(text)
    Path(args.out).write_text(text, encoding="utf-8")

    # 明細CSV(Excelで開ける Shift-JIS)
    with open(args.csv, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(["作業員", "区分", "現場種別", "顧客", "現場"])
        for d in report["details"]:
            w.writerow([d["worker"], d["kind"], d["field"], d["customer"], d["site"]])

    print(f"\nレポート: {args.out}")
    print(f"明細CSV : {args.csv}")
    if not ALIASES_PATH.exists():
        print(f"\n※ 外国人ニックネームの取りこぼしは {ALIASES_PATH.name} で補正できます")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        raise

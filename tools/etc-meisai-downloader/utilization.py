# -*- coding: utf-8 -*-
"""作業員稼働率の集計

番割(全作業員)と社員名簿CSVを突き合わせ、自営業所(宮崎)作業員の稼働率を出す。

区分の判定(ユーザー確認済み 2026-06):
  番割では作業員の所属営業所が氏名前のバッジ(蘇我/若松/八幡/都賀/加曽利/宮崎 等)で
  示される。これと宮崎名簿を使って次の3区分に分ける。

    ・宮崎自営業所  … バッジが「宮崎」or バッジ無し&宮崎名簿にマッチ
    ・他営業所応援  … 宮崎以外の営業所バッジが付く人(=他営業所から宮崎へ応援)
    ・集計対象外    … バッジ無し&宮崎名簿に無い人(他営業所の自前労務。宮崎に無関係)

  外貨を産む現場 = 顧客名に除外キーワード(既定: 第一元商 / 宮崎興業)を含まない現場。
  管理費(送迎応援・寮清掃など)は分子から外す。

  稼働率 = 外貨を産む現場に出た宮崎社員数 ÷ 在籍宮崎社員数

  他営業所応援は「借りた人工」として別集計。集計対象外はカウントしない。

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
HOME_OFFICE = "宮崎"  # 自営業所。バッジにこの語を含めば自営業所扱い

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


def _office_of(badge):
    """バッジ文字列から所属営業所を取り出す。空なら ''。"""
    b = (badge or "").strip()
    # 「都賀営業所」など「営業所」表記を除いて営業所名だけにする
    return b.replace("営業所", "").strip()


class Matcher:
    """番割の (表示名, バッジ) → 区分 を判定する。

    区分: 'miyazaki'(宮崎自営業所) / 'other'(他営業所応援) / 'ignore'(集計対象外)
    """

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

    def in_roster(self, name):
        n = _norm(name)
        if n in self.full:
            return self.full[n]
        if _is_kana(name) and n in self.kana_tok:
            return self.kana_tok[n]
        return None

    def classify(self, name, badge=""):
        """(区分, 名簿コード) を返す。"""
        # 手動補正が最優先(番割表示名 -> 名簿コード or 'other'/'ignore')
        if name in self.aliases:
            v = self.aliases[name]
            if v in ("other", "他営業所", "応援"):
                return "other", None
            if v in ("ignore", "対象外", ""):
                return "ignore", None
            return "miyazaki", v
        office = _office_of(badge)
        if office and HOME_OFFICE not in office:
            return "other", None        # 他営業所バッジ → 他営業所応援
        # バッジが宮崎、またはバッジ無し
        code = self.in_roster(name)
        if office and HOME_OFFICE in office:
            return "miyazaki", code     # 宮崎バッジ → 宮崎(名簿外でも宮崎)
        if code:
            return "miyazaki", code     # バッジ無し&名簿○ → 宮崎
        return "ignore", None           # バッジ無し&名簿× → 他営業所の自前労務


# --------------------------------------------------------------------- 集計
def is_excluded(customer, keywords):
    return any(k in customer for k in keywords)


def compute(assignments, matcher, exclude_keywords, roster_size):
    """稼働率レポート(dict)を返す。

    assignments: [{customer, site, worker, badge, ...}]
    """
    miya_rev, miya_ovh = {}, {}     # key -> 氏名(重複排除)
    miya_on = set()
    oth_rev, oth_ovh = [], []       # (氏名, バッジ, 顧客, 現場)
    ignored = []
    details = []

    for a in assignments:
        cust, site, worker = a["customer"], a["site"], a["worker"]
        badge = a.get("badge", "")
        kind, code = matcher.classify(worker, badge)
        excluded = is_excluded(cust, exclude_keywords)
        field = "管理費" if excluded else "外貨"
        if kind == "miyazaki":
            key = code or worker
            miya_on.add(key)
            (miya_ovh if excluded else miya_rev)[key] = worker
            label = "宮崎"
        elif kind == "other":
            (oth_ovh if excluded else oth_rev).append((worker, badge, cust, site))
            label = "他営業所応援"
        else:
            ignored.append((worker, cust, site))
            label = "対象外"
        details.append({
            "worker": worker, "badge": badge, "customer": cust, "site": site,
            "kind": label, "field": field,
        })

    miya_rev_n = len(miya_rev)
    miya_ovh_only = len([k for k in miya_ovh if k not in miya_rev])
    idle = roster_size - len(miya_on)
    util = (miya_rev_n / roster_size) if roster_size else 0.0

    # 他営業所応援の営業所別内訳
    by_office = {}
    for w, b, c, s in oth_rev + oth_ovh:
        by_office[_office_of(b) or "(不明)"] = by_office.get(_office_of(b) or "(不明)", 0) + 1

    return {
        "roster_size": roster_size,
        "assignments": len(assignments),
        "miya_revenue": miya_rev_n,
        "miya_overhead": miya_ovh_only,
        "miya_idle": idle,
        "miya_overhead_list": sorted({miya_ovh[k] for k in miya_ovh
                                      if k not in miya_rev}),
        "other_revenue": len(oth_rev),
        "other_overhead": len(oth_ovh),
        "other_by_office": by_office,
        "ignored": len(ignored),
        "utilization": util,
        "details": details,
    }


def render(report, exclude_keywords):
    L = []
    L.append("=" * 66)
    L.append("宮崎営業所 作業員稼働率レポート")
    L.append("=" * 66)
    L.append(f"除外(外貨を産まない)顧客キーワード: {' / '.join(exclude_keywords)}")
    L.append("")
    L.append(f"在籍 宮崎社員(分母)      : {report['roster_size']:>4} 名")
    L.append(f"番割の作業員(全営業所)   : {report['assignments']:>4} 名")
    L.append("-" * 66)
    util = report["utilization"] * 100
    L.append(f"★ 稼働率 = 外貨現場の宮崎社員 {report['miya_revenue']} "
             f"÷ 在籍 {report['roster_size']} = {util:.1f}%")
    L.append("-" * 66)
    L.append("【宮崎社員の内訳】")
    L.append(f"  外貨を産む現場に配置 : {report['miya_revenue']:>4} 名  ← 稼働")
    L.append(f"  管理費現場に配置     : {report['miya_overhead']:>4} 名  "
             f"{report['miya_overhead_list']}")
    L.append(f"  番割に無し(休/待機)  : {report['miya_idle']:>4} 名")
    L.append("")
    L.append("【他営業所からの応援(借りた人工)・別集計】")
    L.append(f"  外貨を産む現場       : {report['other_revenue']:>4} 名")
    L.append(f"  管理費現場           : {report['other_overhead']:>4} 名")
    office_str = "  ".join(f"{k}{v}" for k, v in
                           sorted(report["other_by_office"].items(),
                                  key=lambda x: -x[1]))
    if office_str:
        L.append(f"  営業所別             : {office_str}")
    L.append("")
    L.append(f"【集計対象外】他営業所の自前労務 : {report['ignored']:>4} 名")
    L.append("=" * 66)
    return "\n".join(L)


# ---------------------------------------------------- 番割の取得(2系統)
def assignments_from_hks():
    import hks_reader as hr
    return hr.read_all_assignments(log=print)


def assignments_from_inspect(path):
    """workers_inspect.txt から (customer, site, worker, badge) を復元(検証用)。

    新フォーマット(顧客/現場/氏名/バッジ/背景色/車両)を想定。背景色とバッジを
    取り違えないよう、# 始まりを背景色、○×を車両として除外した残りをバッジとみなす。
    """
    out = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if ln.startswith(("#", "=", "-", "番割", "顧客", "色取得", "作業員", "※")):
            continue
        parts = re.split(r" {2,}", ln.rstrip())
        if len(parts) < 3 or not parts[2].strip():
            continue
        badge = ""
        for x in parts[3:]:
            x = x.strip()
            if x and not x.startswith("#") and x not in ("○", "×"):
                badge = x
        out.append({"customer": parts[0].strip(),
                    "site": parts[1].strip(),
                    "worker": parts[2].strip(),
                    "badge": badge})
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
        w.writerow(["作業員", "バッジ", "区分", "現場種別", "顧客", "現場"])
        for d in report["details"]:
            w.writerow([d["worker"], d["badge"], d["kind"], d["field"],
                        d["customer"], d["site"]])

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

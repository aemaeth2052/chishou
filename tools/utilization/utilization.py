# -*- coding: utf-8 -*-
"""作業員稼働率の集計(営業所ごと・日次履歴つき)

番割(全作業員)を読み、各営業所の自営業所作業員の稼働率を出す。開いている番割が
複数(例: 5営業所)あれば、(営業所, 日付)ごとに別々に集計する。

区分の判定(ユーザー確認済み 2026-06):
  番割では作業員の自社/他社が「氏名セルの背景色」で表示される。この背景色
  (worker_colors.json)と手動補正(name_aliases.json)だけで3区分に分ける。
  名簿(CSV)は判定に使わない(任意。在籍数の参考・コード解決にだけ使う)。

    ・自営業所     … 背景色が「自社」に割り当てた色(例: 白)
    ・他営業所応援 … 背景色が「他営業所応援」の色
    ・集計対象外   … 背景色が「対象外」の色

  どの色にも割り当てられていない/採色できなかった人は既定で対象外にし、確認に出す。

  外貨を産む現場 = 顧客名に除外キーワード(既定: 第一元商 / 宮崎興業)を含まない現場。
  管理費(送迎応援・寮清掃など)は分子から外す。

  分母 = 番割に名前のある自営業所社員(現場 + 待機/休み枠)
  分子 = そのうち外貨を産む現場に出た人
  稼働率 = 分子 ÷ 分母

  ・番割の「待機」「休み」枠の人も分母に入れる(番割に名前があるため)。ただし
    外貨にも管理費にも入れない(分母内・非稼働)。待機/休み枠はこの営業所の自前要員
    なので、背景色に関係なく自営業所として分母に算入する。
  他営業所応援は「借りた人工」として別集計。集計対象外はカウントしない。

使い方:
  # Hks の番割予定表(複数可)を開いた状態で実行(名簿は任意)
  python utilization.py
  python utilization.py --roster 名簿.csv   # 在籍数の参考が欲しいとき

  # 番割を読まずに、インスペクタ出力(workers_inspect.txt)から再計算(検証用)
  python utilization.py --inspect workers_inspect.txt
"""

import argparse
import csv
import io
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALIASES_PATH = HERE / "name_aliases.json"
SETTINGS_PATH = HERE / "utilization_settings.json"
HISTORY_PATH = HERE / "utilization_history.csv"
REVIEW_PATH = HERE / "utilization_review.csv"
SNAPSHOT_DIR = HERE / "snapshots"
DEFAULT_EXCLUDE = ("第一元商", "宮崎興業")
# 番割の「待機」「休み」枠。ここに割り当てられた人も分母に入れる(出勤扱い)が、
# 外貨にも管理費にも入れない。
STANDBY_LABELS = ("待機", "休み", "休み/留守", "留守")

DEFAULT_SETTINGS = {
    # 外貨を産まない(管理費)現場の判定。いずれも「部分一致」で除外する。
    "exclude_customer_keywords": list(DEFAULT_EXCLUDE),  # 顧客名に含まれたら除外
    "exclude_site_keywords": [],                          # 現場名に含まれたら除外
    # 事務所スタッフ・ダミー等、番割に出ないなら分母に数えない人(コード or 氏名)
    "non_field_staff": [],
}


def load_settings(path):
    """例外設定(除外キーワード・分母除外スタッフ)を読む。無ければ既定値。"""
    s = {k: list(v) for k, v in DEFAULT_SETTINGS.items()}
    p = Path(path)
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        for k in ("exclude_customer_keywords", "exclude_site_keywords",
                  "non_field_staff"):
            v = raw.get(k)
            if isinstance(v, list):
                s[k] = [str(x) for x in v if str(x).strip()]
    return s

# 名簿CSVの列位置(Hks 出力。ヘッダ: コード,名称,フリガナ,営業所,区分,備考,Bk,在,…)
COL_CODE, COL_NAME, COL_FURI, COL_OFFICE, COL_NOTE, COL_ACTIVE = 0, 1, 2, 3, 5, 7


# ------------------------------------------------------------------ 文字正規化
def _nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def _norm(s):
    """空白(全角・半角)を除いた比較用キー。"""
    return _nfkc(s).replace("　", "").replace(" ", "").strip()


def _is_kana(s):
    """全角カタカナ(+長音・中黒)だけで構成されるか(外国人の短縮名判定)。"""
    t = _nfkc(s).replace("　", "").replace(" ", "")
    return bool(t) and bool(re.fullmatch(r"[ァ-ヶー・]+", t))


def office_key(s):
    """営業所名を比較キーに正規化する。

    '第一元商　宮崎営業所' / '都賀営業所' / 'バッジの宮崎' をすべて '宮崎' '都賀' に揃える。
    """
    t = _nfkc(s)
    for w in ("第一元商", "営業所", "株式会社", "有限会社", "㈱", "(株)", "（株）"):
        t = t.replace(w, "")
    return t.replace("　", "").replace(" ", "").strip()


# ----------------------------------------------------------------------- 名簿
class Employee:
    __slots__ = ("code", "name", "furi", "note", "office")

    def __init__(self, code, name, furi, note, office):
        self.code, self.name, self.furi, self.note = code, name, furi, note
        self.office = office  # 正規化済み営業所キー


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
        out.append(Employee(
            r[COL_CODE].strip(), r[COL_NAME], r[COL_FURI], r[COL_NOTE],
            office_key(r[COL_OFFICE]) if len(r) > COL_OFFICE else "",
        ))
    return out


def load_rosters(paths, active_only=True):
    """複数の名簿CSV(営業所ごとに分かれていてもよい)をまとめて読む。

    paths にはファイルとフォルダを混在指定できる。フォルダは中の *.csv を全部読む。
    同じコードの社員が重複したら最初の1件を採用する。
    各社員は自分のCSVの「営業所」列から営業所キーを持つので、番割ごとの絞り込みは
    そのキーで自動的に効く。
    """
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.csv")) + sorted(p.glob("*.CSV")))
        elif p.exists():
            files.append(p)
        else:
            raise RuntimeError(f"名簿が見つかりません: {p}")
    if not files:
        raise RuntimeError("名簿CSVが1つも見つかりません")
    seen, out = set(), []
    for f in files:
        for e in load_roster(f, active_only=active_only):
            if e.code in seen:
                continue
            seen.add(e.code)
            out.append(e)
    return out


def suggest_roster(name, subset, limit=3):
    """番割の表示名(主に外国人カナ)に近い名簿社員を推測して候補文字列を返す。

    名簿の氏名トークン/フリガナトークンと、表示名が前方一致または部分一致するものを拾う。
    返り値は 'コード:氏名 / コード:氏名' 形式(無ければ空文字)。
    """
    n = _norm(name)
    if len(n) < 2:
        return ""
    out, seen = [], set()
    for e in subset:
        toks = [_norm(t) for t in re.split(r"[　 ]+", e.name.strip())]
        toks += [_norm(t) for t in e.furi.split()]
        for t in toks:
            if len(t) < 2:
                continue
            if t.startswith(n) or n.startswith(t) or n in t or t in n:
                if e.code not in seen:
                    seen.add(e.code)
                    out.append(f"{e.code}:{e.name}")
                break
    return " / ".join(out[:limit])


class Matcher:
    """ある営業所(home)について、番割の (表示名, 背景色) → 区分 を判定する。

    区分: 'home'(自営業所) / 'other'(他営業所応援) / 'ignore'(集計対象外)

    判定は **氏名セルの背景色(worker_colors) と手動補正(name_aliases) だけ** で行う。
    名簿(CSV)は判定に使わない。優先順位:
      1. 手動補正(name_aliases) … 最優先
      2. 氏名セルの背景色(worker_colors)

    色が未割当・採色できなかった人は既定で「対象外」にし、備考で確認を促す
    (本当は自社の白セルなら『色判定』タブで色を登録すれば拾える)。

    名簿(任意)を渡した場合は、自社と判定した人の名簿コードを参考に解決するだけに使う
    (在籍数の参考・対照表のコード表示用。判定そのものには影響しない)。
    """

    def __init__(self, home_key, roster_subset=None, aliases=None, colormap=None):
        self.home = home_key
        self.aliases = aliases or {}
        self.colormap = colormap        # worker_color.ColorMap or None
        self.full = {}       # 正規化フルネーム -> code (名簿があれば。参考用)
        self.kana_tok = {}   # 外国人カナトークン -> code
        for e in (roster_subset or []):
            self.full.setdefault(_norm(e.name), e.code)
            toks = [t for t in re.split(r"[　 ]+", e.name.strip()) if len(t) >= 2]
            toks += [t for t in e.furi.split() if len(t) >= 2]
            for t in toks:
                if _is_kana(t):
                    self.kana_tok.setdefault(_norm(t), e.code)

    def in_roster(self, name):
        """名簿コードを参考解決する(判定には使わない)。無ければ None。"""
        n = _norm(name)
        if n in self.full:
            return self.full[n]
        if _is_kana(name) and n in self.kana_tok:
            return self.kana_tok[n]
        return None

    def classify(self, name, bg=""):
        """(区分, 名簿コード, 備考) を返す。色(+手動補正)だけで判定する。

        背景色がどの登録色からも許容差を超えて離れている(=未割当)・採色できなかった
        人は既定で「対象外」にし、備考で区別して要確認に回せるようにする。
        """
        if name in self.aliases:  # 手動補正が最優先
            v = self.aliases[name]
            if v in ("other", "他営業所", "応援"):
                return "other", None, "手動:他営業所"
            if v in ("ignore", "対象外", ""):
                return "ignore", None, "手動:対象外"
            return "home", v, "手動:自社"

        ck = self.colormap.classify(bg) if self.colormap else None
        if ck == "home":
            return "home", self.in_roster(name), "色:自社"
        if ck == "other":
            return "other", None, "色:他営業所応援"
        if ck == "ignore":
            return "ignore", None, "色:対象外"
        # 色が未割当 / 採色できず → 既定は対象外(確認に出す)
        if bg:
            return "ignore", None, "色未割当→対象外"
        return "ignore", None, "採色できず→対象外"


# --------------------------------------------------------------------- 集計
def is_excluded(customer, site, cust_kw, site_kw):
    """顧客名 or 現場名に除外キーワード(部分一致)が含まれれば True。"""
    return (any(k in (customer or "") for k in cust_kw)
            or any(k in (site or "") for k in site_kw))


def compute_board(office, date, rows, roster, cust_kw, site_kw, aliases,
                  staff=None, colormap=None):
    """1つの番割(office, date)の稼働率レポートを返す。

    rows:   [{customer, site, worker, badge, bg}]
    roster: 名簿(任意・参考用)。在籍数の参考と名簿コードの解決にだけ使う(判定には使わない)。
    staff:  事務所スタッフ等のキー集合(コード/正規化氏名)。番割に出ないなら分母から除く。
    colormap: worker_color.ColorMap。氏名の背景色で自社/他社を判定する(これが主判定)。
    """
    staff = staff or set()
    home = office_key(office)
    subset = [e for e in (roster or []) if e.office == home]
    colormap_empty = not colormap   # 色判定が未設定(=ほぼ全員が対象外になる)
    matcher = Matcher(home, subset, aliases, colormap)

    home_on, home_rev, home_ovh = set(), set(), set()
    home_standby = set()              # 待機・休み枠に割り当てられた自営業所社員(分母内)
    home_taiki = set()                # うち「待機」枠
    home_yasumi = set()               # うち「休み/留守」枠
    home_lent = set()                 # 宮崎タグ付き=他営業所へ貸出した自営業所社員
    home_name = {}                    # key -> 氏名(表示用)
    home_rows = defaultdict(int)      # key -> 番割に出た行数(同名重複の検出用)
    home_places = defaultdict(list)   # key -> 出た場所(重複時の確認表示用)
    oth_on, oth_rev = set(), set()
    oth_office = {}                   # key -> 他営業所キー
    ign = set()
    details = []
    review = {}                       # 取りこぼし候補(氏名 -> 1件)

    for a in rows:
        cust, site, worker = a["customer"], a["site"], a["worker"]
        badge = a.get("badge", "")
        bg = a.get("bg", "")
        status = a.get("status", "")
        is_standby = bool(status) or cust in STANDBY_LABELS
        kind, code, cnote = matcher.classify(worker, bg)
        if is_standby and kind != "home" and not cnote.startswith("手動"):
            # 待機/休み枠はこの営業所の番割に載っている=自前の要員なので、氏名セルの
            # 色に関係なく自社として分母に算入する(枠は詰めて表示されるため、隣の
            # セルや見出しの色を拾ってしまうことがある)。手動補正(name_aliases)だけは
            # 最優先の契約どおり上書きしない。
            kind = "home"
        key = code or worker
        exc = is_excluded(cust, site, cust_kw, site_kw)
        if kind == "home":
            # 色=自社(または待機/休み枠・手動補正)。名簿があればコードを参考に付ける。
            home_on.add(key)
            home_name[key] = worker
            home_rows[key] += 1
            home_places[key].append(cust if is_standby else f"{cust} {site}".strip())
            if is_standby:
                home_standby.add(key)  # 待機/休み枠 → 分母に入れるが外貨/管理費には入れない
                if status == "待機" or (not status and "待機" in (cust or "")):
                    home_taiki.add(key)
                else:
                    home_yasumi.add(key)  # 休み/留守
                label = "自営業所(待機/休み)"
            else:
                if office_key(badge) == home and office_key(badge):
                    home_lent.add(key)    # 自営業所タグ付き=貸出
                (home_ovh if exc else home_rev).add(key)
                label = "自営業所"
        elif kind == "other":
            oth_on.add(key)
            oth_office[key] = office_key(badge) or "(不明)"
            if not exc and not is_standby:
                oth_rev.add(key)
            label = "他営業所応援"
        else:
            ign.add(key)
            label = "対象外"
        field = "待機/休み" if is_standby else ("管理費" if exc else "外貨")
        details.append({"office": office, "date": date, "worker": worker,
                        "badge": badge, "bg": bg, "kind": label, "field": field,
                        "note": cnote, "customer": cust, "site": site})

        # 取りこぼし候補: 背景色が色設定に無い/採色できなかった人(既定で対象外に落ちている)。
        # 本当は自社の白セルなら『色判定』タブで色を登録すれば拾える。意図して対象外の色
        # (橙/オレンジ)に割り当てた人は出さない。待機/休み枠は色に関係なく自社に算入
        # されるので確認不要。
        reason = None
        if is_standby:
            pass
        elif "未割当" in cnote:
            reason = ("要確認", "対象外(色未割当)",
                      f"背景色 {bg or '不明'} が色設定に無い→『色判定』タブで割り当てを")
        elif "採色できず" in cnote:
            reason = ("要確認", "対象外(採色失敗)",
                      "背景色が取れなかった→番割を全表示にして再集計を")
        if reason and worker not in review:
            review[worker] = {"priority": reason[0], "office": office, "date": date,
                              "worker": worker, "badge": badge, "bg": bg,
                              "current": reason[1],
                              "customer": cust, "site": site, "hint": reason[2],
                              "suggest": suggest_roster(worker, subset)}

    # 同じ表示名が同じ番割に複数行出ている自社作業員は1名に畳んで数えている
    # (掛け持ちなら正しい)。同姓同名の「別人」だった場合は分母が過小になるので、
    # 集計値は変えずに要確認へ出して人間が判断できるようにする。
    for key, cnt in home_rows.items():
        if cnt < 2:
            continue
        wname = home_name.get(key, str(key))
        if wname in review:
            continue
        review[wname] = {"priority": "確認推奨", "office": office, "date": date,
                         "worker": wname, "badge": "", "bg": "",
                         "current": f"自社1名として集計(同名{cnt}行)",
                         "customer": " / ".join(home_places[key][:3]), "site": "",
                         "hint": "同一人物の掛け持ちなら問題なし。同姓同名の別人なら"
                                 "分母が1名少ない(名簿コードで確認を)",
                         "suggest": suggest_roster(wname, subset)}

    # 分母 = 番割に名前のある自社(現場 + 待機/休み枠)。名簿にいても番割に名前が
    # なければ分母に入れない。待機/休み枠は分母に入れるが外貨/管理費には入れない。
    present = len(home_on)            # 出勤=分母(現場 + 待機/休み)
    num = len(home_rev)              # 外貨現場(=分子)
    ovh_only = len(home_ovh - home_rev)
    standby = len(home_standby)      # 待機/休み枠(分母内・非稼働)
    standby_taiki = len(home_taiki)  # うち待機
    standby_yasumi = len(home_yasumi)  # うち休み/留守
    roster_size = len(subset)        # 在籍(名簿の在籍社員数。参考)
    roster_on = sum(1 for e in subset if e.code in home_on)
    absent = roster_size - roster_on  # 名簿在籍だが番割に名前なし(分母外・参考)
    denominator = present
    rate = (num / denominator) if denominator else 0.0
    # 実働率 = 外貨 ÷ (出勤 − 休み)。休みは管理で動かせないので分母から除き、
    # 「出られる人をどれだけ外貨現場に出せたか」(配車・営業の実力)を見る。
    # 待機は「出られたのに出せなかった」なので分母に残す。
    denominator_active = denominator - standby_yasumi
    rate_active = (num / denominator_active) if denominator_active else 0.0

    by_office = defaultdict(int)
    for k in oth_on:
        by_office[oth_office[k]] += 1

    return {
        "office": office, "date": date,
        "colormap_empty": colormap_empty,
        "roster_size": roster_size,
        "denominator": denominator, "present": present,
        "revenue": num, "overhead_only": ovh_only,
        "standby": standby, "standby_taiki": standby_taiki,
        "standby_yasumi": standby_yasumi, "absent": absent,
        "rate": rate, "lent_out": len(home_lent),
        "denominator_active": denominator_active, "rate_active": rate_active,
        "overhead_names": sorted(home_name[k] for k in (home_ovh - home_rev)),
        "other_total": len(oth_on), "other_revenue": len(oth_rev),
        "other_by_office": dict(by_office),
        "ignored": len(ign),
        "details": details,
        "review": list(review.values()),
    }


def render_board(r):
    L = []
    L.append("=" * 66)
    L.append(f"作業員稼働率  {r['office']}  {r['date']}")
    L.append("=" * 66)
    if r.get("is_total"):
        L.append("※ 選択した番割の単純合計。営業所間の応援は、貸し手側で自社として"
                 "1回だけ数えている(下の応援人数は内部の貸し借りを含む参考値)。")
        L.append("-" * 66)
    if r.get("colormap_empty"):
        L.append("⚠ 色判定が未設定です。氏名の背景色で自社/他社を判定するため、"
                 "『色判定』タブで白=自社などの色を登録してください(未設定だと全員対象外)。")
        L.append("-" * 66)
    rate = r["rate"] * 100
    rate_active = r.get("rate_active", 0.0) * 100
    L.append(f"★ 稼働率 = 外貨現場 {r['revenue']} ÷ 分母(出勤) {r['denominator']} "
             f"= {rate:.1f}%")
    L.append(f"   (分母 = 番割に名前のある自社(背景色=自社)。待機/休み枠も含む)")
    L.append(f"☆ 実働率 = 外貨現場 {r['revenue']} ÷ (出勤 {r['denominator']} − "
             f"休み {r.get('standby_yasumi', 0)}) = {rate_active:.1f}%")
    L.append(f"   (休みは管理で動かせないので分母から除外。待機は残す=配車・営業の実力)")
    L.append("-" * 66)
    L.append("【自営業所 内訳(分母の中身)】")
    L.append(f"  外貨を産む現場  : {r['revenue']:>4} 名  ← 稼働(分子)")
    if r.get("lent_out"):
        L.append(f"    └ うち他営業所へ貸出 : {r['lent_out']:>4} 名 "
                 f"(自営業所タグ。貸出も稼働として算入)")
    L.append(f"  管理費現場      : {r['overhead_only']:>4} 名  {r['overhead_names']}")
    L.append(f"  待機・休み枠    : {r['standby']:>4} 名  (番割の待機/休み。分母に算入・非稼働)")
    L.append(f"  出勤(=分母)合計 : {r['present']:>4} 名")
    L.append("")
    L.append("【他営業所からの応援(借りた人工)・別集計】")
    L.append(f"  実人数          : {r['other_total']:>4} 名  "
             f"(うち外貨現場 {r['other_revenue']} 名)")
    if r["other_by_office"]:
        office_str = "  ".join(f"{k}{v}" for k, v in
                               sorted(r["other_by_office"].items(),
                                      key=lambda x: -x[1]))
        L.append(f"  応援元営業所    : {office_str}")
    L.append("")
    L.append(f"【集計対象外】他営業所の自前労務 : {r['ignored']:>4} 名")
    L.append("=" * 66)
    return "\n".join(L)


HISTORY_HEADER = ["日付", "営業所", "在籍", "出勤(分母)", "外貨(分子)",
                  "管理費", "待機休み", "番割なし(参考)", "稼働率%", "実働率%",
                  "他営業所応援", "対象外"]
# 実働率% 追加前の旧ヘッダ(既存CSVの読み替え用)
_OLD_HISTORY_HEADER = HISTORY_HEADER[:9] + HISTORY_HEADER[10:]
_RATE_ACTIVE_COL = 9   # 実働率% の列位置(旧形式の行にはここへ空欄を挿す)

COMPANY_TOTAL_LABEL = "全社合計"


def company_totals(reports):
    """同じ日付の複数営業所レポートを合算した「全社合計」レポートを返す。

    2営業所以上そろった日付だけ作る(1営業所しかない日は合計＝その営業所で無意味)。
    営業所間の応援は貸し手側の番割で自社として1回だけ数えられている前提の単純合計。
    other_total(借りた人工)は内部の貸し借りを含む参考値になる。
    """
    by_date = defaultdict(list)
    for r in reports:
        if not r.get("is_total"):
            by_date[r["date"]].append(r)
    totals = []
    sum_keys = ("roster_size", "denominator", "present", "revenue",
                "overhead_only", "standby", "standby_taiki", "standby_yasumi",
                "absent", "lent_out", "other_total", "other_revenue", "ignored")
    for date, rs in sorted(by_date.items()):
        if len(rs) < 2:
            continue
        t = {k: sum(r[k] for r in rs) for k in sum_keys}
        t["office"] = f"{COMPANY_TOTAL_LABEL}({len(rs)}営業所)"
        t["date"] = date
        t["is_total"] = True
        t["colormap_empty"] = any(r.get("colormap_empty") for r in rs)
        t["rate"] = (t["revenue"] / t["denominator"]) if t["denominator"] else 0.0
        t["denominator_active"] = t["denominator"] - t["standby_yasumi"]
        t["rate_active"] = ((t["revenue"] / t["denominator_active"])
                            if t["denominator_active"] else 0.0)
        names = []
        for r in rs:
            names.extend(r.get("overhead_names", []))
        t["overhead_names"] = sorted(names)
        by_office = defaultdict(int)
        for r in rs:
            for k, v in r.get("other_by_office", {}).items():
                by_office[k] += v
        t["other_by_office"] = dict(by_office)
        t["details"] = []
        t["review"] = []
        totals.append(t)
    return totals


def render_support_matrix(reports):
    """営業所間の応援(借り手×貸し手)マトリクスの文字列を返す。データが無ければ空。"""
    by_date = defaultdict(dict)   # date -> {借り手: {貸し手: 人数}}
    lenders = set()
    for r in reports:
        if r.get("is_total") or not r.get("other_by_office"):
            continue
        borrower = office_key(r["office"]) or r["office"]
        by_date[r["date"]][borrower] = r["other_by_office"]
        lenders.update(r["other_by_office"].keys())
    if not by_date:
        return ""
    L = []
    for date, rows in sorted(by_date.items()):
        cols = sorted(lenders)
        L.append("=" * 66)
        L.append(f"営業所間の応援マトリクス(借り手 × 貸し手)  {date}")
        L.append("=" * 66)
        L.append("  " + f"{'借り手＼貸し手':<14}" + "".join(f"{c:>8}" for c in cols)
                 + f"{'計':>8}")
        for borrower, m in sorted(rows.items()):
            vals = [m.get(c, 0) for c in cols]
            L.append("  " + f"{borrower:<14}"
                     + "".join(f"{(v if v else '-'):>8}" for v in vals)
                     + f"{sum(vals):>8}")
        L.append("")
    return "\n".join(L).rstrip()


def append_history(path, reports):
    """日次履歴CSVに追記する。同じ(日付,営業所)は最新で置き換える。

    実働率% 追加前の旧形式のCSVは、読み込み時にその列へ空欄を挿して新形式に揃える。
    """
    path = Path(path)
    existing = []
    if path.exists():
        with open(path, encoding="cp932", errors="replace", newline="") as f:
            rdr = csv.reader(f)
            rows = list(rdr)
        if rows and rows[0] == _OLD_HISTORY_HEADER:
            existing = [r[:_RATE_ACTIVE_COL] + [""] + r[_RATE_ACTIVE_COL:]
                        for r in rows[1:]]
        else:
            existing = rows[1:] if rows else []
    keep = []
    new_keys = {(r["date"], r["office"]) for r in reports}
    for row in existing:
        if len(row) >= 2 and (row[0], row[1]) in new_keys:
            continue  # 同じ日付・営業所の古い行は捨てて入れ替え
        keep.append(row)
    for r in reports:
        keep.append([r["date"], r["office"], r["roster_size"], r["present"],
                     r["revenue"], r["overhead_only"], r["standby"], r["absent"],
                     f"{r['rate'] * 100:.1f}", f"{r.get('rate_active', 0) * 100:.1f}",
                     r["other_total"], r["ignored"]])
    keep.sort(key=lambda x: (str(x[0]), str(x[1])))
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(HISTORY_HEADER)
        w.writerows(keep)


# ---------------------------------------------------- 番割の取得(2系統)
def load_aliases(path=ALIASES_PATH):
    """name_aliases.json を読む(先頭が _ のコメントキーは無視)。"""
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def staff_set(lst):
    """事務スタッフ指定(コード/氏名の混在リスト)を照合用の集合にする。"""
    s = set()
    for x in lst or []:
        x = str(x).strip()
        if x:
            s.add(x)
            s.add(_norm(x))
    return s


# ------------------------------------------------- スナップショット(生データの控え)
# 集計のたびに、読み取った生の割当(誰が・どこに・どの色で)を (営業所,日付) ごとの
# CSVに保存する。番割が画面から消えた後でも、色・対照表・除外の設定を直して
# このファイルだけで過去分を再集計できる(集計を「保存データに対する純粋計算」にする)。
SNAPSHOT_HEADER = ["営業所", "日付", "顧客", "現場", "作業員", "バッジ", "状態", "背景色"]
_FNAME_BAD_RE = re.compile(r'[\\/:*?"<>|]+')


def snapshot_path(office, date, base=SNAPSHOT_DIR):
    """(営業所, 日付) のスナップショットの保存先パスを返す。"""
    office_s = _FNAME_BAD_RE.sub("_", (office or "").strip()) or "営業所不明"
    return Path(base) / f"{date or '日付不明'}_{office_s}.csv"


def save_snapshots(assignments, base=SNAPSHOT_DIR, log=print):
    """読み取った生の割当を (営業所,日付) ごとのCSVに保存する。

    同じ(営業所,日付)は最新の読み取りで上書き(履歴の upsert と同じ考え方)。
    Returns: 保存したファイルパスのリスト。
    """
    boards = defaultdict(list)
    for a in assignments:
        boards[(a.get("office", ""), a.get("date", ""))].append(a)
    if not boards:
        return []
    Path(base).mkdir(parents=True, exist_ok=True)
    paths = []
    for (office, date), rows in sorted(boards.items()):
        p = snapshot_path(office, date, base)
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(SNAPSHOT_HEADER)
            for a in rows:
                w.writerow([a.get("office", ""), a.get("date", ""),
                            a.get("customer", ""), a.get("site", ""),
                            a.get("worker", ""), a.get("badge", ""),
                            a.get("status", ""), a.get("bg", "")])
        paths.append(p)
    log(f"スナップショット保存: {len(paths)} 番割 → {Path(base).name}/")
    return paths


def load_snapshots(paths):
    """スナップショットCSV(ファイル/フォルダ混在可)を読み、割当リストを返す。"""
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("*.csv")) + sorted(p.glob("*.CSV")))
        elif p.exists():
            files.append(p)
        else:
            raise RuntimeError(f"スナップショットが見つかりません: {p}")
    if not files:
        raise RuntimeError("スナップショットCSVが1つも見つかりません")
    out = []
    for fpath in files:
        with open(fpath, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        if not rows or rows[0] != SNAPSHOT_HEADER:
            raise RuntimeError(f"スナップショット形式ではありません: {fpath}")
        for r in rows[1:]:
            if len(r) < len(SNAPSHOT_HEADER) or not r[4].strip():
                continue
            out.append({"office": r[0], "date": r[1], "customer": r[2],
                        "site": r[3], "worker": r[4], "badge": r[5],
                        "status": r[6], "bg": r[7]})
    return out


def read_assignments(select=None, colormap=None, log=print, snapshot=True):
    """開いている番割から全作業員の割当を読み取って返す(既定でスナップショットも保存)。

    GUI はこの戻り値をメモリ保持しておけば、設定変更時に番割を読み直さず
    analyze_assignments() だけで即座に再集計できる。
    """
    if colormap is None:
        colormap = load_colormap()
    # 色マップが登録されているときだけ採色する(空なら採色コスト無し)
    assignments = assignments_from_hks(select=select, color=bool(colormap), log=log)
    if snapshot:
        try:
            save_snapshots(assignments, log=log)
        except Exception as e:
            log(f"スナップショット保存に失敗(集計は続行): {e}")
    return assignments


def analyze_assignments(assignments, roster_paths=None, cust_kw=None, site_kw=None,
                        aliases=None, staff=None, colormap=None):
    """割当リストから (営業所,日付)ごとのレポート一覧を返す(純粋計算)。

    割当の出どころは問わない: 番割の読み取り(read_assignments)・スナップショット
    (load_snapshots)・inspectダンプ(assignments_from_inspect)・GUIのメモリ保持。
    """
    roster = load_rosters(roster_paths, active_only=True) if roster_paths else []
    if aliases is None:
        aliases = load_aliases()
    if colormap is None:
        colormap = load_colormap()
    if cust_kw is None or site_kw is None or staff is None:
        s = load_settings(SETTINGS_PATH)
        cust_kw = s["exclude_customer_keywords"] if cust_kw is None else cust_kw
        site_kw = s["exclude_site_keywords"] if site_kw is None else site_kw
        staff = s["non_field_staff"] if staff is None else staff
    staff_keys = staff_set(staff)
    boards = defaultdict(list)
    for a in assignments:
        boards[(a.get("office", ""), a.get("date", ""))].append(a)
    reports = []
    for (office, date), rows in sorted(boards.items()):
        reports.append(compute_board(office, date, rows, roster,
                                     cust_kw, site_kw, aliases, staff_keys,
                                     colormap=colormap))
    return reports


def analyze(roster_paths=None, inspect_path=None, cust_kw=None, site_kw=None,
            aliases=None, staff=None, select=None, colormap=None,
            snapshot_paths=None, log=print):
    """番割から (営業所,日付)ごとのレポート一覧を返す。GUI/CLI 共通の入口。

    判定は氏名の背景色(worker_colors)で行う。名簿(roster_paths)は任意で、渡せば
    在籍数の参考と名簿コードの解決にだけ使う(判定には影響しない)。

    select: None なら開いている全番割を集計。(営業所, 日付) のタプル集合を渡すと、
            その番割だけを集計する(inspect_path 指定時は無視)。
    colormap: worker_color.ColorMap。None なら worker_colors.json を読む。
    snapshot_paths: スナップショットCSV(ファイル/フォルダ)から再集計する。
            番割は読まない(過去分の遡り再計算用)。
    """
    if colormap is None:
        colormap = load_colormap()
    if inspect_path:
        assignments = assignments_from_inspect(inspect_path)
    elif snapshot_paths:
        assignments = load_snapshots(snapshot_paths)
    else:
        assignments = read_assignments(select=select, colormap=colormap, log=log)
    return analyze_assignments(assignments, roster_paths, cust_kw, site_kw,
                               aliases, staff, colormap)


def collect_review(reports):
    """全レポートの取りこぼし候補を優先度順にまとめて返す。"""
    prio = {"要確認": 0, "確認推奨": 1}
    review = []
    for r in reports:
        review.extend(r["review"])
    review.sort(key=lambda x: (prio.get(x["priority"], 9), x["office"], x["worker"]))
    return review


def assignments_from_hks(select=None, color=False, log=print):
    import hks_reader as hr
    color_factory = None
    if color:
        try:
            import worker_color as wc
            color_factory = wc.make_sampler_for
        except Exception as e:
            log(f"色採取モジュールを読み込めませんでした(色判定なしで続行): {e}")
    return hr.read_all_assignments(select=select, log=log,
                                   color_factory=color_factory)


def load_colormap():
    """worker_colors.json を読んで ColorMap を返す。読めなければ空マップ。"""
    try:
        import worker_color as wc
        return wc.load_color_map()
    except Exception:
        return None


def list_boards(log=print):
    """開いている番割の一覧 [{office, date, update_hhmm}] を返す。

    GUI で「どの番割から集計するか」を選ばせるための列挙。実際の読み取りより軽い。
    """
    import hks_reader as hr
    return hr.list_boards(log=log)


# 見出し行 '# 営業所  YYYY-MM-DD ...'。日付の後ろに '(採色方式: ...)' 等が付いてもよい
_INSPECT_HEAD_RE = re.compile(r"^#+\s*(.+?)\s+(\d{4}-\d{2}-\d{2})\b")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_RECT_RE = re.compile(r"^\d+(?:,\d+){3}$")   # 採色域 'L,T,R,B'


def assignments_from_inspect(path):
    """workers_inspect.txt から [{office,date,customer,site,worker,badge,bg}] を復元。

    2つの形式に対応する(どちらも見出し行 '# 営業所  YYYY-MM-DD ...' で区切られる):
      ・inspect_workers.bat の一覧
        …2スペース以上区切り: 顧客  現場  氏名  [バッジ]  [背景色]  [採色域]  [車両印]
      ・GUI『詳細を書き出す』(worker_color.dump_details) のタブ区切り
        …氏名\\tバッジ\\t背景色\\t採色域\\t顧客\\t現場
    """
    out = []
    office, date = "", ""
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        m = _INSPECT_HEAD_RE.match(ln)
        if m:
            office, date = m.group(1).strip(), m.group(2)
            continue
        if not ln.strip() or ln.startswith(("#", "=", "-", "番割", "顧客", "色取得",
                                            "作業員", "※")):
            continue
        if "\t" in ln:
            # タブ区切り(dump_details): 氏名 バッジ 背景色 採色域 顧客 現場
            parts = [p.strip() for p in ln.split("\t")]
            if len(parts) < 6 or not parts[0]:
                continue
            worker, badge, bg = parts[0], parts[1], parts[2].upper()
            cust, site = parts[4], parts[5]
            if not _HEX_RE.match(bg):
                bg = ""
        else:
            # 2スペース以上区切り(inspect_workers): 顧客 現場 氏名 …
            parts = re.split(r" {2,}", ln.rstrip())
            if len(parts) < 3 or not parts[2].strip():
                continue
            cust, site, worker = parts[0].strip(), parts[1].strip(), parts[2].strip()
            badge, bg = "", ""
            for x in parts[3:]:
                x = x.strip()
                if not x or x in ("○", "×", "待", "-----"):
                    continue          # 車両印・待機印・採色不能マーク
                if _HEX_RE.match(x):
                    bg = x.upper()    # 背景色(自社/他社の色判定用)
                elif _RECT_RE.match(x):
                    continue          # 採色域の座標
                else:
                    badge = x
        status = "待機" if "待機" in cust else ("休み" if cust in STANDBY_LABELS else "")
        out.append({"office": office, "date": date,
                    "customer": cust, "site": site,
                    "worker": worker, "badge": badge, "bg": bg,
                    "status": status})
    return out


def save_settings(settings, path=SETTINGS_PATH):
    """除外キーワード・分母除外スタッフ設定を JSON に保存する。"""
    data = {
        "exclude_customer_keywords": list(settings.get("exclude_customer_keywords", [])),
        "exclude_site_keywords": list(settings.get("exclude_site_keywords", [])),
        "non_field_staff": list(settings.get("non_field_staff", [])),
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def save_aliases(aliases, path=ALIASES_PATH):
    """対照表(name_aliases) を JSON に保存する。既存のコメント(_ キー)は残す。"""
    p = Path(path)
    base = {}
    if p.exists():
        try:
            base = {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items()
                    if k.startswith("_")}
        except Exception:
            base = {}
    base.update(aliases)
    p.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")


def write_outputs(reports, out_path=None, csv_path=None,
                  history_path=HISTORY_PATH, review_path=REVIEW_PATH):
    """レポート/明細/履歴/取りこぼしCSVを書き出す。GUI/CLI 共通。

    Returns: (レポート本文, review一覧, 要確認数, 確認推奨数)
    """
    out_path = out_path or (HERE / "utilization_report.txt")
    csv_path = csv_path or (HERE / "utilization_detail.csv")
    # 2営業所以上そろった日付には全社合計を足す(呼び出し側で足済みなら重複させない)
    reports = list(reports)
    if not any(r.get("is_total") for r in reports):
        reports += company_totals(reports)
    out_text = "\n\n".join(render_board(r) for r in reports)
    matrix = render_support_matrix(reports)
    if matrix:
        out_text += "\n\n" + matrix
    Path(out_path).write_text(out_text, encoding="utf-8")

    with open(csv_path, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(["営業所", "日付", "作業員", "バッジ", "背景色", "区分",
                    "現場種別", "判定備考", "顧客", "現場"])
        for r in reports:
            for d in r["details"]:
                w.writerow([d["office"], d["date"], d["worker"], d["badge"],
                            d.get("bg", ""), d["kind"], d["field"],
                            d.get("note", ""), d["customer"], d["site"]])

    append_history(history_path, reports)

    review = collect_review(reports)
    with open(review_path, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(["優先", "営業所", "日付", "氏名(対照表のキー)", "バッジ", "背景色",
                    "現在の判定", "推奨コード候補", "顧客", "現場", "対応のヒント"])
        for x in review:
            w.writerow([x["priority"], x["office"], x["date"], x["worker"], x["badge"],
                        x.get("bg", ""), x["current"], x.get("suggest", ""),
                        x["customer"], x["site"], x["hint"]])

    n_check = sum(1 for x in review if x["priority"] == "要確認")
    n_reco = sum(1 for x in review if x["priority"] == "確認推奨")
    return out_text, review, n_check, n_reco


def main():
    ap = argparse.ArgumentParser(description="作業員稼働率の集計(営業所ごと)")
    ap.add_argument("--roster", nargs="*", default=None,
                    help="(任意)社員名簿CSV(Shift-JIS)。判定は背景色で行うので必須ではない。"
                         "渡すと在籍数の参考・名簿コード解決に使う。フォルダ可")
    ap.add_argument("--inspect", help="workers_inspect.txt から計算(指定時は番割を読まない)")
    ap.add_argument("--snapshot", nargs="*", default=None,
                    help="スナップショットCSV(snapshots/ のファイル/フォルダ)から再集計。"
                         "番割を読まない。設定を直した後の過去分の遡り再計算用")
    ap.add_argument("--exclude", nargs="*", default=None,
                    help="外貨を産まない顧客キーワード(部分一致)。指定時は設定ファイルより優先")
    ap.add_argument("--out", default=str(HERE / "utilization_report.txt"))
    ap.add_argument("--csv", default=str(HERE / "utilization_detail.csv"))
    ap.add_argument("--history", default=str(HISTORY_PATH),
                    help="日次履歴CSVの保存先(同じ日付・営業所は上書き)")
    ap.add_argument("--review", default=str(REVIEW_PATH),
                    help="取りこぼし候補CSVの保存先")
    args = ap.parse_args()

    aliases = load_aliases()
    settings = load_settings(SETTINGS_PATH)
    cust_kw = args.exclude if args.exclude is not None else settings["exclude_customer_keywords"]
    site_kw = settings["exclude_site_keywords"]
    print(f"除外キーワード  顧客: {cust_kw}  現場: {site_kw}")

    snapshot_paths = args.snapshot if args.snapshot else None
    if args.snapshot is not None and not args.snapshot:
        snapshot_paths = [SNAPSHOT_DIR]   # --snapshot 引数なし = snapshots/ 全部
    reports = analyze(args.roster, inspect_path=args.inspect,
                      cust_kw=cust_kw, site_kw=site_kw, aliases=aliases,
                      snapshot_paths=snapshot_paths)

    out_text, review, n_check, n_reco = write_outputs(
        reports, args.out, args.csv, args.history, args.review)
    print()
    print(out_text)

    print(f"\nレポート: {args.out}")
    print(f"明細CSV : {args.csv}")
    print(f"日次履歴: {args.history}  (番割 {len(reports)} 営業所ぶんを記録)")
    print(f"取りこぼし候補: {args.review}  "
          f"(要確認 {n_check} 件 / 確認推奨 {n_reco} 件)")
    if review:
        print("  → 自社の人が混じっていたら name_aliases.json にコードを追記してください")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        raise

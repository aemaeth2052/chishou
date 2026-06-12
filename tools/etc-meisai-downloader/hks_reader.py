# -*- coding: utf-8 -*-
"""Hks「番割予定表」(WPF) 読み取りモジュール

UI Automation 経由で番割予定表ウィンドウを読み、
顧客→現場→車両→作業員 の構造を取り出す。

画面構造 (2026-06 時点、実物のツリーダンプに基づく):
  Window > Pane > [Text 顧客名][Custom 現場ブロック]... の繰り返し
  現場ブロック内:
    Text  : (先頭の番号) / 現場名 / 出X:XX / 交通手段(車・電等)
    Custom: 左側 = 車両・フラグ(￥地書/ETC/携帯) / 右側 = 作業員
    Text  : 作業内容 / 住所

車両欄の先頭数字が ETC利用照会の車両番号(下4桁)に対応する。
  例) '軽27-④'→27, '1606ハイ'→1606, '1499'→1499, '7772・Caravan'→7772
"""

import datetime
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "hks_records.txt"

TRANSPORTS = {"車", "電", "迎", "送迎", "同乗", "自家用車", "徒歩", "送り"}
FLAG_CHARS = set("￥地書他注")
# 顧客カードではないセクション見出し
NON_CUSTOMER = {"待機", "休み/留守", "休み", "留守"}

# 先頭の装飾記号 (●★☆◎ など) と、顧客名先頭の [若松] [下建] [元 商] 等の角カッコ
_LEAD_MARK_RE = re.compile(r"^[●★☆◎▲△◆◇■□]+\s*")
_LEAD_BRACKET_RE = re.compile(r"^[\[【［][^\]】］]*[\]】］]\s*")


def _strip_lead_marks(text: str) -> str:
    """先頭の装飾(●など)を除去"""
    return _LEAD_MARK_RE.sub("", text).strip()


def _clean_customer(text: str) -> str:
    """顧客名: 先頭の角カッコ ([若松] [下建] 等) と装飾記号を除去"""
    s = text.strip()
    while True:
        prev = s
        s = _LEAD_BRACKET_RE.sub("", s)
        s = _LEAD_MARK_RE.sub("", s)
        if s == prev:
            break
    return s


def find_schedule_windows():
    """番割予定表ウィンドウを全て返す (営業所ごとに複数開いている場合に対応)"""
    from pywinauto import Desktop
    wins = []
    for w in Desktop(backend="uia").windows():
        try:
            if "予定表" in w.window_text():
                wins.append(w)
        except Exception:
            continue
    return wins


_HEADER_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_HEADER_UPDATE_RE = re.compile(r"\[?\s*(\d{1,2}):(\d{2})\s*更新\s*\]?")


def _header_info(win):
    """ウィンドウ直下のヘッダから (営業所, 日付ISO, 更新時刻HH:MM) を取る"""
    try:
        for c in win.children():
            try:
                if c.element_info.control_type != "Text":
                    continue
                t = (c.window_text() or "").strip()
            except Exception:
                continue
            m = _HEADER_DATE_RE.search(t)
            if not m:
                continue
            date_iso = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            office = t[:m.start()].strip()
            um = _HEADER_UPDATE_RE.search(t)
            update = f"{int(um.group(1)):02d}:{um.group(2)}" if um else ""
            return office, date_iso, update
    except Exception:
        pass
    return "", "", ""


def _parse_update_dt(hhmm: str, now: datetime.datetime):
    """HH:MM をその時刻が指す直近の絶対時刻として返す。

    現在時刻より未来になる場合は前日同時刻と解釈する。
    パース不能なら None。
    """
    if not hhmm:
        return None
    try:
        hh, mm = hhmm.split(":")
        hh, mm = int(hh), int(mm)
    except Exception:
        return None
    dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if dt > now:
        dt -= datetime.timedelta(days=1)
    return dt


def _snap(ctrl):
    """コントロールを軽量な辞書ツリーに変換 (UIA往復を1回で済ませる)"""
    try:
        ct = ctrl.element_info.control_type
    except Exception:
        ct = ""
    try:
        txt = ctrl.window_text() or ""
    except Exception:
        txt = ""
    try:
        r = ctrl.rectangle()
        rect = (r.left, r.top, r.right, r.bottom)
    except Exception:
        rect = (0, 0, 0, 0)
    kids = []
    try:
        for k in ctrl.children():
            kids.append(_snap(k))
    except Exception:
        pass
    return {"ct": ct, "text": txt, "rect": rect, "children": kids}


def is_vehicle(text: str) -> bool:
    """車両欄テキストが実車両か (電車パス・電話・フラグを除く)"""
    t = text.strip()
    if not t:
        return False
    if all(c in FLAG_CHARS for c in t):
        return False
    if "携帯" in t or t.startswith("ETC"):
        return False
    # 数字始まり or 軽(軽自動車)始まり を車両とみなす
    return bool(re.match(r"^[0-9]", t) or t.startswith("軽"))


def vehicle_number(text: str) -> str:
    """車両欄テキストから ETC車両番号(下4桁)に対応する数字を取り出す"""
    m = re.search(r"\d+", text)
    return m.group() if m else ""


def _fields_of(block):
    kids = block["children"]
    if len(kids) == 1 and kids[0]["ct"] == "Custom":
        return kids[0]["children"]
    return kids


def _parse_block(block, customer):
    fields = _fields_of(block)
    bl, _, br, _ = block["rect"]
    mid_x = (bl + br) / 2

    plain = []        # (text, rect)
    left_items = []   # {"texts":[...], "rect":...}
    right_items = []
    for f in fields:
        if f["ct"] == "Custom":
            subs = [c["text"].strip() for c in f["children"] if c["text"].strip()]
            cx = (f["rect"][0] + f["rect"][2]) / 2
            item = {"texts": subs, "rect": f["rect"]}
            if cx <= mid_x:
                left_items.append(item)
            else:
                right_items.append(item)
        elif f["ct"] == "Text":
            t = f["text"].strip()
            if t:
                plain.append((t, f["rect"]))

    # 車両 / ETCラベル
    vehicle_raw = ""
    etc_label = ""
    for it in left_items:
        joined = "".join(it["texts"])
        if joined.startswith("ETC") and not etc_label:
            etc_label = joined
        if not vehicle_raw and is_vehicle(joined):
            vehicle_raw = joined

    # 作業員 (右側Customの名前。●★や所属プレフィックスを除去)
    workers = []
    for it in right_items:
        if it["texts"]:
            name = it["texts"][-1].lstrip("●★☆◎").strip()
            if name:
                workers.append(name)

    # 現場名 / 出発 / 交通手段
    site = ""
    departure = ""
    transport = ""
    for t, _ in plain:
        if t in TRANSPORTS and not transport:
            transport = t
    for t, _ in plain:
        if re.match(r"^[▲△]?出\s*\d", t) and not departure:
            departure = re.sub(r"^[▲△]", "", t)
    for t, _ in plain:
        if re.match(r"^\d+$", t):           # 先頭の通し番号
            continue
        if re.match(r"^[▲△]?出\s*\d", t):    # 出発時刻
            continue
        if t in TRANSPORTS or t in FLAG_CHARS:
            continue
        site = _strip_lead_marks(t)
        break

    # 住所 (best-effort)
    address = ""
    for t, _ in plain:
        if "住所" in t or re.search(r"(東京都|千葉県|神奈川県|埼玉県|茨城県|栃木県|群馬県|山梨県)", t):
            address = t.replace("\\n", " / ")

    return {
        "customer": customer,
        "site": site,
        "vehicle_raw": vehicle_raw,
        "vehicle_no": vehicle_number(vehicle_raw),
        "etc_label": etc_label,
        "departure": departure,
        "transport": transport,
        "workers": workers,
        "address": address,
    }


def read_schedule(log=print):
    """開いている全ての番割予定表を読み、現場レコードのリストを返す。

    各レコード: {customer, site, vehicle_raw, vehicle_no, etc_label,
                departure, transport, workers, address, office, date}
    車両が特定できたレコードのみ返す。
    """
    wins = find_schedule_windows()
    if not wins:
        raise RuntimeError(
            "番割予定表ウィンドウが見つかりません。Hksで番割予定表を表示してください。"
        )

    records = []
    now = datetime.datetime.now()
    for win in wins:
        office, date_iso, update_hhmm = _header_info(win)
        update_dt = _parse_update_dt(update_hhmm, now)
        update_dt_iso = update_dt.isoformat() if update_dt else ""
        label = office or "営業所不明"
        # Pane (カードの器) を取得
        pane = None
        for c in win.children():
            try:
                if c.element_info.control_type == "Pane":
                    pane = c
                    break
            except Exception:
                continue
        if pane is None:
            log(f"  {label}: カード領域(Pane)が見つからずスキップしました")
            continue

        log(f"番割予定表を読み取っています... ({label} {date_iso or '日付不明'}"
            f"{' 更新' + update_hhmm if update_hhmm else ''})")
        root = _snap(pane)

        count = 0
        current_customer = None
        for child in root["children"]:
            if child["ct"] == "Text":
                t = child["text"].strip()
                if t and t not in NON_CUSTOMER:
                    current_customer = _clean_customer(t)
                elif t in NON_CUSTOMER:
                    current_customer = None   # 待機/休み セクションに入った
            elif child["ct"] == "Custom" and current_customer:
                rec = _parse_block(child, current_customer)
                # 車両が取れたものだけ採用 (待機・休み・電車のみは除外)
                if rec["vehicle_no"]:
                    rec["office"] = office
                    rec["date"] = date_iso
                    rec["update_hhmm"] = update_hhmm
                    rec["update_dt"] = update_dt_iso
                    records.append(rec)
                    count += 1
        log(f"  → {count} 件の車両割当を取得")

    log(f"読み取り完了: 合計 {len(records)} 件 (予定表 {len(wins)} 画面)")
    return records


def build_vehicle_map(records):
    """車両番号 -> [レコード...] の辞書 (ETC明細との突き合わせ用)"""
    m = {}
    for r in records:
        m.setdefault(r["vehicle_no"], []).append(r)
    return m


# ---------------------------------------------------------- 単体テスト用
def _main():
    try:
        records = read_schedule()
    except Exception as e:
        msg = f"エラー: {e}"
        print(msg)
        OUT.write_text(msg, encoding="utf-8")
        sys.exit(1)

    lines = []

    def w(s=""):
        print(s)
        lines.append(str(s))

    w("=" * 70)
    w(f"番割予定表 読み取り結果: {len(records)} 件")
    w("=" * 70)
    w(f"{'車両':<8}{'顧客':<22}{'現場':<28}作業員")
    w("-" * 70)
    for r in sorted(records, key=lambda x: (x["vehicle_no"].zfill(4))):
        workers = "、".join(r["workers"][:4])
        w(f"{r['vehicle_no']:<8}{r['customer'][:20]:<22}{r['site'][:26]:<28}{workers}")

    # 車両番号の重複チェック
    vm = build_vehicle_map(records)
    dups = {k: v for k, v in vm.items() if len(v) > 1}
    if dups:
        w("")
        w("【注意】同じ車両番号が複数の現場に出ています:")
        for num, recs in sorted(dups.items()):
            sites = " / ".join(f"{x['customer']}:{x['site']}" for x in recs)
            w(f"  車両{num}: {sites}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n結果を保存しました: {OUT}")


if __name__ == "__main__":
    _main()

# -*- coding: utf-8 -*-
"""番割予定表 作業員インスペクタ (稼働率計測の下調べ用)

目的:
  作業員稼働率を出すために、番割から「どんな情報が取れるか」を実機で確認する。
  特に次の2点を確かめる。

    (1) 外注/応援作業員は色が違って表示される、とのこと。
        → その「色」が機械的に拾えるかを、作業員1人ごとに調べて吐き出す。
    (2) 管理費など外貨を産まない現場を除外したい。
        → 現場名を全部(車両の有無に関係なく)一覧化し、除外条件に
           使える文字列があるかを見えるようにする。

  ※ hks_reader 本体は「車両が割り当たった現場」しか残さない(ETC突合が目的)。
     稼働率では全作業員が要るので、このスクリプトは車両の有無を問わず
     全ブロック・全作業員を出力する。

実行:
  Hks の「番割予定表」を画面に出した状態で、Hks と同じ PC で実行する。
  inspect_workers.bat をダブルクリックしてもよい。

出力(このスクリプトと同じフォルダ):
  workers_inspect.txt : 人が読む用の一覧(作業員・現場・色)。ここを開発者に共有。
  workers_colors.txt  : 検出された文字色のクラスタ一覧(色ごとの人数)。
"""

import ctypes
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "workers_inspect.txt"
COLORS = HERE / "workers_colors.txt"

# 番割の座標(矩形)は物理ピクセルで返ってくる。ピクセル色サンプリングと
# 座標系を一致させるため、プロセスを DPI 認識にしておく。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2 相当
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import hks_reader as hr  # noqa: E402  (DPI 設定後に import したい)


# ----------------------------------------------------------------- 画面の色取得
class Sampler:
    """画面のピクセル色を (x, y) 物理座標で取得する。

    Pillow があれば全画面を1回だけキャプチャして高速に読む。
    無ければ GDI の GetPixel に切り替える(低速だが追加ライブラリ不要)。
    番割が複数モニタにまたがっていても拾えるよう、仮想スクリーン全体を対象にする。
    """

    def __init__(self):
        self.mode = None
        user32 = ctypes.windll.user32
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        self.vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        self.vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        try:
            from PIL import ImageGrab
            self.img = ImageGrab.grab(all_screens=True)
            self.px = self.img.load()
            self.W, self.H = self.img.size
            self.mode = "pil"
        except Exception:
            self.gdi = ctypes.windll.gdi32
            self.hdc = user32.GetDC(0)
            self.mode = "gdi"

    def get(self, x, y):
        """(R, G, B) を返す。取得不能なら None。"""
        if self.mode == "pil":
            ix, iy = x - self.vx, y - self.vy
            if 0 <= ix < self.W and 0 <= iy < self.H:
                c = self.px[ix, iy]
                return (c[0], c[1], c[2])
            return None
        v = self.gdi.GetPixel(self.hdc, x, y)
        if v == 0xFFFFFFFF:  # CLR_INVALID
            return None
        return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def text_color(sampler, rect):
    """矩形内をなめて (文字色, 背景色) を推定する。

    背景色 = 最頻色。文字色 = 背景から十分離れた色のうち最頻のもの。
    アンチエイリアスで中間色が出るので、背景との距離が閾値超のものだけを文字候補にする。
    取れなければ (None, None)。
    """
    l, t, r, b = rect
    if r - l < 2 or b - t < 2:
        return None, None
    step = 1 if sampler.mode == "pil" else 2  # GDI は遅いので間引く
    counts = defaultdict(int)
    yy = t + 1
    while yy < b - 1:
        xx = l + 1
        while xx < r - 1:
            c = sampler.get(xx, yy)
            if c is not None:
                counts[c] += 1
            xx += step
        yy += step
    if not counts:
        return None, None
    bg = max(counts, key=lambda k: counts[k])
    THRESH = 60  # 背景からこの距離以上離れていれば「文字」とみなす
    text = None
    best = 0
    for col, n in counts.items():
        if _dist(col, bg) >= THRESH and n > best:
            best, text = n, col
    if text is None:
        text = bg  # 文字が拾えない(背景と同色?)→ 区別なしとして背景色を返す
    return text, bg


def hexc(c):
    return "#%02X%02X%02X" % (c[0], c[1], c[2]) if c else "-----"


def cluster_key(c):
    """アンチエイリアスのゆらぎを丸めて色クラスタの代表値にする(各色24刻み)。"""
    return tuple((v // 24) * 24 for v in c)


# ------------------------------------------------------------- ブロック→作業員
def worker_rects(block):
    """1ブロックから (作業員名, 矩形) のリストを返す。

    hks_reader._parse_block と同じ「右側 Custom = 作業員」判定を使うが、
    色サンプリング用に矩形も持って返すのが違い。
    """
    fields = hr._fields_of(block)
    bl, _, br, _ = block["rect"]
    mid_x = (bl + br) / 2
    out = []
    for f in fields:
        if f["ct"] != "Custom":
            continue
        cx = (f["rect"][0] + f["rect"][2]) / 2
        if cx <= mid_x:
            continue  # 左側は車両・フラグ
        texts = [c for c in f["children"] if c["text"].strip()]
        if not texts:
            continue
        last = texts[-1]  # 作業員名は末尾テキスト
        name = hr._clean_worker_name(last["text"])
        if name:
            out.append((name, last["rect"]))
    return out


def main():
    metas = hr.enumerate_windows()
    if not metas:
        OUT.write_text(
            "番割予定表ウィンドウが見つかりません。\n"
            "Hks で番割予定表を表示してから再実行してください。",
            encoding="utf-8",
        )
        print("番割予定表が見つかりません")
        return

    sampler = Sampler()
    lines = []
    color_members = defaultdict(list)  # クラスタ色 -> [(名前, 現場, hex)]

    def w(s=""):
        print(s)
        lines.append(str(s))

    w("=" * 78)
    w(f"番割 作業員インスペクタ  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"色取得方式: {sampler.mode}  /  予定表 {len(metas)} 画面")
    w("=" * 78)

    total_workers = 0
    for m in metas:
        win = m["win"]
        office = m.get("office", "") or "営業所不明"
        date_iso = m.get("date", "") or "日付不明"

        pane = None
        for c in win.children():
            try:
                if c.element_info.control_type == "Pane":
                    pane = c
                    break
            except Exception:
                continue
        if pane is None:
            w(f"\n[{office} {date_iso}] カード領域(Pane)が見つからずスキップ")
            continue

        try:
            root = hr._snap_cached(pane)
        except Exception as e:
            w(f"  高速読取に失敗、通常方式に切替: {e}")
            root = hr._snap(pane)

        w("")
        w("#" * 78)
        w(f"# {office}  {date_iso}")
        w("#" * 78)
        w(f"{'顧客':<16}{'現場':<22}{'作業員':<12}{'文字色':<9}{'背景':<9}矩形")
        w("-" * 78)

        current_customer = None
        for child in root["children"]:
            if child["ct"] == "Text":
                t = child["text"].strip()
                if t and t not in hr.NON_CUSTOMER:
                    current_customer = hr._clean_customer(t)
                elif t in hr.NON_CUSTOMER:
                    current_customer = None
            elif child["ct"] == "Custom" and current_customer:
                rec = hr._parse_block(child, current_customer)
                site = rec["site"]
                has_vehicle = "○" if rec["vehicle_no"] else "×"
                for name, rect in worker_rects(child):
                    total_workers += 1
                    tc, bg = text_color(sampler, rect)
                    w(f"{current_customer[:14]:<16}{site[:20]:<22}"
                      f"{name[:10]:<12}{hexc(tc):<9}{hexc(bg):<9}"
                      f"{tuple(rect)}  車両{has_vehicle}")
                    if tc is not None:
                        color_members[cluster_key(tc)].append(
                            (name, f"{current_customer}:{site}", hexc(tc))
                        )

    w("")
    w("=" * 78)
    w(f"作業員 総数(のべ): {total_workers} 人")
    w("=" * 78)

    OUT.write_text("\n".join(lines), encoding="utf-8")

    # --------- 文字色クラスタの集計(外注/応援が色で分かれるかの判定材料)
    clines = []

    def cw(s=""):
        print(s)
        clines.append(str(s))

    cw("=" * 78)
    cw("検出された文字色クラスタ(人数の多い順)")
    cw("外注/応援が色で分かれているなら、ここに別クラスタとして出るはず。")
    cw("=" * 78)
    for key in sorted(color_members, key=lambda k: -len(color_members[k])):
        members = color_members[key]
        sample_hex = members[0][2]
        names = "、".join(n for n, _, _ in members[:8])
        cw("")
        cw(f"■ 代表色 {sample_hex}  ({len(members)} 人)")
        cw(f"   例: {names}")
    COLORS.write_text("\n".join(clines), encoding="utf-8")

    print()
    print(f"一覧:        {OUT}")
    print(f"色クラスタ:  {COLORS}")
    print("この2ファイルを開発者(Claude)に共有してください。")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.write_text("FATAL:\n" + traceback.format_exc(), encoding="utf-8")
        print("FATAL. workers_inspect.txt を確認してください。")
        raise

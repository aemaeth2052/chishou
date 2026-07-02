# -*- coding: utf-8 -*-
"""氏名セルの背景色の採取と、色→区分マップ。

番割では作業員の自社/他社の別が「氏名の背景色」で表示される。本モジュールは
その背景色を実機の画面から拾い(採色)、`worker_colors.json`(色→区分マップ)で
自社(home)/他営業所応援(other)/集計対象外(ignore) に対応づける役割を持つ。

採色の主役は「ウィンドウを一瞬前面に出して画面キャプチャ」方式。ユーザーが実際に
見ている色をそのまま読むため最も正確。氏名セルの色が取れない場合は PrintWindow
(隠れていても撮れるが、このアプリでは白セルの描画が再現されないことがある)に
フォールバックする。

判定は色(RGB)の一致ではなく「オレンジ度(G−B)」で行う。実機データ(2026-06)で、
自社セル=白/灰/桃(G−B < 20)・他社セル=オレンジ系(G−B > 38)と完全分離するため。

このモジュールは inspect_workers / utilization / GUI から共通で使う。
"""

import ctypes
from ctypes import wintypes
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
COLORS_PATH = HERE / "worker_colors.json"

# 区分の正規キー
KIND_HOME = "home"     # 自社(自営業所)
KIND_OTHER = "other"   # 他営業所応援(借りた人工)
KIND_IGNORE = "ignore"  # 集計対象外(他営業所の自前労務)
VALID_KINDS = (KIND_HOME, KIND_OTHER, KIND_IGNORE)

# 設定ファイルで使える区分の別名 → 正規キー
KIND_ALIASES = {
    "home": KIND_HOME, "自社": KIND_HOME, "自営業所": KIND_HOME,
    "other": KIND_OTHER, "他社": KIND_OTHER, "他営業所": KIND_OTHER, "応援": KIND_OTHER,
    "ignore": KIND_IGNORE, "対象外": KIND_IGNORE, "無視": KIND_IGNORE,
}

# オレンジ度(G−B)の許容差。登録色との差がこれ以内なら同じ色とみなす。
# 判定(classify)とスキャン時の色集約の両方に効く。実機では自社<20/他社>38。
DEFAULT_TOLERANCE = 25


# ----------------------------------------------------------------- 色ユーティリティ
def hexc(c):
    """(R,G,B) を '#RRGGBB' に。None は '-----'。"""
    return "#%02X%02X%02X" % (c[0], c[1], c[2]) if c else "-----"


def to_rgb(color):
    """'#RRGGBB' / (r,g,b) / [r,g,b] を (r,g,b) タプルに正規化。不正なら None。"""
    if color is None:
        return None
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        try:
            return (int(color[0]), int(color[1]), int(color[2]))
        except (TypeError, ValueError):
            return None
    s = str(color).strip().lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            return None
    return None


def warmth(color):
    """色の「オレンジ度」= G − B を返す。

    実機データ(2026-06)で判明: 番割の自社セルは白/灰/桃(=中立色、G≈B)、他社セルは
    オレンジ系(暖色のグラデで G>B)。採色域が小さく文字のにじみで白が桃/灰に散らばる
    ため RGB の一致では1色にまとまらないが、G−B で見ると 自社<20 / 他社>38 と完全に
    分離する。よって背景色の判定は RGB 距離ではなく、この「オレンジ度」で行う。
    """
    rgb = to_rgb(color)
    if rgb is None:
        return None
    return rgb[1] - rgb[2]


def cluster_key(c):
    """アンチエイリアスのゆらぎを丸めた色クラスタの代表値(各色24刻み)。"""
    return tuple((v // 24) * 24 for v in c)


# ------------------------------------------------------------- 矩形→背景色の推定
def _median_bg(rs, gs, bs):
    """チャンネルごとの中央値で背景色を返す。

    背景は領域の過半を占める平らな塗りで、文字(黒・赤など)や記号は少数派。
    最頻色(mode)だと白がアンチエイリアスで多数の淡色に割れて、にじみ色に負けてしまう
    (白セルがベージュ/灰に化ける)。中央値なら、背景が過半なら文字色の外れ値に
    引きずられず、白なら白・橙なら橙を安定して返す。
    """
    if not rs:
        return None
    rs = sorted(rs)
    gs = sorted(gs)
    bs = sorted(bs)
    m = len(rs) // 2
    return (rs[m], gs[m], bs[m])


def _bg_from_get(get, rect, step=1):
    """get(x,y)->(r,g,b)|None を使って、矩形内の背景色(中央値)を返す。"""
    l, t, r, b = rect
    if r - l < 2 or b - t < 2:
        return None
    rs, gs, bs = [], [], []
    yy = t + 1
    while yy < b - 1:
        xx = l + 1
        while xx < r - 1:
            c = get(xx, yy)
            if c is not None:
                rs.append(c[0]); gs.append(c[1]); bs.append(c[2])
            xx += step
        yy += step
    return _median_bg(rs, gs, bs)


# ------------------------------------------------------------------- 採色クラス
class _BaseSampler:
    """get(x,y)->(r,g,b)|None を持つ採色器の共通部分。"""

    mode = "none"
    step = 1

    def get(self, x, y):
        raise NotImplementedError

    def bg(self, rect):
        """氏名セル矩形の背景色を '#RRGGBB' で返す。取れなければ ''。"""
        c = _bg_from_get(self.get, rect, self.step)
        return hexc(c) if c is not None else ""


# ------- BITMAP 構造体(PrintWindow 用)
class _BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD * 3)]


_GDI_RESTYPES_DONE = {"v": False}


def _init_gdi_restypes(user32, gdi32):
    """GDI/User32 のハンドル戻り値を64bit幅に設定する(一度だけ)。

    既定の ctypes は戻り値を 32bit int とみなすため、Win64 ではハンドル(ポインタ)が
    切り詰められて PrintWindow/GetDIBits が無効ハンドルで失敗しうる。明示的に
    restype/argtypes を設定して取りこぼしを防ぐ。
    """
    if _GDI_RESTYPES_DONE["v"]:
        return
    user32.GetWindowDC.restype = wintypes.HDC
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.POINTER(_BMI), wintypes.UINT,
    ]
    _GDI_RESTYPES_DONE["v"] = True


class WindowSampler(_BaseSampler):
    """PrintWindow で1ウィンドウのビットマップを取り、画面座標で色を引く採色器。

    番割ウィンドウが最前面でなくても、他ウィンドウに一部隠れていても拾えるのが利点。
    取得に失敗(黒画像など)した場合は self.ok=False になるので、呼び出し側で
    スクリーン採色にフォールバックすること(make_sampler_for が面倒を見る)。
    """

    mode = "printwindow"

    def __init__(self, hwnd):
        self.ok = False
        self.left = self.top = 0
        self.w = self.h = 0
        self.buf = None
        try:
            self._capture(int(hwnd))
        except Exception:
            self.ok = False

    def _capture(self, hwnd):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        _init_gdi_restypes(user32, gdi32)  # 64bit でハンドルが切り詰められないように
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        self.left, self.top = rect.left, rect.top
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return
        hdc_win = user32.GetWindowDC(hwnd)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
        old = gdi32.SelectObject(hdc_mem, hbmp)
        # PW_RENDERFULLCONTENT(=2): WPF/DirectComposition でも中身を描かせる
        user32.PrintWindow(hwnd, hdc_mem, 2)

        bmi = _BMI()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0      # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)

        if not got:
            return
        self.w, self.h, self.buf = w, h, buf.raw
        self.ok = not self._looks_blank()

    def _looks_blank(self):
        """中身が真っ黒(=PrintWindow が描けていない)かを抜き取りで判定する。"""
        if not self.buf:
            return True
        n = self.w * self.h
        if n == 0:
            return True
        nonblack = 0
        # 全面を舐めると重いので等間隔に最大 ~2000 点だけ見る
        stepn = max(1, n // 2000)
        for i in range(0, n, stepn):
            o = i * 4
            if self.buf[o] > 8 or self.buf[o + 1] > 8 or self.buf[o + 2] > 8:
                nonblack += 1
                if nonblack > 5:
                    return False
        return True

    def get(self, x, y):
        if not self.buf:
            return None
        ix, iy = x - self.left, y - self.top
        if 0 <= ix < self.w and 0 <= iy < self.h:
            o = (iy * self.w + ix) * 4
            return (self.buf[o + 2], self.buf[o + 1], self.buf[o])  # BGRA→RGB
        return None


class ScreenSampler(_BaseSampler):
    """画面全体(仮想スクリーン)のスクリーンショットから色を引く採色器。

    Pillow があれば全画面を1回キャプチャ(高速)。無ければ GDI GetPixel(低速)。
    番割が最前面・全表示でないと、隠れた部分は背面ウィンドウの色を拾ってしまう点に
    注意(make_sampler_for が採色前にウィンドウを前面化する)。
    """

    def __init__(self):
        user32 = ctypes.windll.user32
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        self.vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        self.vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        try:
            from PIL import ImageGrab
            self.img = ImageGrab.grab(all_screens=True)
            self.px = self.img.load()
            self.W, self.H = self.img.size
            self.mode = "screen-pil"
            self.step = 1
        except Exception:
            self.gdi = ctypes.windll.gdi32
            self.hdc = user32.GetDC(0)
            self.mode = "screen-gdi"
            self.step = 2  # GDI は遅いので間引く

    def get(self, x, y):
        if self.mode == "screen-pil":
            ix, iy = x - self.vx, y - self.vy
            if 0 <= ix < self.W and 0 <= iy < self.H:
                c = self.px[ix, iy]
                return (c[0], c[1], c[2])
            return None
        # GetPixel は COLORREF(DWORD) を返すが ctypes 既定の c_int だと
        # CLR_INVALID(0xFFFFFFFF) が -1 になり比較をすり抜けて「白」に化けるので、
        # 32bit に丸めてから判定する。
        v = self.gdi.GetPixel(self.hdc, x, y) & 0xFFFFFFFF
        if v == 0xFFFFFFFF:  # CLR_INVALID (画面外など)
            return None
        return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)

    def bg(self, rect):
        """氏名セル矩形の背景色。PILキャプチャ時は crop でまとめて読む(高速)。

        採色域はセル全体×人数ぶんあるので、1画素ずつ px[x,y] を呼ぶと数百万回の
        Pythonループになる。crop→チャンネル分解→C実装のソートで中央値を取れば同じ
        結果を桁違いに速く出せる。GDIフォールバック時は従来の間引きループ。
        """
        if self.mode != "screen-pil":
            return super().bg(rect)
        l, t, r, b = rect
        if r - l < 2 or b - t < 2:
            return ""
        # 従来ループと同じく縁1pxを除いた内側を、画像座標に直してクリップ
        il, it = max(l - self.vx + 1, 0), max(t - self.vy + 1, 0)
        ir, ib = min(r - self.vx - 1, self.W), min(b - self.vy - 1, self.H)
        if ir - il < 1 or ib - it < 1:
            return ""
        box = self.img.crop((il, it, ir, ib))
        med = []
        for ch in box.split()[:3]:
            data = sorted(ch.getdata())
            med.append(data[len(data) // 2])
        return hexc(tuple(med))


def _set_dpi_aware():
    """採色の座標系(物理ピクセル)を UIA の矩形に合わせるため DPI 認識にする。

    プロセスの DPI 認識は最初のウィンドウ生成前しか変えられないため、本モジュールの
    import 時(末尾)に一度だけ呼ぶ。GUI は tk.Tk() より先に本モジュールを import する。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2 相当
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _sample_rects(root, limit=10):
    """番割ツリーから検証用の氏名セル矩形を数点集める。"""
    import hks_reader as hr
    out = []
    for _c, _s, _n, _b, rect, _sb, _hv in hr.iter_board_workers(root):
        if rect and rect[2] - rect[0] >= 2 and rect[3] - rect[1] >= 2:
            out.append(rect)
            if len(out) >= limit:
                break
    return out


def _sampler_hits(sampler, rects):
    """サンプル矩形のうち1つでも背景色が取れれば True(座標系が合っている)。"""
    if not rects:
        return True   # 検証材料が無ければ採用(従来挙動)
    return any(sampler.bg(r) for r in rects)


def _raise_window(win):
    """番割ウィンドウを前面に出す(画面キャプチャ採色を正確にするため)。"""
    hwnd = getattr(win, "handle", None)
    if not hwnd:
        return
    try:
        win.set_focus()   # pywinauto。前面化の各種トリック込み
    except Exception:
        pass
    try:
        user32 = ctypes.windll.user32
        user32.BringWindowToTop(int(hwnd))
        user32.SetForegroundWindow(int(hwnd))
    except Exception:
        pass
    time.sleep(0.18)      # 前面化後の再描画を待つ


def make_sampler_for(win, root):
    """win＋番割ツリーから最適な採色器を選ぶ。

    1) ウィンドウを前面に出して画面キャプチャで採色(最も正確。実際に表示されている
       色＝ユーザーが見ている白/橙/オレンジをそのまま読む)。
    2) 取れなければ PrintWindow(隠れていても撮れるが、このアプリでは白セル等の描画が
       再現されないことがある)。
    3) どちらも駄目なら 1) のキャプチャをそのまま返す(取れないセルは "" になり
       「採色できず」として要確認に出る。古いキャプチャの再利用はしない——前のボードを
       撮った画像で別のボードを採色すると、誤った色を黙って返してしまうため)。

    氏名セルを数点サンプルして実際に色が取れるかで採否を判定する。
    """
    rects = _sample_rects(root)

    # 1) 前面化 + 画面キャプチャ(最も正確)
    screen = None
    try:
        _raise_window(win)
        screen = ScreenSampler()         # 前面化後に撮り直す(キャッシュは使わない)
        if _sampler_hits(screen, rects):
            return screen
    except Exception:
        pass

    # 2) フォールバック: PrintWindow
    try:
        hwnd = getattr(win, "handle", None)
        if hwnd:
            ws = WindowSampler(int(hwnd))
            if ws.ok and _sampler_hits(ws, rects):
                return ws
    except Exception:
        pass

    # 3) 検証は通らなかったが、今撮った画面キャプチャを最後の手段として返す
    return screen if screen is not None else ScreenSampler()


def _iter_selected_boards(select=None, log=print):
    """開いている番割を (営業所, 日付, ツリー, 採色器) で順に返す共通ジェネレータ。

    scan_open_boards / dump_details / inspect_workers が同じ列挙・Pane探索・採色器
    選択を共有するための入口。select: None なら全番割。(営業所, 日付) のタプル集合で
    絞り込み。番割が1つも無ければ RuntimeError。
    """
    import hks_reader as hr
    metas = hr.enumerate_windows()
    if select is not None:
        wanted = {tuple(k) for k in select}
        metas = [m for m in metas if hr.board_key(m) in wanted]
    if not metas:
        raise RuntimeError("番割予定表ウィンドウが見つかりません。Hksで表示してください。")
    for m in metas:
        office = m.get("office", "") or "営業所不明"
        date = m.get("date", "") or "日付不明"
        root = hr.board_root(m["win"], log=log)
        if root is None:
            log(f"  {office}: カード領域(Pane)が見つからずスキップ")
            continue
        yield office, date, root, make_sampler_for(m["win"], root)


# ------------------------------------------------------------------- 色→区分マップ
class ColorMap:
    """背景色 → 区分(home/other/ignore) を「オレンジ度(G−B)」で判定する。

    自社=中立色(白/灰/桃, G≈B)・他社=オレンジ(G>B)。登録色のオレンジ度に最も近い
    区分を返すので、白が桃や灰に化けても(オレンジ度が低い限り)同じ区分になる。
    どの登録色からも許容差(tolerance)を超えて離れた色は None(=未割当)を返し、
    呼び出し側が「対象外+要確認」に落とせるようにする。1色しか登録していない状態で
    未知の色(例: 新しい区分のオレンジ)が黙って自社に化けるのを防ぐ安全弁。
    """

    def __init__(self, entries=None, tolerance=DEFAULT_TOLERANCE):
        # entries: [(rgb, kind, label)]
        self.entries = list(entries or [])
        self.tolerance = tolerance
        self._warm = [(rgb[1] - rgb[2], kind, label or "")
                      for rgb, kind, label in self.entries]

    def __bool__(self):
        return bool(self.entries)

    def _nearest(self, color):
        """(オレンジ度の差, 区分, ラベル) の最近傍。判定不能なら None。"""
        w = warmth(color)
        if w is None or not self._warm:
            return None
        return min(((abs(ew - w), kind, label) for ew, kind, label in self._warm),
                   key=lambda x: x[0])

    def classify(self, color):
        """色 → 'home'/'other'/'ignore'。許容差を超える色・未登録は None。"""
        n = self._nearest(color)
        if n is None or n[0] > self.tolerance:
            return None
        return n[1]

    def label_of(self, color):
        """色に最も近い登録色のラベルを返す(許容差外・未登録なら '')。"""
        n = self._nearest(color)
        if n is None or n[0] > self.tolerance:
            return ""
        return n[2]


def _merge_clusters(clusters, tol):
    """色クラスタを「オレンジ度(G−B)」が近いものへ集約する。

    白セルは桃/灰に散らばり RGB ではまとまらないが、オレンジ度(G−B)で見ると自社は
    すべて低オレンジ度・他社は高オレンジ度に分かれる。tol(=オレンジ度の許容差)以内の
    クラスタを同じ色とみなして1つにまとめる。代表色は人数が最多のもの。
    """
    out = []
    for c in sorted(clusters, key=lambda x: -x["count"]):
        cw = warmth(c["hex"])
        rep = None
        if cw is not None:
            for r in out:
                if abs(warmth(r["hex"]) - cw) <= tol:
                    rep = r
                    break
        if rep is None:
            out.append({"hex": c["hex"], "count": c["count"],
                        "badges": set(c["badges"]), "names": list(c["names"])})
        else:
            rep["count"] += c["count"]
            rep["badges"] |= set(c["badges"])
            for n in c["names"]:
                if len(rep["names"]) < 12:
                    rep["names"].append(n)
    return out


def scan_open_boards(select=None, tolerance=DEFAULT_TOLERANCE, log=print):
    """開いている番割を採色し、背景色クラスタの一覧を返す(GUI 色判定タブ用)。

    select: None なら開いている全番割。(営業所, 日付) のタプル集合で絞り込める。
    tolerance: オレンジ度(G−B)の差がこれ以内の色は同じ背景色として1つに集約する。

    Returns: [{"hex","count","badges":[...],"names":[...]}] を人数の多い順に。
    """
    import hks_reader as hr
    clusters = {}  # cluster_key -> dict
    for office, _date, root, sampler in _iter_selected_boards(select, log):
        n = miss = 0
        first_miss_rect = None
        for _cust, _site, name, badge, rect, _sb, _hv in hr.iter_board_workers(root):
            n += 1
            rgb = to_rgb(sampler.bg(rect))
            if rgb is None:
                miss += 1
                if first_miss_rect is None:
                    first_miss_rect = rect
                continue
            key = cluster_key(rgb)
            c = clusters.setdefault(key, {"hex": hexc(rgb), "count": 0,
                                          "badges": set(), "names": []})
            c["count"] += 1
            if badge:
                c["badges"].add(badge)
            if len(c["names"]) < 12:
                c["names"].append(name)
        log(f"  {office} (採色 {sampler.mode}): {n} 名 / 背景色取得不能 {miss}")
        if n and miss == n:
            # 全滅は座標系のズレ(DPI拡大率>100%でプロセスがDPI非対応)が主因。
            sm = getattr(sampler, "mode", "?")
            extra = ""
            if sm == "printwindow":
                extra = (f" 窓左上({sampler.left},{sampler.top}) "
                         f"ビットマップ{sampler.w}x{sampler.h}")
            log(f"  ⚠ {office}: 全{n}名の背景色が取れません(採色 {sm}{extra}"
                f" / 氏名矩形例 {first_miss_rect})。"
                "画面の拡大率が100%超の可能性。GUIを再起動すると改善することがあります"
                "(起動時にDPI対応を有効化)。改善しなければ番割を最前面・全表示で再試行を。")

    raw = len(clusters)
    merged = _merge_clusters(list(clusters.values()), tolerance)
    out = [{"hex": c["hex"], "count": c["count"],
            "badges": sorted(c["badges"]), "names": c["names"]}
           for c in merged]
    out.sort(key=lambda x: -x["count"])
    log(f"色スキャン完了: {len(out)} 色(生 {raw} 色を許容差 {tolerance} で集約)")
    return out


def dump_details(path, select=None, log=print):
    """開いている番割を採色し、作業員1人ごとの (氏名・バッジ・背景色・採色域) を
    ファイルに書き出す(診断用)。返り値は人数。

    GUI の『色判定』タブの『詳細を書き出す』から呼ぶ。色が想定どおり読めない人の
    採色域が隣のセルにはみ出していないか等を、この出力で確認できる。
    出力はタブ区切り。utilization.assignments_from_inspect はこの形式も読める。
    """
    import hks_reader as hr
    lines = ["# 作業員ごとの背景色と採色域(診断用)",
             "# 氏名\tバッジ\t背景色\t採色域(L,T,R,B)\t顧客\t現場"]
    total = 0
    for office, date, root, sampler in _iter_selected_boards(select, log):
        lines.append(f"\n## {office}  {date}  (採色 {sampler.mode})")
        n = 0
        for cust, site, name, badge, rect, _sb, _hv in hr.iter_board_workers(root):
            bg = sampler.bg(rect)
            rstr = ",".join(str(int(v)) for v in rect)
            lines.append(f"{name}\t{badge}\t{bg or '-----'}\t{rstr}\t{cust}\t{site}")
            n += 1
        total += n
        log(f"  {office} (採色 {sampler.mode}): {n} 名")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    log(f"詳細を書き出しました: {path} ({total} 名)")
    return total


def normalize_kind(v):
    """設定値(home/自社/other/... )を正規キーに。不正なら None。"""
    return KIND_ALIASES.get(str(v).strip().lower()) or KIND_ALIASES.get(str(v).strip())


def load_color_map(path=COLORS_PATH):
    """worker_colors.json を読んで ColorMap を返す。無ければ空マップ。"""
    p = Path(path)
    if not p.exists():
        return ColorMap()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ColorMap()
    tol = raw.get("tolerance", DEFAULT_TOLERANCE)
    try:
        tol = int(tol)
    except (TypeError, ValueError):
        tol = DEFAULT_TOLERANCE
    entries = []
    for c in raw.get("colors", []):
        if not isinstance(c, dict):
            continue
        rgb = to_rgb(c.get("hex") or c.get("rgb"))
        kind = normalize_kind(c.get("kind", ""))
        if rgb is None or kind is None:
            continue
        entries.append((rgb, kind, str(c.get("label", "")).strip()))
    return ColorMap(entries, tol)


def save_color_map(colors, tolerance=DEFAULT_TOLERANCE, path=COLORS_PATH):
    """色→区分の割り当てを worker_colors.json に保存する。

    colors: [{"hex": "#RRGGBB", "kind": "home/other/ignore", "label": "..."}]
    既存ファイルの先頭が _ のコメントキーは引き継ぐ。
    """
    p = Path(path)
    base = {}
    if p.exists():
        try:
            base = {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items()
                    if k.startswith("_")}
        except Exception:
            base = {}
    out = []
    for c in colors:
        rgb = to_rgb(c.get("hex") or c.get("rgb"))
        kind = normalize_kind(c.get("kind", ""))
        if rgb is None or kind is None:
            continue
        out.append({"hex": hexc(rgb), "kind": kind, "label": str(c.get("label", ""))})
    base["tolerance"] = int(tolerance)
    base["colors"] = out
    p.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")


# このモジュールを import した時点で DPI 認識にしておく。GUI(utilization_app)は先頭で
# worker_color を import するため、tk.Tk() でウィンドウを作る前にプロセスが DPI 対応になり、
# UIA の矩形(物理px)と GetWindowRect/PrintWindow(物理px)の座標系が一致する。
_set_dpi_aware()

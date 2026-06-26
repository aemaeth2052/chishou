# -*- coding: utf-8 -*-
"""氏名セルの背景色の採取と、色→区分マップ。

番割では作業員の自社/他社の別が「氏名の背景色」で表示される。本モジュールは
その背景色を実機の画面から拾い(採色)、`worker_colors.json`(色→区分マップ)で
自社(home)/他営業所応援(other)/集計対象外(ignore) に対応づける役割を持つ。

採色の主役は **PrintWindow** 方式。番割ウィンドウのビットマップを直接取得するため、
ウィンドウが最前面でなくても、別ウィンドウに一部隠れていても色を拾える。
PrintWindow が黒画像しか返さない環境(ハードウェア合成のWPF等)では、従来どおり
画面全体のスクリーンショット(Pillow / GDI)に自動でフォールバックする。

このモジュールは inspect_workers / utilization / GUI から共通で使う。
"""

import ctypes
from collections import defaultdict
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

DEFAULT_TOLERANCE = 40  # 背景色がこの距離(R+G+B差の合計)以内なら同じ色とみなす


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


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def cluster_key(c):
    """アンチエイリアスのゆらぎを丸めた色クラスタの代表値(各色24刻み)。"""
    return tuple((v // 24) * 24 for v in c)


# ------------------------------------------------------------- 矩形→背景色の推定
def _bg_from_get(get, rect, step=1, ignore_dark=True):
    """get(x,y)->(r,g,b)|None を使って、矩形内の背景色を「チャンネルごとの中央値」で返す。

    背景は領域の過半を占める平らな塗りで、文字(黒・赤など)やバッジ・記号は少数派。
    最頻色(mode)だと白がアンチエイリアスで多数の淡色に割れて、にじみ色に負けてしまう
    (白セルがベージュ/灰に化ける)。中央値なら、背景が過半なら文字色や badge 色の
    外れ値に引きずられず、白なら白・橙なら橙を安定して返す。
    """
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
    if not rs:
        return None
    rs.sort(); gs.sort(); bs.sort()
    m = len(rs) // 2
    return (rs[m], gs[m], bs[m])


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
    スクリーン採色にフォールバックすること(make_window_sampler が面倒を見る)。
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
    番割が最前面・全表示でないと、隠れた部分は背面ウィンドウの色を拾ってしまう点に注意。
    PrintWindow が使えない環境でのフォールバック。
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
        v = self.gdi.GetPixel(self.hdc, x, y)
        if v == 0xFFFFFFFF:  # CLR_INVALID
            return None
        return (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)


def _set_dpi_aware():
    """採色の座標系(物理ピクセル)を UIA の矩形に合わせるため DPI 認識にする。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2 相当
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_SHARED_SCREEN = {"s": None}


def _shared_screen():
    if _SHARED_SCREEN["s"] is None:
        _SHARED_SCREEN["s"] = ScreenSampler()
    return _SHARED_SCREEN["s"]


def reset_screen_cache():
    """スクリーンキャプチャのキャッシュを破棄(番割を動かした後に呼ぶ)。"""
    _SHARED_SCREEN["s"] = None


def make_window_sampler(win):
    """pywinauto のウィンドウから採色器を作る(矩形検証なし・互換用)。

    まず PrintWindow を試し、黒画像など失敗したら共有のスクリーン採色に切り替える。
    返り値は bg(rect)->'#RRGGBB' を持つ採色器。失敗時も必ず何か返す(最低限スクリーン)。
    """
    _set_dpi_aware()
    try:
        hwnd = getattr(win, "handle", None)
        if hwnd:
            ws = WindowSampler(hwnd)
            if ws.ok:
                return ws
    except Exception:
        pass
    return _shared_screen()


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
    3) どちらも駄目なら共有スクリーン採色を返す(集計は色なしで続行できる)。

    氏名セルを数点サンプルして実際に色が取れるかで採否を判定する。
    """
    _set_dpi_aware()
    rects = _sample_rects(root)

    # 1) 前面化 + 画面キャプチャ(最も正確)
    try:
        _raise_window(win)
        s = ScreenSampler()              # 前面化後に撮り直す(キャッシュは使わない)
        if _sampler_hits(s, rects):
            return s
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

    return _shared_screen()


# ------------------------------------------------------------------- 色→区分マップ
class ColorMap:
    """背景色 → 区分(home/other/ignore) を最近傍で判定する。

    番割の背景色は基本フラットな単色なので、登録色との距離が tolerance 以内なら
    その区分とみなす。未登録(距離超過)の色は None を返し、呼び出し側が従来の
    バッジ/名簿判定にフォールバックできるようにする。
    """

    def __init__(self, entries=None, tolerance=DEFAULT_TOLERANCE):
        # entries: [(rgb, kind, label)]
        self.entries = list(entries or [])
        self.tolerance = tolerance

    def __bool__(self):
        return bool(self.entries)

    def classify(self, color):
        """色(hex/rgb) → 'home'/'other'/'ignore'、未登録なら None。"""
        rgb = to_rgb(color)
        if rgb is None or not self.entries:
            return None
        best_kind, best_d = None, 10 ** 9
        for crgb, kind, _label in self.entries:
            d = _dist(rgb, crgb)
            if d < best_d:
                best_d, best_kind = d, kind
        return best_kind if best_d <= self.tolerance else None

    def label_of(self, color):
        """色に最も近い登録色のラベルを返す(なければ '')。"""
        rgb = to_rgb(color)
        if rgb is None or not self.entries:
            return ""
        best, best_d = "", 10 ** 9
        for crgb, _kind, label in self.entries:
            d = _dist(rgb, crgb)
            if d < best_d:
                best_d, best = d, label or ""
        return best if best_d <= self.tolerance else ""


def _merge_clusters(clusters, tol):
    """近い色クラスタを最頻(最多人数)色へ集約する。

    番割の同じ背景色でも、描画の濃淡で微妙に違う色が複数出る。tol(=許容差)以内の
    クラスタは同じ色とみなして1つにまとめる。代表色は人数が最多のものを採る。
    """
    out = []
    for c in sorted(clusters, key=lambda x: -x["count"]):
        crgb = to_rgb(c["hex"])
        rep = None
        if crgb is not None:
            for r in out:
                if _dist(to_rgb(r["hex"]), crgb) <= tol:
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
    tolerance: この距離以内の色は同じ背景色として1つに集約する(許容差)。

    Returns: [{"hex","count","badges":[...],"names":[...]}] を人数の多い順に。
    """
    import hks_reader as hr
    _set_dpi_aware()
    reset_screen_cache()
    metas = hr.enumerate_windows()
    if select is not None:
        wanted = {tuple(k) for k in select}
        metas = [m for m in metas if hr.board_key(m) in wanted]
    if not metas:
        raise RuntimeError("番割予定表ウィンドウが見つかりません。Hksで表示してください。")

    clusters = {}  # cluster_key -> dict
    for m in metas:
        win = m["win"]
        office = m.get("office", "") or "営業所不明"
        pane = None
        for c in win.children():
            try:
                if c.element_info.control_type == "Pane":
                    pane = c
                    break
            except Exception:
                continue
        if pane is None:
            log(f"  {office}: カード領域が見つからずスキップ")
            continue
        try:
            root = hr._snap_cached(pane)
        except Exception:
            root = hr._snap(pane)
        sampler = make_sampler_for(win, root)
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
    """
    import hks_reader as hr
    _set_dpi_aware()
    reset_screen_cache()
    metas = hr.enumerate_windows()
    if select is not None:
        wanted = {tuple(k) for k in select}
        metas = [m for m in metas if hr.board_key(m) in wanted]
    if not metas:
        raise RuntimeError("番割予定表ウィンドウが見つかりません。Hksで表示してください。")

    lines = ["# 作業員ごとの背景色と採色域(診断用)",
             "# 氏名\tバッジ\t背景色\t採色域(L,T,R,B)\t顧客\t現場"]
    total = 0
    for m in metas:
        win = m["win"]
        office = m.get("office", "") or "営業所不明"
        date = m.get("date", "") or "日付不明"
        pane = None
        for c in win.children():
            try:
                if c.element_info.control_type == "Pane":
                    pane = c
                    break
            except Exception:
                continue
        if pane is None:
            continue
        try:
            root = hr._snap_cached(pane)
        except Exception:
            root = hr._snap(pane)
        sampler = make_sampler_for(win, root)
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

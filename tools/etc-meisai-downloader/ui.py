# -*- coding: utf-8 -*-
"""画面の見た目（番割集計と同じデザイン）。

Windows 11 風のテーマ（sv-ttk）と日本語の読みやすいフォント、白いカードの区画、
カレンダー（日曜始まり・土日の色分け）を用意する。sv-ttk が無い環境では標準の clam テーマに色を付けて代わりにする。
"""

from __future__ import annotations

import calendar
import datetime
import sys
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

# 色（Windows 11 の配色に合わせる）
ACCENT = "#005fb8"
HEADER_BG = "#0b3a66"
HEADER_SUB = "#b8d3ee"
INK = "#1c1c1c"
MUTED = "#6e6e6e"
CARD = "#ffffff"
SUNDAY, SATURDAY, OTHER_MONTH = "#c42b1c", "#005fb8", "#b4b4b4"
OK, NG, WARN = "#0f7b0f", "#c42b1c", "#9d5d00"
CHECKED_ROW = "#e6f3fb"  # 一覧でチェックした行の色

UI_FONTS = ("Yu Gothic UI", "Meiryo UI", "Meiryo", "MS UI Gothic")
MONO_FONTS = ("BIZ UDGothic", "BIZ UDゴシック", "MS Gothic", "ＭＳ ゴシック")
WEEKDAYS = ("日", "月", "火", "水", "木", "金", "土")  # 日曜始まり


def enable_dpi_awareness() -> None:
    """Windows の表示倍率（125%・150% など）で、画面が引き伸ばされてぼやけないようにする。
    Tk の窓を作る前に呼ぶ。番割の取込で読み込む pywinauto と同じ設定（モニターごと）にそろえる
    （そろえないと、取込を押した時点で表示の大きさが変わってしまう）。"""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def weekday_name(d: datetime.date) -> str:
    return "月火水木金土日"[d.weekday()]


def japanese_date(d: datetime.date) -> str:
    return f"{d.year}年{d.month}月{d.day}日（{weekday_name(d)}）"


class Theme:
    """Windows 11 風のテーマ（sv-ttk）を当て、日本語の読みやすいフォントにそろえる。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        # 表示倍率（100% なら 1.0、150% なら 1.5）。ピクセルで決めている大きさに掛ける
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        families = set(tkfont.families(root))
        self.ui = next((f for f in UI_FONTS if f in families), None)
        self.mono = next((f for f in MONO_FONTS if f in families), "TkFixedFont")
        self.modern = self._apply_sun_valley()
        if not self.modern:
            self._apply_fallback()
        if self.ui:
            for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkCaptionFont",
                         "SunValleyCaptionFont", "SunValleyBodyFont", "SunValleyBodyStrongFont",
                         "SunValleyBodyLargeFont", "SunValleySubtitleFont", "SunValleyTitleFont"):
                try:
                    tkfont.nametofont(name, root).configure(family=self.ui)
                except tk.TclError:
                    pass
        # sv-ttk の文字（ボタン・タブ・一覧の見出しなど）はピクセルで決まっているので、表示倍率に合わせて大きくする
        for name in ("SunValleyCaptionFont", "SunValleyBodyFont", "SunValleyBodyStrongFont", "SunValleyBodyLargeFont",
                     "SunValleySubtitleFont", "SunValleyTitleFont", "SunValleyTitleLargeFont", "SunValleyDisplayFont"):
            try:
                f = tkfont.nametofont(name, root)
            except tk.TclError:
                continue
            if f.cget("size") < 0:
                f.configure(size=-self.px(-f.cget("size")))
        style = ttk.Style(root)
        style.configure("Big.Accent.TButton", font=self.font(12, "bold"), padding=(20, 6))
        style.configure("Note.TLabel", foreground=MUTED, font=self.font(9))
        style.configure("Section.TLabel", font=self.font(11, "bold"))
        style.configure("Page.TLabel", font=self.font(14, "bold"))
        style.configure("Ok.TLabel", foreground=OK, font=self.font(10, "bold"))
        style.configure("Ng.TLabel", foreground=NG, font=self.font(10, "bold"))
        style.configure("Required.TLabel", foreground=NG)
        style.configure("Nav.Toolbutton", anchor="w", padding=(14, 8))
        style.map("Nav.Toolbutton", foreground=[("selected", ACCENT)], font=[("selected", self.font(10, "bold"))])
        style.configure("Treeview", rowheight=self.px(26))
        style.configure("Date.TButton", font=self.font(11), padding=(10, 3))  # 日付のボタン（曜日つき）
        style.configure("Sun.Date.TButton", foreground=SUNDAY)
        style.configure("Sat.Date.TButton", foreground=SATURDAY)

    def px(self, n: int) -> int:
        """100% のときのピクセル数を、今の表示倍率でのピクセル数にする"""
        return int(round(n * self.scale))

    def font(self, size: int, weight: str = "normal") -> tuple:
        return (self.ui or "TkDefaultFont", size, weight)

    def _apply_sun_valley(self) -> bool:
        try:
            import sv_ttk
            sv_ttk.set_theme("light", self.root)
            return True
        except Exception:
            return False

    def _apply_fallback(self) -> None:
        """sv-ttk が無いときの代わり（標準の clam テーマに色を付ける）。"""
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.configure(bg="#fafafa")
        style.configure(".", background="#fafafa")
        style.configure("Card.TFrame", background="#fafafa", relief="solid", borderwidth=1)
        style.configure("Accent.TButton", background=ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", "#1a6fc4"), ("disabled", "#9cbbe0")])
        style.layout("Switch.TCheckbutton", style.layout("TCheckbutton"))


class Card(ttk.Frame):
    """白い角丸の区画。見出しをつけられる。中の部品は ttk.Frame（Card を重ねると枠が二重になる）。"""

    def __init__(self, master: tk.Misc, title: str = "", padding: int = 10) -> None:
        super().__init__(master, style="Card.TFrame", padding=padding)
        if title:
            ttk.Label(self, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 4))


def header_band(root: tk.Misc, theme: Theme, title: str, subtitle: str) -> tk.Frame:
    """画面上の濃紺の帯（題名・説明・今日の日付）。"""
    head = tk.Frame(root, bg=HEADER_BG, padx=18, pady=8)
    left = tk.Frame(head, bg=HEADER_BG)
    left.pack(side="left")
    tk.Label(left, text=title, bg=HEADER_BG, fg="white", font=theme.font(16, "bold")).pack(anchor="w")
    tk.Label(left, text=subtitle, bg=HEADER_BG, fg=HEADER_SUB, font=theme.font(9)).pack(anchor="w")
    tk.Label(head, text="今日 " + japanese_date(datetime.date.today()), bg=HEADER_BG, fg="white",
             font=theme.font(11)).pack(side="right", anchor="e")
    return head


def calendar_icon(theme: Theme, color: str = INK, accent: str = ACCENT) -> tk.PhotoImage:
    """カレンダーのマーク（小さな画像）。表示倍率に合わせた大きさで描く。使う側で参照を持っておくこと。"""
    n = theme.px(18)
    t = max(1, round(theme.scale))  # 線の太さ
    img = tk.PhotoImage(master=theme.root, width=n, height=n)
    top, left, right, bottom = t * 3, t, n - t, n - t
    img.put(accent, to=(left, top, right, top + t * 4))  # 上の帯（月の欄）
    img.put(color, to=(left, top, left + t, bottom))  # 左
    img.put(color, to=(right - t, top, right, bottom))  # 右
    img.put(color, to=(left, bottom - t, right, bottom))  # 下
    for x in (left + (right - left) // 4, right - (right - left) // 4 - t):  # 留め具
        img.put(color, to=(x - t, top - t * 2, x + t, top + t))
    cell = max(2, t * 2)
    inner_top = top + t * 6
    for row in range(3):  # 日付のます
        for col in range(3):
            x = left + t * 2 + col * ((right - left - t * 4 - cell) // 2)
            y = inner_top + row * ((bottom - t * 2 - inner_top - cell) // 2)
            img.put(accent if (row, col) == (1, 1) else color, to=(x, y, x + cell, y + cell))
    return img


def fit_window(win: tk.Tk, theme: Theme, width: int, height: int,
               minimum: tuple[int, int] | None = None) -> tuple[int, int]:
    """表示倍率に合わせた大きさ（100% のときの width×height）で、画面の真ん中に出す。
    画面に入りきらないときは、画面の大きさ（タスクバーと題名の帯の分を空ける）まで小さくする。
    実際の大きさ（幅, 高さ）を返す。"""
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    max_w, max_h = sw - theme.px(16), sh - theme.px(80)
    w, h = min(theme.px(width), max_w), min(theme.px(height), max_h)
    if minimum:
        win.minsize(min(theme.px(minimum[0]), w), min(theme.px(minimum[1]), h))
    win.geometry(f"{w}x{h}+{max((sw - w) // 2, 0)}+{max((max_h - h) // 2, 0)}")
    return w, h


def center_on(win: tk.Toplevel, parent: tk.Misc, top: int | None = None) -> None:
    """画面を親ウィンドウの中央（top を渡すと、親の上端から top の位置）に出す。"""
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (top if top is not None else (parent.winfo_height() - win.winfo_reqheight()) // 3)
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class CalendarPopup(tk.Toplevel):
    """日付をカレンダーから選ぶ小さな画面。日をクリックすると on_pick(日付) を呼んで閉じる。"""

    def __init__(self, master: tk.Misc, anchor: tk.Widget, initial: datetime.date, on_pick, theme: Theme,
                 earliest: datetime.date | None = None) -> None:
        super().__init__(master)
        self.withdraw()
        self.title("日付を選ぶ")
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.configure(bg=CARD)
        self.theme = theme
        self.on_pick = on_pick
        self.selected = initial
        self.earliest = earliest  # これより前の日は薄く表示する（ETC利用照会サービスは過去62日まで）
        self.year, self.month = initial.year, initial.month

        head = tk.Frame(self, bg=CARD, padx=12, pady=10)
        head.pack(fill="x")
        ttk.Button(head, text="◀", width=3, command=lambda: self._move(-1)).pack(side="left")
        self.caption = tk.Label(head, bg=CARD, fg=INK, font=theme.font(13, "bold"))
        self.caption.pack(side="left", expand=True, fill="x")
        ttk.Button(head, text="▶", width=3, command=lambda: self._move(1)).pack(side="right")

        self.days = tk.Frame(self, bg=CARD, padx=12)
        self.days.pack()

        foot = tk.Frame(self, bg=CARD, padx=12, pady=10)
        foot.pack(fill="x")
        ttk.Button(foot, text="今日", command=lambda: self._pick(datetime.date.today())).pack(side="left")
        ttk.Button(foot, text="閉じる", command=self.destroy).pack(side="right")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Prior>", lambda _e: self._move(-1))  # PageUp: 前の月
        self.bind("<Next>", lambda _e: self._move(1))  # PageDown: 次の月
        self._draw()
        self.update_idletasks()
        self.geometry(f"+{anchor.winfo_rootx()}+{anchor.winfo_rooty() + anchor.winfo_height() + 4}")
        self.deiconify()
        self.grab_set()
        self.focus_set()

    def _move(self, delta: int) -> None:
        m = self.month - 1 + delta
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self._draw()

    def _draw(self) -> None:
        for w in self.days.winfo_children():
            w.destroy()
        self.caption.config(text=f"{self.year}年 {self.month}月")
        for col, name in enumerate(WEEKDAYS):
            fg = SUNDAY if col == 0 else SATURDAY if col == 6 else MUTED
            tk.Label(self.days, text=name, fg=fg, bg=CARD, width=4, font=self.theme.font(9)).grid(
                row=0, column=col, pady=(0, 4))
        today = datetime.date.today()
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(self.year, self.month)  # 日曜始まり
        for row, week in enumerate(weeks, start=1):
            for col, d in enumerate(week):
                outside = d.month != self.month or (self.earliest is not None and d < self.earliest) or d > today
                fg = OTHER_MONTH if outside else SUNDAY if col == 0 else SATURDAY if col == 6 else INK
                bg, weight = CARD, "normal"
                if d == self.selected:
                    fg, bg, weight = "white", ACCENT, "bold"
                elif d == today:
                    bg, weight = "#dbeafb", "bold"
                cell = tk.Label(self.days, text=str(d.day), width=4, height=1, fg=fg, bg=bg, cursor="hand2",
                                font=self.theme.font(11, weight), pady=5)
                cell.grid(row=row, column=col, padx=1, pady=1)
                cell.bind("<Button-1>", lambda _e, d=d: self._pick(d))
                if d != self.selected:  # マウスを乗せると薄く色をつける
                    cell.bind("<Enter>", lambda _e, c=cell: c.config(bg="#e8f1fb"))
                    cell.bind("<Leave>", lambda _e, c=cell, b=bg: c.config(bg=b))

    def _pick(self, d: datetime.date) -> None:
        self.on_pick(d)
        self.destroy()

# -*- coding: utf-8 -*-
"""ETC利用明細ダウンローダー GUI

画面のデザインは番割集計と同じ（Windows 11 風のテーマ・濃紺の帯・白いカード。ui.py）。
タブ構成:
  ダウンロード: 検索期間 / 番割の取込 / 対象の車両 / 検索開始 / 進行状況
  設定        : 左の一覧で ログイン / 保存先 / PDFへの書き込み / 車両の登録 を切り替える
"""

import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# paths を最優先でimportして PLAYWRIGHT_BROWSERS_PATH を設定
# (この前に playwright が読まれると環境変数が効かなくなる)
from paths import config_path, migrate_old_data, user_data_dir  # noqa: I001

import browser_setup
import downloader
import ui
from ui import ACCENT, CHECKED_ROW, INK, MUTED, NG, OK, WARN

TITLE = "ETC利用明細ダウンローダー"
SUBTITLE = "ETC利用照会サービスから、車両ごとの利用明細PDFをまとめてダウンロードします"
MAX_DAYS = 62  # ETC利用照会サービスで照会できる日数

BASE_DIR = Path(__file__).resolve().parent
migrate_old_data(BASE_DIR)
CONFIG_PATH = config_path()

# Windows: タスクバーが pythonw.exe ではなく本アプリのアイコンでグルーピングするよう、
# ウインドウ生成前に独自の AppUserModelID を設定する (これが無いとアイコンが反映されない)。
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "etc.meisai.downloader")
    except Exception:
        pass


def resource_path(rel: str) -> Path:
    """同梱リソース(アイコン等)の絶対パスを返す。
    PyInstaller の onedir 配布では sys._MEIPASS 配下に展開される。
    """
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) if base else BASE_DIR) / rel


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def open_folder(path):
    """OSのファイラーで指定フォルダを開く"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(p))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


def _btn(parent, text, command, style="default", **kw):
    """ボタン。primary / success は目立つ青（Accent）、「〜.TButton」はそのスタイル、それ以外はふつうのボタン。"""
    if style in ("primary", "success"):
        kw.setdefault("style", "Accent.TButton")
    elif style.endswith(".TButton"):
        kw.setdefault("style", style)
    return ttk.Button(parent, text=text, command=command, **kw)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.geometry("1000x900")
        self.minsize(900, 800)
        self.theme = ui.Theme(self)
        self.log_queue = queue.Queue()
        self.running = False
        self._hks_importing = False
        self._started_at = 0.0
        self._progress_text = ""
        self._set_app_icon()

        cfg = load_config()
        today = datetime.date.today()
        first = today.replace(day=1)

        # 旧 "name" は "dept" に移行。
        # 顧客・現場・運転手は番割由来 (日替わり) なので起動時は読み込まない。
        self.vehicles = []
        for v in cfg.get("vehicles", []):
            self.vehicles.append({
                "enabled": v.get("enabled", True),
                "dept": v.get("dept", v.get("name", "")),
                "number": v.get("number", ""),
            })

        # 状態保持用 (タブ間で共有する変数)
        self.var_id = tk.StringVar(value=cfg.get("login_id", ""))
        self.var_pw = tk.StringVar(value=cfg.get("password", ""))
        self.var_save_pw = tk.BooleanVar(value=bool(cfg.get("password")))
        self.var_dir = tk.StringVar(
            value=cfg.get("save_dir", str(Path.home() / "Documents" / "ETC明細"))
        )
        self.var_show = tk.BooleanVar(value=not cfg.get("headless", False))
        self.var_mode = tk.StringVar(value=cfg.get("mode", "list"))
        # デフォルトの検索期間は「昨日」
        yesterday = today - datetime.timedelta(days=1)
        self.var_from = tk.StringVar(value=yesterday.strftime("%Y/%m/%d"))
        self.var_to = tk.StringVar(value=yesterday.strftime("%Y/%m/%d"))
        self._sort_col = cfg.get("sort_col", "dept")
        self._sort_desc = bool(cfg.get("sort_desc", False))
        # PDF書き込み設定 (項目ごとにON/OFF)
        self.var_stamp_customer = tk.BooleanVar(value=cfg.get("stamp_customer", True))
        self.var_stamp_site = tk.BooleanVar(value=cfg.get("stamp_site", True))
        self.var_stamp_driver = tk.BooleanVar(value=cfg.get("stamp_driver", True))
        self.var_stamp_size = tk.IntVar(value=int(cfg.get("stamp_font_size", 14)))

        self.var_dup = tk.StringVar(value=cfg.get("dup_mode", "overwrite"))

        single = cfg.get("single", {})
        self.var_single_dept = tk.StringVar(value=single.get("dept", ""))
        self.var_single_num = tk.StringVar(value=single.get("number", ""))

        self.var_reg_dept = tk.StringVar()
        self.var_reg_num = tk.StringVar()

        # Hks番割は日替わりのため、起動時は常に未取込から始める
        # (前回の取込結果は引き継がない)
        self.hks_records = []
        self.hks_imported_at = ""
        # 更新なしの番割を読み直さないための取込キャッシュ。
        # {(営業所, 日付, 更新HH:MM): [records]}。日替わりのため永続化はしない。
        self._hks_cache = {}
        self.var_hks_status = tk.StringVar()
        self._update_hks_status()

        ui.header_band(self, self.theme, TITLE, SUBTITLE).pack(fill="x")
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(12, 16))
        self.tab_main = ttk.Frame(self.nb, padding=14)
        self.tab_settings = ttk.Frame(self.nb, padding=14)
        self.nb.add(self.tab_main, text="   ダウンロード   ")
        self.nb.add(self.tab_settings, text="   設定   ")

        self._build_main_tab(self.tab_main)
        self._build_settings_tab(self.tab_settings)

        self._refresh_mode()
        self._refresh_list()
        self.after(100, self.poll_log)
        self._chromium_ready = False
        self._ensure_browser()

    def _set_app_icon(self):
        """ウインドウ/タスクバーのアイコンを設定する。
        assets/icon.png があれば全プラットフォームで iconphoto に使う。
        Windows では assets/icon.ico があればタイトルバー用に併用する。
        画像が無ければ何もしない (既定アイコンのまま)。
        失敗理由はログに残す (原因切り分け用)。
        """
        # 探索パス: ソース実行・PyInstaller(_MEIPASS)・exe隣 のいずれでも拾えるように
        png_candidates = [
            resource_path("assets/icon.png"),
            BASE_DIR / "assets" / "icon.png",
            Path(sys.executable).resolve().parent / "assets" / "icon.png",
        ]
        png = next((p for p in png_candidates if p.exists()), None)
        if png is None:
            self.log("アイコン: assets/icon.png が見つかりませんでした")
        else:
            # 元画像(1024px等)が大きすぎるとタイトルバー(16px)・タスクバー(32px)に
            # 描画されないため、用途別の小サイズを作って iconphoto に全部渡す。
            imgs = []
            try:
                from PIL import Image, ImageTk
                resample = getattr(Image, "LANCZOS", None) or Image.Resampling.LANCZOS
                im = Image.open(png).convert("RGBA")
                for s in (256, 64, 48, 32, 16):
                    imgs.append(ImageTk.PhotoImage(im.resize((s, s), resample)))
            except ImportError:
                self.log("  (Pillow未導入。setup.bat を再実行してください)")
            except Exception as e:
                self.log(f"アイコン画像の生成に失敗: {e}")
            if not imgs:
                # フォールバック: Tk標準で1枚だけ
                try:
                    imgs = [tk.PhotoImage(file=str(png))]
                except Exception as e:
                    self.log(f"アイコン読み込み失敗(Tk標準): {e}")
            if imgs:
                self._icon_imgs = imgs  # GC防止に参照保持
                try:
                    self.iconphoto(True, *imgs)
                except Exception as e:
                    self.log(f"iconphoto 失敗: {e}")
            else:
                self.log("アイコンを設定できませんでした。PNG形式・サイズを確認してください")
        # Windows ではタイトルバー/タスクバーに確実に出すため .ico を iconbitmap で適用する。
        # (iconphoto の PNG は Windows のタイトルバー・タスクバーに反映されないことが多い)
        if sys.platform == "win32" and png is not None:
            ico = self._ensure_windows_ico(png)
            if ico:
                try:
                    self.iconbitmap(default=str(ico))
                except Exception as e:
                    self.log(f"アイコン(.ico)設定失敗: {e}")
                # Tkのiconbitmapが効かない環境向けに、Win32 APIで直接も適用する。
                # ウインドウ生成・表示のタイミング差に備え、複数回リトライする。
                for _delay in (150, 600, 1500, 3000):
                    self.after(_delay, lambda i=ico: self._apply_win_icon_native(i))

    def _apply_win_icon_native(self, ico_path):
        """Win32 の WM_SETICON / クラスアイコンで直接アイコンを設定する。

        Tk の iconphoto/iconbitmap は、環境によっては winfo_id() が
        タイトルバーを持つ実窓と別の内部窓を指すため効かないことがある。
        そこで本GUIスレッドの全ウインドウを列挙し、タイトルを持つ実窓へ直接適用する。
        実窓は生成が少し遅れ、かつ Tk が直後に自前アイコンで上書きすることがあるため、
        複数回のリトライで当て続ける (ログは初回成功時の1回だけ)。
        """
        try:
            import ctypes
            from ctypes import wintypes
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            WM_SETICON = 0x0080
            ICON_SMALL, ICON_BIG = 0, 1
            GCLP_HICON, GCLP_HICONSM = -14, -34
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            # 64bitでハンドル/ポインタが切り詰められないよう型を明示する
            u.LoadImageW.restype = wintypes.HANDLE
            u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                     wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                     wintypes.UINT]
            u.SendMessageW.restype = ctypes.c_void_p
            u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       ctypes.c_void_p, ctypes.c_void_p]
            setcls = getattr(u, "SetClassLongPtrW", None) or u.SetClassLongW
            p = str(ico_path)
            big = u.LoadImageW(None, p, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            small = u.LoadImageW(None, p, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            self._hicons = (big, small)  # ハンドル参照を保持

            def apply_to(h):
                if not h:
                    return
                if big:
                    u.SendMessageW(h, WM_SETICON, ICON_BIG, big)
                if small:
                    u.SendMessageW(h, WM_SETICON, ICON_SMALL, small)
                try:
                    if big:
                        setcls(h, GCLP_HICON, big)
                    if small:
                        setcls(h, GCLP_HICONSM, small)
                except Exception:
                    pass

            apply_to(self.winfo_id())
            # 本GUIスレッドの全ウインドウを列挙し、タイトルを持つ実窓へ適用する。
            tid = k.GetCurrentThreadId()
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                             wintypes.LPARAM)

            def _cb(h, _lp):
                try:
                    if u.GetWindowTextLengthW(h) > 0 or u.IsWindowVisible(h):
                        apply_to(h)
                except Exception:
                    pass
                return True

            u.EnumThreadWindows(tid, WNDENUMPROC(_cb), 0)
        except Exception:
            pass

    def _ensure_windows_ico(self, png):
        """Windows用 .ico のパスを返す。既存が無ければ PNG から生成する。"""
        for c in (resource_path("assets/icon.ico"),
                  BASE_DIR / "assets" / "icon.ico",
                  Path(sys.executable).resolve().parent / "assets" / "icon.ico",
                  user_data_dir() / "icon.ico"):  # 前回生成したキャッシュを再利用
            try:
                if c.exists():
                    return c
            except Exception:
                pass
        # 無ければ Pillow で PNG → .ico を生成 (ユーザーデータ領域に保存)
        try:
            from PIL import Image
            resample = getattr(Image, "LANCZOS", None)
            if resample is None:
                resample = Image.Resampling.LANCZOS
            ico = user_data_dir() / "icon.ico"
            im = Image.open(png).convert("RGBA")
            # 各表示サイズを明示的に作って .ico にまとめる (互換性重視)
            sizes = [256, 128, 64, 48, 32, 24, 16]
            frames = [im.resize((s, s), resample) for s in sizes]
            frames[0].save(ico, format="ICO", append_images=frames[1:])
            return ico
        except ImportError:
            self.log("  (Pillow未導入のため .ico を生成できません。setup.bat を再実行してください)")
        except Exception as e:
            self.log(f"アイコン(.ico)生成失敗: {e}")
        return None

    # ============================================================ ブラウザ準備
    def _ensure_browser(self):
        """Chromium の有無を確認し、未インストールなら初回ダウンロード"""
        self.btn_run.configure(state="disabled", text="ブラウザ確認中...")
        self.set_status("ブラウザの準備を確認しています...", kind="busy")
        self.log("ブラウザの準備状況を確認しています...")

        def on_ready():
            self._chromium_ready = True
            self.after(0, self._restore_run_button)
            self.log("実行できる状態になりました")
            self.set_status("準備完了。期間と対象を確認したら「検索開始」を押してください", kind="success")
            self.after(0, self._first_run_check)

        def on_fail():
            self.after(0, lambda: self.btn_run.configure(state="disabled", text="ブラウザ未準備"))
            self.set_status("ブラウザの準備に失敗しました。管理者に連絡してください", kind="error")
            self.after(0, lambda: messagebox.showerror(
                "ブラウザの準備に失敗しました",
                "ブラウザ(Chromium)のダウンロードに失敗しました。\n"
                "・インターネットに接続できているか確認してください\n"
                "・社内プロキシで遮断されている可能性があります\n"
                "アプリを再起動するか、管理者にご相談ください",
            ))

        browser_setup.ensure_chromium_async(log=self.log, on_ready=on_ready, on_fail=on_fail)

    # ============================================================ ダウンロード タブ
    def _build_main_tab(self, root):
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(1, weight=4)
        root.rowconfigure(3, weight=2)

        # --- 検索期間 (最初に決める。日常運用ではここから入力する) ---
        period = ui.Card(root, f"検索期間（過去{MAX_DAYS}日以内）")
        period.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        line = ttk.Frame(period)
        line.pack(fill="x")
        self._make_date_input(line, self.var_from).pack(side="left")
        ttk.Label(line, text="〜").pack(side="left", padx=6)
        self._make_date_input(line, self.var_to).pack(side="left")
        self.period_label = ttk.Label(period, style="Weekday.TLabel")
        self.period_label.pack(anchor="w", pady=(8, 6))
        shortcuts = ttk.Frame(period)
        shortcuts.pack(fill="x")
        # ショートカット: 「昨日：mm/dd(曜)」→「今月」→「先月」
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        ttk.Button(shortcuts, text=f"昨日 {yesterday:%m/%d}（{ui.weekday_name(yesterday)}）",
                   command=self.set_yesterday).pack(side="left")
        ttk.Button(shortcuts, text="今月", command=self.set_this_month).pack(side="left", padx=6)
        ttk.Button(shortcuts, text="先月", command=self.set_last_month).pack(side="left")
        for var in (self.var_from, self.var_to):
            var.trace_add("write", lambda *_: self._show_period())

        # --- Hks番割の取込 (PDF名と按分レポートに顧客・現場を反映) ---
        hks = ui.Card(root, "番割の取込")
        hks.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.btn_hks = _btn(hks, "番割全体表示から取込", self.on_import_hks)
        self.btn_hks.pack(anchor="w")
        status = ttk.Label(hks, textvariable=self.var_hks_status, style="Note.TLabel", justify="left")
        status.pack(anchor="w", fill="x", pady=(8, 0))
        hks.bind("<Configure>", lambda e: status.config(wraplength=max(e.width - 30, 160)), add="+")

        # --- 対象の車両 ---
        target = ui.Card(root, padding=12)
        target.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(14, 0))
        top = ttk.Frame(target)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="対象の車両", style="Section.TLabel").pack(side="left")
        for text, value in (("登録済みの車両から選ぶ", "list"), ("1台だけ指定", "single")):
            ttk.Radiobutton(top, text=text, value=value, variable=self.var_mode,
                            command=self._refresh_mode).pack(side="left", padx=(16 if value == "list" else 8, 0))
        self.list_tools = ttk.Frame(top)
        self.list_tools.pack(side="right")
        ttk.Button(self.list_tools, text="すべて外す", command=lambda: self._set_all(False)).pack(side="right")
        ttk.Button(self.list_tools, text="すべて選ぶ", command=lambda: self._set_all(True)).pack(side="right", padx=6)

        # 検索対象に応じた切替エリア (リスト or 単一車両)
        self.mode_area = ttk.Frame(target)
        self.mode_area.pack(fill="both", expand=True)

        # --- リストモード: 登録済み車両 ---
        self.box_list = ttk.Frame(self.mode_area)
        tree_frame = ttk.Frame(self.box_list)
        tree_frame.pack(fill="both", expand=True)
        # 内部キーは互換性のため "dept" のまま (既存 config.json を壊さない)。
        # 表示上の扱いは「所属」をやめて「備考」(自由記入・空欄可) とする。
        cols = ("on", "number", "customer", "site", "driver", "dept")
        headers = {"on": "対象", "number": "車両番号", "customer": "顧客",
                   "site": "現場", "driver": "運転手", "dept": "備考"}
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=7, selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=headers[c], command=lambda col=c: self._sort_by(col))
        self.tree.column("on", width=52, anchor="center", stretch=False)
        self.tree.column("number", width=96, anchor="center", stretch=False)
        self.tree.column("customer", width=170, stretch=True)
        self.tree.column("site", width=210, stretch=True)
        self.tree.column("driver", width=100, stretch=False)
        self.tree.column("dept", width=110, stretch=False)
        self.tree.tag_configure("checked", background=CHECKED_ROW)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double)

        action = ttk.Frame(self.box_list)
        action.pack(fill="x", pady=(8, 0))
        _btn(action, "選んだ行を削除", self._delete_selected).pack(side="left")
        ttk.Label(action, text="「対象」をクリックでオン・オフ、行をダブルクリックで顧客・現場・運転手を編集、"
                               "見出しで並べ替え。新しい車両は「設定」タブで登録します。",
                  style="Note.TLabel").pack(side="left", padx=10)

        # --- 単一モード: 1台指定 ---
        self.box_single = ttk.Frame(self.mode_area)
        line = ttk.Frame(self.box_single)
        line.pack(anchor="w", pady=(4, 0))
        ttk.Label(line, text="車両番号（下4桁）").pack(side="left")
        vcmd = (self.register(self._validate_plate_number), "%P")
        ttk.Entry(line, textvariable=self.var_single_num, width=8, validate="key",
                  validatecommand=vcmd).pack(side="left", padx=(6, 18))
        ttk.Label(line, text="備考").pack(side="left")
        self.cb_single_dept = ttk.Combobox(line, textvariable=self.var_single_dept, width=20, values=[])
        self.cb_single_dept.pack(side="left", padx=6)
        ttk.Label(self.box_single, text="この1台だけを検索します。登録済みの一覧には追加されません。",
                  style="Note.TLabel").pack(anchor="w", pady=(8, 0))

        # --- 実行 ---
        action = ttk.Frame(root)
        action.grid(row=2, column=0, columnspan=2, sticky="we", pady=14)
        self.btn_run = _btn(action, "検索開始", self.on_run, style="Big.Accent.TButton")
        self.btn_run.pack(side="left")
        self.progress = ttk.Progressbar(action, mode="determinate", length=240)
        self.elapsed_label = ttk.Label(action, text="", style="Note.TLabel")
        ttk.Checkbutton(action, text="ブラウザの動きを表示する", variable=self.var_show,
                        style="Switch.TCheckbutton").pack(side="right")

        # --- 進行状況 (色分けしたログ) ---
        log_card = ui.Card(root, "進行状況", padding=10)
        log_card.grid(row=3, column=0, columnspan=2, sticky="nsew")
        frame = ttk.Frame(log_card)
        frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(frame, height=6, state="disabled", wrap="none", font=(self.theme.mono, 10),
                                bg=ui.CARD, fg=INK, relief="flat", bd=0, padx=8, pady=6, highlightthickness=0,
                                spacing1=1, spacing3=1)
        ybar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")
        self.log_text.pack(side="left", fill="both", expand=True)
        bold = (self.theme.mono, 10, "bold")
        self.log_text.tag_configure("head", foreground=ACCENT, font=bold, spacing1=8)
        self.log_text.tag_configure("good", foreground=OK)
        self.log_text.tag_configure("warn", foreground=WARN)
        self.log_text.tag_configure("error", foreground=NG, font=bold)
        self.log_text.tag_configure("step", foreground=MUTED)

        # --- 状態と結果 ---
        bottom = ttk.Frame(root)
        bottom.grid(row=4, column=0, columnspan=2, sticky="we", pady=(12, 0))
        self.status_bar = ttk.Label(bottom, text="● 準備しています…", font=self.theme.font(10))
        self.status_bar.pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="保存先を開く", command=lambda: open_folder(self.var_dir.get())).pack(side="right")
        self.btn_report = ttk.Button(bottom, text="按分レポートを開く", command=self.open_report)
        self.btn_report.pack(side="right", padx=6)
        self._show_period()
        self._refresh_report_button()

    def _show_period(self):
        """検索期間の下に、曜日つきの日付と日数を出す（読めない・範囲外なら赤）。"""
        try:
            d_from = self.parse_date(self.var_from.get(), "開始日")
            d_to = self.parse_date(self.var_to.get(), "終了日")
        except ValueError:
            self.period_label.config(text="日付の形が違います（例 2026/06/01）", foreground=NG)
            return
        if d_from > d_to:
            self.period_label.config(text="開始日が終了日より後になっています", foreground=NG)
            return
        if (datetime.date.today() - d_from).days > MAX_DAYS:
            self.period_label.config(text=f"開始日が{MAX_DAYS}日より前です（照会できません）", foreground=NG)
            return
        days = (d_to - d_from).days + 1
        text = ui.japanese_date(d_from) if days == 1 else \
            f"{ui.japanese_date(d_from)} 〜 {ui.japanese_date(d_to)}（{days}日間）"
        self.period_label.config(text=text, foreground=INK)

    def _latest_report(self):
        try:
            files = list(Path(self.var_dir.get()).glob("按分レポート_*.csv"))
        except OSError:
            return None
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def _refresh_report_button(self):
        if hasattr(self, "btn_report"):
            self.btn_report.configure(state="normal" if self._latest_report() else "disabled")

    def open_report(self):
        path = self._latest_report()
        if path is None:
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # Excel などで開く
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self.log(f"按分レポートを開けませんでした: {e}")

    # =========================================================== 設定タブ
    def _build_settings_tab(self, root):
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        nav = ttk.Frame(root)
        nav.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        holder = ui.Card(root, padding=18)
        holder.grid(row=0, column=1, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.page_var = tk.StringVar(value="ログイン")
        self.pages = {}
        for name, build in (("ログイン", self._page_login), ("保存先", self._page_save),
                            ("PDFへの書き込み", self._page_stamp), ("車両の登録", self._page_vehicles)):
            ttk.Radiobutton(nav, text=name, value=name, variable=self.page_var, style="Nav.Toolbutton",
                            command=self._show_page, width=16).pack(fill="x", pady=2)
            page = ttk.Frame(holder)
            page.grid(row=0, column=0, sticky="nsew")
            ttk.Label(page, text=name, style="Page.TLabel").pack(anchor="w", pady=(0, 10))
            build(page)
            self.pages[name] = page
        self._show_page()

        bottom = ttk.Frame(root)
        bottom.grid(row=1, column=0, columnspan=2, sticky="we", pady=(12, 0))
        _btn(bottom, "保存", self._save_now, style="primary", width=10).pack(side="left")
        self.save_status = ttk.Label(bottom, text="")
        self.save_status.pack(side="left", padx=10)
        ttk.Label(bottom, text="設定はこのPCに保存されます（「検索開始」を押したときも保存します）。",
                  style="Note.TLabel").pack(side="right")

    def _show_page(self):
        self.pages[self.page_var.get()].tkraise()

    @staticmethod
    def _note(parent, text):
        """説明の文。画面の幅に合わせて折り返す。"""
        label = ttk.Label(parent, text=text, style="Note.TLabel", wraplength=560, justify="left")
        label.pack(anchor="w", fill="x", pady=(2, 0))
        parent.bind("<Configure>", lambda e: label.config(wraplength=max(e.width - 8, 200)), add="+")
        return label

    def _page_login(self, page):
        self._note(page, "ETC利用照会サービス（https://www.etc-meisai.jp/）のログイン情報です。")
        grid = ttk.Frame(page)
        grid.pack(anchor="w", pady=(10, 0))
        ttk.Label(grid, text="ユーザーID").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.var_id, width=32).grid(row=0, column=1, sticky="w", padx=10, pady=4)
        ttk.Label(grid, text="パスワード").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.var_pw, width=32, show="*").grid(row=1, column=1, sticky="w", padx=10, pady=4)
        ttk.Checkbutton(page, text="パスワードを保存する", variable=self.var_save_pw,
                        style="Switch.TCheckbutton").pack(anchor="w", pady=(10, 0))
        self._note(page, "パスワードはこのPCの設定ファイルにそのまま保存されます。"
                         "PCのログインパスワードなどで、ほかの人が使えないようにしてください。")

    def _page_save(self, page):
        ttk.Label(page, text="PDFの保存先", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        line = ttk.Frame(page)
        line.pack(fill="x")
        ttk.Entry(line, textvariable=self.var_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(line, text="参照…", command=self.browse_dir).pack(side="left", padx=(6, 0))
        ttk.Button(line, text="開く", command=lambda: open_folder(self.var_dir.get())).pack(side="left", padx=(6, 0))
        ttk.Label(page, text="同じ名前のPDFがすでにあるとき", style="Section.TLabel").pack(anchor="w", pady=(18, 6))
        for label, val in (
            ("連番を付けて保存する（例 20260610_1499_2.pdf）", "rename"),
            ("上書きする", "overwrite"),
            ("スキップする（ダウンロードしない）", "skip"),
        ):
            ttk.Radiobutton(page, text=label, value=val, variable=self.var_dup).pack(anchor="w", pady=2)

    def _page_stamp(self, page):
        self._note(page, "番割を取り込んだ1日分の検索では、明細PDFの下に顧客・現場・運転手を書き込みます。"
                         "情報がない車両や、複数日の検索では書き込みません。")
        for text, var in (("顧客名", self.var_stamp_customer), ("現場名", self.var_stamp_site),
                          ("運転手", self.var_stamp_driver)):
            ttk.Checkbutton(page, text=text, variable=var, style="Switch.TCheckbutton").pack(anchor="w", pady=4)
        line = ttk.Frame(page)
        line.pack(anchor="w", pady=(10, 0))
        ttk.Label(line, text="文字の大きさ").pack(side="left")
        ttk.Spinbox(line, from_=6, to=72, increment=1, width=5, textvariable=self.var_stamp_size).pack(
            side="left", padx=6)
        ttk.Label(line, text="pt").pack(side="left")

    def _page_vehicles(self, page):
        ttk.Label(page, text="新しい車両を登録する", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        line = ttk.Frame(page)
        line.pack(anchor="w")
        ttk.Label(line, text="車両番号（下4桁）").pack(side="left")
        ttk.Label(line, text="*", style="Required.TLabel").pack(side="left", padx=(2, 0))
        vcmd = (self.register(self._validate_plate_number), "%P")
        ttk.Entry(line, textvariable=self.var_reg_num, width=8, validate="key",
                  validatecommand=vcmd).pack(side="left", padx=(6, 18))
        ttk.Label(line, text="備考").pack(side="left")
        self.cb_reg_dept = ttk.Combobox(line, textvariable=self.var_reg_dept, width=20, values=[])
        self.cb_reg_dept.pack(side="left", padx=6)
        _btn(line, "登録", self._register_vehicle, style="primary").pack(side="left", padx=(6, 0))
        self._note(page, "登録した車両は「ダウンロード」タブの一覧に出ます。"
                         "番号や備考を変えるときは、一覧から削除してから登録し直してください。")
        ttk.Label(page, text="車両の一覧をほかのPCに渡す", style="Section.TLabel").pack(anchor="w", pady=(18, 6))
        line = ttk.Frame(page)
        line.pack(anchor="w")
        ttk.Button(line, text="CSVに書き出す", command=self._export_vehicles).pack(side="left")
        ttk.Button(line, text="CSVを読み込む", command=self._import_vehicles).pack(side="left", padx=6)
        self._note(page, "形式は1行目が「備考,車両番号」の CSV です。Excel で作ってまとめて読み込むこともできます。")

    # ============================================================ 共通処理
    def _refresh_mode(self):
        if self.var_mode.get() == "list":
            self.box_single.pack_forget()
            self.box_list.pack(in_=self.mode_area, fill="both", expand=True)
            self.list_tools.pack(side="right")
        else:
            self.box_list.pack_forget()
            self.list_tools.pack_forget()
            self.box_single.pack(in_=self.mode_area, fill="x")
            self.cb_single_dept["values"] = self._depts()

    def _depts(self):
        return sorted({v["dept"] for v in self.vehicles if v.get("dept")})

    @staticmethod
    def _ellipsis(s, n):
        s = str(s or "")
        return s if len(s) <= n else s[: n - 1] + "…"

    def _sort_by(self, col):
        """列ヘッダクリック: 同じ列なら昇順/降順をトグル"""
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        self._refresh_list()

    def _refresh_list(self):
        col = self._sort_col

        def key_fn(i):
            v = self.vehicles[i]
            if col == "on":
                return (not v.get("enabled", True),)
            if col == "number":
                n = v.get("number", "")
                try:
                    return (0, int(n))
                except ValueError:
                    return (1, n)
            return (str(v.get(col, "")),)

        if col:
            order = sorted(range(len(self.vehicles)), key=key_fn, reverse=self._sort_desc)
        else:
            order = list(range(len(self.vehicles)))

        if hasattr(self, "tree"):
            self.tree.delete(*self.tree.get_children())
            for idx in order:
                v = self.vehicles[idx]
                mark = "☑" if v.get("enabled", True) else "☐"
                tags = ("checked",) if v.get("enabled", True) else ()
                self.tree.insert("", "end", iid=str(idx), tags=tags, values=(
                    mark, v.get("number", ""),
                    self._ellipsis(v.get("customer", ""), 14),
                    self._ellipsis(v.get("site", ""), 18),
                    self._ellipsis(v.get("driver", ""), 8),
                    v.get("dept", ""),
                ))
            # ヘッダにソート方向を表示
            headers = {"on": "対象", "number": "車両番号", "customer": "顧客",
                       "site": "現場", "driver": "運転手", "dept": "備考"}
            for c, label in headers.items():
                suffix = ""
                if c == self._sort_col:
                    suffix = " ▼" if self._sort_desc else " ▲"
                self.tree.heading(c, text=label + suffix)

        depts = self._depts()
        if hasattr(self, "cb_reg_dept"):
            self.cb_reg_dept["values"] = depts
        if hasattr(self, "cb_single_dept"):
            self.cb_single_dept["values"] = depts

    def _on_tree_double(self, event):
        """行ダブルクリック → 顧客・現場・運転手の編集ダイアログ"""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        col = self.tree.identify_column(event.x)
        if col == "#1":   # 対象列はトグル操作 (シングルクリックで処理済み)
            return
        idx = int(item)
        v = self.vehicles[idx]

        _note = v.get("dept", "")
        dlg = tk.Toplevel(self)
        # 位置確定までは隠しておく (一瞬左上に出てから移動する見え方を防ぐ)
        dlg.withdraw()
        dlg.title(f"編集: 車両{v.get('number', '')}" + (f" / {_note}" if _note else ""))
        dlg.transient(self)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)

        vars_ = {}
        for r, (key, label) in enumerate(
                [("customer", "顧客"), ("site", "現場"), ("driver", "運転手")]):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=3)
            sv = tk.StringVar(value=v.get(key, ""))
            ttk.Entry(frm, textvariable=sv, width=42).grid(row=r, column=1, padx=6, pady=3)
            vars_[key] = sv
        ttk.Label(frm, text="※備考・車両番号の変更は削除→再登録で",
                  foreground="#888").grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, pady=(10, 0), sticky="e")

        def ok():
            for key, sv in vars_.items():
                v[key] = sv.get().strip()
            save_config(self._current_config())
            self._refresh_list()
            dlg.destroy()

        _btn(btns, "キャンセル", dlg.destroy, style="secondary").pack(side="right", padx=4)
        _btn(btns, "保存", ok, style="primary").pack(side="right", padx=4)
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

        # 画面左上ではなく、今のウィンドウ中央に出す
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.deiconify()
        dlg.grab_set()

    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item or col != "#1":
            return
        idx = int(item)
        self.vehicles[idx]["enabled"] = not self.vehicles[idx].get("enabled", True)
        self._refresh_list()

    def _set_all(self, value):
        for v in self.vehicles:
            v["enabled"] = value
        self._refresh_list()

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("削除", "削除する車両を一覧でクリックして選択してください")
            return
        idx = int(sel[0])
        v = self.vehicles[idx]
        if not messagebox.askyesno("確認", f"「{v.get('dept', '')} / {v.get('number', '')}」を削除しますか？"):
            return
        del self.vehicles[idx]
        self._refresh_list()

    @staticmethod
    def _validate_plate_number(proposed):
        """車両番号フィールドの入力検証: 半角数字のみ・最大4桁。
        IME経由の全角数字や記号・かなはこの時点で弾く (空は許可=削除可)。
        """
        return proposed == "" or (
            proposed.isascii() and proposed.isdigit() and len(proposed) <= 4
        )

    def _register_vehicle(self):
        dept = self.var_reg_dept.get().strip()  # 備考 (空欄可)
        number = self.var_reg_num.get().strip()
        if not number:
            messagebox.showerror("登録エラー", "車両番号を入力してください")
            return
        if not number.isdigit() or len(number) > 4:
            messagebox.showerror("登録エラー", "車両番号はナンバーの下4桁の数字で入力してください")
            return
        if any(v.get("number") == number for v in self.vehicles):
            messagebox.showerror("登録エラー", "同じ車両番号の車両がすでに登録されています")
            return
        self.vehicles.append({"enabled": True, "dept": dept, "number": number})
        self.var_reg_dept.set("")
        self.var_reg_num.set("")
        self._refresh_list()
        self.log(f"車両を登録しました: {dept} / {number}")

    def _export_vehicles(self):
        if not self.vehicles:
            messagebox.showinfo("書き出し", "登録済みの車両がありません")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSVファイル", "*.csv")],
            initialfile="車両リスト.csv",
        )
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["備考", "車両番号"])
            for v in self.vehicles:
                w.writerow([v.get("dept", ""), v.get("number", "")])
        messagebox.showinfo("書き出し", f"{len(self.vehicles)} 台を書き出しました:\n{path}")

    def _import_vehicles(self):
        path = filedialog.askopenfilename(filetypes=[("CSVファイル", "*.csv")])
        if not path:
            return
        import csv
        added, skipped, bad = 0, 0, []
        try:
            # Excel保存のCSV(cp932)とUTF-8の両方を受け付ける
            for enc in ("utf-8-sig", "cp932"):
                try:
                    with open(path, newline="", encoding=enc) as f:
                        rows = list(csv.reader(f))
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError("文字コードを判定できませんでした")
        except Exception as e:
            messagebox.showerror("読み込みエラー", str(e))
            return
        for lineno, row in enumerate(rows, 1):
            if not row or not any(cell.strip() for cell in row):
                continue
            dept = row[0].strip()
            number = row[1].strip() if len(row) > 1 else ""
            if dept in ("所属", "備考"):  # ヘッダ行 (旧ヘッダ「所属」も許容)
                continue
            if not number.isdigit() or len(number) > 4:
                bad.append(f"{lineno}行目: {dept},{number}")
                continue
            if any(v.get("number") == number for v in self.vehicles):
                skipped += 1
                continue
            self.vehicles.append({"enabled": True, "dept": dept, "number": number})
            added += 1
        self._refresh_list()
        msg = f"追加 {added} 台 / 重複スキップ {skipped} 台"
        if bad:
            msg += f"\n形式エラー {len(bad)} 件:\n" + "\n".join(bad[:5])
        messagebox.showinfo("読み込み結果", msg)

    # ============================================================ Hks取込
    # pywinauto(COM) はスレッドのアパートメントに紐づくため、取込処理は
    # 常駐の専用スレッド1本で行う (毎回新スレッドだと2回目以降が失敗する)
    def _ensure_hks_thread(self):
        if getattr(self, "_hks_thread", None) is None or not self._hks_thread.is_alive():
            self._hks_jobs = queue.Queue()

            def loop():
                try:
                    import comtypes
                    comtypes.CoInitialize()
                except Exception:
                    pass
                while True:
                    job = self._hks_jobs.get()
                    if job is None:
                        return
                    try:
                        job()
                    except Exception as e:
                        self.log(f"Hks取込エラー: {e}")

            self._hks_thread = threading.Thread(target=loop, daemon=True)
            self._hks_thread.start()

    def _hks_dates(self):
        return sorted({r.get("date") for r in self.hks_records if r.get("date")})

    def _hks_offices_summary(self):
        # 営業所別の件数を「営業所名:N」の形で並べる
        from collections import Counter
        cnt = Counter((r.get("date", ""), r.get("office", "")) for r in self.hks_records)
        parts = []
        for (d, o), n in sorted(cnt.items()):
            parts.append(f"{d} {o or '営業所不明'}({n})")
        return " / ".join(parts)

    def _update_hks_status(self):
        if self.hks_records:
            dates = self._hks_dates()
            self.var_hks_status.set(
                f"取込済: {len(self.hks_records)}件 "
                f"({', '.join(dates) or '日付不明'} / 取込{self.hks_imported_at})"
            )
        else:
            self.var_hks_status.set("未取込。Hksの番割全体表示（番割予定表）を開いてから押すと、"
                                    "車両ごとの顧客・現場・運転手を PDF名と按分レポートに入れます。")

    def on_import_hks(self):
        self.btn_hks.configure(state="disabled", text="取込中…")
        # 取込中は検索開始を押せないようにする
        self._hks_importing = True
        self.btn_run.configure(state="disabled")
        self.set_status("番割予定表を読み取っています...", kind="busy")

        def worker():
            try:
                import hks_reader
                metas = hks_reader.enumerate_windows()
                if not metas:
                    raise RuntimeError(
                        "番割予定表ウィンドウが見つかりません。Hksで番割予定表を表示してください。"
                    )
                # 2窓以上ならユーザーに選ばせる
                if len(metas) >= 2:
                    chosen = self._ask_window_selection_sync(metas)
                    if chosen is None:
                        self.log("Hks取込をキャンセルしました")
                        self.set_status("番割予定表の取込をキャンセルしました", kind="info")
                        return
                    metas = [metas[i] for i in chosen]
                self.log(f"{len(metas)} 画面を取り込みます")
                records = hks_reader.read_windows(metas, log=self.log, cache=self._hks_cache)
                records = self._dedup_hks_windows(records)
                self.hks_records = records
                self.hks_imported_at = datetime.datetime.now().strftime("%m/%d %H:%M")
                save_config(self._current_config())

                dates = sorted({r.get("date") for r in records if r.get("date")})
                self.log(f"Hks番割を取り込みました: {len(records)} 件 / "
                         f"対象日: {', '.join(dates) or '不明'}")

                # 車両ごとに集約 (同一車両が複数日付に出る場合は新しい日付を優先)
                from collections import OrderedDict, defaultdict
                by_no = OrderedDict()
                for r in records:
                    by_no.setdefault(r["vehicle_no"], []).append(r)

                # 同一現場(日付+現場)に複数車両が割り当てられているか集計。
                # 該当する車両は運転手を特定できないため空欄にする (顧客・現場は残す)。
                site_vehicles = defaultdict(set)
                for r in records:
                    s = r.get("site", "")
                    if s:
                        site_vehicles[(r.get("date", ""), s)].add(r["vehicle_no"])
                shared_sites = {k for k, nos in site_vehicles.items() if len(nos) >= 2}

                info_by_no = {}
                multi_list = []
                shared_driver_cleared = 0
                for no, recs in by_no.items():
                    latest = max((r.get("date") or "") for r in recs)
                    recs = [r for r in recs if (r.get("date") or "") == latest]
                    # 同顧客・同現場の重複は1件扱い (別営業所の乗合は複数現場ではない)
                    pairs = list(dict.fromkeys((r["customer"], r["site"]) for r in recs))
                    customers = list(dict.fromkeys(c for c, _ in pairs))
                    sites = list(dict.fromkeys(s for _, s in pairs))
                    drivers = list(dict.fromkeys(
                        r.get("driver", "") for r in recs if r.get("driver")
                    ))
                    driver_str = " / ".join(drivers)
                    # この車両の現場のいずれかが「複数車両の現場」なら運転手を空欄に
                    veh_keys = {(r.get("date", ""), r.get("site", "")) for r in recs if r.get("site")}
                    if veh_keys & shared_sites:
                        if driver_str:
                            shared_driver_cleared += 1
                        driver_str = ""
                    info_by_no[no] = {
                        "customer": " / ".join(customers),
                        "site": " / ".join(sites),
                        "driver": driver_str,
                        "hks_date": latest,
                    }
                    if len(pairs) > 1:
                        multi_list.append(f"{latest} 車両{no}: " + " / ".join(sites))

                # 登録済み車両リストへ反映
                matched, unmatched = 0, []
                registered = {v.get("number") for v in self.vehicles}
                for v in self.vehicles:
                    info = info_by_no.get(v.get("number"))
                    if info:
                        v.update(info)
                        matched += 1
                    else:
                        # 今回の番割に出てこない車両は情報をクリア
                        v.update({"customer": "", "site": "", "driver": "", "hks_date": ""})
                for no in info_by_no:
                    if no not in registered:
                        unmatched.append(no)
                save_config(self._current_config())
                self.after(0, self._refresh_list)

                self.log(f"登録済み車両への反映: {matched} 台に顧客・現場・運転手をセットしました")
                if shared_driver_cleared:
                    self.log(f"  うち {shared_driver_cleared} 台は同一現場に複数車両のため運転手を空欄にしました")
                self.set_status(f"番割予定表の取込が完了しました ({matched} 台に反映)", kind="success")
                if unmatched:
                    self.log(f"※番割にあるが未登録の車両: {', '.join(sorted(unmatched, key=lambda x: x.zfill(4)))}")
                if multi_list:
                    self.log(f"⚠ 複数現場に割り当てられた車両が {len(multi_list)} 件あります")
                    self.after(0, lambda: messagebox.showwarning(
                        "複数現場の車両",
                        "同じ車両が複数の現場に割り当てられています。\n"
                        "PDF名は「複数現場」、按分レポートには全現場を記録します。\n\n"
                        + "\n".join(multi_list)))
            except ImportError as e:
                # 実際に読み込めなかったモジュール名を出す。
                # (pywinauto 本体だけでなく、UIA用の comtypes 生成失敗等も切り分けるため)
                self.log(f"Hks取込エラー: 必要なモジュールを読み込めません → {e}")
                self.set_status(f"必要なモジュールの読み込みに失敗: {e}", kind="error")
            except Exception as e:
                self.log(f"Hks取込エラー: {e}")
                self.set_status(f"番割予定表の取込に失敗しました: {e}", kind="error")
            finally:
                def restore():
                    self.btn_hks.configure(state="normal", text="番割全体表示から取込")
                    self._update_hks_status()
                    # 取込が終わったら検索開始を押せる状態に戻す
                    self._hks_importing = False
                    self._restore_run_button()
                self.after(0, restore)

        self._ensure_hks_thread()
        self._hks_jobs.put(worker)

    def _ask_window_selection_sync(self, metas):
        """ワーカースレッドから呼ぶ。メインスレッドで選択ダイアログを表示して結果を待つ。
        戻り値: 選択されたインデックスのリスト / None(キャンセル)
        """
        event = threading.Event()
        result = {"indices": None}

        def show():
            result["indices"] = self._show_window_selection_dialog(metas)
            event.set()

        self.after(0, show)
        event.wait()
        return result["indices"]

    def _show_window_selection_dialog(self, metas):
        dlg = tk.Toplevel(self)
        dlg.title("取り込む番割予定表を選択")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)

        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text=f"番割予定表が {len(metas)} 件見つかりました。取り込むものを選んでください。\n"
                 "(対象列をクリックでON/OFF / 取り込めるのは同じ日付の番割だけです)",
        ).pack(anchor="w", pady=(0, 10))

        tree_frame = ttk.Frame(frm)
        tree_frame.pack(fill="both", expand=True)
        cols = ("on", "office", "date", "update")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                            height=min(len(metas), 12), selectmode="none")
        tree.heading("on", text="対象")
        tree.heading("office", text="営業所")
        tree.heading("date", text="日付")
        tree.heading("update", text="更新")
        tree.column("on", width=50, anchor="center", stretch=False)
        tree.column("office", width=200, anchor="w", stretch=False)
        tree.column("date", width=110, anchor="center", stretch=False)
        tree.column("update", width=80, anchor="center", stretch=False)
        # チェック済みの行をうっすら水色でハイライトする
        tree.tag_configure("checked", background="#e6f3fb")
        tree.pack(fill="both", expand=True)

        # 検索対象が単日なら、その日付の番割だけを初期選択する。
        # (検索日と違う番割は初期状態でチェックしない)
        target_iso = None
        try:
            d_from = self.parse_date(self.var_from.get(), "")
            d_to = self.parse_date(self.var_to.get(), "")
            if d_from == d_to:
                target_iso = str(d_from)
        except Exception:
            target_iso = None

        if target_iso is not None:
            checked = [m.get("date") == target_iso for m in metas]
        else:
            checked = [False] * len(metas)

        def render():
            tree.delete(*tree.get_children())
            for i, m in enumerate(metas):
                tree.insert("", "end", iid=str(i), tags=("checked",) if checked[i] else (), values=(
                    "☑" if checked[i] else "☐",
                    m.get("office") or "営業所不明",
                    m.get("date") or "日付不明",
                    m.get("update_hhmm") or "不明",
                ))

        def on_click(event):
            row = tree.identify_row(event.y)
            if row:
                i = int(row)
                checked[i] = not checked[i]
                render()

        tree.bind("<Button-1>", on_click)
        render()

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))

        result = {"indices": None}

        def all_on():
            for i in range(len(checked)):
                checked[i] = True
            render()

        def all_off():
            for i in range(len(checked)):
                checked[i] = False
            render()

        def ok():
            idx = [i for i, c in enumerate(checked) if c]
            if not idx:
                messagebox.showwarning("選択なし", "少なくとも1件選んでください")
                return
            # 複数日の番割を同時に取り込むのは誤作動の元なので禁止
            dates = {metas[i].get("date") for i in idx}
            if len(dates) > 1:
                messagebox.showerror(
                    "複数日は取り込めません",
                    "異なる日付の番割を同時に取り込むことはできません。\n"
                    "同じ日付の番割だけを選択してください。\n\n"
                    f"選択中の日付: {', '.join(sorted(d or '日付不明' for d in dates))}",
                )
                return
            result["indices"] = idx
            dlg.destroy()

        def cancel():
            dlg.destroy()

        _btn(btns, "全選択", all_on, style="secondary").pack(side="left", padx=2)
        _btn(btns, "全解除", all_off, style="secondary").pack(side="left", padx=2)
        _btn(btns, "キャンセル", cancel, style="secondary").pack(side="right", padx=2)
        _btn(btns, "取り込む", ok, style="primary").pack(side="right", padx=2)
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: cancel())

        # 親の中央に配置
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + 80
        dlg.geometry(f"+{max(x, 50)}+{max(y, 50)}")

        self.wait_window(dlg)
        return result["indices"]

    def _dedup_hks_windows(self, records):
        """同じ(営業所,日付)の予定表が複数あれば、更新時刻が最も新しいものだけ残す。
        24時間以上前の予定表は除外する。除外内容はログに残す。
        """
        if not records:
            return records
        # ウィンドウキー: (office, date) -> (latest_update_dt_iso, update_hhmm)
        windows = {}
        for r in records:
            key = (r.get("office", ""), r.get("date", ""))
            u = r.get("update_dt", "")
            prev = windows.get(key)
            if prev is None or (u and u > prev[0]):
                windows[key] = (u, r.get("update_hhmm", ""))
        # 24時間判定
        now = datetime.datetime.now()
        cutoff = now - datetime.timedelta(hours=24)
        kept_windows = {}
        for key, (u_iso, hhmm) in windows.items():
            office, date = key
            if not u_iso:
                # 更新時刻不明 → 採用 (他に同条件がなければ)
                kept_windows[key] = (u_iso, hhmm)
                continue
            try:
                u_dt = datetime.datetime.fromisoformat(u_iso)
            except Exception:
                kept_windows[key] = (u_iso, hhmm)
                continue
            if u_dt < cutoff:
                self.log(f"  予定表 [{date} {office or '営業所不明'} 更新{hhmm}] "
                         f"は24時間以上経過しているため除外しました")
            else:
                kept_windows[key] = (u_iso, hhmm)

        kept = []
        dropped = []
        for r in records:
            key = (r.get("office", ""), r.get("date", ""))
            keep = windows.get(key)
            if key in kept_windows and r.get("update_dt", "") == kept_windows[key][0]:
                kept.append(r)
            else:
                dropped.append(r)
        # 重複ウィンドウ (同 office,date で更新時刻違い) の件数を集計してログ
        from collections import Counter
        cnt = Counter((r.get("office", ""), r.get("date", ""), r.get("update_hhmm", ""))
                      for r in dropped if (r.get("office", ""), r.get("date", "")) in kept_windows)
        for (office, date, hhmm), n in sorted(cnt.items()):
            self.log(f"  予定表 [{date} {office or '営業所不明'} 更新{hhmm}] "
                     f"はより新しい同条件の予定表があるため除外しました ({n}件)")
        return kept

    def _stamp_size(self):
        """文字サイズ設定を安全に読む (空欄・異常値は既定14、6〜72に丸め)"""
        try:
            v = int(self.var_stamp_size.get())
        except Exception:
            v = 14
        return max(6, min(v, 72))

    def _save_now(self):
        save_config(self._current_config())
        self._refresh_settings_badge()
        self._refresh_report_button()
        self.save_status.configure(text=f"✓ 保存しました（{datetime.datetime.now():%H:%M}）", style="Ok.TLabel")

    def _current_config(self):
        return {
            "login_id": self.var_id.get().strip(),
            "password": self.var_pw.get() if self.var_save_pw.get() else "",
            "save_dir": self.var_dir.get(),
            "headless": not self.var_show.get(),
            "vehicles": self.vehicles,
            "mode": self.var_mode.get(),
            "sort_col": self._sort_col,
            "sort_desc": self._sort_desc,
            "dup_mode": self.var_dup.get(),
            "stamp_customer": self.var_stamp_customer.get(),
            "stamp_site": self.var_stamp_site.get(),
            "stamp_driver": self.var_stamp_driver.get(),
            "stamp_font_size": self._stamp_size(),
            # 番割(hks_records / hks_imported_at)は日替わりで起動時に必ず未取込から
            # 始める設計のため、config.json には保存しない (書いても読み戻さないため無駄)。
            "single": {
                "dept": self.var_single_dept.get().strip(),
                "number": self.var_single_num.get().strip(),
            },
        }

    def _make_date_input(self, parent, var):
        """日付入力欄。直接入力できる Entry と、カレンダーを開くボタンを並べる。"""
        frame = ttk.Frame(parent)
        entry = ttk.Entry(frame, textvariable=var, width=11, font=self.theme.font(11))
        entry.pack(side="left")
        button = ttk.Button(frame, text="カレンダー")
        button.configure(command=lambda: self._open_calendar_popup(button, var))
        button.pack(side="left", padx=(4, 0))
        return frame

    def _open_calendar_popup(self, anchor, var):
        """anchor ウィジェットの真下にカレンダーを開き、選んだ日付を var に入れる"""
        try:
            cur = self.parse_date(var.get(), "")
        except Exception:
            cur = datetime.date.today()
        earliest = datetime.date.today() - datetime.timedelta(days=MAX_DAYS)
        ui.CalendarPopup(self, anchor, cur, lambda d: var.set(d.strftime("%Y/%m/%d")), self.theme, earliest)

    # 期間ショートカット
    def set_this_month(self):
        today = datetime.date.today()
        self.var_from.set(today.replace(day=1).strftime("%Y/%m/%d"))
        self.var_to.set(today.strftime("%Y/%m/%d"))

    def set_last_month(self):
        first_this = datetime.date.today().replace(day=1)
        last_end = first_this - datetime.timedelta(days=1)
        self.var_from.set(last_end.replace(day=1).strftime("%Y/%m/%d"))
        self.var_to.set(last_end.strftime("%Y/%m/%d"))

    def set_yesterday(self):
        y = datetime.date.today() - datetime.timedelta(days=1)
        self.var_from.set(y.strftime("%Y/%m/%d"))
        self.var_to.set(y.strftime("%Y/%m/%d"))

    def browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_dir.get() or str(Path.home()))
        if d:
            self.var_dir.set(d)

    def _first_run_check(self):
        """ログイン情報が未入力なら案内する (起動時1回のみ)"""
        if getattr(self, "_first_run_done", False):
            return
        self._first_run_done = True
        self._refresh_settings_badge()
        if not self.var_id.get().strip() or not self.var_pw.get():
            if messagebox.askyesno(
                "初回設定のお願い",
                "ETC利用照会サービスのID・パスワードがまだ設定されていません。\n"
                "「設定」タブで入力してください。\n\n"
                "今すぐ設定タブを開きますか？",
            ):
                self.nb.select(self.tab_settings)
                self.page_var.set("ログイン")
                self._show_page()

    def _refresh_settings_badge(self):
        """ログイン情報が未入力なら設定タブのラベルに ● を付ける"""
        need = not (self.var_id.get().strip() and self.var_pw.get())
        try:
            self.nb.tab(self.tab_settings, text="   設定 ●   " if need else "   設定   ")
        except Exception:
            pass

    def set_status(self, text, kind="info"):
        """ステータス帯を更新する。kind: info / busy / success / warning / error"""
        color = {"info": ACCENT, "busy": WARN, "success": OK, "warning": WARN, "error": NG}.get(kind, ACCENT)

        def apply():
            self.status_bar.configure(text=f"● {text}", foreground=color)
        self.after(0, apply)

    def log(self, msg):
        self.log_queue.put(msg)

    def poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n", self._log_tag(msg))
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.poll_log)

    @staticmethod
    def _log_tag(msg):
        """ログの1行の色（見出し＝青、保存できた＝緑、注意＝橙、エラー＝赤、細かい経過＝灰）。"""
        text = str(msg)
        if text.startswith("==="):
            return "head"
        if any(w in text for w in ("エラー", "失敗", "できません", "見つかりません")):
            return "error"
        if text.lstrip().startswith(("⚠", "※")) or "不一致" in text or "除外" in text:
            return "warn"
        if any(w in text for w in ("保存しました", "→ 保存", "完了", "取り込みました", "反映", "実行できる状態")):
            return "good"
        if text.startswith("  "):
            return "step"
        return ""

    def parse_date(self, s, label):
        for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.datetime.strptime(s.strip(), fmt).date()
            except ValueError:
                continue
        raise ValueError(f"{label}の日付形式が不正です: {s} (例: 2026/06/01)")

    def on_run(self):
        if self.running or self._hks_importing:
            return
        try:
            d_from = self.parse_date(self.var_from.get(), "開始日")
            d_to = self.parse_date(self.var_to.get(), "終了日")
            if d_from > d_to:
                raise ValueError("開始日が終了日より後になっています")
            if (datetime.date.today() - d_from).days > 62:
                raise ValueError("開始日が今日から62日より前です(サイトの制約)")
            login_id = self.var_id.get().strip()
            password = self.var_pw.get()
            if not login_id or not password:
                raise ValueError("ユーザーIDとパスワードを「設定」タブで入力してください")

            mode = self.var_mode.get()
            if mode == "single":
                dept = self.var_single_dept.get().strip()
                number = self.var_single_num.get().strip()
                if not number:
                    raise ValueError("車両番号を入力してください")
                if not number.isdigit() or len(number) > 4:
                    raise ValueError("車両番号はナンバーの下4桁の数字で入力してください")
                targets = [{"name": dept, "number": number}]
            else:
                targets = [
                    {"name": v.get("dept", ""), "number": v.get("number", "")}
                    for v in self.vehicles if v.get("enabled") and v.get("number")
                ]
                if not targets:
                    raise ValueError("チェック済みの車両がありません。一覧の「対象」列をクリックして選択してください")
        except ValueError as e:
            messagebox.showerror("入力エラー", str(e))
            return

        save_config(self._current_config())

        # --- 車両リストの顧客・現場・運転手情報を使う ---
        single_day = (d_from == d_to)
        target_date_iso = str(d_from)
        # リストから車両番号 → 顧客・現場・運転手 を構築 (空情報の車両は除く)
        vehicle_info = {}
        info_dates = set()
        for v in self.vehicles:
            no = v.get("number")
            if not no:
                continue
            if not (v.get("customer") or v.get("site") or v.get("driver")):
                continue
            vehicle_info[no] = {
                "customer": v.get("customer", ""),
                "site": v.get("site", ""),
                "driver": v.get("driver", ""),
                "multi": " / " in v.get("site", ""),
            }
            if v.get("hks_date"):
                info_dates.add(v["hks_date"])
        if not vehicle_info:
            vehicle_info = None

        if not single_day:
            if vehicle_info:
                self.log("※複数日検索のため、PDF名・按分レポート・PDF書き込みに顧客・現場は使いません")
            vehicle_info = None
        elif vehicle_info and info_dates and target_date_iso not in info_dates:
            if not messagebox.askyesno(
                "日付の不一致",
                f"車両リストの顧客・現場情報は {', '.join(sorted(info_dates))} の番割です。\n"
                f"検索対象日は {target_date_iso} です。\n\n"
                "このまま実行すると、別の日の割当情報がPDF名等に使われます。\n"
                "続行しますか？\n\n"
                "(いいえ → 正しい日の番割をHksで表示して取込み直してください)",
            ):
                return
            self.log("⚠ 番割と検索日が不一致のまま続行します")
        name_in_filename = bool(vehicle_info) and single_day
        stamp_opts = {
            "customer": self.var_stamp_customer.get(),
            "site": self.var_stamp_site.get(),
            "driver": self.var_stamp_driver.get(),
            "font_size": self._stamp_size(),
        }

        self.running = True
        self.btn_run.configure(state="disabled", text="検索中…")
        self.btn_hks.configure(state="disabled")
        self.nb.tab(self.tab_settings, state="disabled")  # 検索中に設定を変えないようにする
        self.progress.configure(maximum=len(targets), value=0)
        self.progress.pack(side="left", padx=(16, 8))
        self.elapsed_label.pack(side="left")
        self._started_at = datetime.datetime.now().timestamp()
        self._progress_text = f"0 / {len(targets)} 台"
        self._tick()
        self.set_status(f"ETC明細をダウンロードしています（{len(targets)} 台）…", kind="busy")
        self.log(f"=== 開始: {d_from} 〜 {d_to} / 対象 {len(targets)} 台 ===")

        def on_progress(done, total):
            # ワーカースレッドから呼ばれるので UI 更新は after で本スレッドに戻す
            def apply():
                self.progress.configure(maximum=total, value=done)
                self._progress_text = f"{done} / {total} 台"
            self.after(0, apply)

        def worker():
            try:
                downloader.run(
                    login_id=login_id,
                    password=password,
                    date_from=d_from,
                    date_to=d_to,
                    save_dir=self.var_dir.get(),
                    vehicles=targets,
                    headless=not self.var_show.get(),
                    dup_mode=self.var_dup.get(),
                    vehicle_info=vehicle_info,
                    name_in_filename=name_in_filename,
                    stamp_opts=stamp_opts,
                    log=self.log,
                    progress=on_progress,
                )
                self._last_error = None
            except Exception as e:
                self.log(f"エラー: {e}")
                self._last_error = str(e)
            finally:
                self.after(0, self.on_done)

        self._last_error = None
        threading.Thread(target=worker, daemon=True).start()

    def _restore_run_button(self):
        """検索開始ボタンを通常状態へ戻す。
        検索中・番割取込中・ブラウザ未準備のときは戻さない (誤って押せないように)。
        """
        if self.running or self._hks_importing:
            return
        if getattr(self, "_chromium_ready", False):
            self.btn_run.configure(state="normal", text="検索開始")

    def _tick(self):
        """検索中の経過時間と件数（タスクバーの題名にも出す）。"""
        if not self.running:
            return
        sec = int(datetime.datetime.now().timestamp() - self._started_at)
        self.elapsed_label.configure(text=f"経過 {sec // 60}:{sec % 60:02d}　{self._progress_text}")
        self.title(f"{TITLE} - {self._progress_text}")
        self.after(1000, self._tick)

    def on_done(self):
        self.running = False
        self.progress.pack_forget()
        self.elapsed_label.pack_forget()
        self.title(TITLE)
        self.btn_hks.configure(state="normal")
        self.nb.tab(self.tab_settings, state="normal")
        self._restore_run_button()
        self._refresh_report_button()
        if getattr(self, "_last_error", None):
            self.set_status(f"処理中にエラーが発生しました: {self._last_error}", kind="error")
        else:
            self.set_status("完了しました。保存先フォルダーで確認してください", kind="success")


if __name__ == "__main__":
    # ビルド直後の起動チェック用。重い import (playwright/greenlet/pywinauto 等) は
    # このモジュールの読み込み時点で実行されるため、ここに到達できた＝同梱は正常。
    # GUIを開かず即終了する (build.py がこの終了コードで配布物の妥当性を確認する)。
    if "--smoke-test" in sys.argv:
        # 配布物の同梱漏れ検証。通常は遅延importされるモジュール
        # (pywinauto=番割取込, pdf_stamp=PDF書込 等) もここで読み込んで確認する。
        # 起動時importだけだとこれらの取りこぼしを検知できないため。
        import importlib
        mods = ["browser_setup", "downloader", "hks_reader", "pdf_stamp",
                "playwright.sync_api", "comtypes", "PIL.Image"]
        if sys.platform == "win32":
            mods += ["pywinauto", "pywinauto.uia_defines", "pywinauto.application",
                     "pywintypes", "pythoncom", "win32api",
                     # win32ui は pywinauto が実行時に使う。同梱漏れがあると
                     # 番割取込時に DLL load failed になるためここで検証する
                     "win32ui", "win32clipboard"]
        failed = []
        for m in mods:
            try:
                importlib.import_module(m)
            except Exception as e:
                failed.append(f"{m}: {e.__class__.__name__}: {e}")
        # UIA(番割取込)は comtypes が型ライブラリラッパを生成して初めて動く。
        # frozen exe では実行時生成ができないことがあるため、実際に初期化まで試して
        # 同梱漏れ/生成失敗をビルド時に検出する。
        if not failed and sys.platform == "win32":
            try:
                import hks_reader
                hks_reader.find_schedule_windows()  # Desktop(backend="uia") を実初期化
            except Exception as e:
                failed.append(f"uia-init: {e.__class__.__name__}: {e}")
        # console=False の exe では sys.stdout/stderr が None になり得る。
        # ビルド機が結果を確実に読めるよう、指定ファイルにも書き出す。
        out = os.environ.get("ETC_SMOKE_OUT")
        if out:
            try:
                with open(out, "w", encoding="utf-8") as f:
                    f.write("OK" if not failed else "FAILED\n" + "\n".join(failed))
            except Exception:
                pass
        sys.exit(1 if failed else 0)
    App().mainloop()

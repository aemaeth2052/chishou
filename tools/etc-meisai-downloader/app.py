# -*- coding: utf-8 -*-
"""ETC利用明細ダウンローダー GUI

タブ構成:
  メイン: 検索対象 / 期間 / 実行 / ログ
  設定  : ログイン情報 / PDF保存先 / 車両の新規登録
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
from tkinter import filedialog, messagebox, scrolledtext, ttk

# paths を最優先でimportして PLAYWRIGHT_BROWSERS_PATH を設定
# (この前に playwright が読まれると環境変数が効かなくなる)
from paths import config_path, migrate_old_data, user_data_dir  # noqa: I001

try:
    import ttkbootstrap as ttkb
    _BaseWindow = ttkb.Window
    HAS_TTKB = True
except ImportError:
    _BaseWindow = tk.Tk
    HAS_TTKB = False

try:
    from tkcalendar import Calendar as _TkCalendar
    from tkcalendar import DateEntry as _TkCalDateEntry  # noqa: F401
    HAS_DATEENTRY = True
except ImportError:
    HAS_DATEENTRY = False

import browser_setup
import downloader

BASE_DIR = Path(__file__).resolve().parent
migrate_old_data(BASE_DIR)
CONFIG_PATH = config_path()


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
    styles = {
        "primary": "primary",
        "success": "success",
        "warning": "warning",
        "info": "info",
        "danger": "danger-outline",
        "secondary": "secondary-outline",
        "default": None,
    }
    if HAS_TTKB and styles.get(style):
        return ttkb.Button(parent, text=text, command=command, bootstyle=styles[style], **kw)
    return ttk.Button(parent, text=text, command=command, **kw)


def _set_btn_style(btn, style):
    """ttkbootstrap環境ならbootstyleを動的に切替 (フォールバック環境では無視)"""
    if HAS_TTKB:
        try:
            btn.configure(bootstyle=style)
        except Exception:
            pass


class App(_BaseWindow):
    def __init__(self):
        if HAS_TTKB:
            super().__init__(themename="yeti")
        else:
            super().__init__()
        self.title("ETC利用明細ダウンローダー")
        self.geometry("800x760")
        self.minsize(720, 640)
        self.log_queue = queue.Queue()
        self.running = False
        self._hks_importing = False

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
        self.var_hks_status = tk.StringVar()
        self._update_hks_status()

        # ノートブック
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.tab_main = ttk.Frame(self.nb, padding=8)
        self.tab_settings = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_main, text="  メイン  ")
        self.nb.add(self.tab_settings, text="  設定  ")

        self._build_main_tab(self.tab_main)
        self._build_settings_tab(self.tab_settings)

        self._refresh_mode()
        self._refresh_list()
        self.after(100, self.poll_log)
        self._chromium_ready = False
        self._ensure_browser()

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

    # ============================================================ メインタブ
    def _build_main_tab(self, root):
        # --- 期間 (最初に決める。日常運用ではここから入力する) ---
        box2 = ttk.LabelFrame(root, text="検索期間 (過去62日以内)", padding=6)
        box2.pack(fill="x", pady=(0, 4))
        ttk.Label(box2, text="開始").grid(row=0, column=0, sticky="w")
        self._make_date_input(box2, self.var_from).grid(row=0, column=1, padx=4)
        ttk.Label(box2, text="〜 終了").grid(row=0, column=2, sticky="w")
        self._make_date_input(box2, self.var_to).grid(row=0, column=3, padx=4)
        # ショートカット: 「昨日：mm/dd(曜)」→「今月」の順
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        wd = "月火水木金土日"[yesterday.weekday()]
        ttk.Button(box2, text=f"昨日：{yesterday:%m/%d}({wd})",
                   command=self.set_yesterday).grid(row=0, column=4, padx=2)
        ttk.Button(box2, text="今月", command=self.set_this_month, width=5).grid(row=0, column=5, padx=2)

        # --- 検索対象 (横並び、幅を抑える) ---
        mode_row = ttk.Frame(root)
        mode_row.pack(fill="x", pady=(0, 4))
        ttk.Label(mode_row, text="検索対象:").pack(side="left")
        ttk.Radiobutton(
            mode_row, text="登録済みリストから複数選択", value="list",
            variable=self.var_mode, command=self._refresh_mode,
        ).pack(side="left", padx=8)
        ttk.Radiobutton(
            mode_row, text="単一車両を指定", value="single",
            variable=self.var_mode, command=self._refresh_mode,
        ).pack(side="left", padx=4)

        # 検索対象に応じた切替エリア (リスト or 単一車両)
        self.mode_area = ttk.Frame(root)
        self.mode_area.pack(fill="both", expand=True, pady=4)

        # --- リストモード: 登録済み車両 ---
        self.box_list = ttk.LabelFrame(
            self.mode_area, text="登録済み車両 (対象クリックでON/OFF / 行ダブルクリックで編集)",
            padding=6)
        toolbar = ttk.Frame(self.box_list)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="※列タイトルをクリックで並び替え",
                  foreground="#888").pack(side="left")
        _btn(toolbar, "全て解除", lambda: self._set_all(False), style="secondary").pack(side="right", padx=2)
        _btn(toolbar, "全てチェック", lambda: self._set_all(True), style="secondary").pack(side="right", padx=2)

        # Treeview (対象/車両番号/顧客/現場/運転手/備考)
        tree_frame = ttk.Frame(self.box_list)
        tree_frame.pack(fill="both", expand=True, pady=(4, 4))
        # 内部キーは互換性のため "dept" のまま (既存 config.json を壊さない)。
        # 表示上の扱いは「所属」をやめて「備考」(自由記入・空欄可) とする。
        cols = ("on", "number", "customer", "site", "driver", "dept")
        headers = {"on": "対象", "number": "車両番号", "customer": "顧客",
                   "site": "現場", "driver": "運転手", "dept": "備考"}
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8, selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=headers[c], command=lambda col=c: self._sort_by(col))
        self.tree.column("on", width=42, anchor="center", stretch=False)
        # 車両番号列は全角2文字分ほど広げる (68 → 100)
        self.tree.column("number", width=100, anchor="center", stretch=False)
        self.tree.column("customer", width=150, stretch=False)
        self.tree.column("site", width=180, stretch=True)
        self.tree.column("driver", width=90, stretch=False)
        self.tree.column("dept", width=100, stretch=False)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double)

        # 削除のみ (新規登録は設定タブ)
        action = ttk.Frame(self.box_list)
        action.pack(fill="x")
        _btn(action, "選択行を削除", self._delete_selected, style="danger").pack(side="left")
        ttk.Label(
            action,
            text="※新規登録は「設定」タブ / 顧客・現場・運転手はダブルクリックで編集",
            foreground="#888",
        ).pack(side="left", padx=8)

        # --- 単一モード: 1台指定 ---
        self.box_single = ttk.LabelFrame(self.mode_area, text="検索する車両 (1台のみ)", padding=8)
        ttk.Label(self.box_single, text="車両番号(下4桁)").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.box_single, textvariable=self.var_single_num, width=10).grid(row=0, column=1, padx=4)
        ttk.Label(self.box_single, text="備考").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.cb_single_dept = ttk.Combobox(
            self.box_single, textvariable=self.var_single_dept, width=18, values=[])
        self.cb_single_dept.grid(row=0, column=3, padx=4)
        ttk.Label(
            self.box_single,
            text="※この1台のみ検索します。登録リストには追加されません",
            foreground="#888",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # --- Hks番割の取込 (PDF名と按分レポートに顧客・現場を反映) ---
        hks_row = ttk.Frame(root)
        hks_row.pack(fill="x", pady=(0, 4))
        self.btn_hks = _btn(hks_row, "番割全体表示から取込", self.on_import_hks, style="primary")
        self.btn_hks.pack(side="left")
        ttk.Label(hks_row, textvariable=self.var_hks_status, foreground="#888").pack(side="left", padx=8)

        # --- 実行 ---
        runrow = ttk.Frame(root)
        runrow.pack(fill="x", pady=6)
        if HAS_TTKB:
            ttkb.Checkbutton(
                runrow, text="ブラウザの動きを表示する",
                variable=self.var_show, bootstyle="round-toggle",
            ).pack(side="left")
        else:
            ttk.Checkbutton(
                runrow, text="ブラウザの動きを表示する",
                variable=self.var_show,
            ).pack(side="left")
        self.btn_run = _btn(runrow, "検索開始", self.on_run, style="success", width=14)
        self.btn_run.pack(side="right", padx=4)
        _btn(runrow, "保存先を開く", lambda: open_folder(self.var_dir.get()), style="secondary").pack(side="right", padx=4)

        # --- ステータス帯 (重要メッセージを色付きで表示) ---
        self.status_bar = tk.Label(
            root, text="準備しています...", anchor="w",
            bg="#cce6ff", fg="#003366", padx=10, pady=6,
            font=("", 10, "bold"),
        )
        self.status_bar.pack(fill="x", pady=(6, 2))

        # --- ログ ---
        self.log_text = scrolledtext.ScrolledText(root, height=8, state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))

    # =========================================================== 設定タブ
    def _build_settings_tab(self, root):
        # --- ログイン情報 ---
        box1 = ttk.LabelFrame(root, text="ログイン情報 (ETC利用照会サービス)", padding=8)
        box1.pack(fill="x", pady=4)
        ttk.Label(box1, text="ユーザーID").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(box1, textvariable=self.var_id, width=30).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(box1, text="パスワード").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(box1, textvariable=self.var_pw, width=30, show="*").grid(row=1, column=1, sticky="w", padx=6)
        ttk.Checkbutton(
            box1, text="パスワードを保存する",
            variable=self.var_save_pw,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- PDF保存先 ---
        box3 = ttk.LabelFrame(root, text="PDF保存先", padding=8)
        box3.pack(fill="x", pady=4)
        ttk.Entry(box3, textvariable=self.var_dir, width=58).grid(row=0, column=0, sticky="we", padx=2)
        ttk.Button(box3, text="参照...", command=self.browse_dir).grid(row=0, column=1, padx=4)
        _btn(box3, "開く", lambda: open_folder(self.var_dir.get()), style="secondary").grid(row=0, column=2, padx=2)
        box3.columnconfigure(0, weight=1)

        # --- 同名ファイルの扱い ---
        dupbox = ttk.LabelFrame(root, text="同名のPDFがすでにあるとき", padding=8)
        dupbox.pack(fill="x", pady=4)
        for label, val in (
            ("連番を付けて保存 (例: 20260610_1499_2.pdf)", "rename"),
            ("上書きする", "overwrite"),
            ("スキップする (ダウンロードしない)", "skip"),
        ):
            ttk.Radiobutton(dupbox, text=label, value=val, variable=self.var_dup).pack(anchor="w")

        # --- PDFへの書き込み ---
        stampbox = ttk.LabelFrame(root, text="PDFへの書き込み (明細PDF下部に追記する項目)", padding=8)
        stampbox.pack(fill="x", pady=4)
        ttk.Checkbutton(stampbox, text="顧客名", variable=self.var_stamp_customer).pack(side="left", padx=6)
        ttk.Checkbutton(stampbox, text="現場名", variable=self.var_stamp_site).pack(side="left", padx=6)
        ttk.Checkbutton(stampbox, text="運転手", variable=self.var_stamp_driver).pack(side="left", padx=6)
        ttk.Label(stampbox, text="文字サイズ").pack(side="left", padx=(14, 2))
        ttk.Spinbox(stampbox, from_=6, to=72, increment=1, width=5,
                    textvariable=self.var_stamp_size).pack(side="left")
        ttk.Label(stampbox, text="pt").pack(side="left", padx=(2, 0))
        ttk.Label(stampbox, text="※情報がない車両・複数日検索では書き込みません",
                  foreground="#888").pack(side="left", padx=10)

        # --- 車両の新規登録 ---
        regbox = ttk.LabelFrame(root, text="車両の新規登録", padding=8)
        regbox.pack(fill="x", pady=4)
        # 車両番号は必須 → ラベルに赤い「*」を添える
        num_lbl = ttk.Frame(regbox)
        num_lbl.grid(row=0, column=0, sticky="w")
        ttk.Label(num_lbl, text="車両番号(下4桁)").pack(side="left")
        ttk.Label(num_lbl, text="*", foreground="red").pack(side="left", padx=(2, 0))
        vcmd = (self.register(self._validate_plate_number), "%P")
        ttk.Entry(regbox, textvariable=self.var_reg_num, width=10,
                  validate="key", validatecommand=vcmd).grid(row=0, column=1, padx=4)
        ttk.Label(regbox, text="備考").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.cb_reg_dept = ttk.Combobox(regbox, textvariable=self.var_reg_dept, width=18, values=[])
        self.cb_reg_dept.grid(row=0, column=3, padx=4)
        _btn(regbox, "登録", self._register_vehicle, style="primary").grid(row=0, column=4, padx=8)
        ttk.Label(
            regbox,
            text="※登録した車両は「メイン」タブの一覧に表示されます。\n"
                 "  変更したいときは一覧から削除してから再登録してください。",
            foreground="#888",
            justify="left",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        # --- 車両リストの受け渡し ---
        iobox = ttk.LabelFrame(root, text="車両リストの受け渡し (別PCへの配布用)", padding=8)
        iobox.pack(fill="x", pady=4)
        _btn(iobox, "CSVに書き出す", self._export_vehicles, style="secondary").pack(side="left", padx=4)
        _btn(iobox, "CSVを読み込む", self._import_vehicles, style="secondary").pack(side="left", padx=4)
        ttk.Label(
            iobox,
            text="形式: 1行目ヘッダ「備考,車両番号」。Excelでの編集・一括作成も可",
            foreground="#888",
        ).pack(side="left", padx=8)

        # 設定保存ボタン
        save_row = ttk.Frame(root)
        save_row.pack(fill="x", pady=8)
        _btn(save_row, "設定を保存", self._save_now, style="primary").pack(side="right")

    # ============================================================ 共通処理
    def _refresh_mode(self):
        if self.var_mode.get() == "list":
            self.box_single.pack_forget()
            self.box_list.pack(in_=self.mode_area, fill="both", expand=True)
        else:
            self.box_list.pack_forget()
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
                self.tree.insert("", "end", iid=str(idx), values=(
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
            self.var_hks_status.set("未取込 (業務システムの番割予定表を開いた状態で押してください)")

    def on_import_hks(self):
        self.btn_hks.configure(state="disabled", text="取込中...")
        _set_btn_style(self.btn_hks, "warning")
        # 取込中は検索開始を押せないようにする
        self._hks_importing = True
        self.btn_run.configure(state="disabled")
        self.set_status("Hks番割を読み取っています...", kind="busy")

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
                        self.set_status("Hks番割の取込をキャンセルしました", kind="info")
                        return
                    metas = [metas[i] for i in chosen]
                self.log(f"{len(metas)} 画面を取り込みます")
                records = hks_reader.read_windows(metas, log=self.log)
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
                self.set_status(f"Hks番割の取込が完了しました ({matched} 台に反映)", kind="success")
                if unmatched:
                    self.log(f"※番割にあるが未登録の車両: {', '.join(sorted(unmatched, key=lambda x: x.zfill(4)))}")
                if multi_list:
                    self.log(f"⚠ 複数現場に割り当てられた車両が {len(multi_list)} 件あります")
                    self.after(0, lambda: messagebox.showwarning(
                        "複数現場の車両",
                        "同じ車両が複数の現場に割り当てられています。\n"
                        "PDF名は「複数現場」、按分レポートには全現場を記録します。\n\n"
                        + "\n".join(multi_list)))
            except ImportError:
                self.log("エラー: pywinauto がインストールされていません。setup.bat を再実行してください")
                self.set_status("ライブラリが不足しています。setup.bat を再実行してください", kind="error")
            except Exception as e:
                self.log(f"Hks取込エラー: {e}")
                self.set_status(f"Hks取込に失敗しました: {e}", kind="error")
            finally:
                def restore():
                    self.btn_hks.configure(state="normal", text="番割全体表示から取込")
                    _set_btn_style(self.btn_hks, "primary")
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
        """文字サイズ設定を安全に読む (空欄・異常値は既定27、6〜72に丸め)"""
        try:
            v = int(self.var_stamp_size.get())
        except Exception:
            v = 27
        return max(6, min(v, 72))

    def _save_now(self):
        save_config(self._current_config())
        self._refresh_settings_badge()
        messagebox.showinfo("保存", "設定を保存しました")

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
            "hks_records": self.hks_records,
            "hks_imported_at": self.hks_imported_at,
            "single": {
                "dept": self.var_single_dept.get().strip(),
                "number": self.var_single_num.get().strip(),
            },
        }

    def _make_date_input(self, parent, var):
        """日付入力欄。直接入力できる Entry と、カレンダーを開く📅ボタンを並べる。

        旧実装の tkcalendar.DateEntry は枠内でマウスカーソルが消え、
        カレンダーを開くクリック範囲も狭かったため、両立できるよう作り替えた。
        tkcalendar が無い環境では Entry だけにフォールバックする。
        """
        frame = ttk.Frame(parent)
        entry = ttk.Entry(frame, textvariable=var, width=11)
        entry.pack(side="left")
        if HAS_DATEENTRY:
            # 押しやすい大きさのボタンでカレンダーを開く
            ttk.Button(
                frame, text="📅", width=3,
                command=lambda: self._open_calendar_popup(entry, var),
            ).pack(side="left", padx=(2, 0))
        return frame

    def _open_calendar_popup(self, anchor, var):
        """anchor ウィジェットの真下にカレンダーを開き、選んだ日付を var に入れる"""
        try:
            cur = self.parse_date(var.get(), "")
        except Exception:
            cur = datetime.date.today()

        top = tk.Toplevel(self)
        # 位置確定までは隠しておく (一瞬左上に出てから移動する見え方を防ぐ)
        top.withdraw()
        top.transient(self)
        top.title("日付を選択")
        top.resizable(False, False)
        cal = _TkCalendar(
            top, selectmode="day", locale="ja_JP",
            date_pattern="yyyy/mm/dd", showweeknumbers=False,
            year=cur.year, month=cur.month, day=cur.day,
        )
        cal.pack(padx=8, pady=8)

        def choose(_event=None):
            var.set(cal.get_date())
            top.destroy()

        # 日付をダブルクリック / 「決定」/ Enter で確定 (単クリックの月移動では閉じない)
        cal.bind("<Double-1>", choose)
        ttk.Button(top, text="決定", command=choose).pack(pady=(0, 8))
        top.bind("<Return>", choose)
        top.bind("<Escape>", lambda e: top.destroy())

        # anchor の真下に配置してから表示する
        top.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height()
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.deiconify()
        top.grab_set()

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

    def _refresh_settings_badge(self):
        """ログイン情報が未入力なら設定タブのラベルに ● を付ける"""
        need = not (self.var_id.get().strip() and self.var_pw.get())
        try:
            self.nb.tab(self.tab_settings, text="  設定 ●  " if need else "  設定  ")
        except Exception:
            pass

    def set_status(self, text, kind="info"):
        """ステータス帯を更新する。kind: info / busy / success / warning / error"""
        palette = {
            "info":    ("#cce6ff", "#003366"),
            "busy":    ("#fff3cd", "#856404"),
            "success": ("#d4edda", "#155724"),
            "warning": ("#fff3cd", "#856404"),
            "error":   ("#f8d7da", "#721c24"),
        }
        bg, fg = palette.get(kind, palette["info"])
        def apply():
            self.status_bar.configure(text=text, bg=bg, fg=fg)
        self.after(0, apply)

    def log(self, msg):
        self.log_queue.put(msg)

    def poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.poll_log)

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
        self.btn_run.configure(state="disabled", text="検索中...")
        self.set_status(f"ETC明細をダウンロード中... ({len(targets)} 台)", kind="busy")
        self.log(f"=== 開始: {d_from} 〜 {d_to} / 対象 {len(targets)} 台 ===")

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

    def on_done(self):
        self.running = False
        self._restore_run_button()
        if getattr(self, "_last_error", None):
            self.set_status(f"処理中にエラーが発生しました: {self._last_error}", kind="error")
        else:
            self.set_status("完了しました。保存先フォルダで確認してください", kind="success")


if __name__ == "__main__":
    App().mainloop()

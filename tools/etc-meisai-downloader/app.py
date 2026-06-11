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

try:
    import ttkbootstrap as ttkb
    _BaseWindow = ttkb.Window
    HAS_TTKB = True
except ImportError:
    _BaseWindow = tk.Tk
    HAS_TTKB = False

import browser_setup
import downloader
from paths import config_path, migrate_old_data, user_data_dir

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
        "danger": "danger-outline",
        "secondary": "secondary-outline",
        "default": None,
    }
    if HAS_TTKB and styles.get(style):
        return ttkb.Button(parent, text=text, command=command, bootstyle=styles[style], **kw)
    return ttk.Button(parent, text=text, command=command, **kw)


class App(_BaseWindow):
    def __init__(self):
        if HAS_TTKB:
            super().__init__(themename="cosmo")
        else:
            super().__init__()
        self.title("ETC利用明細ダウンローダー")
        self.geometry("780x620")
        self.minsize(700, 540)
        self.log_queue = queue.Queue()
        self.running = False

        cfg = load_config()
        today = datetime.date.today()
        first = today.replace(day=1)

        # 旧 "name" は "dept" に移行
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
        self.var_from = tk.StringVar(value=first.strftime("%Y/%m/%d"))
        self.var_to = tk.StringVar(value=today.strftime("%Y/%m/%d"))
        self.var_sort = tk.StringVar(value=cfg.get("sort", "dept"))

        self.var_dup = tk.StringVar(value=cfg.get("dup_mode", "rename"))

        single = cfg.get("single", {})
        self.var_single_dept = tk.StringVar(value=single.get("dept", ""))
        self.var_single_num = tk.StringVar(value=single.get("number", ""))

        self.var_reg_dept = tk.StringVar()
        self.var_reg_num = tk.StringVar()

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
        self.log("ブラウザの準備状況を確認しています...")

        def on_ready():
            self._chromium_ready = True
            self.after(0, lambda: self.btn_run.configure(
                state="normal", text="▶ 実行" if HAS_TTKB else "実行"))
            self.log("実行できる状態になりました")

        def on_fail():
            self.after(0, lambda: self.btn_run.configure(state="disabled", text="ブラウザ未準備"))
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
        self.box_list = ttk.LabelFrame(self.mode_area, text="登録済み車両 (クリックで対象ON/OFF)", padding=6)
        # ソート
        sort_row = ttk.Frame(self.box_list)
        sort_row.pack(fill="x")
        ttk.Label(sort_row, text="並び順:").pack(side="left")
        for label, val in (("所属順", "dept"), ("車両番号順", "number"), ("登録順", "added")):
            ttk.Radiobutton(
                sort_row, text=label, value=val, variable=self.var_sort,
                command=self._refresh_list,
            ).pack(side="left", padx=4)
        _btn(sort_row, "全て解除", lambda: self._set_all(False), style="secondary").pack(side="right", padx=2)
        _btn(sort_row, "全てチェック", lambda: self._set_all(True), style="secondary").pack(side="right", padx=2)

        # Treeview
        tree_frame = ttk.Frame(self.box_list)
        tree_frame.pack(fill="both", expand=True, pady=(4, 4))
        cols = ("on", "dept", "number")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8, selectmode="browse")
        self.tree.heading("on", text="対象")
        self.tree.heading("dept", text="所属")
        self.tree.heading("number", text="車両番号")
        self.tree.column("on", width=50, anchor="center", stretch=False)
        self.tree.column("dept", width=120, stretch=False)
        self.tree.column("number", width=100, anchor="center", stretch=False)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)

        # 削除のみ (新規登録は設定タブ)
        action = ttk.Frame(self.box_list)
        action.pack(fill="x")
        _btn(action, "選択行を削除", self._delete_selected, style="danger").pack(side="left")
        ttk.Label(
            action,
            text="※新規登録・編集は「設定」タブから",
            foreground="#888",
        ).pack(side="left", padx=8)

        # --- 単一モード: 1台指定 ---
        self.box_single = ttk.LabelFrame(self.mode_area, text="検索する車両 (1台のみ)", padding=8)
        ttk.Label(self.box_single, text="所属").grid(row=0, column=0, sticky="w")
        self.cb_single_dept = ttk.Combobox(
            self.box_single, textvariable=self.var_single_dept, width=18, values=[])
        self.cb_single_dept.grid(row=0, column=1, padx=4)
        ttk.Label(self.box_single, text="車両番号(下4桁)").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(self.box_single, textvariable=self.var_single_num, width=10).grid(row=0, column=3, padx=4)
        ttk.Label(
            self.box_single,
            text="※この1台のみ検索します。登録リストには追加されません",
            foreground="#888",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # --- 期間 ---
        box2 = ttk.LabelFrame(root, text="検索期間 (YYYY/MM/DD ※過去62日以内)", padding=6)
        box2.pack(fill="x", pady=4)
        ttk.Label(box2, text="開始").grid(row=0, column=0, sticky="w")
        ttk.Entry(box2, textvariable=self.var_from, width=12).grid(row=0, column=1, padx=4)
        ttk.Label(box2, text="〜 終了").grid(row=0, column=2, sticky="w")
        ttk.Entry(box2, textvariable=self.var_to, width=12).grid(row=0, column=3, padx=4)
        ttk.Button(box2, text="今月", command=self.set_this_month, width=5).grid(row=0, column=4, padx=2)
        ttk.Button(box2, text="先月", command=self.set_last_month, width=5).grid(row=0, column=5, padx=2)
        ttk.Button(box2, text="昨日", command=self.set_yesterday, width=5).grid(row=0, column=6, padx=2)

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
        self.btn_run = _btn(runrow, "▶ 実行" if HAS_TTKB else "実行", self.on_run, style="success", width=14)
        self.btn_run.pack(side="right", padx=4)
        _btn(runrow, "📂 保存先を開く", lambda: open_folder(self.var_dir.get()), style="secondary").pack(side="right", padx=4)

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
            box1, text="パスワードを保存する (config.json に平文保存)",
            variable=self.var_save_pw,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- PDF保存先 ---
        box3 = ttk.LabelFrame(root, text="PDF保存先", padding=8)
        box3.pack(fill="x", pady=4)
        ttk.Entry(box3, textvariable=self.var_dir, width=58).grid(row=0, column=0, sticky="we", padx=2)
        ttk.Button(box3, text="参照...", command=self.browse_dir).grid(row=0, column=1, padx=4)
        _btn(box3, "📂 開く", lambda: open_folder(self.var_dir.get()), style="secondary").grid(row=0, column=2, padx=2)
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

        # --- 車両の新規登録 ---
        regbox = ttk.LabelFrame(root, text="車両の新規登録", padding=8)
        regbox.pack(fill="x", pady=4)
        ttk.Label(regbox, text="所属").grid(row=0, column=0, sticky="w")
        self.cb_reg_dept = ttk.Combobox(regbox, textvariable=self.var_reg_dept, width=18, values=[])
        self.cb_reg_dept.grid(row=0, column=1, padx=4)
        ttk.Label(regbox, text="車両番号(下4桁)").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(regbox, textvariable=self.var_reg_num, width=10).grid(row=0, column=3, padx=4)
        _btn(regbox, "＋ 登録", self._register_vehicle, style="primary").grid(row=0, column=4, padx=8)
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
        _btn(iobox, "📤 CSVに書き出す", self._export_vehicles, style="secondary").pack(side="left", padx=4)
        _btn(iobox, "📥 CSVを読み込む", self._import_vehicles, style="secondary").pack(side="left", padx=4)
        ttk.Label(
            iobox,
            text="形式: 1行目ヘッダ「所属,車両番号」。Excelでの編集・一括作成も可",
            foreground="#888",
        ).pack(side="left", padx=8)

        # 設定保存ボタン
        save_row = ttk.Frame(root)
        save_row.pack(fill="x", pady=8)
        _btn(save_row, "💾 設定を保存", self._save_now, style="primary").pack(side="right")

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

    def _refresh_list(self):
        key = self.var_sort.get()
        if key == "dept":
            order = sorted(
                range(len(self.vehicles)),
                key=lambda i: (self.vehicles[i].get("dept", ""), self.vehicles[i].get("number", "")),
            )
        elif key == "number":
            def num_key(i):
                n = self.vehicles[i].get("number", "")
                try:
                    return (0, int(n))
                except ValueError:
                    return (1, n)
            order = sorted(range(len(self.vehicles)), key=num_key)
        else:
            order = list(range(len(self.vehicles)))

        if hasattr(self, "tree"):
            self.tree.delete(*self.tree.get_children())
            for idx in order:
                v = self.vehicles[idx]
                mark = "☑" if v.get("enabled", True) else "☐"
                self.tree.insert("", "end", iid=str(idx), values=(mark, v.get("dept", ""), v.get("number", "")))

        depts = self._depts()
        if hasattr(self, "cb_reg_dept"):
            self.cb_reg_dept["values"] = depts
        if hasattr(self, "cb_single_dept"):
            self.cb_single_dept["values"] = depts

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

    def _register_vehicle(self):
        dept = self.var_reg_dept.get().strip()
        number = self.var_reg_num.get().strip()
        if not dept or not number:
            messagebox.showerror("登録エラー", "所属と車両番号の両方を入力してください")
            return
        if not number.isdigit() or len(number) > 4:
            messagebox.showerror("登録エラー", "車両番号はナンバーの下4桁の数字で入力してください")
            return
        if any(v.get("number") == number and v.get("dept") == dept for v in self.vehicles):
            messagebox.showerror("登録エラー", "同じ所属・車両番号の車両がすでに登録されています")
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
            w.writerow(["所属", "車両番号"])
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
            if dept == "所属":  # ヘッダ行
                continue
            if not number.isdigit() or len(number) > 4:
                bad.append(f"{lineno}行目: {dept},{number}")
                continue
            if any(v.get("number") == number and v.get("dept") == dept for v in self.vehicles):
                skipped += 1
                continue
            self.vehicles.append({"enabled": True, "dept": dept, "number": number})
            added += 1
        self._refresh_list()
        msg = f"追加 {added} 台 / 重複スキップ {skipped} 台"
        if bad:
            msg += f"\n形式エラー {len(bad)} 件:\n" + "\n".join(bad[:5])
        messagebox.showinfo("読み込み結果", msg)

    def _save_now(self):
        save_config(self._current_config())
        messagebox.showinfo("保存", "設定を保存しました")

    def _current_config(self):
        return {
            "login_id": self.var_id.get().strip(),
            "password": self.var_pw.get() if self.var_save_pw.get() else "",
            "save_dir": self.var_dir.get(),
            "headless": not self.var_show.get(),
            "vehicles": self.vehicles,
            "mode": self.var_mode.get(),
            "sort": self.var_sort.get(),
            "dup_mode": self.var_dup.get(),
            "single": {
                "dept": self.var_single_dept.get().strip(),
                "number": self.var_single_num.get().strip(),
            },
        }

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
        if self.running:
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

        self.running = True
        self.btn_run.configure(state="disabled", text="実行中...")
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
                    log=self.log,
                )
            except Exception as e:
                self.log(f"エラー: {e}")
            finally:
                self.after(0, self.on_done)

        threading.Thread(target=worker, daemon=True).start()

    def on_done(self):
        self.running = False
        self.btn_run.configure(state="normal", text="▶ 実行" if HAS_TTKB else "実行")


if __name__ == "__main__":
    App().mainloop()

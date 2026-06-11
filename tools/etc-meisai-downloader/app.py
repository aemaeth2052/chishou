# -*- coding: utf-8 -*-
"""ETC利用明細ダウンローダー GUI

車両を登録(所属+車両番号)しておき、リストから選択 or 単一車両指定で
指定期間の利用明細PDFをまとめてダウンロードする。
"""

import datetime
import json
import queue
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

import downloader

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


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


def _btn(parent, text, command, style="default", **kw):
    """ttkbootstrap があればスタイル付きボタン、無ければ標準ボタン"""
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
        self.geometry("820x860")
        self.minsize(720, 700)
        self.log_queue = queue.Queue()
        self.running = False

        cfg = load_config()
        today = datetime.date.today()
        first = today.replace(day=1)

        # 登録済み車両: [{"enabled": bool, "dept": str, "number": str}]
        # 旧バージョンの "name" キーは "dept" に移行する
        self.vehicles = []
        for v in cfg.get("vehicles", []):
            self.vehicles.append({
                "enabled": v.get("enabled", True),
                "dept": v.get("dept", v.get("name", "")),
                "number": v.get("number", ""),
            })

        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        # --- ログイン情報 ---
        box1 = ttk.LabelFrame(outer, text="ログイン情報 (ETC利用照会サービス)", padding=8)
        box1.pack(fill="x", pady=4)
        ttk.Label(box1, text="ユーザーID").grid(row=0, column=0, sticky="w")
        self.var_id = tk.StringVar(value=cfg.get("login_id", ""))
        ttk.Entry(box1, textvariable=self.var_id, width=30).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(box1, text="パスワード").grid(row=1, column=0, sticky="w")
        self.var_pw = tk.StringVar(value=cfg.get("password", ""))
        ttk.Entry(box1, textvariable=self.var_pw, width=30, show="*").grid(row=1, column=1, sticky="w", padx=6)
        self.var_save_pw = tk.BooleanVar(value=bool(cfg.get("password")))
        ttk.Checkbutton(
            box1, text="パスワードを保存する(このPC内の config.json に平文保存)",
            variable=self.var_save_pw,
        ).grid(row=2, column=0, columnspan=2, sticky="w")

        # --- 検索対象モード ---
        box_mode = ttk.LabelFrame(outer, text="検索対象", padding=8)
        box_mode.pack(fill="x", pady=4)
        self.var_mode = tk.StringVar(value=cfg.get("mode", "list"))
        ttk.Radiobutton(
            box_mode, text="登録済みリストから複数選択", value="list",
            variable=self.var_mode, command=self._refresh_mode,
        ).pack(side="left", padx=8)
        ttk.Radiobutton(
            box_mode, text="単一車両を指定", value="single",
            variable=self.var_mode, command=self._refresh_mode,
        ).pack(side="left", padx=8)

        # --- リストモード: 登録済み車両 ---
        self.box_list = ttk.LabelFrame(outer, text="登録済み車両 (チェックを入れたものを検索)", padding=8)
        self._build_list_section(self.box_list)

        # --- 単一モード: 1台指定 ---
        self.box_single = ttk.LabelFrame(outer, text="検索する車両 (1台のみ)", padding=8)
        self._build_single_section(self.box_single, cfg)

        # --- 期間 ---
        box2 = ttk.LabelFrame(outer, text="検索期間 (YYYY/MM/DD ※過去62日以内)", padding=8)
        box2.pack(fill="x", pady=4)
        ttk.Label(box2, text="開始日").grid(row=0, column=0, sticky="w")
        self.var_from = tk.StringVar(value=first.strftime("%Y/%m/%d"))
        ttk.Entry(box2, textvariable=self.var_from, width=14).grid(row=0, column=1, padx=6)
        ttk.Label(box2, text="終了日").grid(row=0, column=2, sticky="w")
        self.var_to = tk.StringVar(value=today.strftime("%Y/%m/%d"))
        ttk.Entry(box2, textvariable=self.var_to, width=14).grid(row=0, column=3, padx=6)
        ttk.Button(box2, text="今月", command=self.set_this_month).grid(row=0, column=4, padx=4)
        ttk.Button(box2, text="先月", command=self.set_last_month).grid(row=0, column=5, padx=4)
        ttk.Button(box2, text="昨日", command=self.set_yesterday).grid(row=0, column=6, padx=4)

        # --- 保存先 ---
        box3 = ttk.LabelFrame(outer, text="PDF保存先", padding=8)
        box3.pack(fill="x", pady=4)
        self.var_dir = tk.StringVar(value=cfg.get("save_dir", str(Path.home() / "Documents" / "ETC明細")))
        ttk.Entry(box3, textvariable=self.var_dir, width=58).grid(row=0, column=0, sticky="we", padx=2)
        ttk.Button(box3, text="参照...", command=self.browse_dir).grid(row=0, column=1, padx=4)

        # --- 実行 ---
        box4 = ttk.Frame(outer)
        box4.pack(fill="x", pady=6)
        self.var_show = tk.BooleanVar(value=not cfg.get("headless", False))
        if HAS_TTKB:
            ttkb.Checkbutton(
                box4, text="ブラウザの動きを表示する(初回は表示推奨)",
                variable=self.var_show, bootstyle="round-toggle",
            ).pack(side="left")
        else:
            ttk.Checkbutton(box4, text="ブラウザの動きを表示する(初回は表示推奨)", variable=self.var_show).pack(side="left")
        self.btn_run = _btn(box4, "▶ 実行" if HAS_TTKB else "実行", self.on_run, style="success", width=18)
        self.btn_run.pack(side="right")

        # --- ログ ---
        self.log_text = scrolledtext.ScrolledText(outer, height=10, state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=4)

        self._refresh_mode()
        self._refresh_list()
        self.after(100, self.poll_log)

    # ================================================== リストセクション
    def _build_list_section(self, parent):
        # 上段: ソートと一括操作
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="並び順:").pack(side="left")
        self.var_sort = tk.StringVar(value="dept")
        for label, val in (("所属順", "dept"), ("車両番号順", "number"), ("登録順", "added")):
            ttk.Radiobutton(
                toolbar, text=label, value=val, variable=self.var_sort,
                command=self._refresh_list,
            ).pack(side="left", padx=4)
        _btn(toolbar, "全てチェック", lambda: self._set_all(True), style="secondary").pack(side="right", padx=2)
        _btn(toolbar, "全て解除", lambda: self._set_all(False), style="secondary").pack(side="right", padx=2)

        # 中段: Treeview で表示専用の一覧
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, pady=4)
        cols = ("on", "dept", "number")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8, selectmode="browse")
        self.tree.heading("on", text="対象")
        self.tree.heading("dept", text="所属")
        self.tree.heading("number", text="車両番号")
        self.tree.column("on", width=50, anchor="center", stretch=False)
        self.tree.column("dept", width=200)
        self.tree.column("number", width=100, anchor="center")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # 行クリックで対象チェックをトグル
        self.tree.bind("<Button-1>", self._on_tree_click)

        # 下段: 削除と新規登録フォーム
        action = ttk.Frame(parent)
        action.pack(fill="x", pady=(4, 6))
        _btn(action, "選択行を削除", self._delete_selected, style="danger").pack(side="left")
        ttk.Label(
            action,
            text="※登録内容を変更したいときは、削除してから再登録してください",
            foreground="#888",
        ).pack(side="left", padx=8)

        regbox = ttk.LabelFrame(parent, text="新規登録", padding=6)
        regbox.pack(fill="x")
        ttk.Label(regbox, text="所属").grid(row=0, column=0, sticky="w")
        self.var_reg_dept = tk.StringVar()
        self.cb_reg_dept = ttk.Combobox(regbox, textvariable=self.var_reg_dept, width=24, values=[])
        self.cb_reg_dept.grid(row=0, column=1, padx=4)
        ttk.Label(regbox, text="車両番号(下4桁)").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.var_reg_num = tk.StringVar()
        ttk.Entry(regbox, textvariable=self.var_reg_num, width=10).grid(row=0, column=3, padx=4)
        _btn(regbox, "＋ 登録", self._register_vehicle, style="primary").grid(row=0, column=4, padx=8)

        self.box_list.pack(fill="both", expand=True, pady=4)

    # ================================================== 単一モードセクション
    def _build_single_section(self, parent, cfg):
        single = cfg.get("single", {})
        ttk.Label(parent, text="所属").grid(row=0, column=0, sticky="w")
        self.var_single_dept = tk.StringVar(value=single.get("dept", ""))
        self.cb_single_dept = ttk.Combobox(parent, textvariable=self.var_single_dept, width=24, values=[])
        self.cb_single_dept.grid(row=0, column=1, padx=4)
        ttk.Label(parent, text="車両番号(下4桁)").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.var_single_num = tk.StringVar(value=single.get("number", ""))
        ttk.Entry(parent, textvariable=self.var_single_num, width=10).grid(row=0, column=3, padx=4)
        ttk.Label(
            parent,
            text="※この1台のみ検索します。登録リストには追加されません",
            foreground="#888",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

    # ================================================== モード切替
    def _refresh_mode(self):
        if self.var_mode.get() == "list":
            self.box_single.forget()
            self.box_list.pack(fill="both", expand=True, pady=4)
        else:
            self.box_list.forget()
            self.box_single.pack(fill="x", pady=4)
            # 所属候補を反映
            self.cb_single_dept["values"] = sorted({v["dept"] for v in self.vehicles if v.get("dept")})

    # ================================================== リスト操作
    def _depts(self):
        return sorted({v["dept"] for v in self.vehicles if v.get("dept")})

    def _refresh_list(self):
        # ソート
        key = self.var_sort.get()
        if key == "dept":
            self._display_order = sorted(
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
            self._display_order = sorted(range(len(self.vehicles)), key=num_key)
        else:
            self._display_order = list(range(len(self.vehicles)))

        # 描画
        self.tree.delete(*self.tree.get_children())
        for idx in self._display_order:
            v = self.vehicles[idx]
            mark = "☑" if v.get("enabled", True) else "☐"
            self.tree.insert("", "end", iid=str(idx), values=(mark, v.get("dept", ""), v.get("number", "")))

        # 所属候補を最新化
        depts = self._depts()
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

    # ================================================== 期間ショートカット
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

    # ================================================== ログ
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

    # ================================================== 実行
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
                raise ValueError("ユーザーIDとパスワードを入力してください")

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

        # 保存
        cfg = {
            "login_id": login_id,
            "password": password if self.var_save_pw.get() else "",
            "save_dir": self.var_dir.get(),
            "headless": not self.var_show.get(),
            "vehicles": self.vehicles,
            "mode": mode,
            "single": {
                "dept": self.var_single_dept.get().strip(),
                "number": self.var_single_num.get().strip(),
            },
        }
        save_config(cfg)

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

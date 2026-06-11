# -*- coding: utf-8 -*-
"""ETC利用明細ダウンローダー GUI

車両を登録(名前+車両番号)しておき、チェックを入れた車両だけ
指定期間で検索して利用明細PDFをまとめてダウンロードする。
"""

import datetime
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ttkbootstrap があればモダンテーマを適用 (無くても従来の見た目で動く)
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


class VehicleRow:
    """車両1台分の入力行 (チェックボックス + 名前 + 車両番号 + 削除ボタン)"""

    def __init__(self, parent, on_delete, name="", number="", enabled=True):
        self.frame = ttk.Frame(parent)
        self.var_on = tk.BooleanVar(value=enabled)
        self.var_name = tk.StringVar(value=name)
        self.var_num = tk.StringVar(value=number)
        ttk.Checkbutton(self.frame, variable=self.var_on).pack(side="left")
        ttk.Label(self.frame, text="名前").pack(side="left")
        ttk.Entry(self.frame, textvariable=self.var_name, width=20).pack(side="left", padx=4)
        ttk.Label(self.frame, text="車両番号(下4桁)").pack(side="left")
        ttk.Entry(self.frame, textvariable=self.var_num, width=8).pack(side="left", padx=4)
        if HAS_TTKB:
            ttkb.Button(
                self.frame, text="削除", width=4,
                command=lambda: on_delete(self), bootstyle="danger-outline",
            ).pack(side="left", padx=4)
        else:
            ttk.Button(self.frame, text="削除", width=4, command=lambda: on_delete(self)).pack(side="left", padx=4)
        self.frame.pack(fill="x", pady=2)

    def to_dict(self):
        return {
            "enabled": self.var_on.get(),
            "name": self.var_name.get().strip(),
            "number": self.var_num.get().strip(),
        }

    def destroy(self):
        self.frame.destroy()


class App(_BaseWindow):
    def __init__(self):
        if HAS_TTKB:
            super().__init__(themename="cosmo")
        else:
            super().__init__()
        self.title("ETC利用明細ダウンローダー")
        self.geometry("760x760")
        self.minsize(640, 600)
        self.log_queue = queue.Queue()
        self.running = False
        self.rows = []

        cfg = load_config()
        today = datetime.date.today()
        first = today.replace(day=1)

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

        # --- 車両一覧 ---
        box_v = ttk.LabelFrame(outer, text="車両 (チェックしたものだけダウンロードします)", padding=8)
        box_v.pack(fill="both", expand=True, pady=4)
        toolbar = ttk.Frame(box_v)
        toolbar.pack(fill="x")
        if HAS_TTKB:
            ttkb.Button(toolbar, text="＋ 車両を追加", command=self.add_row, bootstyle="primary").pack(side="left")
            ttkb.Button(toolbar, text="全てチェック", command=lambda: self._set_all(True), bootstyle="secondary-outline").pack(side="left", padx=4)
            ttkb.Button(toolbar, text="全て解除", command=lambda: self._set_all(False), bootstyle="secondary-outline").pack(side="left")
        else:
            ttk.Button(toolbar, text="車両を追加", command=self.add_row).pack(side="left")
            ttk.Button(toolbar, text="全てチェック", command=lambda: self._set_all(True)).pack(side="left", padx=4)
            ttk.Button(toolbar, text="全て解除", command=lambda: self._set_all(False)).pack(side="left")

        # 車両リスト (スクロール可能エリア)
        canvas = tk.Canvas(box_v, highlightthickness=0, height=180)
        scroll = ttk.Scrollbar(box_v, orient="vertical", command=canvas.yview)
        self.list_frame = ttk.Frame(canvas)
        self.list_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, pady=4)
        scroll.pack(side="right", fill="y")

        for v in cfg.get("vehicles", []):
            self.add_row(v.get("name", ""), v.get("number", ""), v.get("enabled", True))
        if not self.rows:
            self.add_row()

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

        # --- オプション + 実行 ---
        box4 = ttk.Frame(outer)
        box4.pack(fill="x", pady=6)
        self.var_show = tk.BooleanVar(value=not cfg.get("headless", False))
        if HAS_TTKB:
            ttkb.Checkbutton(
                box4, text="ブラウザの動きを表示する(初回は表示推奨)",
                variable=self.var_show, bootstyle="round-toggle",
            ).pack(side="left")
            self.btn_run = ttkb.Button(
                box4, text="▶ 実行", command=self.on_run, width=18, bootstyle="success",
            )
        else:
            ttk.Checkbutton(box4, text="ブラウザの動きを表示する(初回は表示推奨)", variable=self.var_show).pack(side="left")
            self.btn_run = ttk.Button(box4, text="実行", command=self.on_run, width=16)
        self.btn_run.pack(side="right")

        # --- ログ ---
        self.log_text = scrolledtext.ScrolledText(outer, height=12, state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=4)

        self.after(100, self.poll_log)

    # 車両リスト操作
    def add_row(self, name="", number="", enabled=True):
        row = VehicleRow(self.list_frame, self.delete_row, name=name, number=number, enabled=enabled)
        self.rows.append(row)

    def delete_row(self, row):
        row.destroy()
        self.rows.remove(row)

    def _set_all(self, value):
        for r in self.rows:
            r.var_on.set(value)

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
                raise ValueError("ユーザーIDとパスワードを入力してください")
        except ValueError as e:
            messagebox.showerror("入力エラー", str(e))
            return

        all_vehicles = [r.to_dict() for r in self.rows]
        # 保存(全車両分)
        cfg = {
            "login_id": login_id,
            "password": password if self.var_save_pw.get() else "",
            "save_dir": self.var_dir.get(),
            "headless": not self.var_show.get(),
            "vehicles": all_vehicles,
        }
        save_config(cfg)

        # 実行対象: チェック済み かつ 車両番号が入っているもの
        targets = [v for v in all_vehicles if v["enabled"] and v["number"]]
        if not targets:
            if not messagebox.askyesno(
                "確認",
                "チェック済みの車両がありません。"
                "車両番号を指定せず全件PDFを1つ取得しますか？",
            ):
                return
            targets = [{"name": "", "number": ""}]

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

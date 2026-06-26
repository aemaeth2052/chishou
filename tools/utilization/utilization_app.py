# -*- coding: utf-8 -*-
"""作業員稼働率 GUI

番割(Hks)から営業所ごとの稼働率を集計し、結果・取りこぼし・履歴を画面で見る。
除外キーワード(例外リスト)と対照表(外国人ニックネーム)も画面で編集できる。

タブ:
  集計     : 名簿の指定 → 「番割から集計」→ 営業所ごとの稼働率を表示
  除外設定 : 外貨を産まない顧客/現場のキーワード(部分一致)を編集
  対照表   : 取りこぼし候補を見て、自社/他営業所/対象外を割り当て(name_aliases.json)
  履歴     : utilization_history.csv の推移を表示

集計ロジックは utilization.py を呼ぶだけ(画面は薄いラッパ)。
"""

import csv
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import utilization as U
import worker_color as WC

try:
    import ttkbootstrap as ttkb
    HAS_TTKB = True
except ImportError:
    HAS_TTKB = False

HERE = Path(__file__).resolve().parent
APP_CONFIG = HERE / "utilization_app_config.json"


def load_app_config():
    if APP_CONFIG.exists():
        try:
            return json.loads(APP_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_app_config(cfg):
    try:
        APP_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:
        pass


class App:
    def __init__(self, root):
        self.root = root
        root.title("作業員稼働率")
        root.geometry("960x640")

        self.cfg = load_app_config()
        self.roster_paths = list(self.cfg.get("roster_paths", []))
        self.aliases = U.load_aliases()
        self.settings = U.load_settings(U.SETTINGS_PATH)
        self.last_review = []
        self.roster_index = {}   # コード -> Employee (対照表の営業所表示用)
        self.q = queue.Queue()

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_run = ttk.Frame(nb)
        self.tab_excl = ttk.Frame(nb)
        self.tab_color = ttk.Frame(nb)
        self.tab_alias = ttk.Frame(nb)
        self.tab_hist = ttk.Frame(nb)
        self.tab_gs = ttk.Frame(nb)
        nb.add(self.tab_run, text="集計")
        nb.add(self.tab_excl, text="除外設定")
        nb.add(self.tab_color, text="色判定")
        nb.add(self.tab_alias, text="対照表")
        nb.add(self.tab_hist, text="履歴")
        nb.add(self.tab_gs, text="Google連携")

        self._build_run()
        self._build_exclude()
        self._build_color()
        self._build_alias()
        self._build_history()
        self._build_gsheet()

        self.root.after(120, self._poll)

    # ----------------------------------------------------------- 集計タブ
    def _build_run(self):
        f = self.tab_run
        top = ttk.LabelFrame(f, text="社員名簿(任意・参考用。判定は『色判定』タブの背景色で行う)")
        top.pack(fill="x", padx=8, pady=6)
        self.lst_roster = tk.Listbox(top, height=4)
        self.lst_roster.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        for p in self.roster_paths:
            self.lst_roster.insert("end", p)
        btns = ttk.Frame(top)
        btns.pack(side="left", fill="y", padx=4, pady=6)
        ttk.Button(btns, text="CSV追加", command=self._add_roster_file).pack(fill="x", pady=2)
        ttk.Button(btns, text="フォルダ追加", command=self._add_roster_dir).pack(fill="x", pady=2)
        ttk.Button(btns, text="削除", command=self._del_roster).pack(fill="x", pady=2)

        run = ttk.Frame(f)
        run.pack(fill="x", padx=8, pady=4)
        self.btn_run = ttk.Button(run, text="番割から集計する",
                                  command=self._run_live)
        self.btn_run.pack(side="left")
        ttk.Button(run, text="検証: inspectファイルから",
                   command=self._run_inspect).pack(side="left", padx=6)
        ttk.Button(run, text="出力フォルダを開く",
                   command=self._open_folder).pack(side="right")

        cols = ("営業所", "日付", "リスト", "在籍(分母)", "外貨", "休み・待機",
                "稼働率%", "貸出", "他営業所応援", "対象外")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        for c in cols:
            self.tree.heading(c, text=c)
            w = 150 if c == "営業所" else (70 if c in ("日付", "在籍(分母)", "休み・待機") else 60)
            self.tree.column(c, width=w, anchor="center")
        self.tree.column("営業所", anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=4)

        self.log = scrolledtext.ScrolledText(f, height=7)
        self.log.pack(fill="both", expand=False, padx=8, pady=6)

    def _add_roster_file(self):
        paths = filedialog.askopenfilenames(
            title="名簿CSVを選択", filetypes=[("CSV", "*.csv"), ("すべて", "*.*")])
        for p in paths:
            if p not in self.roster_paths:
                self.roster_paths.append(p)
                self.lst_roster.insert("end", p)
        self._save_cfg()

    def _add_roster_dir(self):
        d = filedialog.askdirectory(title="名簿CSVの入ったフォルダを選択")
        if d and d not in self.roster_paths:
            self.roster_paths.append(d)
            self.lst_roster.insert("end", d)
            self._save_cfg()

    def _del_roster(self):
        sel = list(self.lst_roster.curselection())
        for i in reversed(sel):
            self.lst_roster.delete(i)
            del self.roster_paths[i]
        self._save_cfg()

    def _save_cfg(self):
        self.cfg["roster_paths"] = self.roster_paths
        save_app_config(self.cfg)

    def _logmsg(self, s):
        self.log.insert("end", s + "\n")
        self.log.see("end")

    def _run_live(self):
        # 開いている番割を列挙し、対象を選ばせてから集計する
        # 判定は背景色で行うので名簿は任意。色設定が無い場合だけ注意を促す。
        if not WC.load_color_map():
            if not messagebox.askokcancel(
                    "色判定が未設定",
                    "氏名の背景色で自社/他社を判定しますが、色がまだ設定されていません。\n"
                    "このままだと全員が対象外になります。\n\n"
                    "先に『色判定』タブで色を登録するのがおすすめです。"
                    "このまま続けますか?"):
                return
        self.btn_run.config(state="disabled")
        self._logmsg("開いている番割予定表を確認しています...")

        def work():
            try:
                boards = U.list_boards(log=lambda s: self.q.put(("log", s)))
                self.q.put(("boards", boards))
            except Exception as e:
                self.q.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _run_inspect(self):
        p = filedialog.askopenfilename(
            title="workers_inspect.txt を選択",
            filetypes=[("テキスト", "*.txt"), ("すべて", "*.*")])
        if p:
            self._start_compute(inspect_path=p, select=None)

    def _choose_boards(self, boards):
        """開いている番割をチェックボックスで一覧表示し、集計対象を選ばせる。"""
        if not boards:
            self._logmsg("番割予定表ウィンドウが見つかりませんでした。")
            messagebox.showwarning(
                "番割が見つかりません",
                "開いている番割予定表ウィンドウが見つかりませんでした。\n"
                "Hksで番割予定表を表示してから、もう一度実行してください。")
            self.btn_run.config(state="normal")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("集計する番割を選ぶ")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(dlg, text="集計する番割にチェックを入れてください(営業所×日付)。"
                  ).pack(anchor="w", padx=12, pady=(12, 4))

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=12, pady=4)
        vars_ = []
        for b in boards:
            v = tk.BooleanVar(value=True)
            office = b.get("office") or "営業所不明"
            date = b.get("date") or "日付不明"
            upd = b.get("update_hhmm")
            label = f"{office}    {date}" + (f"    ({upd} 更新)" if upd else "")
            ttk.Checkbutton(body, text=label, variable=v).pack(anchor="w", pady=1)
            vars_.append((v, b))

        def set_all(val):
            for v, _ in vars_:
                v.set(val)

        bar = ttk.Frame(dlg)
        bar.pack(fill="x", padx=12, pady=4)
        ttk.Button(bar, text="全選択", command=lambda: set_all(True)).pack(side="left")
        ttk.Button(bar, text="全解除", command=lambda: set_all(False)).pack(
            side="left", padx=4)

        def on_ok():
            select = [(b.get("office", ""), b.get("date", ""))
                      for v, b in vars_ if v.get()]
            if not select:
                messagebox.showinfo("未選択", "1つ以上の番割を選んでください。", parent=dlg)
                return
            dlg.destroy()
            self._logmsg(f"選んだ {len(select)} 件の番割から集計します。")
            self._start_compute(inspect_path=None, select=select)

        def on_cancel():
            dlg.destroy()
            self._logmsg("集計をキャンセルしました。")
            self.btn_run.config(state="normal")

        act = ttk.Frame(dlg)
        act.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(act, text="この番割で集計", command=on_ok).pack(side="right")
        ttk.Button(act, text="キャンセル", command=on_cancel).pack(side="right", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", on_cancel)

    def _start_compute(self, inspect_path=None, select=None):
        # 名簿は任意(判定は背景色)。roster_paths が空でも集計できる。
        self.btn_run.config(state="disabled")
        self._logmsg("集計を開始します...")
        # 最新の対照表・除外設定を読み直して使う
        self.aliases = U.load_aliases()
        self.settings = U.load_settings(U.SETTINGS_PATH)
        gs = self.cfg.get("gsheet", {})

        def work():
            try:
                reports = U.analyze(
                    self.roster_paths, inspect_path=inspect_path,
                    cust_kw=self.settings["exclude_customer_keywords"],
                    site_kw=self.settings["exclude_site_keywords"],
                    aliases=self.aliases, select=select,
                    log=lambda s: self.q.put(("log", s)))
                _, review, nchk, nreco = U.write_outputs(reports)
                self.q.put(("done", (reports, review, nchk, nreco)))
                if gs.get("enabled") and gs.get("sa_json") and gs.get("spreadsheet"):
                    try:
                        import sheets_sync
                        sheets_sync.sync_history(
                            reports, gs["sa_json"], gs["spreadsheet"],
                            gs.get("worksheet", "稼働率履歴"),
                            log=lambda s: self.q.put(("gslog", s)))
                    except Exception as e:
                        self.q.put(("gslog", f"Googleシート更新に失敗: {e}"))
            except Exception as e:
                self.q.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._logmsg(payload)
                elif kind == "boards":
                    self._choose_boards(payload)
                elif kind == "colorlog":
                    self._color_log(payload)
                elif kind == "colors":
                    self._on_colors(payload)
                elif kind == "colorerr":
                    self._color_log("エラー: " + payload)
                    self.btn_color_scan.config(state="normal")
                elif kind == "gslog":
                    self._gslog(payload)
                elif kind == "error":
                    self._logmsg("エラー: " + payload)
                    messagebox.showerror("集計エラー", payload)
                    self.btn_run.config(state="normal")
                elif kind == "done":
                    self._on_done(*payload)
                    self.btn_run.config(state="normal")
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _on_done(self, reports, review, nchk, nreco):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for r in reports:
            self.tree.insert("", "end", values=(
                r["office"], r["date"], r["roster_size"], r["present"],
                r["revenue"], r["standby"],
                f"{r['rate'] * 100:.1f}",
                r["lent_out"], r["other_total"], r["ignored"]))
        self.last_review = review
        self._load_roster_index()
        self._refresh_review()
        self._logmsg(f"完了: {len(reports)} 営業所。"
                     f"取りこぼし候補 要確認{nchk} / 確認推奨{nreco} 件 "
                     f"(対照表タブで割り当て可)")
        self._load_history()

    def _open_folder(self):
        import subprocess
        import sys
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(HERE)])
            else:
                subprocess.Popen(["xdg-open", str(HERE)])
        except Exception as e:
            self._logmsg(f"フォルダを開けませんでした: {e}")

    # --------------------------------------------------------- 除外設定タブ
    def _build_exclude(self):
        f = self.tab_excl
        ttk.Label(f, text="外貨を産まない(管理費)現場の条件。いずれも部分一致。"
                  ).pack(anchor="w", padx=8, pady=6)
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, padx=8, pady=4)

        self.lb_cust = self._kw_panel(
            body, "顧客名キーワード(部分一致)", self.settings["exclude_customer_keywords"], 0)
        self.lb_site = self._kw_panel(
            body, "現場名キーワード(部分一致)", self.settings["exclude_site_keywords"], 1)
        self.lb_staff = self._kw_panel(
            body, "事務所スタッフ等 分母除外(コード or 氏名)",
            self.settings.get("non_field_staff", []), 2)

        ttk.Button(f, text="保存", command=self._save_exclude).pack(pady=8)

    def _kw_panel(self, parent, title, items, col):
        fr = ttk.LabelFrame(parent, text=title)
        fr.grid(row=0, column=col, sticky="nsew", padx=6)
        parent.columnconfigure(col, weight=1)
        parent.rowconfigure(0, weight=1)
        lb = tk.Listbox(fr, height=12)
        lb.pack(fill="both", expand=True, padx=6, pady=6)
        for it in items:
            lb.insert("end", it)
        row = ttk.Frame(fr)
        row.pack(fill="x", padx=6, pady=4)
        ent = ttk.Entry(row)
        ent.pack(side="left", fill="x", expand=True)

        def add():
            v = ent.get().strip()
            if v and v not in lb.get(0, "end"):
                lb.insert("end", v)
                ent.delete(0, "end")

        def rem():
            for i in reversed(list(lb.curselection())):
                lb.delete(i)

        ttk.Button(row, text="追加", command=add).pack(side="left", padx=2)
        ttk.Button(row, text="削除", command=rem).pack(side="left")
        return lb

    def _save_exclude(self):
        self.settings = {
            "exclude_customer_keywords": list(self.lb_cust.get(0, "end")),
            "exclude_site_keywords": list(self.lb_site.get(0, "end")),
            "non_field_staff": list(self.lb_staff.get(0, "end")),
        }
        U.save_settings(self.settings)
        messagebox.showinfo("保存しました",
                            "除外設定を保存しました。次の集計から反映されます。")

    # ----------------------------------------------------------- 色判定タブ
    def _build_color(self):
        f = self.tab_color
        ttk.Label(
            f, text="氏名の背景色で自社/他社を判定します。番割を開いて『番割の色を読み取る』を"
            "押し、検出された各色に区分を割り当てて保存してください。"
            "ここで割り当てた色が集計時の主判定になります(未割当の色はバッジ＋名簿で判定)。"
        ).pack(anchor="w", padx=8, pady=(8, 2))

        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=8, pady=4)
        self.btn_color_scan = ttk.Button(bar, text="番割の色を読み取る",
                                         command=self._scan_colors)
        self.btn_color_scan.pack(side="left")
        ttk.Label(bar, text="  許容差:").pack(side="left")
        cm = WC.load_color_map()
        self.var_tol = tk.StringVar(value=str(getattr(cm, "tolerance", WC.DEFAULT_TOLERANCE)))
        ttk.Entry(bar, textvariable=self.var_tol, width=5).pack(side="left")
        ttk.Label(bar, text="(色が近いと同一視・集約。色が多すぎる時は上げて読み直す)").pack(side="left")
        ttk.Button(bar, text="保存", command=self._save_colors).pack(side="right")

        cols = ("背景色", "区分", "人数", "バッジ", "例(氏名)")
        widths = {"背景色": 90, "区分": 110, "人数": 60, "バッジ": 90, "例(氏名)": 360}
        self.tree_color = ttk.Treeview(f, columns=cols, show="headings", height=10)
        for c in cols:
            self.tree_color.heading(c, text=c)
            self.tree_color.column(c, width=widths.get(c, 90),
                                   anchor="center" if c in ("区分", "人数") else "w")
        self.tree_color.pack(fill="both", expand=True, padx=8, pady=4)

        assign = ttk.Frame(f)
        assign.pack(fill="x", padx=8, pady=4)
        ttk.Label(assign, text="選択した色を:").pack(side="left")
        ttk.Button(assign, text="自社", command=lambda: self._set_color_kind("home")
                   ).pack(side="left", padx=2)
        ttk.Button(assign, text="他営業所応援", command=lambda: self._set_color_kind("other")
                   ).pack(side="left", padx=2)
        ttk.Button(assign, text="対象外", command=lambda: self._set_color_kind("ignore")
                   ).pack(side="left", padx=2)
        ttk.Button(assign, text="未設定に戻す", command=lambda: self._set_color_kind("")
                   ).pack(side="left", padx=8)

        self.color_log = scrolledtext.ScrolledText(f, height=6)
        self.color_log.pack(fill="both", expand=False, padx=8, pady=6)

        # 既存の worker_colors.json があれば、それを一覧に出しておく
        self._show_existing_colors(cm)

    _KIND_JP = {"home": "自社", "other": "他営業所応援", "ignore": "対象外", "": "（未設定）"}

    def _color_log(self, s):
        self.color_log.insert("end", s + "\n")
        self.color_log.see("end")

    def _show_existing_colors(self, cm):
        """保存済みの色マップを一覧に表示(スキャン前でも現状が見えるように)。"""
        for i in self.tree_color.get_children():
            self.tree_color.delete(i)
        for rgb, kind, label in getattr(cm, "entries", []):
            hexv = WC.hexc(rgb)
            self._insert_color_row(hexv, kind, "", "", label or "(保存済み)")

    def _insert_color_row(self, hexv, kind, count, badges, names):
        tag = "c_" + hexv.lstrip("#")
        try:
            self.tree_color.tag_configure(tag, background=hexv)
        except Exception:
            pass
        self.tree_color.insert("", "end", tags=(tag,), values=(
            hexv, self._KIND_JP.get(kind, "（未設定）"), count, badges, names))

    def _scan_colors(self):
        self.btn_color_scan.config(state="disabled")
        try:
            tol = int(self.var_tol.get())
        except ValueError:
            tol = WC.DEFAULT_TOLERANCE
        self._color_log(f"開いている番割を採色しています...(許容差 {tol} で色を集約)")

        def work():
            try:
                clusters = WC.scan_open_boards(
                    tolerance=tol, log=lambda s: self.q.put(("colorlog", s)))
                self.q.put(("colors", clusters))
            except Exception as e:
                self.q.put(("colorerr", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_colors(self, clusters):
        self.btn_color_scan.config(state="normal")
        # 採色中に番割を前面化したので、結果が出たら本アプリを前面に戻す
        try:
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        # 既存マップで分かる色は区分を引き継いで初期表示する
        cm = WC.load_color_map()
        for i in self.tree_color.get_children():
            self.tree_color.delete(i)
        for c in clusters:
            kind = cm.classify(c["hex"]) or ""
            self._insert_color_row(c["hex"], kind, c["count"],
                                   " ".join(c["badges"]), "、".join(c["names"]))
        self._color_log(f"完了: {len(clusters)} 色。各色に区分を割り当てて『保存』してください。")

    def _set_color_kind(self, kind):
        sel = self.tree_color.selection()
        if not sel:
            messagebox.showinfo("未選択", "一覧から色の行を選んでください。")
            return
        for iid in sel:
            vals = list(self.tree_color.item(iid, "values"))
            vals[1] = self._KIND_JP.get(kind, "（未設定）")
            self.tree_color.item(iid, values=vals)

    def _save_colors(self):
        jp2kind = {v: k for k, v in self._KIND_JP.items()}
        colors = []
        for iid in self.tree_color.get_children():
            vals = self.tree_color.item(iid, "values")
            hexv, kindjp = vals[0], vals[1]
            kind = jp2kind.get(kindjp, "")
            if kind:  # 未設定の色は保存しない(=従来判定にフォールバック)
                colors.append({"hex": hexv, "kind": kind,
                               "label": (vals[3] or "")})
        try:
            tol = int(self.var_tol.get())
        except ValueError:
            tol = WC.DEFAULT_TOLERANCE
        WC.save_color_map(colors, tolerance=tol)
        self._color_log(f"保存しました: {len(colors)} 色 → worker_colors.json "
                        f"(許容差 {tol})。次の集計から色判定が効きます。")
        messagebox.showinfo("保存しました",
                            f"{len(colors)} 色を worker_colors.json に保存しました。\n"
                            "次回の集計から、氏名の背景色で自社/他社を判定します。")

    # ----------------------------------------------------------- 対照表タブ
    def _build_alias(self):
        f = self.tab_alias
        ttk.Label(f, text="取りこぼし候補(集計を実行すると表示)。行を選び、"
                  "割当先コードを確認して下のボタンで登録します。"
                  ).pack(anchor="w", padx=8, pady=6)

        cols = ("優先", "氏名", "営業所", "バッジ", "現在の判定", "推奨コード候補", "現場")
        widths = {"優先": 70, "氏名": 110, "営業所": 140, "バッジ": 70,
                  "現在の判定": 130, "推奨コード候補": 160, "現場": 150}
        self.tree_rev = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for c in cols:
            self.tree_rev.heading(c, text=c)
            self.tree_rev.column(c, width=widths.get(c, 90), anchor="w")
        self.tree_rev.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree_rev.bind("<<TreeviewSelect>>", self._on_rev_select)

        # 選択中の候補の文脈(どの営業所の番割に出ている人か)
        self.var_sel = tk.StringVar(value="（上の一覧から候補を選んでください）")
        ttk.Label(f, textvariable=self.var_sel).pack(anchor="w", padx=10, pady=(2, 0))

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="割当先コード:").pack(side="left")
        self.ent_code = ttk.Entry(row, width=14)
        self.ent_code.pack(side="left", padx=4)
        self.ent_code.bind("<KeyRelease>", lambda _e: self._refresh_code_label())
        self.var_code_resolved = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.var_code_resolved, width=30).pack(
            side="left", padx=4)
        ttk.Button(row, text="自社として登録",
                   command=lambda: self._assign("code")).pack(side="left", padx=2)
        ttk.Button(row, text="他営業所応援",
                   command=lambda: self._assign("other")).pack(side="left", padx=2)
        ttk.Button(row, text="対象外",
                   command=lambda: self._assign("ignore")).pack(side="left", padx=2)

        cur = ttk.LabelFrame(f, text="現在の対照表(name_aliases.json)")
        cur.pack(fill="both", expand=True, padx=8, pady=6)
        self.tree_al = ttk.Treeview(cur, columns=("氏名", "割当"), show="headings",
                                    height=7)
        self.tree_al.heading("氏名", text="氏名")
        self.tree_al.heading("割当", text="割当(コード/other/ignore)")
        self.tree_al.column("氏名", width=200, anchor="w")
        self.tree_al.column("割当", width=260, anchor="w")
        self.tree_al.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        ttk.Button(cur, text="選択を削除", command=self._del_alias).pack(
            side="left", padx=4)
        self._load_roster_index()
        self._refresh_alias_list()

    def _load_roster_index(self):
        """名簿を読み、コード -> Employee の辞書を作る(対照表の営業所表示用)。"""
        self.roster_index = {}
        if not self.roster_paths:
            return
        try:
            for e in U.load_rosters(self.roster_paths, active_only=False):
                self.roster_index[e.code] = e
        except Exception:
            pass  # 名簿が未指定/不正でも対照表自体は使えるようにする

    def _code_office(self, code):
        """コードを名簿で解決して Employee を返す(無ければ None)。"""
        return self.roster_index.get((code or "").strip())

    def _refresh_code_label(self):
        """入力中のコードを名簿で解決し「→ 氏名(営業所)」をライブ表示。"""
        code = self.ent_code.get().strip()
        e = self._code_office(code)
        if not code:
            self.var_code_resolved.set("")
        elif e:
            self.var_code_resolved.set(f"→ {e.name.strip()}（{e.office or '営業所不明'}）")
        else:
            self.var_code_resolved.set("→ ⚠ 名簿に無いコード")

    def _on_rev_select(self, _evt):
        sel = self.tree_rev.selection()
        if not sel:
            return
        vals = self.tree_rev.item(sel[0], "values")
        name = vals[1] if len(vals) > 1 else ""
        suggest = vals[5] if len(vals) > 5 else ""
        code = suggest.split(":", 1)[0].strip() if suggest else ""
        self.ent_code.delete(0, "end")
        if code:
            self.ent_code.insert(0, code)
        rev = next((x for x in self.last_review if x["worker"] == name), None)
        if rev:
            self.var_sel.set(
                f"選択中: {rev['worker']}　／　番割の営業所: {rev['office']}"
                f"（{rev['date']}）　／　現在: {rev['current']}")
        self._refresh_code_label()

    def _assign(self, mode):
        sel = self.tree_rev.selection()
        if not sel:
            messagebox.showinfo("未選択", "上の一覧から対象の行を選んでください。")
            return
        vals = self.tree_rev.item(sel[0], "values")
        name = vals[1]
        board_office = vals[2] if len(vals) > 2 else ""
        if mode == "code":
            val = self.ent_code.get().strip()
            if not val:
                messagebox.showwarning("コード未入力", "割当先のコードを入れてください。")
                return
            e = self._code_office(val)
            if e:
                detail = (f"{name} を 自社 として登録します。\n\n"
                          f"割当コード {val} = {e.name.strip()}（{e.office or '営業所不明'}）\n"
                          f"この人が出ている番割: {board_office}\n\n再集計で反映されます。")
                bo = U.office_key(board_office)
                if e.office and bo and e.office != bo:
                    detail += (f"\n\n⚠ 注意: コードの営業所（{e.office}）と"
                               f"番割の営業所（{bo}）が一致していません。"
                               "別人のコードを入れていないか確認してください。")
            else:
                detail = (f"{name} を 自社 として登録します。\n\n"
                          f"⚠ コード {val} は名簿に見つかりません。コードを確認してください。\n"
                          f"この人が出ている番割: {board_office}\n\n再集計で反映されます。")
            if not messagebox.askokcancel("自社として登録", detail):
                return
        else:
            val = mode  # 'other' / 'ignore'
            label = "他営業所応援" if mode == "other" else "対象外"
            if not messagebox.askokcancel(
                    label, f"{name} を 「{label}」 として登録します。\n"
                           f"（出ている番割: {board_office}）\n\n再集計で反映されます。"):
                return
        self.aliases[name] = val
        U.save_aliases(self.aliases)
        self._refresh_alias_list()
        self._logmsg(f"対照表に登録: {name} → {val}")

    def _refresh_review(self):
        for i in self.tree_rev.get_children():
            self.tree_rev.delete(i)
        for x in self.last_review:
            self.tree_rev.insert("", "end", values=(
                x["priority"], x["worker"], x["office"], x["badge"], x["current"],
                x.get("suggest", ""), x["site"]))

    def _refresh_alias_list(self):
        for i in self.tree_al.get_children():
            self.tree_al.delete(i)
        for k, v in sorted(self.aliases.items()):
            self.tree_al.insert("", "end", values=(k, v))

    def _del_alias(self):
        sel = self.tree_al.selection()
        if not sel:
            return
        key = self.tree_al.item(sel[0], "values")[0]
        self.aliases.pop(key, None)
        U.save_aliases(self.aliases)
        self._refresh_alias_list()

    # ----------------------------------------------------------- 履歴タブ
    def _build_history(self):
        f = self.tab_hist
        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="更新", command=self._load_history).pack(side="left")
        ttk.Label(bar, text="  utilization_history.csv").pack(side="left")
        self.tree_hist = ttk.Treeview(f, show="headings", height=20)
        self.tree_hist.pack(fill="both", expand=True, padx=8, pady=4)
        self._load_history()

    def _load_history(self):
        path = U.HISTORY_PATH
        for i in self.tree_hist.get_children():
            self.tree_hist.delete(i)
        if not Path(path).exists():
            return
        try:
            with open(path, encoding="cp932", errors="replace", newline="") as fp:
                rows = list(csv.reader(fp))
        except Exception:
            return
        if not rows:
            return
        header = rows[0]
        self.tree_hist["columns"] = header
        for c in header:
            self.tree_hist.heading(c, text=c)
            self.tree_hist.column(c, width=90, anchor="center")
        self.tree_hist.column(header[1] if len(header) > 1 else header[0],
                              width=150, anchor="w")
        for r in rows[1:]:
            self.tree_hist.insert("", "end", values=r)

    # -------------------------------------------------------- Google連携タブ
    def _build_gsheet(self):
        f = self.tab_gs
        g = self.cfg.get("gsheet", {})
        self.gs_enabled = tk.BooleanVar(value=bool(g.get("enabled", False)))
        self.gs_json = tk.StringVar(value=g.get("sa_json", ""))
        self.gs_url = tk.StringVar(value=g.get("spreadsheet", ""))
        self.gs_ws = tk.StringVar(value=g.get("worksheet", "稼働率履歴"))

        ttk.Label(f, text="サービスアカウント方式で、集計のたびに(日付×営業所)を"
                  "スプレッドシートへ上書き/追記します。").pack(anchor="w", padx=8, pady=6)

        frm = ttk.Frame(f)
        frm.pack(fill="x", padx=8, pady=4)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="サービスアカウントJSON:").grid(row=0, column=0, sticky="e", pady=3)
        ttk.Entry(frm, textvariable=self.gs_json).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(frm, text="参照", command=self._pick_json).grid(row=0, column=2)
        ttk.Label(frm, text="スプレッドシートURL/ID:").grid(row=1, column=0, sticky="e", pady=3)
        ttk.Entry(frm, textvariable=self.gs_url).grid(row=1, column=1, columnspan=2,
                                                      sticky="ew", padx=4)
        ttk.Label(frm, text="ワークシート名:").grid(row=2, column=0, sticky="e", pady=3)
        ttk.Entry(frm, textvariable=self.gs_ws).grid(row=2, column=1, columnspan=2,
                                                     sticky="ew", padx=4)

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Checkbutton(row, text="集計時にスプレッドシートも更新する",
                        variable=self.gs_enabled).pack(side="left")
        ttk.Button(row, text="保存", command=self._save_gsheet).pack(side="right")
        ttk.Button(row, text="接続テスト", command=self._test_gsheet).pack(side="right", padx=6)

        self.gs_log = scrolledtext.ScrolledText(f, height=8)
        self.gs_log.pack(fill="both", expand=True, padx=8, pady=6)
        self.gs_log.insert("end",
                           "事前準備:\n"
                           " 1. Google Cloud でプロジェクト作成→Google Sheets APIを有効化\n"
                           " 2. サービスアカウント作成→JSONキーをダウンロード\n"
                           " 3. 対象シートを、そのアカウントのメール(...iam.gserviceaccount.com)に\n"
                           "    『編集者』で共有\n"
                           " 4. 上にJSONパスとシートURLを入れて『接続テスト』→『保存』\n")

    def _pick_json(self):
        p = filedialog.askopenfilename(title="サービスアカウントJSONを選択",
                                       filetypes=[("JSON", "*.json"), ("すべて", "*.*")])
        if p:
            self.gs_json.set(p)

    def _gs_dict(self):
        return {"enabled": bool(self.gs_enabled.get()), "sa_json": self.gs_json.get().strip(),
                "spreadsheet": self.gs_url.get().strip(), "worksheet": self.gs_ws.get().strip() or "稼働率履歴"}

    def _save_gsheet(self):
        self.cfg["gsheet"] = self._gs_dict()
        save_app_config(self.cfg)
        self._gslog("設定を保存しました。")

    def _gslog(self, s):
        self.gs_log.insert("end", s + "\n")
        self.gs_log.see("end")

    def _test_gsheet(self):
        g = self._gs_dict()
        if not g["sa_json"] or not g["spreadsheet"]:
            messagebox.showwarning("未入力", "JSONパスとシートURLを入れてください。")
            return
        self._gslog("接続テスト中...")

        def work():
            try:
                import sheets_sync
                msg = sheets_sync.test_connection(g["sa_json"], g["spreadsheet"], g["worksheet"])
                self.q.put(("gslog", msg))
            except ImportError:
                self.q.put(("gslog", "gspread 未インストール。setup を実行してください"
                            "(pip install gspread)。"))
            except Exception as e:
                self.q.put(("gslog", f"接続失敗: {e}"))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------- 起動
def main():
    # worker_color の import 時にプロセスを DPI 対応にしている(採色の座標系を合わせるため)。
    # その結果 tk が自動拡大しないので、画面のDPIに合わせて手動でスケール・初期サイズを補正する。
    if HAS_TTKB:
        root = ttkb.Window(themename="cosmo")
    else:
        root = tk.Tk()
    dpi = 96
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForSystem() or 96
        if dpi != 96:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        dpi = 96
    App(root)
    if dpi != 96:  # App.__init__ が 960x640 を設定するので、その後に拡大率ぶん補正する
        try:
            root.geometry(f"{int(960 * dpi / 96)}x{int(640 * dpi / 96)}")
        except Exception:
            pass
    root.mainloop()


if __name__ == "__main__":
    main()

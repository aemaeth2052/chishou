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
        self.q = queue.Queue()

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_run = ttk.Frame(nb)
        self.tab_excl = ttk.Frame(nb)
        self.tab_alias = ttk.Frame(nb)
        self.tab_hist = ttk.Frame(nb)
        self.tab_gs = ttk.Frame(nb)
        nb.add(self.tab_run, text="集計")
        nb.add(self.tab_excl, text="除外設定")
        nb.add(self.tab_alias, text="対照表")
        nb.add(self.tab_hist, text="履歴")
        nb.add(self.tab_gs, text="Google連携")

        self._build_run()
        self._build_exclude()
        self._build_alias()
        self._build_history()
        self._build_gsheet()

        self.root.after(120, self._poll)

    # ----------------------------------------------------------- 集計タブ
    def _build_run(self):
        f = self.tab_run
        top = ttk.LabelFrame(f, text="社員名簿(営業所ごとのCSV、またはフォルダ)")
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

        cols = ("営業所", "日付", "在籍", "分母", "出勤", "外貨", "稼働率%",
                "貸出", "他営業所応援", "対象外")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        for c in cols:
            self.tree.heading(c, text=c)
            w = 150 if c == "営業所" else (70 if c in ("日付",) else 60)
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
        self._start_run(inspect_path=None)

    def _run_inspect(self):
        p = filedialog.askopenfilename(
            title="workers_inspect.txt を選択",
            filetypes=[("テキスト", "*.txt"), ("すべて", "*.*")])
        if p:
            self._start_run(inspect_path=p)

    def _start_run(self, inspect_path):
        if not self.roster_paths:
            messagebox.showwarning("名簿が未指定", "先に社員名簿を追加してください。")
            return
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
                    aliases=self.aliases,
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
                r["office"], r["date"], r["roster_size"], r["denominator"],
                r["present"], r["revenue"], f"{r['rate'] * 100:.1f}",
                r["lent_out"], r["other_total"], r["ignored"]))
        self.last_review = review
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

    # ----------------------------------------------------------- 対照表タブ
    def _build_alias(self):
        f = self.tab_alias
        ttk.Label(f, text="取りこぼし候補(集計を実行すると表示)。"
                  "行を選んで下のボタンで割り当てると対照表に登録されます。"
                  ).pack(anchor="w", padx=8, pady=6)

        cols = ("優先", "氏名", "バッジ", "現在の判定", "推奨コード候補", "現場")
        self.tree_rev = ttk.Treeview(f, columns=cols, show="headings", height=9)
        for c in cols:
            self.tree_rev.heading(c, text=c)
            self.tree_rev.column(c, width=150 if c in ("推奨コード候補", "現場") else 90,
                                 anchor="w")
        self.tree_rev.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree_rev.bind("<<TreeviewSelect>>", self._on_rev_select)

        row = ttk.Frame(f)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="割当先コード/値:").pack(side="left")
        self.ent_code = ttk.Entry(row, width=22)
        self.ent_code.pack(side="left", padx=4)
        ttk.Button(row, text="自社として登録",
                   command=lambda: self._assign("code")).pack(side="left", padx=2)
        ttk.Button(row, text="他営業所",
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
        self._refresh_alias_list()

    def _on_rev_select(self, _evt):
        sel = self.tree_rev.selection()
        if not sel:
            return
        vals = self.tree_rev.item(sel[0], "values")
        suggest = vals[4] if len(vals) > 4 else ""
        code = suggest.split(":", 1)[0].strip() if suggest else ""
        self.ent_code.delete(0, "end")
        if code:
            self.ent_code.insert(0, code)

    def _assign(self, mode):
        sel = self.tree_rev.selection()
        if not sel:
            messagebox.showinfo("未選択", "上の一覧から対象の行を選んでください。")
            return
        name = self.tree_rev.item(sel[0], "values")[1]
        if mode == "code":
            val = self.ent_code.get().strip()
            if not val:
                messagebox.showwarning("コード未入力", "割当先のコードを入れてください。")
                return
        else:
            val = mode  # 'other' / 'ignore'
        self.aliases[name] = val
        U.save_aliases(self.aliases)
        self._refresh_alias_list()
        messagebox.showinfo("登録しました",
                            f"{name} → {val}\n再集計すると反映されます。")

    def _refresh_review(self):
        for i in self.tree_rev.get_children():
            self.tree_rev.delete(i)
        for x in self.last_review:
            self.tree_rev.insert("", "end", values=(
                x["priority"], x["worker"], x["badge"], x["current"],
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
    if HAS_TTKB:
        root = ttkb.Window(themename="cosmo")
    else:
        root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

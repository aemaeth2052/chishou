"""ダウンロードの流れの確認。ETC利用照会サービスに似せた偽サイト（この PC の中だけ）で downloader.run を通しで動かす。
GitHub Actions の Windows 上と、手元で実行する（Chromium が要る。使い方: python tests\\check_download.py）。

- 明細が1ページに収まる車両は「全頁選択」を省き、2ページ以上ある車両は全頁選択する。どちらもPDFに明細が全部入る
- 画面に件数が出ていないときは、これまでどおり全頁選択する（調査用に結果画面のつくりをログに出す）
- 全頁選択しないとPDFを出さない作りなら、全頁選択して取り直す
- ログインの後は画像を読み込まない
- パスワードが違えば、最初のログインですぐ止まる
"""
import datetime
import http.cookies
import http.server
import socketserver
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import downloader  # noqa: E402

PAGE = 3  # 1ページの明細数
DATA = {"27": 2, "33": 5, "44": 0, "55": 3}  # 車両番号: 明細の件数
MODE = {"count": True, "strict": False}  # count: 件数を表示する / strict: 全頁選択しないとPDFを出さない
LOG = []  # (パス, ログイン済みか)
SESS = {}
GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")

LOGIN = """<html><body><img src="/img/logo.gif"><form method=post action="/login">
<input type=text name=risLoginId><input type=password name=risPassword>
<input type=image src="/img/login.gif" alt="ログイン"></form></body></html>"""

FORM = """<html><head><script>
function submitKensaku(n, f, url) { const fm = document.forms[f]; fm.action = url; fm.submit(); }
</script></head><body><img src="/img/logo.gif?v=2"><form name=frm method=post>
<select name=fromYYYY>{y}</select><select name=fromMM>{m}</select><select name=fromDD>{d}</select>
<select name=toYYYY>{y}</select><select name=toMM>{m}</select><select name=toDD>{d}</select>
<input type=radio name=sokoKbn value=0 checked><input type=radio name=sokoKbn value=1>
<input type=text name=sharyoNo><input type=checkbox name=hyojiCard value=c1></form></body></html>"""


def opts(rng, w):
    return "".join(f"<option value='{v:0{w}d}'>{v}</option>" for v in rng)


def result_page(s):
    rows = s["rows"]
    if not rows:
        return "<html><body>ご利用はありません</body></html>"
    page_rows = rows[:PAGE]
    trs = "".join(
        f"<tr><td><input type=checkbox name=hakkoMeisai value='{r}' {'checked' if s['allon'] else ''}></td>"
        f"<td>{r}</td><td><img src='/img/row.png'></td><td>100円</td></tr>" for r in page_rows)
    count = f"<p>全{len(rows)}件</p>" if MODE["count"] else ""
    pager = "<a href='#' onclick='return false'>次頁</a>" if len(rows) > PAGE else ""
    return f"""<html><head><script>
function allOn(m) {{ document.forms['frm'].mode.value = m; document.forms['frm'].action = '/etc/R?funccode=1032000000&nextfunc=1032000000'; document.forms['frm'].submit(); }}
</script></head><body><a href="/etc/R?funccode=1033000000&nextfunc=1033000000">検索条件の指定</a>
<img src="/img/banner.jpg">{count}<form name=frm method=post><input type=hidden name=mode value="">
<a href="#" onclick="allOn('ALLON'); return false;"><img src="/img/allon.gif" alt="全頁選択"></a>
<table>{trs}</table>{pager}<p>通行料金合計 {len(rows) * 100}円</p>
<input type=button name=pdfBtn value="利用明細ＰＤＦ出力" onclick="submitPdf('frm','/etc/R?funccode=1032000000&nextfunc=1032400000')">
</form></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def sess(self):
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sid = c["sid"].value if "sid" in c else None
        return sid, SESS.get(sid)

    def send(self, body, ctype="text/html; charset=utf-8", cookie=None):
        body = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", f"sid={cookie}; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def form(self):
        n = int(self.headers.get("Content-Length") or 0)
        return urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)

    def do_GET(self):
        sid, s = self.sess()
        LOG.append(("GET", self.path, s is not None))
        if self.path.startswith("/img/"):
            return self.send(GIF, "image/gif")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("funccode") == ["1033000000"] and s:
            y = opts(range(2025, 2027), 4)
            return self.send(FORM.replace("{y}", y).replace("{m}", opts(range(1, 13), 2)).replace("{d}", opts(range(1, 32), 2)))
        return self.send(LOGIN)

    def do_POST(self):
        sid, s = self.sess()
        LOG.append(("POST", self.path, s is not None))
        f = self.form()
        if self.path == "/login":
            if f.get("risPassword") == ["pw"]:
                SESS["s1"] = {"rows": [], "allon": False}
                return self.send("<html><body>メニュー</body></html>", cookie="s1")
            return self.send(LOGIN)
        if not s:
            return self.send(LOGIN)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        nxt = q.get("nextfunc", [""])[0]
        if nxt == "1032000000" and q.get("funccode") == ["1033000000"]:  # 検索
            no = f.get("sharyoNo", [""])[0]
            s["rows"] = [f"{no}-{i}" for i in range(DATA.get(no, 0))]
            s["allon"] = False
            return self.send(result_page(s))
        if nxt == "1032000000":  # 全頁選択
            s["allon"] = f.get("mode") == ["ALLON"] or s["allon"]
            return self.send(result_page(s))
        if nxt == "1032400000":  # PDF
            picked = s["rows"] if s["allon"] else ([] if MODE["strict"] else f.get("hakkoMeisai", []))
            if not picked:
                return self.send("<html><body>明細を選択してください</body></html>")
            return self.send(("%PDF-1.4\n" + "\n".join(picked)).encode(), "application/pdf")
        return self.send(LOGIN)

    def log_message(self, *a):
        pass


def main():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    downloader.LOGIN_URL = base + "/etc/R?funccode=1013000000&nextfunc=1013000000"
    downloader.SEARCH_FORM_URL = base + "/etc/R?funccode=1033000000&nextfunc=1033000000"
    downloader.TOP_URL = base + "/"
    downloader.LOG_DIR = Path(tempfile.mkdtemp())  # 本物の履歴（logs/history.csv）に混ぜない
    day = datetime.date(2026, 9, 28)
    vehicles = [{"name": "", "number": n} for n in DATA]

    def go(label, **mode):
        MODE.update({"count": True, "strict": False}, **mode)
        LOG.clear()
        out = Path(tempfile.mkdtemp())
        lines = []
        counts = downloader.run("id", "pw", day, day, out, vehicles=vehicles, headless=True, log=lines.append)
        pdfs = {p.name.split("_")[1].split(".")[0]: p.read_bytes().decode().split("\n")[1:] for p in out.glob("*.pdf")}
        allon = sum(1 for m, p, _ in LOG if m == "POST" and "funccode=1032000000&nextfunc=1032000000" in p)
        first = next(i for i, (m, p, _) in enumerate(LOG) if "nextfunc=1032000000" in p)  # 最初の検索から後
        imgs_after = sum(1 for m, p, ok in LOG[first:] if p.startswith("/img/"))
        imgs_before = sum(1 for m, p, ok in LOG if p.startswith("/img/") and not ok)
        print(f"--- {label}: {counts} 全頁選択 {allon}回 / 画像 ログイン前 {imgs_before}・後 {imgs_after}")
        for line in lines:
            if any(k in line for k in ("全頁選択", "所要時間", "調査用", "失敗", "画像")):
                print("   ", line)
        for no, n in DATA.items():
            if n:
                assert pdfs.get(no) == [f"{no}-{i}" for i in range(n)], (no, pdfs.get(no))
        assert imgs_after == 0, imgs_after
        return allon, lines

    allon, _ = go("件数あり")
    assert allon == 1, allon  # 5件（2ページ）の車両だけ
    allon, lines = go("件数の表示なし", count=False)
    assert allon == 3, allon
    assert any("調査用" in s for s in lines)
    allon, lines = go("全頁選択しないとPDFを出さない", strict=True)
    assert allon == 3, allon
    assert any("取り直しました" in s for s in lines)
    try:
        downloader.run("id", "wrong", day, day, Path(tempfile.mkdtemp()), vehicles=vehicles, headless=True, log=lambda s: None)
        raise AssertionError("ログイン失敗で止まっていません")
    except downloader.LoginFailedError:
        print("--- パスワード違い: すぐ止まる ok")
    print("ok")


if __name__ == "__main__":
    main()

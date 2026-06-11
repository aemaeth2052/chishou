# -*- coding: utf-8 -*-
"""ETC利用照会サービス (www2.etc-meisai.jp) 利用明細PDF自動ダウンロード

ログイン後の実際の画面構造 (2026-06 時点) に基づく実装:
- メニュー「検索条件の指定」で 車両番号・期間 を設定して検索
- 結果画面 (利用明細) で「全頁選択」→「利用明細ＰＤＦ出力」
- PDFは form "frm" を POST して取得 (ポップアップを開かず直接ダウンロード)

車両番号のリストを順に処理し、車両ごとに1つのPDFを保存する。

サイトの画面構成は予告なく変わることがある。動かなくなった場合は
logs/error_* に保存されるスクリーンショットとHTMLを確認して修正する。
"""

import base64
import datetime
import re
import unicodedata
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"

LOGIN_URL = "https://www2.etc-meisai.jp/etc/R?funccode=1013000000&nextfunc=1013000000"
TOP_URL = "https://www.etc-meisai.jp/"

# ログイン画面の部品候補 (上から順に試す)
SELECTORS = {
    "login_id": [
        'input[name="risLoginId"]',
        'input[name="loginId"]',
        'input[type="text"][name*="ogin" i]',
    ],
    "login_password": [
        'input[name="risPassword"]',
        'input[name="password"]',
        'input[type="password"]',
    ],
    "login_button": [
        'role=button[name="ログイン"]',
        'input[type="submit"][value*="ログイン"]',
        'input[type="image"][alt*="ログイン"]',
        'a:has-text("ログイン")',
    ],
}


class EtcMeisaiError(Exception):
    """利用者向けメッセージ付きのエラー"""


def _find(page, key, timeout=10000):
    """SELECTORS[key] の候補を順に試し、最初に可視になった locator を返す"""
    last_err = None
    for sel in SELECTORS[key]:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout // len(SELECTORS[key]) + 1500)
            return loc
        except PWTimeoutError as e:
            last_err = e
    raise EtcMeisaiError(
        f"画面部品が見つかりません: {key}\n"
        f"サイトの画面構成が変わった可能性があります。logs フォルダのスクリーンショットを確認してください。"
    ) from last_err


def _sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "output"


def _dump(page, log, prefix="error"):
    """画面の状態を logs/ に保存する"""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = LOG_DIR / f"{prefix}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(out / "screenshot.png"), full_page=True)
        (out / "page.html").write_text(page.content(), encoding="utf-8")
        (out / "url.txt").write_text(page.url, encoding="utf-8")
        log(f"画面の状態を保存しました: {out}")
    except Exception:
        log("画面の保存に失敗しました")
    return out


# ---------------------------------------------------------------- ログイン

def _login(page, login_id, password, log):
    log("ログインページを開いています...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
    except Exception:
        page.goto(TOP_URL, wait_until="domcontentloaded")
    # URL変更で404に飛ばされた場合は、トップページの「ログイン」リンクから入り直す
    if "お探しのページが見つかりません" in page.inner_text("body"):
        log("ログインURLが変わっているようです。トップページから入り直します...")
        page.goto(TOP_URL, wait_until="domcontentloaded")
        page.locator('#globalNav a:has-text("ログイン")').first.click()
        page.wait_for_load_state("domcontentloaded")

    _find(page, "login_id").fill(login_id)
    _find(page, "login_password").fill(password)
    _find(page, "login_button").click()
    page.wait_for_load_state("domcontentloaded")

    body = page.inner_text("body")
    for ng in ("パスワードが正しくありません", "ログインできません", "認証に失敗"):
        if ng in body:
            raise EtcMeisaiError("ログインに失敗しました。IDとパスワードを確認してください。")
    log("ログインしました")


# ------------------------------------------------------- 検索条件の指定画面

def _goto_search_page(page):
    """メニュー「検索条件の指定」へ移動"""
    page.locator('a:has-text("検索条件の指定")').first.click()
    page.wait_for_load_state("domcontentloaded")


def _check_all_cards(page, log):
    """カード選択のチェックボックスがあれば全てONにする (明細行のものは除く)"""
    try:
        names = page.evaluate(
            """() => {
                const names = new Set();
                for (const el of document.querySelectorAll('input[type=checkbox]')) {
                    if (el.name === 'hakkoMeisai' || !el.name) continue;
                    const tbl = el.closest('table');
                    if (tbl && tbl.innerText.includes('カード')) names.add(el.name);
                }
                return [...names];
            }"""
        )
        for name in names:
            boxes = page.locator(f'input[type="checkbox"][name="{name}"]')
            for i in range(boxes.count()):
                boxes.nth(i).check()
        if names:
            log("  カードを全選択しました")
    except Exception:
        log("  カード全選択はスキップしました (チェックボックスなし)")


def _classify_date_selects(page):
    """ページ内の <select> を年/月/日に分類し、(年,月,日) の組をDOM順に返す"""
    sels = page.eval_on_selector_all(
        "select",
        """els => els.map((s, i) => ({
            i,
            disabled: s.disabled,
            values: [...s.options].map(o => (o.value || o.textContent).trim()),
        }))""",
    )
    classified = []
    for s in sels:
        nums = []
        for v in s["values"]:
            try:
                nums.append(int(v))
            except ValueError:
                pass
        if not nums:
            kind = None
        elif all(2000 <= n <= 2099 for n in nums):
            kind = "year"
        elif min(nums) >= 1 and max(nums) <= 12:
            kind = "month"
        elif min(nums) >= 1 and max(nums) <= 31 and max(nums) > 12:
            kind = "day"
        else:
            kind = None
        classified.append((s["i"], kind))

    # DOM順で 年→月→日 と連続する組を拾う
    triples = []
    k = 0
    while k <= len(classified) - 3:
        kinds = [classified[k][1], classified[k + 1][1], classified[k + 2][1]]
        if kinds == ["year", "month", "day"]:
            triples.append((classified[k][0], classified[k + 1][0], classified[k + 2][0]))
            k += 3
        else:
            k += 1
    return triples


def _enable_date_inputs(page, triples):
    """日付指定のラジオボタンがある場合、日付selectが有効になるまで試す"""
    def year_disabled():
        idx = triples[0][0]
        return page.locator("select").nth(idx).is_disabled()

    if not year_disabled():
        return
    radios = page.locator('input[type="radio"]')
    for i in range(min(radios.count(), 10)):
        try:
            radios.nth(i).check()
            page.wait_for_timeout(300)
            if not year_disabled():
                return
        except Exception:
            continue


def _select_number(page, select_index, number):
    """select の option から数値が一致するものを選ぶ"""
    loc = page.locator("select").nth(select_index)
    options = loc.evaluate(
        "s => [...s.options].map(o => ({v: o.value, t: o.textContent.trim()}))"
    )
    for o in options:
        try:
            if int(o["v"] or o["t"]) == number:
                loc.select_option(value=o["v"])
                return
        except ValueError:
            continue
    raise EtcMeisaiError(f"日付の選択肢に {number} が見つかりません")


def _set_dates(page, date_from, date_to):
    triples = _classify_date_selects(page)
    if len(triples) < 2:
        raise EtcMeisaiError(
            "期間指定の年月日プルダウンを特定できませんでした。"
            "検索条件画面の構成が想定と異なります。"
        )
    _enable_date_inputs(page, triples)
    for (yi, mi, di), d in zip(triples[:2], (date_from, date_to)):
        _select_number(page, yi, d.year)
        _select_number(page, mi, d.month)
        _select_number(page, di, d.day)


def _set_vehicle_number(page, number):
    """「車両番号」ラベルの近くのテキスト入力に車両番号を入れる"""
    name = page.evaluate(
        """() => {
            for (const el of document.querySelectorAll('input[type=text]')) {
                const tr = el.closest('tr');
                if (tr && tr.innerText.includes('車両番号')) return el.name;
            }
            return null;
        }"""
    )
    if not name:
        raise EtcMeisaiError("検索条件画面で「車両番号」の入力欄が見つかりませんでした。")
    page.fill(f'input[name="{name}"]', str(number))


def _submit_search(page):
    for sel in (
        'input[type="button"][value*="検索"]',
        'input[type="submit"][value*="検索"]',
        'button:has-text("検索")',
        'input[type="button"][value*="表示"]',
        'a:has-text("検索する")',
    ):
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.click()
            page.wait_for_load_state("domcontentloaded")
            return
    raise EtcMeisaiError("検索条件画面の「検索」ボタンが見つかりませんでした。")


# ------------------------------------------------------------- 結果→PDF保存

_FETCH_PDF_JS = """
async (action) => {
    const f = document.forms['frm'];
    if (!f) return {error: 'form frm not found'};
    const data = new URLSearchParams();
    for (const el of Array.from(f.elements)) {
        if (!el.name || el.disabled) continue;
        const t = (el.type || '').toLowerCase();
        if ((t === 'checkbox' || t === 'radio') && !el.checked) continue;
        if (t === 'button' || t === 'submit' || t === 'image') continue;
        data.append(el.name, el.value);
    }
    const res = await fetch(action, {method: 'POST', body: data, credentials: 'same-origin'});
    const buf = new Uint8Array(await res.arrayBuffer());
    let s = '';
    for (let i = 0; i < buf.length; i += 0x8000) {
        s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    }
    return {b64: btoa(s), status: res.status, ct: res.headers.get('content-type') || ''};
}
"""


def _download_pdf(page, dest: Path, log):
    """結果画面で全頁選択し、利用明細PDFをPOSTで取得して保存する"""
    # 明細が1件もなければスキップ
    if page.locator('input[name="hakkoMeisai"]').count() == 0:
        return False

    # 全頁選択 (全ページ分の明細をPDF対象にする)
    link = page.locator('a[onclick*="ALLON"]').first
    if not link.count():
        link = page.locator('a:has-text("全頁選択")').first
    if link.count():
        link.click()
        page.wait_for_load_state("domcontentloaded")

    # 「利用明細ＰＤＦ出力」のPOST先URLを onclick から取り出す
    action = page.evaluate(
        r"""() => {
            for (const el of document.querySelectorAll('[onclick]')) {
                const txt = (el.value || el.textContent || '');
                if (txt.includes('明細') && /[ＰP][ＤD][ＦF]/.test(txt)) {
                    const m = el.getAttribute('onclick').match(/'(\/etc\/R\?[^']+)'/);
                    if (m) return m[1];
                }
            }
            return null;
        }"""
    )
    if not action:
        raise EtcMeisaiError("「利用明細ＰＤＦ出力」ボタンが見つかりませんでした。")

    result = page.evaluate(_FETCH_PDF_JS, action)
    if result.get("error"):
        raise EtcMeisaiError(f"PDF取得に失敗しました: {result['error']}")
    data = base64.b64decode(result["b64"])
    if not data.startswith(b"%PDF"):
        bad = LOG_DIR / "last_pdf_response.bin"
        LOG_DIR.mkdir(exist_ok=True)
        bad.write_bytes(data)
        raise EtcMeisaiError(
            f"PDFではない応答が返りました (status={result['status']}, "
            f"content-type={result['ct']})。logs/last_pdf_response.bin を確認してください。"
        )
    dest.write_bytes(data)
    return True


# -------------------------------------------------------------------- main

def run(login_id, password, date_from, date_to, save_dir,
        vehicle_numbers=None, headless=False, log=print):
    """メイン処理。GUI からスレッドで呼ばれる。

    vehicle_numbers: 車両番号の文字列リスト (例: ["27", "31"])。
                     空なら車両番号を変更せず1回だけ検索する。
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    period = f"{date_from:%Y%m%d}-{date_to:%Y%m%d}"
    vehicle_numbers = [str(v).strip() for v in (vehicle_numbers or []) if str(v).strip()]
    targets = vehicle_numbers or [None]
    saved = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _login(page, login_id, password, log)

            first = True
            for i, vehicle in enumerate(targets, 1):
                name = f"車両{vehicle}" if vehicle else "全車両"
                log(f"[{i}/{len(targets)}] {name}: 検索条件を設定中 ({date_from} 〜 {date_to})")
                _goto_search_page(page)
                if first:
                    # 初回のみ検索条件画面の状態を記録 (画面構成の確認用)
                    _dump(page, lambda m: None, prefix="snapshot_search")
                    first = False
                _check_all_cards(page, log)
                if vehicle:
                    _set_vehicle_number(page, vehicle)
                _set_dates(page, date_from, date_to)
                _submit_search(page)

                dest = save_dir / f"{_sanitize_filename(name)}_{period}.pdf"
                if _download_pdf(page, dest, log):
                    saved.append(dest)
                    log(f"  → 保存: {dest.name}")
                else:
                    log("  → 期間内の利用データなし。スキップします")

            # ログアウト
            try:
                page.locator('a:has-text("ログアウト")').first.click(timeout=5000)
                log("ログアウトしました")
            except Exception:
                log("ログアウトリンクが見つかりませんでした(セッションはブラウザ終了で切れます)")

            log(f"完了: {len(saved)} 件のPDFを {save_dir} に保存しました")
            return saved
        except Exception:
            _dump(page, log, prefix="error")
            raise
        finally:
            context.close()
            browser.close()

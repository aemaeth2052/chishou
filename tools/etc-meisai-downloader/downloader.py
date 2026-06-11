# -*- coding: utf-8 -*-
"""ETC利用照会サービス (www2.etc-meisai.jp) 利用明細PDF自動ダウンロード

ログイン後の実画面 (2026-06 時点) に基づく実装:
- 検索条件画面 (funccode=1033000000) のフォーム frm に値を直接セット:
    fromYYYY, fromMM, fromDD, toYYYY, toMM, toDD  : 期間
    sokoKbn = 1 (ETC無線走行のみ)                : 車両番号指定の前提
    sharyoNo                                     : 車両番号(下4桁)
  カードは全て (name=hyojiCard) チェックON
- 「検索」ボタンの JavaScript 関数 submitKensaku() を呼んで明細画面へ
- 明細画面 (funccode=1032000000) で全頁選択(ALLON) → 利用明細PDFをPOSTで取得

サイトの画面構成は予告なく変わる可能性がある。動かなくなったら
logs/error_* のスクリーンショットとHTMLを確認して修正する。
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

LOGIN_SELECTORS = {
    "id": [
        'input[name="risLoginId"]',
        'input[name="loginId"]',
        'input[type="text"][name*="ogin" i]',
    ],
    "pw": [
        'input[name="risPassword"]',
        'input[name="password"]',
        'input[type="password"]',
    ],
    "btn": [
        'role=button[name="ログイン"]',
        'input[type="submit"][value*="ログイン"]',
        'input[type="image"][alt*="ログイン"]',
        'a:has-text("ログイン")',
    ],
}


class EtcMeisaiError(Exception):
    """利用者向けメッセージ付きのエラー"""


def _find(page, candidates, timeout=10000):
    last_err = None
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout // len(candidates) + 1500)
            return loc
        except PWTimeoutError as e:
            last_err = e
    raise EtcMeisaiError(f"画面部品が見つかりません: {candidates[0]}") from last_err


def _sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "output"


def _dump(page, log, prefix="error"):
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
    if "お探しのページが見つかりません" in page.inner_text("body"):
        log("ログインURLが変わっているようです。トップページから入り直します...")
        page.goto(TOP_URL, wait_until="domcontentloaded")
        page.locator('#globalNav a:has-text("ログイン")').first.click()
        page.wait_for_load_state("domcontentloaded")

    _find(page, LOGIN_SELECTORS["id"]).fill(login_id)
    _find(page, LOGIN_SELECTORS["pw"]).fill(password)
    _find(page, LOGIN_SELECTORS["btn"]).click()
    page.wait_for_load_state("domcontentloaded")

    body = page.inner_text("body")
    for ng in ("パスワードが正しくありません", "ログインできません", "認証に失敗"):
        if ng in body:
            raise EtcMeisaiError("ログインに失敗しました。IDとパスワードを確認してください。")
    log("ログインしました")


# ------------------------------------------------------- 検索条件の指定画面

def _goto_search_form(page):
    """メニュー「検索条件の指定」をクリックして検索条件画面へ"""
    page.locator('a:has-text("検索条件の指定")').first.click()
    page.wait_for_load_state("domcontentloaded")
    # 検索フォームが現れるまで待つ
    page.wait_for_selector('select[name="fromYYYY"]', timeout=15000)


def _set_search_conditions(page, vehicle_no, date_from, date_to):
    """検索条件画面のフォームに値を設定する (画面の構造に直接合わせる)"""
    # 走行区分 = ETC無線走行のみ (これがONでないと車両番号指定が無効になる)
    page.evaluate(
        """() => {
            for (const el of document.querySelectorAll('input[name="sokoKbn"]')) {
                if (el.value === '1') el.click();
            }
        }"""
    )
    # 期間
    for sel, val in [
        ("fromYYYY", f"{date_from.year:04d}"),
        ("fromMM",   f"{date_from.month:02d}"),
        ("fromDD",   f"{date_from.day:02d}"),
        ("toYYYY",   f"{date_to.year:04d}"),
        ("toMM",     f"{date_to.month:02d}"),
        ("toDD",     f"{date_to.day:02d}"),
    ]:
        loc = page.locator(f'select[name="{sel}"]')
        try:
            loc.select_option(value=val)
        except Exception:
            raise EtcMeisaiError(
                f"期間の {sel}={val} を選択できませんでした。"
                f"指定期間がサイトの選択肢の範囲外(過去62日)の可能性があります。"
            )
    # 車両番号
    if vehicle_no:
        page.fill('input[name="sharyoNo"]', str(vehicle_no))
    else:
        page.fill('input[name="sharyoNo"]', "")
    # カードは全選択
    page.evaluate(
        """() => {
            for (const el of document.querySelectorAll('input[name="hyojiCard"]')) el.checked = true;
        }"""
    )


def _submit_search(page):
    """検索ボタン (submitKensaku 関数) を呼ぶ"""
    page.evaluate(
        "() => submitKensaku('hyojiCard', 'frm', "
        "'/etc/R?funccode=1033000000&nextfunc=1032000000')"
    )
    page.wait_for_load_state("domcontentloaded")
    # 検索結果画面に到達したことの確認:
    # 明細あり → hakkoMeisai チェックボックス / 明細なし → 「ご利用はありません」
    page.wait_for_function(
        "() => document.querySelector('input[name=\"hakkoMeisai\"]') "
        "|| document.body.innerText.includes('ご利用はありません') "
        "|| document.body.innerText.includes('該当する')",
        timeout=30000,
    )


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
    if page.locator('input[name="hakkoMeisai"]').count() == 0:
        return False

    # 全頁選択 (全ページ分の明細をPDF対象にする)
    link = page.locator('a[onclick*="ALLON"]').first
    if not link.count():
        link = page.locator('a:has-text("全頁選択")').first
    if link.count():
        link.click()
        page.wait_for_load_state("domcontentloaded")

    # 「利用明細ＰＤＦ出力」のPOST先URLを onclick から取り出す (nextfunc=1032400000)
    action = page.evaluate(
        r"""() => {
            for (const el of document.querySelectorAll('[onclick]')) {
                const txt = (el.value || el.textContent || '');
                if (txt.includes('明細') && /[ＰP][ＤD][ＦF]/.test(txt)) {
                    const m = el.getAttribute('onclick').match(/'(\/etc\/R\?[^']+1032400000[^']*)'/);
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
        vehicles=None, headless=False, log=print):
    """メイン処理。GUI からスレッドで呼ばれる。

    vehicles: [{"name": "営業1号車", "number": "27"}, ...]
              空なら車両番号指定なしで1回だけ検索する。
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    # 期間が1日のみならファイル名の日付は1回だけ書く
    if date_from == date_to:
        period = f"{date_from:%Y%m%d}"
    else:
        period = f"{date_from:%Y%m%d}-{date_to:%Y%m%d}"
    targets = list(vehicles or [])
    if not targets:
        targets = [{"name": "全車両", "number": ""}]
    saved = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _login(page, login_id, password, log)

            for i, v in enumerate(targets, 1):
                name = (v.get("name") or "").strip()
                number = str(v.get("number") or "").strip()
                label = f"{name}({number})" if name and number else (name or f"車両{number}" or "全車両")
                log(f"[{i}/{len(targets)}] {label}: 検索条件を設定中 ({date_from} 〜 {date_to})")

                _goto_search_form(page)
                _set_search_conditions(page, number, date_from, date_to)
                _submit_search(page)

                # ファイル名: 名前があれば「名前_車両番号_期間.pdf」、なければ「車両番号_期間.pdf」
                parts = [p for p in (name, f"車両{number}" if number else "") if p]
                stem = "_".join(_sanitize_filename(x) for x in parts) or "全車両"
                dest = save_dir / f"{stem}_{period}.pdf"
                if _download_pdf(page, dest, log):
                    saved.append(dest)
                    log(f"  → 保存: {dest.name}")
                else:
                    log("  → 期間内の利用データなし。スキップします")

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

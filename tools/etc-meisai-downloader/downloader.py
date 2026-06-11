# -*- coding: utf-8 -*-
"""ETC利用照会サービス (www.etc-meisai.jp) 利用明細PDF自動ダウンロード

ブラウザ(Chromium)を自動操作して、登録されている全ETCカード(車両)について
指定期間の利用明細を検索し、PDFを保存する。

サイトの画面構成は予告なく変わることがあるため、画面部品の特定は
SELECTORS の「候補リスト」方式にしている。動かなくなった場合は
logs/error_* に保存されるスクリーンショットとHTMLを確認して
SELECTORS を修正すればよい。
"""

import datetime
import json
import re
import unicodedata
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"

LOGIN_URL = "https://www.etc-meisai.jp/etc/R?funccode=1013000000&nextfunc=1013000000"
TOP_URL = "https://www.etc-meisai.jp/"

# 画面部品の候補。上から順に試して最初に見つかったものを使う。
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
    # ログイン後、明細検索画面へ移動するリンク/ボタン
    "search_menu": [
        'role=link[name*="利用明細"]',
        'a:has-text("利用明細")',
        'role=button[name*="利用明細"]',
    ],
    # カード選択 (select想定。checkbox構成の場合は要修正)
    "card_select": [
        'select[name*="card" i]',
        'select[name*="Card" i]',
        'select[name*="meisai" i]',
    ],
    # 検索期間: 日付指定ラジオ
    "date_radio": [
        'input[type="radio"][value*="term" i]',
        'input[type="radio"][name*="kikan" i]',
    ],
    "from_year": ['select[name*="fromY" i]', 'select[name*="startY" i]'],
    "from_month": ['select[name*="fromM" i]', 'select[name*="startM" i]'],
    "from_day": ['select[name*="fromD" i]', 'select[name*="startD" i]'],
    "to_year": ['select[name*="toY" i]', 'select[name*="endY" i]'],
    "to_month": ['select[name*="toM" i]', 'select[name*="endM" i]'],
    "to_day": ['select[name*="toD" i]', 'select[name*="endD" i]'],
    "search_button": [
        'role=button[name="検索"]',
        'input[type="submit"][value*="検索"]',
        'input[type="image"][alt*="検索"]',
        'a:has-text("検索")',
    ],
    "pdf_button": [
        'role=button[name*="PDF"]',
        'input[type="submit"][value*="PDF"]',
        'input[type="image"][alt*="PDF"]',
        'a:has-text("PDF")',
    ],
    "logout": [
        'role=link[name*="ログアウト"]',
        'a:has-text("ログアウト")',
        'input[value*="ログアウト"]',
    ],
}

# 検索結果が0件のときに画面に出る文言の候補
NO_DATA_PATTERNS = ["該当するデータ", "該当する明細", "利用明細はありません", "データがありません"]


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
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name) or "card"


def _dump_error(page, log):
    """エラー時に画面の状態を logs/ に保存する"""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = LOG_DIR / f"error_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(out / "screenshot.png"), full_page=True)
        (out / "page.html").write_text(page.content(), encoding="utf-8")
        (out / "url.txt").write_text(page.url, encoding="utf-8")
        log(f"エラー時の画面を保存しました: {out}")
    except Exception:
        log("エラー画面の保存に失敗しました")


def _set_date(page, prefix: str, d: datetime.date):
    """from/to の年月日 select に日付を設定する。値形式の揺れに備え数通り試す"""
    parts = [("year", f"{d.year}"), ("month", f"{d.month}"), ("day", f"{d.day}")]
    for unit, value in parts:
        loc = _find(page, f"{prefix}_{unit}")
        for candidate in (value, value.zfill(2)):
            try:
                loc.select_option(value=candidate)
                break
            except Exception:
                try:
                    loc.select_option(label=candidate)
                    break
                except Exception:
                    continue
        else:
            raise EtcMeisaiError(f"日付の設定に失敗しました: {prefix} {unit}={value}")


def run(login_id, password, date_from, date_to, save_dir, headless=False, log=print):
    """メイン処理。GUI からスレッドで呼ばれる。

    date_from / date_to: datetime.date
    log: 進捗メッセージを受け取る callable
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    period = f"{date_from:%Y%m%d}-{date_to:%Y%m%d}"
    saved = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True, locale="ja-JP")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            # --- ログイン ---
            log("ログインページを開いています...")
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded")
            except Exception:
                page.goto(TOP_URL, wait_until="domcontentloaded")
            _find(page, "login_id").fill(login_id)
            _find(page, "login_password").fill(password)
            _find(page, "login_button").click()
            page.wait_for_load_state("domcontentloaded")

            body = page.inner_text("body")
            for ng in ("パスワードが正しくありません", "ログインできません", "認証に失敗"):
                if ng in body:
                    raise EtcMeisaiError("ログインに失敗しました。IDとパスワードを確認してください。")
            log("ログインしました")

            # --- 明細検索画面へ ---
            def goto_search():
                _find(page, "search_menu").click()
                page.wait_for_load_state("domcontentloaded")

            goto_search()

            # --- カード(車両)一覧の取得 ---
            card_select = _find(page, "card_select")
            options = card_select.locator("option").all()
            cards = []
            for opt in options:
                value = opt.get_attribute("value") or ""
                label = (opt.inner_text() or "").strip()
                # 「すべて」「選択してください」のような項目は除外
                if not value or any(w in label for w in ("すべて", "全て", "選択")):
                    continue
                cards.append((value, label))
            if not cards:
                raise EtcMeisaiError("ETCカードの一覧を取得できませんでした。")
            log(f"カード {len(cards)} 件を検出: " + ", ".join(label for _, label in cards))

            # --- カードごとに検索してPDF保存 ---
            for i, (value, label) in enumerate(cards, 1):
                log(f"[{i}/{len(cards)}] {label}: 検索中 ({date_from} 〜 {date_to})")
                if i > 1:
                    goto_search()
                    card_select = _find(page, "card_select")
                card_select.select_option(value=value)

                # 「日付指定」ラジオがある画面構成なら選択する(無ければスキップ)
                try:
                    radio = page.locator(SELECTORS["date_radio"][0]).first
                    if radio.count() and radio.is_visible():
                        radio.check()
                except Exception:
                    pass

                _set_date(page, "from", date_from)
                _set_date(page, "to", date_to)
                _find(page, "search_button").click()
                page.wait_for_load_state("domcontentloaded")

                body = page.inner_text("body")
                if any(pat in body for pat in NO_DATA_PATTERNS):
                    log(f"  → 期間内の利用データなし。スキップします")
                    continue

                with page.expect_download() as dl_info:
                    _find(page, "pdf_button").click()
                dest = save_dir / f"{_sanitize_filename(label)}_{period}.pdf"
                dl_info.value.save_as(str(dest))
                saved.append(dest)
                log(f"  → 保存: {dest.name}")

            # --- ログアウト ---
            try:
                _find(page, "logout", timeout=5000).click()
                log("ログアウトしました")
            except Exception:
                log("ログアウトリンクが見つかりませんでした(セッションはブラウザ終了で切れます)")

            log(f"完了: {len(saved)} 件のPDFを {save_dir} に保存しました")
            return saved
        except Exception:
            _dump_error(page, log)
            raise
        finally:
            context.close()
            browser.close()

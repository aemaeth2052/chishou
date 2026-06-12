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

# paths を最初にimportして PLAYWRIGHT_BROWSERS_PATH を設定してから playwright を読み込む
from paths import log_dir  # noqa: I001 -- order matters

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = log_dir()

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
    saved_any = False
    try:
        (out / "url.txt").write_text(page.url, encoding="utf-8")
        saved_any = True
    except Exception:
        pass
    try:
        page.screenshot(path=str(out / "screenshot.png"), full_page=True)
        saved_any = True
    except Exception:
        pass
    try:
        (out / "page.html").write_text(page.content(), encoding="utf-8")
        saved_any = True
    except Exception:
        pass
    log(f"画面の状態を保存しました: {out}" if saved_any
        else f"画面の保存に失敗しました (フォルダのみ作成: {out})")
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

SEARCH_FORM_URL = (
    "https://www2.etc-meisai.jp/etc/R"
    "?funccode=1033000000&nextfunc=1033000000"
)


def _goto_search_form(page):
    """検索条件画面へ移動。メニューが見つからなければURL直叩きでフォールバック"""
    try:
        link = page.locator('a:has-text("検索条件の指定")').first
        link.wait_for(state="visible", timeout=4000)
        link.click()
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        page.goto(SEARCH_FORM_URL, wait_until="domcontentloaded")
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
    # 検索結果画面に到達したことの確認 (body が null の瞬間を避ける):
    page.wait_for_function(
        """() => {
            const b = document.body;
            if (!b) return false;
            if (document.querySelector('input[name="hakkoMeisai"]')) return true;
            const t = b.innerText || '';
            return t.includes('ご利用はありません') || t.includes('該当する');
        }""",
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


def _total_fare(page):
    """結果画面の「通行料金合計」を円(int)で返す。見つからなければ None"""
    body = page.inner_text("body")
    m = re.search(r"通行料金合計[\s\S]{0,80}?([0-9][0-9,]*)", body)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


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

def _unique_path(dest: Path) -> Path:
    """dest が存在する場合、_2, _3 ... と連番を付けた空きパスを返す"""
    if not dest.exists():
        return dest
    for n in range(2, 1000):
        cand = dest.with_name(f"{dest.stem}_{n}{dest.suffix}")
        if not cand.exists():
            return cand
    raise EtcMeisaiError(f"連番の空きがありません: {dest.name}")


def _append_history(rows):
    """実行履歴を logs/history.csv に追記する (Excelで開ける形式)"""
    import csv
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / "history.csv"
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["実行日時", "開始日", "終了日", "所属", "車両番号", "結果", "詳細"])
        w.writerows(rows)


def run(login_id, password, date_from, date_to, save_dir,
        vehicles=None, headless=False, dup_mode="rename",
        vehicle_info=None, log=print):
    """メイン処理。GUI からスレッドで呼ばれる。

    vehicles: [{"name": "所属", "number": "27"}, ...]
              空なら車両番号指定なしで1回だけ検索する。
    dup_mode: 同名ファイルがあるときの動作 "overwrite" / "rename" / "skip"
    vehicle_info: Hks番割から取り込んだ {車両番号: {"customer":…, "site":…}}。
                  あればPDFファイル名と按分レポートに反映する。
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
        targets = [{"name": "", "number": ""}]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.set_default_timeout(30000)

        def process(v):
            """1台分の処理。dict(status/detail/fare) を返す"""
            number = str(v.get("number") or "").strip()
            _goto_search_form(page)
            _set_search_conditions(page, number, date_from, date_to)
            _submit_search(page)
            fare = _total_fare(page)

            # ファイル名: 日付_車両ナンバー[_顧客_現場].pdf
            parts = [period, number or "全車両"]
            info = (vehicle_info or {}).get(number)
            if info:
                for key in ("customer", "site"):
                    p = _sanitize_filename(str(info.get(key) or ""))[:20]
                    if p:
                        parts.append(p)
            dest = save_dir / ("_".join(parts) + ".pdf")
            if dest.exists():
                if dup_mode == "skip":
                    return {"status": "skipped",
                            "detail": f"同名ファイルあり: {dest.name}", "fare": fare}
                if dup_mode == "rename":
                    dest = _unique_path(dest)
            if _download_pdf(page, dest, log):
                return {"status": "saved", "detail": dest.name, "fare": fare}
            return {"status": "no_data", "detail": "", "fare": fare}

        results = {}  # index -> {"status","detail","fare"}
        try:
            _login(page, login_id, password, log)

            def label_of(v):
                name = (v.get("name") or "").strip()
                number = str(v.get("number") or "").strip()
                return f"{name}({number})" if name and number else (name or f"車両{number}" or "全車両")

            # 1巡目: 失敗しても次の車両へ進む
            for i, v in enumerate(targets):
                log(f"[{i + 1}/{len(targets)}] {label_of(v)}: 検索中 ({date_from} 〜 {date_to})")
                try:
                    r = process(v)
                    results[i] = r
                    msgs = {
                        "saved": f"  → 保存: {r['detail']}",
                        "no_data": "  → 期間内の利用データなし",
                        "skipped": f"  → {r['detail']} のためスキップ",
                    }
                    log(msgs[r["status"]])
                except Exception as e:
                    results[i] = {"status": "failed", "detail": str(e), "fare": None}
                    log(f"  → 失敗: {e}")
                    _dump(page, log, prefix="error")

            # 2巡目: 失敗した車両だけ1回リトライ
            retry_idx = [i for i, r in results.items() if r["status"] == "failed"]
            if retry_idx:
                log(f"--- 失敗した {len(retry_idx)} 台をリトライします ---")
                for i in retry_idx:
                    v = targets[i]
                    log(f"[リトライ] {label_of(v)}")
                    try:
                        r = process(v)
                        results[i] = r
                        log(f"  → リトライ成功: {r['detail'] or '利用データなし'}")
                    except Exception as e:
                        results[i] = {"status": "failed", "detail": str(e), "fare": None}
                        log(f"  → リトライも失敗: {e}")

            try:
                page.locator('a:has-text("ログアウト")').first.click(timeout=5000)
                log("ログアウトしました")
            except Exception:
                pass
        except Exception:
            # ログイン失敗などの全体エラー
            _dump(page, log, prefix="error")
            raise
        finally:
            context.close()
            browser.close()

    # --- サマリと履歴 ---
    counts = {"saved": 0, "no_data": 0, "skipped": 0, "failed": 0}
    history_rows = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status_ja = {"saved": "保存", "no_data": "利用なし", "skipped": "スキップ", "failed": "失敗"}
    report_rows = []
    for i, v in enumerate(targets):
        r = results.get(i, {"status": "failed", "detail": "未処理", "fare": None})
        counts[r["status"]] += 1
        number = str(v.get("number") or "").strip()
        history_rows.append([
            now, str(date_from), str(date_to),
            (v.get("name") or "").strip(), number,
            status_ja[r["status"]], r["detail"],
        ])
        info = (vehicle_info or {}).get(number, {})
        report_rows.append([
            number,
            info.get("customer", ""), info.get("site", ""),
            str(date_from), str(date_to),
            "" if r["fare"] is None else r["fare"],
            status_ja[r["status"]], r["detail"],
        ])
    try:
        _append_history(history_rows)
    except Exception as e:
        log(f"履歴の記録に失敗しました: {e}")

    # --- 按分レポート (車両×顧客×現場×通行料金合計) ---
    try:
        import csv
        report_path = save_dir / f"按分レポート_{period}.csv"
        with report_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["車両番号", "顧客", "現場", "開始日", "終了日",
                        "通行料金合計(円)", "結果", "詳細"])
            w.writerows(report_rows)
        log(f"按分レポートを保存しました: {report_path.name}")
    except Exception as e:
        log(f"按分レポートの作成に失敗しました: {e}")

    log(f"=== 完了: 保存 {counts['saved']}台 / 利用なし {counts['no_data']}台"
        f" / スキップ {counts['skipped']}台 / 失敗 {counts['failed']}台 ===")
    if counts["failed"]:
        failed_labels = [
            f"{(targets[i].get('name') or '').strip()}({targets[i].get('number')})"
            for i, r in results.items() if r["status"] == "failed"
        ]
        log(f"失敗した車両: {', '.join(failed_labels)}")
        log("時間をおいて再実行するか、logs フォルダのエラー記録を確認してください")
    log(f"保存先: {save_dir} / 履歴: logs/history.csv")
    return counts

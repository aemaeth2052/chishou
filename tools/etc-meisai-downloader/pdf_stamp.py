# -*- coding: utf-8 -*-
"""利用明細PDFへの情報書き込み

PDF各ページの下部に「顧客:〇〇 / 現場:〇〇 / 運転:〇〇」を3行縦積みで
追記する。最下行(運転)がY_MM、上に積む(顧客が最上)。行間は font_size+6pt。
"""

import io
from pathlib import Path

FONT_NAME = "HeiseiKakuGo-W5"
DEFAULT_FONT_SIZE = 14
X_MM = 18          # 左端からの位置
Y_MM = 30          # 下端からの位置 (最下行の基準ベースライン)
LINE_GAP_PT = 6    # 行間 (フォントサイズに加えるベースライン間距離)


def _items(info: dict, opts: dict):
    """描画対象の行リスト。先頭=顧客(最上行)、末尾=運転(最下行)。"""
    items = []
    for key, prefix in (("customer", "顧客"), ("site", "現場"), ("driver", "運転")):
        if opts.get(key) and info.get(key):
            items.append(f"{prefix}：{info[key]}")
    return items


def has_anything_to_stamp(info: dict, opts: dict) -> bool:
    return bool(_items(info, opts))


def build_label(info: dict, opts: dict) -> str:
    """1行表現の代替フォーマット (按分レポート等に使用可)"""
    return "  ".join(_items(info, opts))


def stamp_pdf(path, info: dict, opts: dict, font_size: int = None) -> bool:
    """PDFの全ページに 顧客/現場/運転 を縦積みで書き込んで上書き保存する"""
    items = _items(info, opts)
    if not items:
        return False
    try:
        font_size = int(font_size or DEFAULT_FONT_SIZE)
    except (TypeError, ValueError):
        font_size = DEFAULT_FONT_SIZE
    font_size = max(6, min(font_size, 72))

    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    try:
        pdfmetrics.getFont(FONT_NAME)
    except Exception:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))

    path = Path(path)
    reader = PdfReader(str(path))
    writer = PdfWriter()
    n = len(items)
    gap_pt = font_size + LINE_GAP_PT
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        max_w = w - 2 * X_MM * mm

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))
        c.setFont(FONT_NAME, font_size)
        for i, text in enumerate(items):
            # i=0 (顧客) が最上、i=n-1 (運転) が最下行 (Y_MM mm)
            y_pt = Y_MM * mm + (n - 1 - i) * gap_pt
            # 1行ずつ独立に幅チェック → 必要なら末尾を「…」で詰める
            t = text
            while t and pdfmetrics.stringWidth(t, FONT_NAME, font_size) > max_w:
                t = t[:-2] + "…"
            c.drawString(X_MM * mm, y_pt, t)
        c.save()
        buf.seek(0)
        overlay = PdfReader(buf).pages[0]
        page.merge_page(overlay)
        writer.add_page(page)

    tmp = path.with_name(path.stem + ".stamp.tmp")
    with tmp.open("wb") as f:
        writer.write(f)
    tmp.replace(path)
    return True

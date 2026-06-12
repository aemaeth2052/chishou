# -*- coding: utf-8 -*-
"""利用明細PDFへの情報書き込み

PDF各ページの下部 (フッターロゴの上あたり) に
「顧客：〇〇　現場：〇〇　運転：〇〇」を1行追記する。

日本語フォントは reportlab 内蔵の CIDフォント (HeiseiKakuGo-W5) を使うため
フォントファイルの同梱は不要。
"""

import io
from pathlib import Path

FONT_NAME = "HeiseiKakuGo-W5"
FONT_SIZE = 27     # 9pt の3倍
X_MM = 18          # 左端からの位置
Y_MM = 30          # 下端からの位置 (フッターロゴの上)


def build_label(info: dict, opts: dict) -> str:
    """設定でONの項目だけを連結したラベル文字列を作る。情報が無ければ空文字"""
    parts = []
    if opts.get("customer") and info.get("customer"):
        parts.append(f"顧客：{info['customer']}")
    if opts.get("site") and info.get("site"):
        parts.append(f"現場：{info['site']}")
    if opts.get("driver") and info.get("driver"):
        parts.append(f"運転：{info['driver']}")
    return "　　".join(parts)


def stamp_pdf(path, label: str) -> bool:
    """PDFの全ページ下部に label を書き込んで上書き保存する"""
    if not label:
        return False
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
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)

        # ページ幅に収まるよう必要なら末尾を「…」で詰める
        text = label
        max_w = w - 2 * X_MM * mm
        while text and pdfmetrics.stringWidth(text, FONT_NAME, FONT_SIZE) > max_w:
            text = text[:-2] + "…"

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))
        c.setFont(FONT_NAME, FONT_SIZE)
        c.drawString(X_MM * mm, Y_MM * mm, text)
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

# -*- coding: utf-8 -*-
"""
地商ボールパーク 地域共生 運営構想
NotebookLM生成PDF（9枚, 16:9）を、後から文字編集できるネイティブPPTXで再現する。
イラストは図形で近似し、テキスト・表・レイアウトはすべて編集可能要素で構成する。
"""
from pptx import Presentation
from pptx.util import Emu, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.oxml.ns import qn

# ---- カラーパレット（PDFから採取） ----
NAVY      = RGBColor(0x1B, 0x3A, 0x5E)   # 濃紺（タイトル・主要図形）
NAVY_DK   = RGBColor(0x14, 0x2C, 0x47)
GREEN     = RGBColor(0x4F, 0x8C, 0x3F)   # 緑
GREEN_LT  = RGBColor(0xE6, 0xF0, 0xDF)
YELLOW    = RGBColor(0xEF, 0xB6, 0x1E)   # 黄
YELLOW_LT = RGBColor(0xFB, 0xF0, 0xD4)
ORANGE    = RGBColor(0xC4, 0x63, 0x3C)   # オレンジ茶
ORANGE_BR = RGBColor(0xE3, 0x8A, 0x5C)   # 明るいオレンジ
ORANGE_LT = RGBColor(0xF6, 0xE4, 0xDA)
BLUE      = RGBColor(0x3E, 0x6E, 0x9C)   # 中間ブルー
GRAY      = RGBColor(0x9A, 0xA3, 0xAE)
GRAY_LT   = RGBColor(0xE9, 0xEB, 0xEE)
GRAY_BOX  = RGBColor(0xCB, 0xCF, 0xD6)
BG        = RGBColor(0xF5, 0xF3, 0xEA)   # クリーム背景
WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
INK       = RGBColor(0x1B, 0x3A, 0x5E)
TXT_GRAY  = RGBColor(0x55, 0x5A, 0x60)

JP_FONT = "Yu Gothic"          # 本文（環境に応じMeiryo等にフォールバック）
JP_BOLD = "Yu Gothic"

EMU = 914400
def IN(v): return Emu(int(v * EMU))

prs = Presentation()
prs.slide_width  = IN(13.333)
prs.slide_height = IN(7.5)
BLANK = prs.slide_layouts[6]
SW, SH = 13.333, 7.5


# ============ ヘルパ ============
def add_slide(bg=BG):
    s = prs.slides.add_slide(BLANK)
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, IN(0), IN(0), IN(SW), IN(SH))
    r.fill.solid(); r.fill.fore_color.rgb = bg
    r.line.fill.background()
    r.shadow.inherit = False
    return s

def _set_font(run, size, color, bold, font=None):
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.bold = bold
    run.font.name = font or JP_FONT
    # 東アジアフォント指定
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn('a:ea'))
    if ea is None:
        ea = rPr.makeelement(qn('a:ea'), {})
        rPr.append(ea)
    ea.set('typeface', font or JP_FONT)

def textbox(s, x, y, w, h, lines, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
            wrap=True):
    """lines: list of (text, size, color, bold) or list of such -> paragraphs"""
    tb = s.shapes.add_textbox(IN(x), IN(y), IN(w), IN(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = 0; tf.margin_right = 0
    tf.margin_top = 0; tf.margin_bottom = 0
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if isinstance(ln, tuple):
            ln = [ln]
        for seg in ln:
            text, size, color, bold = seg[0], seg[1], seg[2], seg[3]
            run = p.add_run(); run.text = text
            _set_font(run, size, color, bold, seg[4] if len(seg) > 4 else None)
    return tb

def set_text(shape, lines, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE):
    tf = shape.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = Pt(6); tf.margin_right = Pt(6)
    tf.margin_top = Pt(3); tf.margin_bottom = Pt(3)
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if isinstance(ln, tuple):
            ln = [ln]
        for seg in ln:
            run = p.add_run(); run.text = seg[0]
            _set_font(run, seg[1], seg[2], seg[3], seg[4] if len(seg) > 4 else None)

def shape(s, kind, x, y, w, h, fill=None, line=None, line_w=1.0,
          shadow=False, round_=None):
    sp = s.shapes.add_shape(kind, IN(x), IN(y), IN(w), IN(h))
    if fill is None:
        sp.fill.background()
    else:
        sp.fill.solid(); sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line; sp.line.width = Pt(line_w)
    sp.shadow.inherit = False
    if shadow:
        _soft_shadow(sp)
    if round_ is not None:
        try:
            sp.adjustments[0] = round_
        except Exception:
            pass
    return sp

def _soft_shadow(sp):
    spPr = sp._element.spPr
    el = spPr.makeelement(qn('a:effectLst'), {})
    sh = spPr.makeelement(qn('a:outerShdw'),
        {'blurRad':'40000','dist':'25000','dir':'5400000','rotWithShape':'0'})
    clr = spPr.makeelement(qn('a:srgbClr'), {'val':'2A2A2A'})
    alpha = spPr.makeelement(qn('a:alpha'), {'val':'24000'})
    clr.append(alpha); sh.append(clr); el.append(sh); spPr.append(el)

def title(s, lines, x=0.62, y=0.45, w=8.0, accent=True):
    if accent:
        shape(s, MSO_SHAPE.RECTANGLE, x, y+0.04, 0.11, 0.34*len(lines)+0.18,
              fill=NAVY)
    textbox(s, x+0.28, y, w, 1.6,
            [[(t, 30, NAVY, True)] for t in lines],
            anchor=MSO_ANCHOR.TOP)

def footer(s):
    textbox(s, SW-2.1, SH-0.42, 1.9, 0.3,
            [[("◈ NotebookLM", 9, GRAY, False)]], align=PP_ALIGN.RIGHT)


# ==================================================================
# Slide 1 : タイトル
# ==================================================================
s = add_slide()
# 外枠の細線
fr = shape(s, MSO_SHAPE.RECTANGLE, 0.35, 0.35, SW-0.70, SH-0.70,
           fill=None, line=RGBColor(0xD9,0xD5,0xC7), line_w=0.75)
# 左：野球場ラインアート（簡略）――扇形＋ダイヤ
# 観客席風の同心の弧（色付き）
import math
cx, cy = 3.05, 3.35
# サンバースト風の弧セグメントを近似（緑〜黄のブロック）
for ri, (rad, col) in enumerate([(2.35, GREEN), (1.95, YELLOW), (1.55, GREEN_LT)]):
    pass
# 簡略：上半分の扇（塗り）を重ねる
arc_colors = [GREEN, RGBColor(0x8F,0xB5,0x7F), YELLOW, RGBColor(0xF2,0xD0,0x8A)]
# ブロック状のスタンドを表現
seg_specs = [
    (2.55, GREEN), (2.15, RGBColor(0x9C,0xC0,0x89)), (1.78, YELLOW), (1.42, RGBColor(0xF4,0xD8,0x9B)),
]
for rad, col in seg_specs:
    blk = shape(s, MSO_SHAPE.BLOCK_ARC, cx-rad, cy-rad, rad*2, rad*2, fill=col)
    try:
        blk.adjustments[0] = 180.0     # start angle
        blk.adjustments[1] = 360.0     # end angle (上半分)
    except Exception:
        pass
# 内野ダイヤ（白で隠す→グラウンド）
shape(s, MSO_SHAPE.RECTANGLE, cx-2.7, cy-0.02, 5.4, 2.9, fill=BG)
# ダイヤモンド
dia = shape(s, MSO_SHAPE.DIAMOND, cx-0.85, cy+0.25, 1.7, 1.7,
            fill=None, line=NAVY, line_w=1.6)
# ホームからの扇ライン
shape(s, MSO_SHAPE.CHORD, cx-2.4, cy, 4.8, 4.8, fill=None, line=NAVY, line_w=1.2)
# ピッチャーマウンド円
shape(s, MSO_SHAPE.OVAL, cx-0.18, cy+0.95, 0.36, 0.36, fill=None, line=NAVY, line_w=1.2)

# 右：タイトル
textbox(s, 7.0, 2.05, 6.0, 1.9,
        [[("地商ボールパークの地域", 38, NAVY, True)],
         [("共生に向けた運営構想", 38, NAVY, True)]])
textbox(s, 7.0, 3.85, 6.0, 1.3,
        [[("加曽利町における「開かれた施設」", 18, TXT_GRAY, False)],
         [("への移行と確実な管理体制の構築", 18, TXT_GRAY, False)]])
shape(s, MSO_SHAPE.RECTANGLE, 7.02, 5.35, 2.0, 0.03, fill=NAVY)
textbox(s, 7.0, 5.55, 6.0, 0.5,
        [[("千葉市 ご説明資料 / 2026年6月", 16, NAVY, True)]])
footer(s)


# ==================================================================
# Slide 2 : 単なる野球グラウンドから → 交流の場へ
# ==================================================================
s = add_slide()
title(s, ["単なる野球グラウンドから、", "地域に開かれた交流の場へ"], y=0.5)

# 左ボックス（現在）
lb = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.7, 2.05, 4.85, 4.7,
           fill=WHITE, line=NAVY, line_w=1.5, round_=0.04)
textbox(s, 0.95, 2.3, 4.4, 0.9,
        [[("【現在】野球・スポーツ利用を中心とした", 14.5, NAVY, True)],
         [("単一目的の施設", 14.5, NAVY, True)]])
# 現在：閉じたグラウンド（フェンス＋バッター）の近似
shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 1.05, 3.35, 4.15, 3.1,
      fill=RGBColor(0xEE,0xEE,0xE9), line=GRAY, line_w=1.0, round_=0.03)
# フェンス格子（縦線）
for i in range(9):
    xx = 1.25 + i*0.46
    shape(s, MSO_SHAPE.RECTANGLE, xx, 3.55, 0.012, 1.3, fill=GRAY)
for j in range(3):
    yy = 3.55 + j*0.45
    shape(s, MSO_SHAPE.RECTANGLE, 1.25, yy, 3.95, 0.012, fill=GRAY)
# ゲート
shape(s, MSO_SHAPE.RECTANGLE, 2.05, 5.0, 2.05, 1.25, fill=RGBColor(0xE0,0xE0,0xDA),
      line=GRAY, line_w=1.2)
shape(s, MSO_SHAPE.RECTANGLE, 3.07, 5.0, 0.02, 1.25, fill=GRAY)
# バッター（シルエット簡略）
shape(s, MSO_SHAPE.OVAL, 2.95, 3.55, 0.28, 0.28, fill=NAVY)
shape(s, MSO_SHAPE.RECTANGLE, 3.0, 3.83, 0.18, 0.55, fill=NAVY)

# 右ボックス（今後）
rb = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 7.78, 2.05, 4.85, 4.7,
           fill=WHITE, line=NAVY, line_w=1.5, round_=0.04)
textbox(s, 8.03, 2.3, 4.4, 0.9,
        [[("【今後】平時から人が集まり、顔の見える", 14.5, NAVY, True)],
         [("関係をつくる多目的スペース", 14.5, NAVY, True)]])
# 多世代の人々（色付き丸で表現：高齢者=緑、家族=黄、若者=オレンジ）
people = [(8.6,3.7,GREEN),(11.2,3.55,YELLOW),(11.75,3.7,YELLOW),
          (10.7,3.8,YELLOW),(11.95,4.0,YELLOW),
          (9.4,5.4,ORANGE),(10.0,5.5,ORANGE),(10.6,5.4,ORANGE)]
for (px,py,col) in people:
    shape(s, MSO_SHAPE.OVAL, px, py, 0.32, 0.32, fill=col)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, px-0.02, py+0.34, 0.36, 0.5, fill=col, round_=0.4)
# ネットワーク線（ベンチ等は省略しテキストで補完しない＝図のみ）
shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 9.2, 5.75, 2.4, 0.5, fill=RGBColor(0xD8,0xCB,0xBE),
      line=ORANGE, line_w=1.0, round_=0.3)

# 中央：3本の矢印
arrows = [
    (3.05, ["特定層の利用", "→ 多世代の交流", "（少年野球から高齢者まで）"], GREEN),
    (4.25, ["週末だけの稼働", "→ 平日を含めた", "日常的な活用"], YELLOW),
    (5.45, ["スポーツ専用", "→ 学び・健康づくり・", "地域行事の舞台へ"], ORANGE),
]
for (ay, txts, col) in arrows:
    ar = shape(s, MSO_SHAPE.RIGHT_ARROW, 5.7, ay, 2.0, 1.0, fill=WHITE,
               line=col, line_w=2.0)
    try:
        ar.adjustments[0]=0.55; ar.adjustments[1]=0.55
    except Exception: pass
    lines=[]
    for t in txts:
        if t.startswith("（"):
            lines.append([(t, 8.5, TXT_GRAY, False)])
        elif t.startswith("→"):
            lines.append([(t, 11.5, col, True)])
        else:
            lines.append([(t, 11.5, NAVY, True)])
    textbox(s, 5.62, ay-0.02, 1.85, 1.0, lines, align=PP_ALIGN.CENTER,
            anchor=MSO_ANCHOR.MIDDLE)
footer(s)


# ==================================================================
# Slide 3 : 一般社団法人 設立の目的（比較表）
# ==================================================================
s = add_slide()
title(s, ["責任主体を明確化する", "「一般社団法人」設立の目的"], y=0.5)

row_labels = ["営利性・公益性", "責任の所在", "会計・記録管理"]
col_x = [2.65, 5.55, 8.45]
col_w = 2.7
head_y = 1.95
row_y = [2.95, 4.05, 5.15]
row_h = 1.0
# 行ラベル
for i,rl in enumerate(row_labels):
    shape(s, MSO_SHAPE.RECTANGLE, 0.7, row_y[i], 1.75, row_h,
          fill=RGBColor(0xCE,0xD6,0xE2))
    textbox(s, 0.72, row_y[i], 1.71, row_h, [[(rl,13,NAVY,True)]],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

cols = [
    ("株式会社", WHITE, NAVY, [("営利目的の","印象が強い"),("明確",),("厳格",)], False),
    ("任意団体", WHITE, NAVY, [("公益性は高いが","持続性に課題"),("曖昧になりやすい",),("不透明になるリスク",)], False),
    ("一般社団法人", YELLOW_LT, NAVY, [("公益性を担保し","地域貢献に専念できる"),("法人格により責任主体が明確",),("厳格なルールと","記録管理の徹底")], True),
]
for ci,(name,fill,txtc,cells,hi) in enumerate(cols):
    x = col_x[ci]
    lc = YELLOW if hi else NAVY
    lw = 2.5 if hi else 1.2
    # ヘッダ
    hd = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, head_y, col_w, 0.85,
               fill=fill, line=lc, line_w=lw, round_=0.08, shadow=hi)
    textbox(s, x, head_y, col_w, 0.85, [[(name, 18, NAVY, True)]],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    # ハイライト列のチェックバッジ
    if hi:
        bd = shape(s, MSO_SHAPE.OVAL, x+col_w-0.55, head_y-0.35, 0.7, 0.7,
                   fill=YELLOW)
        textbox(s, x+col_w-0.55, head_y-0.35, 0.7, 0.7, [[("✓",20,WHITE,True)]],
                align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    # セル
    for ri,cell in enumerate(cells):
        cy_ = row_y[ri]
        cf = YELLOW_LT if hi else WHITE
        shape(s, MSO_SHAPE.RECTANGLE, x, cy_, col_w, row_h,
              fill=cf, line=(YELLOW if hi else NAVY), line_w=(1.5 if hi else 1.0))
        textbox(s, x, cy_, col_w, row_h, [[(t,12.5,NAVY,False)] for t in cell],
                align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# 下部メッセージ帯
shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.7, 6.35, SW-1.4, 0.78,
      fill=NAVY, round_=0.18)
textbox(s, 0.9, 6.35, SW-1.8, 0.78,
        [[("地域や関係団体と連携する際の「信頼できる窓口」として、一般社団法人を設立します。",
           16, WHITE, True)]], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
footer(s)


# ==================================================================
# Slide 4 : 各主体の役割分担（中央ダイヤ＋4方向）
# ==================================================================
s = add_slide()
title(s, ["各主体の役割を分担した確実な運営体制"], y=0.5, w=9.0)
# 右上注記
nb = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 9.4, 0.5, 3.3, 0.95,
           fill=BG, line=YELLOW, line_w=1.2, round_=0.12)
textbox(s, 9.5, 0.5, 3.1, 0.95,
        [[("「責任者、連絡窓口、現場管理者を",12.5,NAVY,True)],
         [("明確に分離・配置します」",12.5,NAVY,True)]],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# 中央ダイヤ
dcx, dcy = 6.55, 4.25
dia = shape(s, MSO_SHAPE.DIAMOND, dcx-1.45, dcy-1.45, 2.9, 2.9, fill=NAVY)
textbox(s, dcx-1.25, dcy-0.75, 2.5, 1.5,
        [[("一般社団法人",17,WHITE,True)],
         [("",4,WHITE,False)],
         [("企画、運営方針、地域連携、",11,WHITE,False)],
         [("公益性の担保",11,WHITE,False)]],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# 4方向ボックス
def role_box(x,y,w,h,head,body,col):
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=WHITE,
          line=col, line_w=2.2, round_=0.1)
    textbox(s, x+0.1, y+0.12, w-0.2, h-0.2,
            [[(head,15,NAVY,True)]]+[[(b,11,TXT_GRAY,False)] for b in body],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

role_box(4.95, 1.65, 3.2, 0.95, "行政・関係機関",
         ["地域活動に関する助言、必要に応じた連携"], ORANGE)
role_box(0.7, 3.55, 3.2, 1.4, "地商総業",
         ["土地・施設に関する方針、","必要な維持管理"], BLUE)
role_box(9.2, 3.55, 3.2, 1.4, "地域団体",
         ["利用、協力、イベント、","地域活動への参加"], GREEN)
role_box(4.95, 5.9, 3.2, 0.95, "実務協力会社",
         ["清掃、草刈り、駐車場管理、設備確認、現場対応"], GRAY)

# 双方向矢印
def darrow(x,y,w,h,vert=False):
    k = MSO_SHAPE.UP_DOWN_ARROW if vert else MSO_SHAPE.LEFT_RIGHT_ARROW
    a = shape(s, k, x, y, w, h, fill=NAVY)
    return a
darrow(6.35, 2.68, 0.4, 0.45, vert=True)   # 上（行政→法人）
darrow(6.35, 5.35, 0.4, 0.45, vert=True)   # 下（法人→実務）
darrow(3.95, 4.05, 0.9, 0.4)               # 左
darrow(8.25, 4.05, 0.9, 0.4)               # 右
footer(s)


# ==================================================================
# Slide 5 : 平時の管理体制（オレンジ帯＋3カラム）
# ==================================================================
s = add_slide()
title(s, ["理念だけでは不十分。最優先される「平時の管理体制」"], y=0.5, w=12.0)
# オレンジ帯
shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.7, 1.55, SW-1.4, 1.35,
      fill=ORANGE_BR, round_=0.08)
textbox(s, 0.9, 1.55, SW-1.8, 1.35,
        [[("地域に安心して使っていただくため、",19,NAVY_DK,True)],
         [("まずは平時の管理体制を整えることを最優先にします。",19,NAVY_DK,True)]],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

cols5 = [
    ("1. 施設・インフラ管理",
     ["駐車場利用ルール","（無断駐車・放置車両対応）","ゴミ、清掃、草刈りの徹底",
      "トイレ・衛生環境の改善","鍵・設備・備品の管理"]),
    ("2. 安全・トラブル対応",
     ["事故・ケガ・トラブル","発生時の対応","近隣苦情への迅速な対応",
      "緊急時の連絡体制の確立"]),
    ("3. 運営・記録管理",
     ["利用申込ルールの厳格化","利用時間・方法の明確化",
      "活動記録・会計記録の","確実な保存"]),
]
cx5 = [0.85, 4.78, 8.71]
cw5 = 3.65
for ci,(head,items) in enumerate(cols5):
    x = cx5[ci]
    # 立体箱の本体
    shape(s, MSO_SHAPE.RECTANGLE, x, 3.35, cw5, 3.55, fill=GRAY_BOX)
    # ヘッダ帯
    shape(s, MSO_SHAPE.RECTANGLE, x, 3.35, cw5, 0.7, fill=NAVY)
    textbox(s, x, 3.35, cw5, 0.7, [[(head,15.5,WHITE,True)]],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    # 中の白タグ：2行ずつまとめる
    # itemsを「項目」に整形（連続行はまとめる）
    chips = []
    buf = []
    for it in items:
        if it.startswith("（") or (buf and (it.startswith("確実") or it.endswith("対応") and buf[0].startswith("事故")) ):
            buf.append(it)
        else:
            if buf: chips.append(buf)
            buf=[it]
    if buf: chips.append(buf)
    # シンプルに：手動でグルーピング
    groups_map = {
        0: [["駐車場利用ルール","（無断駐車・放置車両対応）"],["ゴミ、清掃、草刈りの徹底"],
            ["トイレ・衛生環境の改善"],["鍵・設備・備品の管理"]],
        1: [["事故・ケガ・トラブル","発生時の対応"],["近隣苦情への迅速な対応"],
            ["緊急時の連絡体制の確立"]],
        2: [["利用申込ルールの厳格化"],["利用時間・方法の明確化"],
            ["活動記録・会計記録の","確実な保存"]],
    }
    groups = groups_map[ci]
    cy_ = 4.25
    for g in groups:
        gh = 0.5 if len(g)==1 else 0.72
        shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x+0.3, cy_, cw5-0.6, gh,
              fill=WHITE, round_=0.18)
        textbox(s, x+0.32, cy_, cw5-0.64, gh, [[(t,12,NAVY,False)] for t in g],
                align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        cy_ += gh + 0.16
footer(s)


# ==================================================================
# Slide 6 : 生活リズムに合わせた施設の在り方（ピラミッド3段）
# ==================================================================
s = add_slide()
title(s, ["地域の生活リズムに合わせた、", "新しい施設の在り方"], y=0.5)

tiers = [
    (GREEN, "【週末】", "スポーツと活気",
     ["少年野球","地域スポーツ","家族の交流","地域イベント"], 4.05, 9.2),
    (YELLOW, "【平日】", "地域活動と居場所",
     ["高齢者の健康づくり","ウォーキング","学び・交流・見守りの場"], 2.7, 10.6),
    (NAVY, "【常時】", "管理と安全",
     ["清掃","草刈り","駐車場管理","トイレ管理","安全管理","近隣対応","緊急連絡"], 1.3, 12.0),
]
ty = 2.65
for (col,tag,headtxt,chips,xstart,width) in tiers:
    th = 1.25
    cx_ = SW/2
    x = cx_ - width/2
    txc = WHITE if col==NAVY else (NAVY if col==YELLOW else WHITE)
    bar = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, ty, width, th, fill=col, round_=0.07)
    # タグ＋見出し
    textbox(s, x+0.35, ty+0.12, 1.7, th-0.2, [[(tag, 19, txc, True)]],
            align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
    textbox(s, x+2.0, ty+0.1, width-2.3, 0.6, [[(headtxt, 23, txc, True)]],
            align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP)
    # チップ
    chipy = ty+0.78
    chipx = x+2.05
    for c in chips:
        cw_ = 0.34 + len(c)*0.205
        chip = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, chipx, chipy, cw_, 0.36,
                     fill=(WHITE if col!=YELLOW else WHITE),
                     line=col, line_w=1.0, round_=0.5)
        textbox(s, chipx, chipy, cw_, 0.36, [[(c, 10.5, NAVY, True)]],
                align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        chipx += cw_ + 0.14
    ty += th + 0.12

# 下メッセージ
shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.9, 6.75, SW-1.8, 0.6, fill=WHITE,
      line=NAVY, line_w=1.3, round_=0.4)
textbox(s, 0.9, 6.75, SW-1.8, 0.6,
        [[("週末はスポーツ、平日は地域活動。そして常時は「管理と安全」が支えます。",
           15, NAVY, True)]], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
footer(s)


# ==================================================================
# Slide 7 : 還元される多様な地域プログラム
# ==================================================================
s = add_slide()
title(s, ["加曽利町に還元される多様な地域プログラム"], y=0.5, w=11.0)
# 左：Field ダイヤ＋同心円
fcx, fcy = 2.85, 4.1
for rad in (2.0, 1.55, 1.1):
    shape(s, MSO_SHAPE.OVAL, fcx-rad, fcy-rad, rad*2, rad*2,
          fill=None, line=RGBColor(0xC9,0xCF,0xD8), line_w=1.0)
fd = shape(s, MSO_SHAPE.DIAMOND, fcx-0.95, fcy-0.95, 1.9, 1.9, fill=BLUE)
shape(s, MSO_SHAPE.OVAL, fcx-0.62, fcy-0.62, 1.24, 1.24, fill=None, line=RGBColor(0xBF,0xD2,0xE6), line_w=1.0)
textbox(s, fcx-0.95, fcy-0.95, 1.9, 1.9, [[("Field",18,WHITE,True)]],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# 右：3カテゴリカード
cards = [
    (GREEN, "スポーツ・健康",
     ["⚾  少年野球・地域スポーツへの協力","👟  高齢者向けの軽運動・健康づくり推進"]),
    (YELLOW, "学び・就労支援",
     ["📖  日本語教室・学習支援の提供","🤝  職業訓練・就労に向けた支援の実施"]),
    (ORANGE, "地域交流・自治",
     ["👪  親子向けイベントの開催","🏠  自治会・地域団体との連携および地域行事への協力","🧹  地域清掃活動の共同実施"]),
]
cy7 = 1.5
cardx = 6.6
cardw = 6.1
for (col,head,items) in cards:
    ch = 0.62 + len(items)*0.4 + 0.12
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, cardx, cy7, cardw, ch, fill=WHITE,
          line=col, line_w=1.2, round_=0.06, shadow=True)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, cardx, cy7, cardw, 0.55, fill=col, round_=0.06)
    shape(s, MSO_SHAPE.RECTANGLE, cardx, cy7+0.28, cardw, 0.27, fill=col)
    txc = NAVY if col==YELLOW else WHITE
    textbox(s, cardx+0.25, cy7, cardw-0.4, 0.55, [[(head,16,txc,True)]],
            align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
    iy = cy7+0.62
    for it in items:
        textbox(s, cardx+0.3, iy, cardw-0.6, 0.4, [[(it,12.5,NAVY,False)]],
                align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
        iy += 0.4
    # コネクタ線
    shape(s, MSO_SHAPE.RECTANGLE, fcx+1.4, cy7+ch/2-0.01, cardx-(fcx+1.4), 0.025, fill=col)
    cy7 += ch + 0.14

# 右下注記
note = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 9.3, 6.9, 3.4, 0.52,
             fill=WHITE, line=NAVY, line_w=1.0, round_=0.12)
textbox(s, 9.35, 6.9, 3.3, 0.52,
        [[("地元の方々や関係団体との関係づくりを大切にし、",9,NAVY,False)],
         [("無理のない形で活動を広げます。",9,NAVY,False)]],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
footer(s)


# ==================================================================
# Slide 8 : 段階的な育成プロセス（Step1-4）
# ==================================================================
s = add_slide()
title(s, ["段階的な育成プロセス：", "最初から「完成形」を求めない"], y=0.5)

steps = [
    (NAVY,   "Step 1:", "基盤整備",
     "管理責任者・連絡窓口の明確化、現地の利用状況と課題の整理。"),
    (ORANGE, "Step 2:", "体制構築",
     "駐車場・トイレ・清掃の管理体制づくり、利用ルールの作成、会計管理の整備。"),
    (YELLOW, "Step 3:", "地域対話",
     "地域活動に使いやすい運営方法の検討、関係団体との綿密な意見交換。"),
    (GREEN,  "Step 4:", "段階的開放",
     "日常管理を徹底しながら、段階的に「地域に開かれた施設」として運用開始。"),
]
base_x = 1.0
base_y = 5.7
dx = 3.0
dy = -0.78
box = 1.55
for i,(col,st,name,desc) in enumerate(steps):
    x = base_x + i*dx
    y = base_y + i*dy
    # 立体ブロック（影付き角丸）
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, box, box, fill=col,
          round_=0.12, shadow=True)
    txc = NAVY if col==YELLOW else WHITE
    textbox(s, x, y+0.3, box, box-0.5,
            [[(st,14,txc,True)],[(name,15,txc,True)]],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    # 説明（ブロック上の吹き出し位置）
    textbox(s, x-0.55, y-1.15, box+1.1, 1.0,
            [[(desc,10.5,TXT_GRAY,False)]],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.BOTTOM)

# 右下メッセージ
shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 8.8, 5.45, 3.95, 1.45, fill=NAVY, round_=0.06)
textbox(s, 9.0, 5.45, 3.6, 1.45,
        [[("大きな形を急ぐのではなく、",16,WHITE,True)],
         [("日常管理を整えながら施設を",16,WHITE,True)],
         [("育てていきます。",16,WHITE,True)]],
        align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.MIDDLE)
footer(s)


# ==================================================================
# Slide 9 : まとめ（3つの歯車）
# ==================================================================
s = add_slide()
title(s, ["まとめ：民間施設を「公共の価値」へ変換する共生モデル"], y=0.45, w=12.0)

gears = [
    (BLUE,  3.0,  3.6, "1", "［確実な法人運営］", ["一般社団法人による","責任とルールの徹底"]),
    (ORANGE,6.55, 4.3, "2", "［盤石な日常管理］", ["安全、衛生、駐車場、","連絡体制の維持"]),
    (GREEN, 10.1, 3.6, "3", "［豊かな地域活用］", ["週末のスポーツ、","平日の学びと居場所"]),
]
for (col,gx,gy,num,head,body) in gears:
    GR = 2.0
    # 歯車（GEARは python-pptx に GEAR_6 / GEAR_9）
    g = shape(s, MSO_SHAPE.GEAR_9, gx-GR/2, gy-GR/2, GR, GR, fill=col, shadow=True)
    # 中央の白円
    cr = 1.35
    shape(s, MSO_SHAPE.OVAL, gx-cr/2, gy-cr/2, cr, cr, fill=WHITE, line=col, line_w=1.5)
    textbox(s, gx-cr/2, gy-cr/2+0.05, cr, 0.45, [[(num,22,col,True)]],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.TOP)
    textbox(s, gx-cr/2-0.15, gy-0.1, cr+0.3, 0.8,
            [[(head,11.5,NAVY,True)]]+[[(b,9.5,TXT_GRAY,False)] for b in body],
            align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.TOP)

# 中央 Kyosei Engine
textbox(s, 5.55, 1.95, 2.0, 0.8,
        [[("Kyosei",15,NAVY,True)],[("Engine",15,NAVY,True)]],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

# 下帯
shape(s, MSO_SHAPE.RECTANGLE, 0.45, 6.05, SW-0.9, 1.1, fill=NAVY)
textbox(s, 0.7, 6.05, SW-1.4, 1.1,
        [[("地商ボールパークは、確かな管理体制（一般社団法人）を土台として、",15,WHITE,True)],
         [("千葉市加曽利町に貢献する「地域に開かれた民間施設」へと進化します。",15,WHITE,True)]],
        align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
footer(s)


import os
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "地商ボールパーク_地域共生_運営構想.pptx")
prs.save(out)
print("saved:", out, "slides:", len(prs.slides._sldIdLst))

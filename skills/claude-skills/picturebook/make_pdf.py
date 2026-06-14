#!/usr/bin/env python3
"""Assemble a picture book into a page-through PDF (one sentence per page).

Usage:
    python3 make_pdf.py <out.pdf> <book.json>

book.json:
    {
      "title": "波波和 X-Wing",
      "subtitle": "A first reader for Theo ...",
      "glossary": [["狐猴","húhóu","lemur"], ...],   # optional
      "pages": [{"image": "/abs/bobo-01.png", "caption": "波波是一只小狐猴。"}, ...]
    }

Renders a title/glossary page, then one page per sentence (image + caption).
Pure Pillow; uses a macOS CJK font so Chinese renders correctly.
"""
import sys, json, os
from PIL import Image, ImageDraw, ImageFont

W, H = 1240, 1640                      # ~A4 @150dpi portrait
MARGIN = 70
FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]

def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size, index=0)
            except Exception:
                pass
    return ImageFont.load_default()

def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        if draw.textlength(cur + ch, font=fnt) <= max_w:
            cur += ch
        else:
            lines.append(cur); cur = ch
    if cur:
        lines.append(cur)
    return lines

def text_block(draw, x, y, text, fnt, max_w, fill=(40, 40, 50), center_w=None, gap=14):
    for ln in wrap(draw, text, fnt, max_w):
        w = draw.textlength(ln, font=fnt)
        lx = x + (center_w - w) / 2 if center_w else x
        draw.text((lx, y), ln, font=fnt, fill=fill)
        y += fnt.size + gap
    return y

def title_page(book):
    img = Image.new("RGB", (W, H), (255, 252, 245))
    d = ImageDraw.Draw(img)
    y = 260
    y = text_block(d, MARGIN, y, book.get("title", ""), font(96), W - 2 * MARGIN,
                   fill=(30, 30, 40), center_w=W - 2 * MARGIN, gap=24)
    if book.get("subtitle"):
        y += 30
        y = text_block(d, MARGIN, y, book["subtitle"], font(40), W - 2 * MARGIN,
                       fill=(110, 110, 120), center_w=W - 2 * MARGIN, gap=12)
    gloss = book.get("glossary") or []
    if gloss:
        y += 90
        y = text_block(d, MARGIN, y, "生词 — read these together first",
                       font(48), W - 2 * MARGIN, fill=(180, 90, 40))
        y += 24
        f = font(52)
        for word, pinyin, eng in gloss:
            d.text((MARGIN + 20, y), word, font=f, fill=(30, 30, 40))
            d.text((MARGIN + 320, y), pinyin, font=font(44), fill=(90, 110, 160))
            d.text((MARGIN + 660, y), eng, font=font(44), fill=(90, 90, 100))
            y += 78
    return img

def story_page(page):
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    pic_path = page["image"]
    pic = Image.open(pic_path).convert("RGB")
    max_pic_w = W - 2 * MARGIN
    max_pic_h = H - 460
    scale = min(max_pic_w / pic.width, max_pic_h / pic.height)
    pw, ph = int(pic.width * scale), int(pic.height * scale)
    pic = pic.resize((pw, ph), Image.LANCZOS)
    px = (W - pw) // 2
    py = MARGIN + 20
    img.paste(pic, (px, py))
    cap_y = py + ph + 60
    text_block(d, MARGIN, cap_y, page["caption"], font(72), W - 2 * MARGIN,
               fill=(30, 30, 40), center_w=W - 2 * MARGIN, gap=18)
    return img

def main():
    if len(sys.argv) < 3:
        print("usage: make_pdf.py <out.pdf> <book.json>"); return 2
    out = os.path.expanduser(sys.argv[1])
    book = json.loads(open(os.path.expanduser(sys.argv[2]), encoding="utf-8").read())
    pages = [title_page(book)] + [story_page(p) for p in book["pages"]]
    pages[0].save(out, save_all=True, append_images=pages[1:], resolution=150.0)
    print(f"PDF written: {out}  ({len(pages)} pages)")
    return 0

if __name__ == "__main__":
    sys.exit(main())

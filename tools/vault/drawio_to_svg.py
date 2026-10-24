#!/usr/bin/env python3
"""drawio_to_svg — turn a plain .drawio into a .drawio.svg Obsidian can show.

Renders boxes, HTML labels (bold / colored / small spans, <br> lines) and
orthogonal connectors from the diagram itself, and embeds the original
mxfile in the SVG's `content` attribute, so draw.io still opens the result
as a fully editable diagram (and re-renders it exactly on its next save).
Covers the vault's org charts (rects + elbow edges); not a general renderer.

Usage: drawio_to_svg.py in.drawio [out.drawio.svg]   (2026-10-23)
"""
import html, re, sys
import xml.etree.ElementTree as ET
from pathlib import Path

FONT = "Helvetica,Arial,sans-serif"


def style(s):
    out = {}
    for kv in (s or "").split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1); out[k] = v
        elif kv:
            out[kv] = "1"
    return out


def label_lines(value):
    """'<b>Name</b><br><span style="color:#555">Title</span>' →
    [(text, bold, color, size)]"""
    out = []
    for part in re.split(r"<br\s*/?>", value or ""):
        col = re.search(r"color:\s*(#[0-9a-fA-F]{3,6})", part)
        size = re.search(r"font-size:\s*(\d+)px", part)
        txt = html.unescape(re.sub(r"<[^>]+>", "", part)).strip()
        if txt:
            out.append((txt, "<b>" in part, col.group(1) if col else None, int(size.group(1)) if size else None))
    return out


def convert(src: Path, dst: Path):
    raw = src.read_text(encoding="utf-8")
    root = ET.fromstring(raw)
    cells = list(root.iter("mxCell"))
    geo = {}
    for c in cells:
        g = c.find("mxGeometry")
        if c.get("vertex") and g is not None:
            geo[c.get("id")] = tuple(float(g.get(k, 0)) for k in ("x", "y", "width", "height"))
    if not geo:
        sys.exit("no shapes found (compressed diagram? save uncompressed)")
    x0 = min(x for x, *_ in geo.values()) - 40; y0 = min(y for _, y, *_ in geo.values()) - 40
    x1 = max(x + w for x, _, w, _ in geo.values()) + 40; y1 = max(y + h for _, y, _, h in geo.values()) + 40
    parts = []
    for c in cells:                                    # edges under boxes
        if not c.get("edge") or c.get("source") not in geo or c.get("target") not in geo:
            continue
        st = style(c.get("style"))
        sx, sy, sw, sh = geo[c.get("source")]; tx, ty, tw, th = geo[c.get("target")]
        a = (sx + float(st.get("exitX", .5)) * sw, sy + float(st.get("exitY", 1)) * sh)
        b = (tx + float(st.get("entryX", .5)) * tw, ty + float(st.get("entryY", 0)) * th)
        g = c.find("mxGeometry"); arr = g.find("Array") if g is not None else None
        pts = [a]
        if arr is not None:
            pts += [(float(p.get("x", 0)), float(p.get("y", 0))) for p in arr.iter("mxPoint")]
        elif float(st.get("entryY", 0)) == 0:          # top entry: down, across, down
            my = a[1] + (b[1] - a[1]) / 2; pts += [(a[0], my), (b[0], my)]
        else:                                           # side entry: down, then across
            pts += [(a[0], b[1])]
        pts.append(b)
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        dash = f' stroke-dasharray="{st.get("dashPattern", "4 4")}"' if st.get("dashed") == "1" else ""
        parts.append(f'<path d="{d}" fill="none" stroke="{st.get("strokeColor", "#999999")}"{dash}/>')
    for c in cells:
        if c.get("id") not in geo:
            continue
        st = style(c.get("style")); x, y, w, h = geo[c.get("id")]
        dash = ' stroke-dasharray="4 4"' if st.get("dashed") == "1" else ""
        rx = ' rx="9"' if st.get("rounded") == "1" else ""
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}"{rx} fill="{st.get("fillColor", "#ffffff")}" '
                     f'stroke="{st.get("strokeColor", "#000000")}"{dash}/>')
        lines = label_lines(c.get("value"))
        base = int(float(st.get("fontSize", 12)))
        ty0 = y + h / 2 - (len(lines) - 1) * 8 + 4
        for k, (txt, bold, col, size) in enumerate(lines):
            col = col or st.get("fontColor", "#000000")
            parts.append(f'<text x="{x + w / 2}" y="{ty0 + k * 16}" text-anchor="middle" font-family="{FONT}" '
                         f'font-size="{size or base}" font-weight="{"bold" if bold else "normal"}" '
                         f'fill="{col}">{html.escape(txt)}</text>')
    W, H = x1 - x0, y1 - y0
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{W}" height="{H}" '
           f'viewBox="{x0} {y0} {W} {H}" content="{html.escape(raw, quote=True)}" style="background-color:#ffffff">'
           f'<rect x="{x0}" y="{y0}" width="{W}" height="{H}" fill="#ffffff"/>' + "".join(parts) + "</svg>")
    dst.write_text(svg, encoding="utf-8")


if __name__ == "__main__":
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name(src.name.removesuffix(".drawio") + ".drawio.svg")
    convert(src, dst); print(dst)

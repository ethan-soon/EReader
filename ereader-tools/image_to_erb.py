#!/usr/bin/env python3
"""
image_to_erb.py
===============
Turn a finished **image** (a custom menu / splash / status screen you drew in an
online editor like Photopea) into a single-page `.erb` the firmware can display
straight from the SD card. A "screen" is just a 1-page book, so this reuses the
exact same `ErbWriter` + rotate/pack convention as the EPUB pipeline -- no new
on-device format, no new firmware loader needed.

    Author at 480x800 (portrait, how the panel sits)  ==>  export PNG/BMP
      -> resize to 480x800, convert to 1bpp (threshold for line art / menus,
         or Floyd-Steinberg dither for photos)
      -> bake panel rotation (default 90deg CW = PIL ROTATE_270) -> 800x480
      -> ErbWriter(800, 480, bpp=1, use_rle=False, panel_rotation=0)
    => one 48000-byte panel-native page the single-buffer firmware f_reads
       straight into its framebuffer, identical in layout to an ORV page.

Examples
--------
    # a menu drawn at 480x800 in Photopea, exported as PNG (pure B/W line art)
    python image_to_erb.py menu.png -o menu.erb

    # a photo/cover -> dither for smooth tones instead of hard threshold
    python image_to_erb.py cover.jpg -o cover.erb --dither

    # source isn't exactly 480x800: letterbox onto white instead of stretching
    python image_to_erb.py logo.png -o splash.erb --fit

    # panel mounted the other way / image upside-down on the device
    python image_to_erb.py menu.png -o menu.erb --rotate 270

Copy the resulting .erb to the SD card root (one .erb per screen).
Verify it first with:  python preview.py menu.erb --pages 0 --out preview/
"""

import argparse
import json
import os
import sys

from PIL import Image, ImageOps

from erb_format import ErbWriter
from layout import _PANEL_TRANSPOSE   # {90: ROTATE_270, 180: ROTATE_180, 270: ROTATE_90}

LOGICAL_W, LOGICAL_H = 480, 800       # one portrait ORV page


def _map_point(px, py, rotate):
    """Map a pixel from the 480x800 authoring (portrait) space into the stored
    panel-native space, matching exactly how the page bits are rotated
    (erb_rotate_1bpp / _PANEL_TRANSPOSE). Returns (X, Y)."""
    if rotate == 0:
        return px, py
    if rotate == 90:                  # 90 CW  -> stored 800x480
        return LOGICAL_H - 1 - py, px
    if rotate == 180:                 # stored 480x800
        return LOGICAL_W - 1 - px, LOGICAL_H - 1 - py
    if rotate == 270:                 # 90 CCW -> stored 800x480
        return py, LOGICAL_W - 1 - px
    raise ValueError(f"bad rotate {rotate}")


def transform_rect(x, y, w, h, rotate):
    """Transform an axis-aligned rectangle from portrait authoring coordinates
    into panel-native coordinates by mapping its corners and taking the bounding
    box (rotation is a multiple of 90 so the box is exact)."""
    corners = [(x, y), (x + w - 1, y), (x, y + h - 1), (x + w - 1, y + h - 1)]
    xs, ys = zip(*(_map_point(cx, cy, rotate) for cx, cy in corners))
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    return {"x": x0, "y": y0, "w": x1 - x0 + 1, "h": y1 - y0 + 1}


def load_items(path, rotate):
    """Read an items JSON describing menu hit-boxes in portrait (480x800) space
    and return a list of panel-native {x,y,w,h,id} dicts in file order.

    JSON shape:
        { "items": [ {"x":40,"y":200,"w":400,"h":80,"id":0,"action":"library"}, ... ] }
    `id` defaults to the item's index; `action` is documentation only (the
    firmware owns what each item does)."""
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    items = doc["items"] if isinstance(doc, dict) else doc
    out = []
    for i, it in enumerate(items):
        for k in ("x", "y", "w", "h"):
            if k not in it:
                sys.exit(f"items[{i}] missing '{k}'")
        rect = transform_rect(it["x"], it["y"], it["w"], it["h"], rotate)
        rect["id"] = int(it.get("id", i))
        out.append(rect)
    return out


def to_logical(im, fit, invert):
    """Normalize any source image to a 480x800 'L' (grayscale) image."""
    # Flatten transparency onto white so undrawn areas read as white (0xFF).
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("L")
    if invert:
        im = ImageOps.invert(im)

    if im.size != (LOGICAL_W, LOGICAL_H):
        if fit:
            # preserve aspect ratio, pad the rest with white (letterbox)
            im = ImageOps.pad(im, (LOGICAL_W, LOGICAL_H), method=Image.LANCZOS,
                              color=255, centering=(0.5, 0.5))
        else:
            im = im.resize((LOGICAL_W, LOGICAL_H), Image.LANCZOS)
    return im


def to_page_bytes(im_L, rotate, dither, threshold):
    """1bpp pack mirroring layout._pack_1bpp: bake rotation, then to mode '1'
    (MSB-first, bit 1 = white). Threshold for crisp line art; dither for photos."""
    op = _PANEL_TRANSPOSE.get(rotate)
    if op is not None:
        im_L = im_L.transpose(op)
    if dither:
        bw = im_L.convert("1")                      # Floyd-Steinberg
    else:
        # hard threshold: pixels >= `threshold` become white, else black
        bw = im_L.point(lambda v: 255 if v >= threshold else 0).convert("1",
                                                                dither=Image.NONE)
    return bw.tobytes()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert an image (custom menu/splash/cover) to a 1-page .erb")
    ap.add_argument("input", help="input image (PNG/BMP/JPG/...)")
    ap.add_argument("-o", "--output", required=True, help="output .erb")
    ap.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=90,
                    help="degrees the page is rotated to fit the panel "
                         "(default 90 = portrait, matches the book pipeline; "
                         "use 270 if it shows upside-down)")
    ap.add_argument("--dither", action="store_true",
                    help="Floyd-Steinberg dither (use for photos/covers); "
                         "default is a hard threshold, best for menus/line art")
    ap.add_argument("--threshold", type=int, default=128,
                    help="threshold 0-255 for non-dithered mode (default 128)")
    ap.add_argument("--fit", action="store_true",
                    help="letterbox onto white to keep aspect ratio "
                         "(default: stretch to 480x800)")
    ap.add_argument("--invert", action="store_true",
                    help="swap black/white (e.g. white-on-black source art)")
    ap.add_argument("--items", default=None,
                    help="JSON of selectable item hit-boxes in portrait "
                         "(480x800) coords; baked into the .erb as panel-native "
                         "rectangles for the firmware menu engine")
    ap.add_argument("--title", default=None, help="metadata title (default: file name)")
    args = ap.parse_args(argv)

    if not os.path.exists(args.input):
        sys.exit(f"input not found: {args.input}")

    menu_items = load_items(args.items, args.rotate) if args.items else None

    try:
        src = Image.open(args.input)
    except Exception as e:
        sys.exit(f"could not open image: {e}")

    im_L = to_logical(src, args.fit, args.invert)
    page = to_page_bytes(im_L, args.rotate, args.dither, args.threshold)

    # rotate 90/270 -> stored panel-native 800x480; 0/180 -> 480x800.
    stored_w, stored_h = ((LOGICAL_H, LOGICAL_W) if args.rotate in (90, 270)
                          else (LOGICAL_W, LOGICAL_H))
    assert len(page) == stored_w * stored_h // 8, (
        f"page is {len(page)} B, expected {stored_w * stored_h // 8}")

    title = args.title or os.path.splitext(os.path.basename(args.output))[0]
    writer = ErbWriter(width=stored_w, height=stored_h, bpp=1,
                       use_rle=False, panel_rotation=0)
    stats = writer.write(args.output, [page],
                         [(title, 0)],
                         {"title": title, "author": "image_to_erb", "language": "en"},
                         menu_items=menu_items)

    print(f"Wrote {stats['path']}")
    print(f"  1 page, {stats['page_bytes'] if 'page_bytes' in stats else len(page)} B/page, "
          f"stored {stored_w}x{stored_h} panel-native (panel_rotation=0, no RLE)")
    if menu_items:
        print(f"  {len(menu_items)} menu item(s) baked in (panel-native rects):")
        for it in menu_items:
            print(f"    id={it['id']:<3} x={it['x']:<4} y={it['y']:<4} "
                  f"w={it['w']:<4} h={it['h']:<4}")
    print(f"  file size: {stats['file_bytes']} B")
    print(f"  preview it:  python preview.py {args.output} --pages 0 --out preview/")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
convert.py
==========
Command-line front end: EPUB (or TXT) -> .erb

Examples
--------
    python3 convert.py book.epub -o book.erb
    python3 convert.py book.epub -o book.erb --width 800 --height 480 \
        --font-size 24 --margin-x 48 --no-rle
    python3 convert.py notes.txt  -o notes.erb --txt

The output .erb is copied to the SD card; the firmware reads it directly.
"""

import argparse
import os
import sys

from erb_format import ErbWriter
from layout import LayoutConfig, LayoutEngine


def load_blocks(path, is_txt):
    if is_txt or path.lower().endswith(".txt"):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        blocks = []
        for para in text.split("\n\n"):
            para = para.strip()
            if para:
                blocks.append({"type": "para",
                               "runs": [(" ".join(para.split()), frozenset())]})
        title = os.path.splitext(os.path.basename(path))[0]
        meta = {"title": title, "author": "Unknown", "language": "en"}
        toc = [(title, 0)]
        return blocks, toc, meta

    from epub_parser import EpubParser
    parser = EpubParser(path)
    blocks, toc = parser.blocks()
    meta = parser.metadata()
    return blocks, toc, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert EPUB/TXT to .erb for the e-reader")
    ap.add_argument("input", help="input .epub or .txt")
    ap.add_argument("-o", "--output", required=True, help="output .erb")
    ap.add_argument("--txt", action="store_true", help="force plain-text mode")
    ap.add_argument("--orientation", choices=["portrait", "landscape"],
                    default="portrait",
                    help="page layout orientation (default: portrait)")
    ap.add_argument("--width", type=int, default=None,
                    help="logical page width (overrides --orientation)")
    ap.add_argument("--height", type=int, default=None,
                    help="logical page height (overrides --orientation)")
    ap.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=None,
                    help="degrees the firmware rotates pages to fit the physical "
                         "panel; default 90 for portrait, 0 for landscape")
    ap.add_argument("--margin-x", type=int, default=40)
    ap.add_argument("--margin-y", type=int, default=36)
    ap.add_argument("--font-size", type=int, default=22)
    ap.add_argument("--line-spacing", type=float, default=1.32)
    ap.add_argument("--font-regular", default=None, help="override regular TTF path")
    ap.add_argument("--font-bold", default=None)
    ap.add_argument("--font-italic", default=None)
    ap.add_argument("--font-bolditalic", default=None)
    ap.add_argument("--no-justify", action="store_true")
    ap.add_argument("--no-rle", action="store_true", help="store pages uncompressed")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="only lay out the first N pages (quick test of a big book)")
    args = ap.parse_args(argv)

    if not os.path.exists(args.input):
        sys.exit(f"input not found: {args.input}")

    print(f"Reading {args.input} ...")
    blocks, toc, meta = load_blocks(args.input, args.txt)
    print(f"  title : {meta['title']}")
    print(f"  author: {meta['author']}")
    print(f"  blocks: {len(blocks)}  chapters: {len(toc)}")

    # build font map, applying any overrides
    from layout import DEFAULT_FONTS
    fonts = dict(DEFAULT_FONTS)
    if args.font_regular:    fonts[frozenset()] = args.font_regular
    if args.font_bold:       fonts[frozenset({"b"})] = args.font_bold
    if args.font_italic:     fonts[frozenset({"i"})] = args.font_italic
    if args.font_bolditalic: fonts[frozenset({"b", "i"})] = args.font_bolditalic

    # ---- resolve page dimensions + panel rotation -----------------------
    # The Waveshare 7.5" panel is physically 800x480 (landscape). For portrait
    # reading the panel is mounted rotated, so we lay out 480x800 and tell the
    # firmware to rotate 90 deg when blitting to the panel.
    PANEL_LONG, PANEL_SHORT = 800, 480
    if args.width and args.height:
        page_w, page_h = args.width, args.height
    elif args.orientation == "portrait":
        page_w, page_h = PANEL_SHORT, PANEL_LONG          # 480 x 800
    else:
        page_w, page_h = PANEL_LONG, PANEL_SHORT          # 800 x 480
    if args.rotate is not None:
        rotation = args.rotate
    else:
        rotation = 90 if args.orientation == "portrait" else 0

    cfg = LayoutConfig(
        width=page_w, height=page_h,
        margin_x=args.margin_x, margin_y=args.margin_y,
        body_size=args.font_size, line_spacing=args.line_spacing,
        fonts=fonts, justify=not args.no_justify,
    )

    # map block index -> chapter title for TOC page resolution
    toc_block_index = {bi: title for title, bi in toc}

    print("Laying out pages ...")
    engine = LayoutEngine(cfg)
    pages, toc_pages = engine.run_layout(blocks, toc_block_index,
                                         max_pages=args.max_pages)
    print(f"  rendered {len(pages)} pages at {cfg.width}x{cfg.height} "
          f"({args.orientation}, panel rotation {rotation}\u00b0)")

    writer = ErbWriter(width=cfg.width, height=cfg.height, bpp=1,
                       use_rle=not args.no_rle, panel_rotation=rotation)
    stats = writer.write(args.output, pages, toc_pages, meta)

    print("Wrote", stats["path"])
    print(f"  pages      : {stats['page_count']}")
    print(f"  file size  : {stats['file_bytes']/1024:.1f} KB")
    print(f"  raw frames : {stats['raw_bytes']/1024:.1f} KB")
    if not args.no_rle:
        print(f"  compression: {stats['ratio']*100:.1f}% of raw "
              f"({1/stats['ratio']:.1f}x smaller)" if stats['ratio'] else "")


if __name__ == "__main__":
    main()

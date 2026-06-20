#!/usr/bin/env python3
"""
preview.py
==========
Decode an .erb file (exactly as the firmware would) and write the pages back
out as PNGs, so you can eyeball what the panel will actually show.

    python3 preview.py book.erb --pages 0-5 --out preview/
    python3 preview.py book.erb --contact-sheet sheet.png
"""

import argparse
import os
import struct

from PIL import Image
from erb_format import (MAGIC, HEADER_SIZE, FLAG_RLE, packbits_decode)


class ErbReader:
    def __init__(self, path):
        self.f = open(path, "rb")
        hdr = self.f.read(HEADER_SIZE)
        (magic, version, flags, width, height, bpp, bit_order, panel_rotation,
         page_count, toc_count, meta_off, toc_off, ptable_off,
         pdata_off, bytes_per_page) = struct.unpack(
            "<4sHHHHBBBx IIIIII I", hdr[:44])
        assert magic == MAGIC, "not an .erb file"
        self.version = version
        self.flags = flags
        self.width = width
        self.height = height
        self.bpp = bpp
        self.panel_rotation = panel_rotation
        self.page_count = page_count
        self.bytes_per_page = bytes_per_page
        self.rle = bool(flags & FLAG_RLE)
        self.ptable_off = ptable_off

        # metadata
        self.f.seek(meta_off)
        self.title = self._read_str()
        self.author = self._read_str()
        self.language = self._read_str()

        # TOC
        self.f.seek(toc_off)
        n = struct.unpack("<I", self.f.read(4))[0]
        self.toc = []
        for _ in range(n):
            name = self._read_str()
            page = struct.unpack("<I", self.f.read(4))[0]
            self.toc.append((name, page))

    def _read_str(self):
        ln = struct.unpack("<H", self.f.read(2))[0]
        return self.f.read(ln).decode("utf-8", "replace")

    def page_blob(self, i):
        self.f.seek(self.ptable_off + i * 8)
        off, length = struct.unpack("<II", self.f.read(8))
        self.f.seek(off)
        blob = self.f.read(length)
        return packbits_decode(blob) if self.rle else blob

    def page_image(self, i):
        raw = self.page_blob(i)
        if self.bpp == 2:
            # 4-level gray, 4 px/byte MSB-first, code 0..3 -> gray 0/85/170/255
            w, h = self.width, self.height
            out = bytearray(w * h)
            k = 0
            for byte in raw:
                out[k]     = ((byte >> 6) & 3) * 85
                out[k + 1] = ((byte >> 4) & 3) * 85
                out[k + 2] = ((byte >> 2) & 3) * 85
                out[k + 3] = (byte & 3) * 85
                k += 4
            return Image.frombytes("L", (w, h), bytes(out))
        # 1bpp mono: pack width was rounded up to a multiple of 8
        pack_w = (self.width + 7) // 8 * 8
        img = Image.frombytes("1", (pack_w, self.height), raw)
        return img.crop((0, 0, self.width, self.height)).convert("L")

    def close(self):
        self.f.close()


def parse_range(spec, n):
    if not spec:
        return list(range(n))
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [i for i in out if 0 <= i < n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("erb")
    ap.add_argument("--pages", default="", help="e.g. 0-5 or 0,3,7")
    ap.add_argument("--out", default="preview")
    ap.add_argument("--contact-sheet", default=None)
    args = ap.parse_args()

    r = ErbReader(args.erb)
    print(f"{r.title} -- {r.author} [{r.language}]")
    print(f"{r.page_count} pages, {r.width}x{r.height}, "
          f"{'RLE' if r.rle else 'raw'}, panel rotation {r.panel_rotation}\u00b0")
    print("TOC:")
    for name, page in r.toc:
        print(f"   p{page:>4}  {name}")

    idxs = parse_range(args.pages, r.page_count)

    if args.contact_sheet:
        cols = 4
        rows = (len(idxs) + cols - 1) // cols
        tw = r.width // 3
        th = r.height // 3
        sheet = Image.new("L", (cols * (tw + 8) + 8, rows * (th + 8) + 8), 200)
        for k, i in enumerate(idxs):
            thumb = r.page_image(i).resize((tw, th))
            cx = 8 + (k % cols) * (tw + 8)
            cy = 8 + (k // cols) * (th + 8)
            sheet.paste(thumb, (cx, cy))
        sheet.save(args.contact_sheet)
        print("wrote", args.contact_sheet)
    else:
        os.makedirs(args.out, exist_ok=True)
        for i in idxs:
            p = os.path.join(args.out, f"page_{i:04d}.png")
            r.page_image(i).save(p)
        print(f"wrote {len(idxs)} pages to {args.out}/")
    r.close()


if __name__ == "__main__":
    main()

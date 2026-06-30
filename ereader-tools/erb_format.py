"""
erb_format.py
=============
Definition of the ".erb" (E-Reader Book) container and the writer used by the
conversion pipeline.

Design priorities (in order):
  1. Trivial to parse on an STM32F446RE with no dynamic allocation.
  2. O(1) random access to any page (jump to page N without scanning).
  3. One page == one pre-packed 1-bpp framebuffer that matches the panel,
     so the firmware does zero rendering -- it just blits bytes to the EPD.

Everything is little-endian, because the Cortex-M4 is little-endian and we want
the firmware to read structs with a plain memcpy / pointer cast, no byte swaps.

File layout
-----------
    +--------------------+  offset 0
    | Header (64 bytes)  |
    +--------------------+  meta_off
    | Metadata block     |   title / author / language (UTF-8, len-prefixed)
    +--------------------+  toc_off
    | TOC block          |   chapter name -> page index
    +--------------------+  ptable_off
    | Page offset table  |   page_count * {u32 offset, u32 length}
    +--------------------+  pdata_off
    | Page data blobs     |   concatenated (optionally PackBits-RLE) pages
    +--------------------+

The firmware reads the 64-byte header once, then the (small) page table once
into RAM. To show page N it seeks to table[N].offset, reads table[N].length
bytes, and -- if FLAG_RLE is set -- PackBits-decodes them straight into the
48 KB framebuffer.
"""

import struct

MAGIC = b"ERB1"
VERSION = 1
HEADER_SIZE = 64

# ---- header flags --------------------------------------------------------
FLAG_RLE = 0x0001         # page blobs are PackBits compressed
FLAG_GRAYSCALE = 0x0002   # page blobs are multi-bit grayscale (2bpp 4-level)

# ---- bit order -----------------------------------------------------------
BITORDER_MSB_FIRST = 0    # leftmost pixel is the most-significant bit
# Pixel polarity for 1bpp: bit==1 -> white (0xFF row == all white).
# This matches the Waveshare 7.5" convention (0xFF == white).


# =========================================================================
#  PackBits RLE  (TIFF-style; chosen because the decoder is ~10 lines of C)
# =========================================================================
def packbits_encode(src: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        # length of the run of identical bytes starting at i (cap 128)
        run = 1
        while i + run < n and src[i + run] == src[i] and run < 128:
            run += 1
        if run >= 2:
            out.append(257 - run)          # 2->255 ... 128->129
            out.append(src[i])
            i += run
        else:
            start = i
            lit = 0
            while i < n and lit < 128:
                # break the literal run as soon as a repeat of >=2 begins
                if i + 1 < n and src[i] == src[i + 1]:
                    break
                i += 1
                lit += 1
            out.append(lit - 1)            # 0..127  -> copy lit literals
            out.extend(src[start:i])
    return bytes(out)


def packbits_decode(src: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        h = src[i]; i += 1
        if h < 128:                        # literal run of (h+1) bytes
            cnt = h + 1
            out.extend(src[i:i + cnt]); i += cnt
        elif h > 128:                      # replicate next byte (257-h) times
            cnt = 257 - h
            out.extend(bytes([src[i]]) * cnt); i += 1
        # h == 128 is a no-op marker (never emitted by our encoder)
    return bytes(out)


# =========================================================================
#  Writer
# =========================================================================
class ErbWriter:
    """
    Assembles an .erb file from rendered pages.

    pages      : list[bytes]  -- each is the raw packed framebuffer, bytes_per_
                                 page bytes, row-major, MSB first (1bpp mono or
                                 2bpp 4-level gray, 4 px/byte)
    toc        : list[(str, int)]  -- (chapter title, page index)
    metadata   : dict with keys 'title', 'author', 'language'
    """

    def __init__(self, width, height, bpp=1, bit_order=BITORDER_MSB_FIRST,
                 use_rle=True, panel_rotation=0):
        self.width = width
        self.height = height
        self.bpp = bpp
        self.bit_order = bit_order
        self.use_rle = use_rle
        self.panel_rotation = panel_rotation  # degrees firmware rotates to fit panel
        self.bytes_per_page = (width * height * bpp + 7) // 8

    # -- block serializers --------------------------------------------------
    @staticmethod
    def _pack_str(s: str) -> bytes:
        b = s.encode("utf-8")
        if len(b) > 0xFFFF:
            b = b[:0xFFFF]
        return struct.pack("<H", len(b)) + b

    def _build_meta(self, metadata) -> bytes:
        out = bytearray()
        for key in ("title", "author", "language"):
            out += self._pack_str(metadata.get(key, "") or "")
        return bytes(out)

    def _build_toc(self, toc) -> bytes:
        out = bytearray()
        out += struct.pack("<I", len(toc))
        for title, page_index in toc:
            out += self._pack_str(title)
            out += struct.pack("<I", page_index)
        return bytes(out)

    # Menu geometry block (only for "screen" .erb files used as firmware menus).
    # Rectangles are already in PANEL-NATIVE pixel coordinates (the same space
    # as the stored page), so the firmware XOR-inverts them directly with no
    # rotation math. Layout:
    #     u16 version (=1)
    #     u16 count
    #     count * { u16 x, u16 y, u16 w, u16 h, u16 id }
    MENU_BLOCK_VERSION = 1

    def _build_menu(self, menu_items) -> bytes:
        out = bytearray()
        out += struct.pack("<HH", self.MENU_BLOCK_VERSION, len(menu_items))
        for it in menu_items:
            out += struct.pack("<HHHHH",
                               int(it["x"]), int(it["y"]),
                               int(it["w"]), int(it["h"]),
                               int(it.get("id", 0)) & 0xFFFF)
        return bytes(out)

    # -- main ---------------------------------------------------------------
    def write(self, path, pages, toc, metadata, menu_items=None):
        flags = 0
        if self.use_rle:
            flags |= FLAG_RLE
        if self.bpp != 1:
            flags |= FLAG_GRAYSCALE   # 2bpp 4-level gray (also covers 4bpp)

        meta_block = self._build_meta(metadata)
        toc_block = self._build_toc(toc)

        # encode page blobs
        blobs = []
        for p in pages:
            assert len(p) == self.bytes_per_page, (
                f"page is {len(p)} bytes, expected {self.bytes_per_page}")
            blobs.append(packbits_encode(p) if self.use_rle else p)

        page_count = len(pages)
        meta_off = HEADER_SIZE
        toc_off = meta_off + len(meta_block)
        ptable_off = toc_off + len(toc_block)
        ptable_size = page_count * 8          # {u32 offset, u32 length}
        pdata_off = ptable_off + ptable_size

        # build page table (absolute offsets into the file)
        page_table = bytearray()
        cursor = pdata_off
        for blob in blobs:
            page_table += struct.pack("<II", cursor, len(blob))
            cursor += len(blob)

        # Optional menu geometry block is appended AFTER the page data, so adding
        # it never shifts any existing offset (books simply leave menu_off = 0).
        menu_block = self._build_menu(menu_items) if menu_items else b""
        menu_off = cursor if menu_block else 0

        # header (48 bytes of fields, padded to 64)
        header = struct.pack(
            "<4sHHHHBBBx IIIIII I I",
            MAGIC,                # 4s  magic
            VERSION,              # H   version
            flags,                # H   flags
            self.width,           # H   width
            self.height,          # H   height
            self.bpp,             # B   bpp
            self.bit_order,       # B   bit_order
            self.panel_rotation,  # B   panel_rotation (0/90/180/270)
                                  # x   1 reserved/pad byte
            page_count,           # I   page_count
            len(toc),             # I   toc_count
            meta_off,             # I   meta_off
            toc_off,              # I   toc_off
            ptable_off,           # I   ptable_off
            pdata_off,            # I   pdata_off
            self.bytes_per_page,  # I   bytes_per_page
            menu_off,             # I   menu_off (0 = no menu geometry)
        )
        header += b"\x00" * (HEADER_SIZE - len(header))

        with open(path, "wb") as f:
            f.write(header)
            f.write(meta_block)
            f.write(toc_block)
            f.write(page_table)
            for blob in blobs:
                f.write(blob)
            if menu_block:
                f.write(menu_block)

        total = cursor
        raw = page_count * self.bytes_per_page
        return {
            "path": path,
            "page_count": page_count,
            "file_bytes": total,
            "raw_bytes": raw,
            "ratio": (total / raw) if raw else 0,
        }

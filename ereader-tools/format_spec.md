# `.erb` format specification (v1)

The **E-Reader Book** container holds a whole book as a sequence of
pre-rendered, panel-ready 1-bit pages plus a small index. The design goal is
that the STM32F446RE does **zero** layout or font work at runtime: it seeks to a
page, reads it, optionally RLE-decodes it, and DMAs it to the EPD.

All integers are **little-endian** (matches the Cortex-M4, so structs map
directly with no byte-swapping).

## File layout

```
offset 0      +-------------------------+
              | Header (64 bytes)       |
meta_off      +-------------------------+
              | Metadata block          |  title / author / language
toc_off       +-------------------------+
              | TOC block               |  chapter name -> page index
ptable_off    +-------------------------+
              | Page offset table       |  page_count x {u32 off, u32 len}
pdata_off     +-------------------------+
              | Page data blobs         |  concatenated, optionally PackBits
              +-------------------------+
```

## Header (64 bytes)

| Field           | Type     | Notes                                        |
|-----------------|----------|----------------------------------------------|
| magic           | char[4]  | `"ERB1"`                                     |
| version         | u16      | 1                                            |
| flags           | u16      | bit0 = RLE, bit1 = grayscale (reserved)      |
| width           | u16      | pixels (e.g. 800)                            |
| height          | u16      | pixels (e.g. 480)                            |
| bpp             | u8       | 1                                            |
| bit_order       | u8       | 0 = MSB-first                                |
| panel_rotation  | u8       | degrees firmware rotates page onto panel (0/90/180/270) |
| reserved        | u8       | padding for 4-byte alignment                 |
| page_count      | u32      |                                              |
| toc_count       | u32      |                                              |
| meta_off        | u32      | file offset of metadata block                |
| toc_off         | u32      | file offset of TOC block                     |
| ptable_off      | u32      | file offset of page offset table             |
| pdata_off       | u32      | file offset of first page blob               |
| bytes_per_page  | u32      | uncompressed framebuffer size (48000 for 800x480) |
| (padding)       | u8[20]   | zeros up to 64 bytes                         |

## Metadata block

Three length-prefixed UTF-8 strings, in order: **title**, **author**,
**language**. Each is `u16 length` followed by `length` bytes.

## TOC block

```
u32 count
count x {
    u16 name_len
    char name[name_len]   (UTF-8)
    u32 page_index        (0-based page where the chapter starts)
}
```

## Page offset table

`page_count` entries, each `{u32 offset, u32 length}`. `offset` is the absolute
file offset of the page blob; `length` is its byte length (compressed length if
`FLAG_RLE`). To show page *N*: seek `table[N].offset`, read `table[N].length`
bytes.

## Page data

Each page is a 1-bpp framebuffer, **row-major**, 8 pixels per byte, **MSB =
leftmost pixel**, and **bit value 1 = white**. For 800x480 that is
800/8 = 100 bytes/row x 480 = 48000 bytes. This matches the Waveshare 7.5"
convention (`0xFF` == white), so it is panel-ready with no transform.

If `FLAG_RLE` is set, each blob is **PackBits**-compressed (TIFF style). The
decoder is ~12 lines of C (see `firmware/erb_reader.c`):

- header byte `0..127`  -> copy next `(h+1)` literal bytes
- header byte `129..255` -> repeat next byte `(257-h)` times
- header byte `128`     -> no-op (never emitted by the encoder)

Text pages compress ~3-7x because of the long white runs.

## Orientation

Pages are laid out and stored at the **logical reading size** (e.g. 480x800 for
portrait). Because the Waveshare 7.5" is physically 800x480 (landscape), the
panel is mounted rotated for a portrait reader, and `panel_rotation` tells the
firmware how many degrees to rotate each page before sending it to the panel
(90 for portrait by default; flip to 270 if your panel is mounted the other
way). The rotation is a sub-millisecond 1bpp memory shuffle -- see
`erb_rotate_1bpp()` in `firmware/erb_reader.c` -- and is dwarfed by the EPD
refresh time. Use `--orientation landscape` (rotation 0) to store panel-native
pages and skip rotation entirely.

## Device-side RAM budget (STM32F446RE, 128 KB)

| Buffer                         | Size                          |
|--------------------------------|-------------------------------|
| One page framebuffer           | 48000 B (800x480 @ 1bpp)      |
| Page offset table (1000 pages) | 8000 B                        |
| RLE scratch (worst-case blob)  | <= 48 KB if RLE; 0 if uncompressed |

Tip: with `--no-rle` the firmware needs no scratch buffer and can `f_read`
straight into the framebuffer, trading SD space for simplicity. With RLE you
save SD reads (faster page turns) at the cost of one scratch buffer; you can
also stream-decode to avoid holding the whole compressed blob.

## Resume / page jump

"Remember last page" and "jump to page" are just storing/seeking an integer
page index. The device keeps a tiny per-book record (e.g. `book.erb -> page 137`)
in its own config file or flash.

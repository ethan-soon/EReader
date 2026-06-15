# ereader-tools

A PC-side pipeline that converts **EPUB / TXT** books into `.erb` files your
STM32F446RE e-reader can display directly. All the hard work (HTML parsing,
text layout, line breaking, font rasterization, image dithering, pagination)
happens here on the PC. The firmware does **no** rendering — it seeks to a page,
reads it, optionally RLE-decodes it, and pushes the bytes to the Waveshare 7.5"
e-paper panel.

This is the same strategy that lets a tiny MCU show richly formatted books: pay
the layout cost once, at conversion time, and ship finished pixels.

## Why this approach

A full HTML/CSS engine won't fit comfortably in 128 KB of RAM, and on-device
font rasterization is slow and fiddly. By pre-rendering each page to a packed
1-bit framebuffer that already matches the panel, the device-side reader is a
few hundred lines of C with no dynamic allocation, and page turns are just an
SD read + (optional) RLE expand + DMA.

## Install

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

(Uses system TrueType fonts; DejaVu Serif by default. Override with
`--font-regular` etc.)

## Usage

```bash
# EPUB -> .erb (defaults: portrait 480x800, 22px serif, justified, RLE)
python3 convert.py book.epub -o book.erb

# landscape instead of portrait
python3 convert.py book.epub -o book.erb --orientation landscape

# tune layout for your panel / taste
python3 convert.py book.epub -o book.erb \
    --orientation portrait --font-size 24 \
    --margin-x 48 --margin-y 40 --line-spacing 1.35

# if your panel is mounted the other way, flip the rotation
python3 convert.py book.epub -o book.erb --rotate 270

# uncompressed pages (simplest firmware: read straight into framebuffer)
python3 convert.py book.epub -o book.erb --no-rle

# plain text
python3 convert.py notes.txt -o notes.erb --txt
```

> **Orientation:** pages default to **portrait** (480x800), laid out as you'd
> hold a book. The Waveshare 7.5" panel is physically 800x480, so the firmware
> rotates each page by `panel_rotation` (90 by default) when blitting --
> `erb_rotate_1bpp()` in `firmware/erb_reader.c` does this in well under a
> millisecond. Use `--orientation landscape` to store panel-native pages and
> skip rotation.

### Verify before flashing the SD card

```bash
# render pages back to PNG exactly as the device decodes them
python3 preview.py book.erb --pages 0-9 --out preview/
# or a single overview image
python3 preview.py book.erb --contact-sheet sheet.png
```

### Different font sizes

Font size is baked in at conversion time (the device stores no fonts). To offer
selectable sizes, generate a variant per size and let the menu pick the file:

```bash
python3 convert.py book.epub -o book_s.erb --font-size 18
python3 convert.py book.epub -o book_m.erb --font-size 22
python3 convert.py book.epub -o book_l.erb --font-size 28
```

## Files

| File                     | Role                                                   |
|--------------------------|--------------------------------------------------------|
| `convert.py`             | CLI: EPUB/TXT -> `.erb`                                 |
| `epub_parser.py`         | EPUB -> linear block stream (headings/paras/images)    |
| `layout.py`              | Block stream -> paginated 1-bpp framebuffers           |
| `erb_format.py`          | `.erb` writer + PackBits codec                         |
| `preview.py`             | `.erb` -> PNG (decodes like the firmware)              |
| `make_sample_epub.py`    | Generates a test EPUB                                   |
| `format_spec.md`         | The binary format, field by field                      |
| `firmware/erb_format.h`  | C structs for the on-disk format                       |
| `firmware/erb_reader.c`  | Reference C reader (decode page -> framebuffer)        |

## Firmware integration

`firmware/erb_reader.c` is written to drop into the STM32 project: swap the
`stdio` `FILE*` calls for your FatFs `f_open`/`f_lseek`/`f_read`, and call
`erb_render_page()` to decode a page directly into your 48 KB EPD framebuffer.
The host-side self-test (`-DERB_READER_TEST`) decodes a page to a PBM image and
was verified pixel-identical to the Python output, so the preview is faithful.

```bash
cc -DERB_READER_TEST -O2 -o erbtest firmware/erb_reader.c
./erbtest book.erb 0 page0.pbm
```

## What maps to your project goals

- **Library / choose a book** — each book is one `.erb`; the menu lists files.
- **Remember last page / jump to page** — store an integer page index per book.
- **Images in EPUB** — decoded, scaled, and Floyd-Steinberg dithered into pages.
- **No visible latency** — page turn = SD read + RLE expand + DMA, no layout.
- **TOC / chapters** — the TOC block maps chapter names to page indices.

## Known limits (v1) / next steps

- Font size is fixed per file (see "Different font sizes" above).
- Tables and complex CSS layout are flattened to paragraphs.
- Very long unbreakable words can overflow the right margin (no hyphenation yet).
- Grayscale (4bpp) is reserved in the header but the pipeline emits 1bpp.

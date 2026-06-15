"""
layout.py
=========
The layout / pagination engine.

Input : the block stream from epub_parser
Output: a list of pre-packed 1-bpp page framebuffers (bytes) + a page-level TOC.

It does greedy word wrapping with mixed inline styles, renders headings larger,
scales + Floyd-Steinberg-dithers images to 1-bit, and breaks the flow into
fixed-size pages matching the panel. The firmware receives finished pixels and
renders nothing itself.

Page state (current image, draw context, y cursor, "is the page empty yet")
lives on the engine instance so text and image rendering share it cleanly.
"""

import io
import os
from PIL import Image, ImageDraw, ImageFont

# Fonts are bundled with the project (in ./fonts) so rendering is identical on
# Windows / macOS / Linux with no system-font hunting. Paths are resolved
# relative to this file, not the working directory.
_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

DEFAULT_FONTS = {
    frozenset():            os.path.join(_FONT_DIR, "DejaVuSerif.ttf"),
    frozenset({"b"}):       os.path.join(_FONT_DIR, "DejaVuSerif-Bold.ttf"),
    frozenset({"i"}):       os.path.join(_FONT_DIR, "DejaVuSerif-Italic.ttf"),
    frozenset({"b", "i"}):  os.path.join(_FONT_DIR, "DejaVuSerif-BoldItalic.ttf"),
}


class LayoutConfig:
    def __init__(self, **kw):
        self.width = kw.get("width", 800)
        self.height = kw.get("height", 480)
        self.margin_x = kw.get("margin_x", 40)
        self.margin_y = kw.get("margin_y", 36)
        self.body_size = kw.get("body_size", 22)
        self.line_spacing = kw.get("line_spacing", 1.32)  # multiple of font size
        self.para_spacing = kw.get("para_spacing", 10)    # extra px between paras
        self.indent = kw.get("indent", 28)                # first-line indent px
        self.heading_scale = kw.get("heading_scale",
                                    {1: 1.9, 2: 1.6, 3: 1.4, 4: 1.25, 5: 1.12, 6: 1.05})
        self.fonts = kw.get("fonts", DEFAULT_FONTS)
        self.justify = kw.get("justify", True)

    @property
    def content_w(self):
        return self.width - 2 * self.margin_x

    @property
    def content_bottom(self):
        return self.height - self.margin_y


class LayoutEngine:
    def __init__(self, cfg: LayoutConfig):
        self.cfg = cfg
        # round width up to a multiple of 8 so 1bpp packing has no row padding
        self.pack_w = (cfg.width + 7) // 8 * 8
        self._font_cache = {}
        # glyph-coverage caches (per font object): does this face have a real
        # glyph for a given char, or would it fall back to the .notdef box?
        self._notdef_cache = {}
        self._cover_cache = {}
        # page state
        self._pages = []
        self._img = None
        self._draw = None
        self._y = 0
        self._empty = True   # nothing drawn on the current page yet

    # -- fonts --------------------------------------------------------------
    def _font(self, style, size):
        key = (style, size)
        if key not in self._font_cache:
            path = self.cfg.fonts.get(style, self.cfg.fonts[frozenset()])
            try:
                self._font_cache[key] = ImageFont.truetype(path, size)
            except (OSError, IOError):
                # fall back to the bundled regular face, then to anything we can
                fallback = self.cfg.fonts.get(frozenset())
                try:
                    self._font_cache[key] = ImageFont.truetype(fallback, size)
                except (OSError, IOError):
                    raise FileNotFoundError(
                        f"Could not load font '{path}'. Make sure the bundled "
                        f"'fonts/' folder sits next to layout.py, or pass "
                        f"--font-regular/--font-bold etc. with valid .ttf paths."
                    )
        return self._font_cache[key]

    def _text_w(self, text, font):
        return font.getlength(text)

    # -- glyph coverage -----------------------------------------------------
    # A char the font lacks renders as the .notdef box (the empty rectangle).
    # We detect that by comparing each glyph's bitmap to the box a guaranteed-
    # absent codepoint produces, and strip any char that matches so it simply
    # disappears instead of printing a box.
    _PROBE = ""  # private-use codepoint -> always the .notdef glyph

    def _notdef(self, font):
        ref = self._notdef_cache.get(font)
        if ref is None:
            m = font.getmask(self._PROBE)
            ref = (m.size, bytes(m)) if m.size != (0, 0) else None
            self._notdef_cache[font] = ref
        return ref

    def _has_glyph(self, font, ch):
        cov = self._cover_cache.setdefault(font, {})
        if ch in cov:
            return cov[ch]
        m = font.getmask(ch)
        cur = (m.size, bytes(m)) if m.size != (0, 0) else None
        ok = cur != self._notdef(font)
        cov[ch] = ok
        return ok

    def _strip_unsupported(self, text, font):
        # whitespace is always kept ('\n' carries hard breaks downstream)
        if all(ch.isspace() or self._has_glyph(font, ch) for ch in text):
            return text
        return "".join(ch for ch in text
                        if ch.isspace() or self._has_glyph(font, ch))

    def _line_height(self, size):
        return int(round(size * self.cfg.line_spacing))

    # -- page management ----------------------------------------------------
    def _start_page(self):
        self._img = Image.new("L", (self.pack_w, self.cfg.height), 255)
        self._draw = ImageDraw.Draw(self._img)
        self._y = self.cfg.margin_y
        self._empty = True

    def _flush_page(self):
        self._pages.append(self._pack(self._img))
        self._start_page()

    def _ensure_room(self, needed):
        if self._y + needed > self.cfg.content_bottom and not self._empty:
            self._flush_page()

    # -- main ---------------------------------------------------------------
    def run_layout(self, blocks, toc_block_index, max_pages=None):
        """blocks: list of block dicts.
           toc_block_index: dict {block_index: chapter_title}.
           max_pages: stop after roughly this many pages (for quick tests).
           Returns (pages_bytes, toc_pages=[(title, page_index)])."""
        cfg = self.cfg
        self._pages = []
        self._start_page()
        toc_pages = []

        for idx, block in enumerate(blocks):
            if max_pages and len(self._pages) >= max_pages:
                break
            if idx in toc_block_index:
                toc_pages.append((toc_block_index[idx], len(self._pages)))

            btype = block["type"]

            if btype == "break":
                if not self._empty:
                    self._flush_page()
                continue

            if btype == "image":
                self._render_image(block["data"])
                continue

            # heading / paragraph
            if btype == "heading":
                size = int(cfg.body_size * cfg.heading_scale.get(block["level"], 1.3))
                force_bold = True
                indent = 0
                space_before = cfg.para_spacing * 2
                justify = False
            else:
                size = cfg.body_size
                force_bold = False
                indent = cfg.indent
                space_before = cfg.para_spacing
                justify = cfg.justify

            lh = self._line_height(size)
            if not self._empty:
                self._y += space_before

            lines = self._wrap_runs(block["runs"], size, indent, force_bold)
            for li, line in enumerate(lines):
                self._ensure_room(lh)
                is_last = (li == len(lines) - 1)
                self._draw_line(line, self._y, size, justify and not is_last)
                self._y += lh
                self._empty = False

        # flush the final page (always emit at least one page)
        if max_pages is None or len(self._pages) < max_pages:
            if not self._empty or not self._pages:
                self._pages.append(self._pack(self._img))

        if max_pages:
            self._pages = self._pages[:max_pages]
        # keep only TOC entries that point at a page we actually kept
        toc_pages = [(t, p) for (t, p) in toc_pages if p < len(self._pages)]

        return self._pages, toc_pages

    # -- text wrapping ------------------------------------------------------
    def _wrap_runs(self, runs, size, indent, force_bold):
        """Greedy word wrap. Words break only at real whitespace, so a single
        word may carry several style segments (e.g. italic 'veniam' + roman ',').
        Returns [{words:[{segs:[(text,style)], w}], indent}]."""
        cfg = self.cfg

        def style_of(s):
            return frozenset((s | {"b"}) if force_bold else s)

        # 1) build the word list. A word is a run of non-space chars; spaces
        #    and '\n' are boundaries. '\n' additionally emits a hard break.
        words = []          # {"segs":[(text,style)], "w":float, "brk":bool}
        cur_segs = []       # [(text, style)] for the word under construction

        def flush_word():
            if cur_segs:
                w = sum(self._text_w(t, self._font(s, size)) for t, s in cur_segs)
                words.append({"segs": list(cur_segs), "w": w, "brk": False})
                cur_segs.clear()

        for text, style in runs:
            st = style_of(style)
            # drop any char this face can't render (would show as a box)
            text = self._strip_unsupported(text, self._font(st, size))
            buf = ""
            for ch in text:
                if ch == "\n":
                    if buf:
                        cur_segs.append((buf, st)); buf = ""
                    flush_word()
                    words.append({"segs": [], "w": 0, "brk": True})
                elif ch == " ":
                    if buf:
                        cur_segs.append((buf, st)); buf = ""
                    flush_word()
                else:
                    buf += ch
            if buf:
                cur_segs.append((buf, st))
        flush_word()

        # 2) greedy wrap into lines
        space_w_cache = {}

        def space_w(style):
            if style not in space_w_cache:
                space_w_cache[style] = self._text_w(" ", self._font(style, size))
            return space_w_cache[style]

        def avail(first):
            return cfg.content_w - (indent if first else 0)

        lines, cur, cur_w, first_line = [], [], 0, True
        for word in words:
            if word["brk"]:
                lines.append((cur, first_line)); cur, cur_w, first_line = [], 0, False
                continue
            sp = space_w(word["segs"][0][1]) if word["segs"] else 0
            add = word["w"] if not cur else word["w"] + sp
            if cur and cur_w + add > avail(first_line):
                lines.append((cur, first_line)); cur, cur_w, first_line = [], 0, False
                add = word["w"]
            cur.append(word)
            cur_w += add
        if cur:
            lines.append((cur, first_line))

        return [{"words": ws, "indent": indent if is_first else 0}
                for ws, is_first in lines]

    def _draw_line(self, line, y, size, justify):
        cfg = self.cfg
        words = line["words"]
        if not words:
            return
        x = cfg.margin_x + line["indent"]
        natural = sum(w["w"] for w in words)
        gaps = len(words) - 1
        base_sp = self._text_w(" ", self._font(words[0]["segs"][0][1], size)) \
            if words[0]["segs"] else self._text_w(" ", self._font(frozenset(), size))
        if justify and gaps > 0:
            target = cfg.content_w - line["indent"]
            gap = max(base_sp, (target - natural) / gaps)
        else:
            gap = base_sp
        for i, word in enumerate(words):
            wx = x
            for text, style in word["segs"]:
                font = self._font(style, size)
                self._draw.text((wx, y), text, font=font, fill=0)
                wx += self._text_w(text, font)
            x += word["w"] + (gap if i < gaps else 0)

    # -- images -------------------------------------------------------------
    def _render_image(self, data):
        cfg = self.cfg
        try:
            im = Image.open(io.BytesIO(data)).convert("L")
        except Exception:
            return
        max_w = cfg.content_w
        max_h = cfg.height - 2 * cfg.margin_y
        scale = min(max_w / im.width, max_h / im.height, 1.0)
        new_w, new_h = max(1, int(im.width * scale)), max(1, int(im.height * scale))
        im = im.resize((new_w, new_h), Image.LANCZOS).convert("1")  # FS dither

        if not self._empty:
            self._y += cfg.para_spacing
        # move to fresh page if it won't fit and we're not already at the top
        if not self._empty and self._y + new_h > cfg.content_bottom:
            self._flush_page()

        x = cfg.margin_x + (max_w - new_w) // 2
        self._img.paste(im, (int(x), int(self._y)))
        self._y += new_h + cfg.para_spacing
        self._empty = False

    # -- packing ------------------------------------------------------------
    def _pack(self, img):
        # PIL "1" packs MSB-first, bit=1 -> white(255): exactly the Waveshare
        # 0xFF==white convention, so .tobytes() is panel-ready.
        #
        # Use a hard threshold (dither=NONE) rather than the default
        # Floyd-Steinberg dither. Glyphs are drawn antialiased into the "L"
        # page, so their edges are gray; dithering scatters those gray pixels
        # into an on/off pattern that reads as grainy / "missing" pixels in the
        # letters. Thresholding snaps each pixel to solid black/white for crisp
        # text. Images are already dithered to pure 0/255 in _render_image
        # before they reach here, so the threshold leaves them untouched.
        return img.convert("1", dither=Image.NONE).tobytes()

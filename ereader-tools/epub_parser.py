"""
epub_parser.py
==============
Turns an EPUB into a flat, renderer-friendly stream of "blocks".

We deliberately throw away almost all of the EPUB's CSS. A 7.5" 1-bit panel
can't meaningfully reproduce arbitrary web styling, and trying to would mean
shipping an HTML/CSS engine onto the MCU -- exactly what this pipeline exists
to avoid. Instead we keep the structure that matters for reading:

    headings, paragraphs, inline bold/italic, images, and chapter breaks.

Block shapes
------------
    {"type": "heading", "level": int(1..6), "runs": [(text, style), ...]}
    {"type": "para",    "runs": [(text, style), ...]}
    {"type": "image",   "data": bytes}
    {"type": "break"}                      # force a new page (chapter boundary)

`style` is a small set of flags: frozenset() / {"b"} / {"i"} / {"b","i"}.
"""

import re
import posixpath
import warnings

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString, Tag
from bs4 import XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BOLD_TAGS = {"b", "strong"}
ITALIC_TAGS = {"i", "em"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_TAGS = {"script", "style", "head", "title", "nav"}
BLOCK_TAGS = {"p", "div", "li", "blockquote", "section", "article", "tr"}

_ws_re = re.compile(r"\s+")


def _collapse(text: str) -> str:
    return _ws_re.sub(" ", text)


class EpubParser:
    def __init__(self, path):
        self.book = epub.read_epub(path)
        self._image_cache = {}
        # map every image item by several key forms so hrefs resolve reliably
        for item in self.book.get_items_of_type(ebooklib.ITEM_IMAGE):
            name = item.get_name()
            self._image_cache[name] = item.get_content()
            self._image_cache[posixpath.basename(name)] = item.get_content()

    # -- metadata -----------------------------------------------------------
    def metadata(self):
        def first(ns, tag):
            vals = self.book.get_metadata(ns, tag)
            return vals[0][0] if vals else ""
        return {
            "title": first("DC", "title") or "Untitled",
            "author": first("DC", "creator") or "Unknown",
            "language": first("DC", "language") or "en",
        }

    # -- spine order --------------------------------------------------------
    def _spine_items(self):
        items = []
        for idref, _ in self.book.spine:
            item = self.book.get_item_with_id(idref)
            if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            # skip the EPUB navigation document -- the device has its own menu
            if isinstance(item, epub.EpubNav) or idref == "nav":
                continue
            items.append(item)
        return items

    def _resolve_image(self, src, doc_name):
        if not src:
            return None
        # strip query/fragment, resolve relative to the document path
        src = src.split("?")[0].split("#")[0]
        candidates = [
            src,
            posixpath.basename(src),
            posixpath.normpath(posixpath.join(posixpath.dirname(doc_name), src)),
        ]
        for c in candidates:
            if c in self._image_cache:
                return self._image_cache[c]
        return None

    # -- DOM -> runs --------------------------------------------------------
    def _runs_from(self, node, style):
        """Yield (text, style) tuples from an inline subtree."""
        runs = []
        for child in node.children:
            if isinstance(child, NavigableString):
                txt = _collapse(str(child))
                if txt:
                    runs.append((txt, style))
            elif isinstance(child, Tag):
                if child.name in SKIP_TAGS:
                    continue
                child_style = set(style)
                if child.name in BOLD_TAGS:
                    child_style.add("b")
                if child.name in ITALIC_TAGS:
                    child_style.add("i")
                if child.name == "br":
                    runs.append(("\n", style))
                    continue
                if child.name == "img":
                    continue  # images are handled as block-level elements
                runs.extend(self._runs_from(child, frozenset(child_style)))
        return runs

    @staticmethod
    def _clean_runs(runs):
        # merge adjacent same-style runs, drop empties
        merged = []
        for text, style in runs:
            if not text:
                continue
            if merged and merged[-1][1] == style and text != "\n" \
                    and merged[-1][0] != "\n":
                merged[-1] = (merged[-1][0] + text, style)
            else:
                merged.append((text, style))
        return merged

    # -- main ---------------------------------------------------------------
    def blocks(self):
        """Return (blocks, toc) where toc is [(chapter_title, block_index)]."""
        blocks = []
        toc = []

        for item in self._spine_items():
            doc_name = item.get_name()
            soup = BeautifulSoup(item.get_content(), "lxml")
            body = soup.body or soup

            chapter_start_index = len(blocks)
            chapter_title = None

            # chapter boundary -> page break (skip the very first one)
            if blocks:
                blocks.append({"type": "break"})

            # Walk the body in document order. We treat headings, block-level
            # text containers, and images as block boundaries.
            for el in body.descendants:
                if not isinstance(el, Tag):
                    continue
                if el.name in SKIP_TAGS:
                    continue

                if el.name == "img":
                    data = self._resolve_image(el.get("src"), doc_name)
                    if data:
                        blocks.append({"type": "image", "data": data})
                    continue

                if el.name in HEADING_TAGS:
                    runs = self._clean_runs(self._runs_from(el, frozenset()))
                    if runs:
                        level = int(el.name[1])
                        blocks.append({"type": "heading", "level": level,
                                       "runs": runs})
                        if chapter_title is None:
                            chapter_title = "".join(t for t, _ in runs).strip()
                    continue

                if el.name in BLOCK_TAGS:
                    # Only take direct text of this block; nested block tags
                    # are visited on their own as descendants.
                    if el.find(BLOCK_TAGS | HEADING_TAGS | {"img"}):
                        # contains its own sub-blocks; let those be handled
                        # individually -- but still capture loose text here.
                        runs = self._clean_runs(
                            self._direct_text_runs(el))
                    else:
                        runs = self._clean_runs(self._runs_from(el, frozenset()))
                    if runs and any(t.strip() for t, _ in runs):
                        blocks.append({"type": "para", "runs": runs})

            if chapter_title is None:
                chapter_title = item.get_name()
            toc.append((chapter_title, chapter_start_index))

        return blocks, toc

    def _direct_text_runs(self, el):
        """Runs from immediate inline children only (skip nested block tags)."""
        runs = []
        for child in el.children:
            if isinstance(child, NavigableString):
                txt = _collapse(str(child))
                if txt:
                    runs.append((txt, frozenset()))
            elif isinstance(child, Tag):
                if child.name in (BLOCK_TAGS | HEADING_TAGS | SKIP_TAGS | {"img"}):
                    continue
                style = set()
                if child.name in BOLD_TAGS:
                    style.add("b")
                if child.name in ITALIC_TAGS:
                    style.add("i")
                runs.extend(self._runs_from(child, frozenset(style)))
        return runs

#!/usr/bin/env python3
"""
make_sample_epub.py
===================
Generate a small but representative EPUB for testing the pipeline:
multiple chapters, headings, bold/italic inline text, long paragraphs that
force wrapping and pagination, and an embedded generated image.
"""

import io
from ebooklib import epub
from PIL import Image, ImageDraw

LOREM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
         "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim "
         "ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut "
         "aliquip ex ea commodo consequat. Duis aute irure dolor in "
         "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
         "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
         "culpa qui officia deserunt mollit anim id est laborum.")


def make_image_bytes():
    im = Image.new("L", (480, 300), 255)
    d = ImageDraw.Draw(im)
    # a simple gradient + shapes to exercise dithering
    for x in range(480):
        d.line([(x, 0), (x, 300)], fill=int(255 * (x / 480)))
    d.ellipse([120, 60, 360, 240], outline=0, width=4)
    d.rectangle([200, 120, 280, 180], fill=0)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def main():
    book = epub.EpubBook()
    book.set_identifier("sample-erb-001")
    book.set_title("The Test Voyage")
    book.set_language("en")
    book.add_author("Ada Lumen")

    # embedded image
    img_item = epub.EpubItem(uid="img1", file_name="images/figure.png",
                             media_type="image/png", content=make_image_bytes())
    book.add_item(img_item)

    chapters = []
    titles = ["Departure", "The Long Crossing", "Landfall"]
    for ci, title in enumerate(titles, 1):
        c = epub.EpubHtml(title=title, file_name=f"chap_{ci}.xhtml", lang="en")
        body = [f"<h1>{title}</h1>"]
        if ci == 2:
            body.append('<p>Below is a figure from the ship\u2019s log:</p>')
            body.append('<p><img src="images/figure.png" alt="figure"/></p>')
        # several paragraphs with inline styling, enough to span pages
        for p in range(6):
            txt = LOREM
            if p % 2 == 0:
                txt = txt.replace("dolor", "<b>dolor</b>", 1)
            if p % 3 == 0:
                txt = txt.replace("veniam", "<i>veniam</i>", 1)
            body.append(f"<p>{txt}</p>")
        body.append(f"<h2>Notes on {title}</h2>")
        body.append(f"<p>{LOREM}</p>")
        c.content = "<html><body>" + "".join(body) + "</body></html>"
        book.add_item(c)
        chapters.append(c)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + chapters

    epub.write_epub("sample.epub", book)
    print("wrote sample.epub")


if __name__ == "__main__":
    main()

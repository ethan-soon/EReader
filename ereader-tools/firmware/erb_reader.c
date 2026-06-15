/*
 * erb_reader.c
 * ============
 * Reference reader for the .erb format, written to be portable to the STM32
 * firmware. It is deliberately allocation-light: the caller owns the
 * framebuffer and the page table buffer; this code never calls malloc.
 *
 * Porting to the device:
 *   - Replace the <stdio.h> FILE* I/O with your SD/FatFs calls
 *     (f_open / f_lseek / f_read). The structure of the code stays identical.
 *   - erb_render_page() decodes straight into your 48 KB EPD framebuffer.
 *
 * Build a quick host test:
 *   cc -o erbtest erb_reader.c && ./erbtest book.erb 0 out.pbm
 */
#include "erb_format.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

typedef struct {
    FILE *fp;
    erb_header_t hdr;
} erb_file_t;

/* ---- PackBits decode (mirror of packbits_encode in erb_format.py) ----
 * Decodes `src_len` bytes from src into dst, writing at most dst_cap bytes.
 * Returns the number of bytes written, or -1 on overflow. */
int erb_packbits_decode(const uint8_t *src, uint32_t src_len,
                        uint8_t *dst, uint32_t dst_cap)
{
    uint32_t si = 0, di = 0;
    while (si < src_len) {
        uint8_t h = src[si++];
        if (h < 128) {                    /* literal run of (h+1) bytes */
            uint32_t cnt = (uint32_t)h + 1;
            if (di + cnt > dst_cap || si + cnt > src_len) return -1;
            memcpy(dst + di, src + si, cnt);
            di += cnt; si += cnt;
        } else if (h > 128) {             /* replicate next byte (257-h)x */
            uint32_t cnt = 257u - h;
            if (di + cnt > dst_cap || si >= src_len) return -1;
            memset(dst + di, src[si++], cnt);
            di += cnt;
        }
        /* h == 128: no-op (never produced by our encoder) */
    }
    return (int)di;
}

/* ---- open / validate ---- */
int erb_open(erb_file_t *e, const char *path)
{
    e->fp = fopen(path, "rb");
    if (!e->fp) return -1;
    if (fread(&e->hdr, 1, sizeof(erb_header_t), e->fp) != sizeof(erb_header_t))
        return -2;
    if (memcmp(e->hdr.magic, "ERB1", 4) != 0) return -3;
    return 0;
}

void erb_close(erb_file_t *e)
{
    if (e->fp) fclose(e->fp);
    e->fp = NULL;
}

/* ---- read the page table into a caller-provided buffer ----
 * table must hold hdr.page_count entries. */
int erb_read_page_table(erb_file_t *e, erb_page_entry_t *table)
{
    uint32_t n = e->hdr.page_count;
    if (fseek(e->fp, e->hdr.ptable_off, SEEK_SET) != 0) return -1;
    if (fread(table, sizeof(erb_page_entry_t), n, e->fp) != n) return -2;
    return 0;
}

/* ---- render one page into framebuffer (bytes_per_page bytes) ----
 * `scratch` is a temporary buffer big enough to hold the largest compressed
 * blob; pass NULL + 0 when the file is uncompressed. Returns 0 on success. */
int erb_render_page(erb_file_t *e, const erb_page_entry_t *table,
                    uint32_t page, uint8_t *framebuffer,
                    uint8_t *scratch, uint32_t scratch_cap)
{
    if (page >= e->hdr.page_count) return -1;
    const erb_page_entry_t *pe = &table[page];

    if (fseek(e->fp, pe->offset, SEEK_SET) != 0) return -2;

    if (e->hdr.flags & ERB_FLAG_RLE) {
        if (pe->length > scratch_cap) return -3;
        if (fread(scratch, 1, pe->length, e->fp) != pe->length) return -4;
        int n = erb_packbits_decode(scratch, pe->length,
                                    framebuffer, e->hdr.bytes_per_page);
        if (n != (int)e->hdr.bytes_per_page) return -5;
    } else {
        if (pe->length != e->hdr.bytes_per_page) return -6;
        if (fread(framebuffer, 1, pe->length, e->fp) != pe->length) return -7;
    }
    return 0;
}

/* =====================================================================
 * 1bpp rotation: map a decoded page onto the physical panel orientation.
 *
 * The Waveshare 7.5" is physically 800x480 (landscape). A portrait book is
 * laid out 480x800, so the firmware rotates each page by hdr.panel_rotation
 * degrees before sending it to the panel. On a 180 MHz M4 this is a sub-
 * millisecond memory shuffle -- negligible next to the EPD refresh time.
 *
 *   src : decoded logical page, sw x sh, 1bpp MSB-first
 *   dst : panel-native buffer; for 90/270 its size is sh x sw
 * ===================================================================== */
static inline int erb__gp(const uint8_t *b, int stride, int x, int y) {
    return (b[y * stride + (x >> 3)] >> (7 - (x & 7))) & 1;
}
static inline void erb__sp(uint8_t *b, int stride, int x, int y, int v) {
    uint8_t *p = &b[y * stride + (x >> 3)];
    uint8_t m = (uint8_t)(1u << (7 - (x & 7)));
    if (v) *p |= m; else *p &= (uint8_t)~m;
}

void erb_rotate_1bpp(const uint8_t *src, int sw, int sh,
                     uint8_t *dst, int degrees)
{
    int ss = (sw + 7) / 8;
    if (degrees == 0) { memcpy(dst, src, (size_t)ss * sh); return; }
    int dw = (degrees == 180) ? sw : sh;     /* dst width  */
    int ds = (dw + 7) / 8;
    for (int y = 0; y < sh; y++) {
        for (int x = 0; x < sw; x++) {
            int v = erb__gp(src, ss, x, y);
            int dx, dy;
            if (degrees == 90)       { dx = sh - 1 - y; dy = x; }
            else if (degrees == 180) { dx = sw - 1 - x; dy = sh - 1 - y; }
            else /* 270 */           { dx = y;          dy = sw - 1 - x; }
            erb__sp(dst, ds, dx, dy, v);
        }
    }
}

/* =====================================================================
 * Tiny host-side self-test: dump a page to a PBM image you can open.
 * Compiled only when ERB_READER_TEST is defined.
 * ===================================================================== */
#ifdef ERB_READER_TEST
int main(int argc, char **argv)
{
    if (argc < 4) { fprintf(stderr, "usage: %s book.erb PAGE out.pbm\n", argv[0]); return 1; }
    erb_file_t e;
    if (erb_open(&e, argv[1]) != 0) { fprintf(stderr, "open failed\n"); return 1; }

    uint32_t page = (uint32_t)atoi(argv[2]);
    static erb_page_entry_t table[4096];   /* host test cap */
    if (e.hdr.page_count > 4096) { fprintf(stderr, "too many pages for test\n"); return 1; }
    erb_read_page_table(&e, table);

    static uint8_t fb[200000];
    static uint8_t scratch[200000];
    if (erb_render_page(&e, table, page, fb, scratch, sizeof(scratch)) != 0) {
        fprintf(stderr, "render failed\n"); return 1;
    }

    /* write PBM (P4). PBM: 1 = black, but our bit 1 = white, so invert. */
    FILE *o = fopen(argv[3], "wb");
    int pack_w = (e.hdr.width + 7) / 8 * 8;
    fprintf(o, "P4\n%d %d\n", pack_w, e.hdr.height);
    for (uint32_t i = 0; i < e.hdr.bytes_per_page; i++) fputc(~fb[i] & 0xFF, o);
    fclose(o);
    fprintf(stderr, "wrote %s (%u pages total)\n", argv[3], e.hdr.page_count);
    erb_close(&e);
    return 0;
}
#endif

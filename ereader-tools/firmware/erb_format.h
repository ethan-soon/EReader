/*
 * erb_format.h
 * ============
 * On-disk layout of the ".erb" (E-Reader Book) container, for the STM32 side.
 *
 * Everything is little-endian, matching the Cortex-M4, so a packed struct can
 * be filled with a single read from the SD card and used directly.
 *
 * Reading flow on the device:
 *   1. Read the 64-byte header once.
 *   2. Read the page table (page_count * 8 bytes) once into RAM
 *      (e.g. 1000 pages -> 8 KB; fits comfortably).
 *   3. To show page N: seek table[N].offset, read table[N].length bytes,
 *      and -- if FLAG_RLE -- PackBits-decode them into the framebuffer.
 *
 * The framebuffer is bytes_per_page bytes (48000 for 800x480 @ 1bpp),
 * row-major, 8 pixels per byte, MSB = leftmost pixel, bit==1 -> white.
 * That is exactly what the Waveshare 7.5" EPD expects (0xFF == white),
 * so you can DMA it to the panel with no transformation.
 */
#ifndef ERB_FORMAT_H
#define ERB_FORMAT_H

#include <stdint.h>

#define ERB_MAGIC  0x31425245u  /* "ERB1" little-endian */
#define ERB_HEADER_SIZE 64

/* header flags */
#define ERB_FLAG_RLE        0x0001u  /* page blobs are PackBits compressed   */
#define ERB_FLAG_GRAYSCALE  0x0002u  /* reserved: 4bpp grayscale             */

#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[4];        /* 'E','R','B','1'                            */
    uint16_t version;         /* format version (currently 1)              */
    uint16_t flags;           /* ERB_FLAG_*                                */
    uint16_t width;           /* pixels                                    */
    uint16_t height;          /* pixels                                    */
    uint8_t  bpp;             /* bits per pixel (1)                        */
    uint8_t  bit_order;       /* 0 = MSB-first                             */
    uint8_t  panel_rotation;  /* deg to rotate page onto panel: 0/90/180/270*/
    uint8_t  reserved;        /* padding -> next field 4-byte aligned      */
    uint32_t page_count;
    uint32_t toc_count;
    uint32_t meta_off;        /* file offset of metadata block             */
    uint32_t toc_off;         /* file offset of TOC block                  */
    uint32_t ptable_off;      /* file offset of the page offset table      */
    uint32_t pdata_off;       /* file offset of the first page blob        */
    uint32_t bytes_per_page;  /* uncompressed framebuffer size in bytes    */
    /* bytes 44..63 are zero padding to ERB_HEADER_SIZE */
} erb_header_t;

/* one entry of the page offset table */
typedef struct {
    uint32_t offset;          /* absolute file offset of this page blob    */
    uint32_t length;          /* byte length of the (maybe compressed) blob*/
} erb_page_entry_t;
#pragma pack(pop)

/*
 * Metadata block (at meta_off): three length-prefixed UTF-8 strings in order
 *   title, author, language. Each is: uint16 len, then len bytes.
 *
 * TOC block (at toc_off):
 *   uint32 count
 *   count * { uint16 name_len; char name[name_len]; uint32 page_index; }
 */

#endif /* ERB_FORMAT_H */

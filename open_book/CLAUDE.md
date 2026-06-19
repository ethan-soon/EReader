# CLAUDE.md — open_book

Guidance for Claude Code when working in the **open_book** project. This is the
**merge target**: it combines the proven `epaper_test` (Waveshare 7.5" V2 e-paper
on SPI1) and `sdcard_test` (SD-over-SPI2 + FatFs + `.erb` book reader) into one
firmware. Target MCU: **STM32F446RETx** (Cortex-M4, 84 MHz, 512 KB Flash, 128 KB RAM)
on a NUCLEO-F446RE.

## Workflow rules (do NOT do these unless explicitly asked)
- **CubeMX / `.ioc` / pins / clocks / peripherals** — the user edits these in the
  STM32CubeMX GUI and regenerates. Claude *describes* the needed settings; it does
  not edit the `.ioc` or hand-edit generated peripheral config.
- **Builds, flashing, and all git operations** — the user does these manually.
- All custom code lives inside `/* USER CODE BEGIN/END */` markers or in separate
  files the project owns, so CubeMX regeneration preserves it.

UART2 (115200 8N1) is on the ST-LINK virtual COM port — `printf` is retargeted to
it (`__io_putchar` in `main.c` USER CODE 4) for debug.

---

## CURRENT STATE — where we left off (2026-06-16)

**Goal of this session:** merge the two test projects and write a test that prints
**page 0 of ORV** to the e-paper. Both subsystems were tested working individually.

### ✅ Code is DONE (committed to the working tree, not flashed)
Implemented the "print page 0 of ORV" bring-up test. Files added to `Core/{Inc,Src}`:
- **SD + FatFs + .erb reader** (copied from `sdcard_test`): `sd_spi.[ch]`,
  `sd_diskio_spi.[ch]`, `sd_functions.[ch]`, `erb_reader.c`, `erb_format.h`.
- **E-paper driver** (copied from `epaper_test`): `EPD_7in5_V2.[ch]`, `Debug.h`,
  `DEV_Config.c`.
- **`Core/Inc/DEV_Config.h`** — NEW, rewritten to map the Waveshare driver onto
  open_book's new `EP_*` pin labels (the test projects used different labels).
- **`CMakeLists.txt`** — the six `.c` files above were added to `target_sources`.
- **`Core/Src/main.c`** USER CODE blocks — the test itself:
  mount SD → `erb_open("ORV.erb")` → `erb_render_page_n(0, ...)` (decode 480×800,
  RLE) → `erb_rotate_1bpp(...)` onto the 800×480 panel → `EPD_7IN5_V2_Display()`.
  Prints progress over UART at each step.

### ⛔ BLOCKED ON: CubeMX changes the user still must make
The e-paper SPI bus (SPI1: PA5/PA7, CS PA9) is configured, but the **four e-paper
control GPIOs are missing**, and BUSY collides with the SD clock. Add in CubeMX,
using these **exact User Labels** (DEV_Config.h depends on them):

| Pin  | Mode        | User Label | Note                                   |
|------|-------------|------------|----------------------------------------|
| PC7  | GPIO_Output | `EP_DC`    | same pin as epaper_test                |
| PB6  | GPIO_Output | `EP_RST`   | same pin as epaper_test                |
| PA8  | GPIO_Output | `EP_PWR`   | same pin as epaper_test                |
| PC6  | GPIO_Input  | `EP_BUSY`  | **moved** — epaper_test used PB10, now SD_SCK |

**Hardware:** move the e-paper **BUSY jumper from PB10 → PC6**. DC/RST/PWR keep
their epaper_test pins (no rewiring). PC6 is a recommendation (any free GPIO works;
just match the label). Avoid the SWD pins PA13/PA14/PB3.

After regenerating, **verify `main()` does NOT call `MX_FATFS_Init()`** — like
`sdcard_test`, `sd_mount()` links the SD driver to drive 0 itself via
`FATFS_LinkDriver(&SD_Driver, ...)`. If CubeMX re-adds `MX_FATFS_Init()`, the
generic `USER_Driver` stub claims drive 0 and `f_open("ORV.erb")` fails.

### Remaining steps to finish the test
1. Apply the CubeMX pin changes above + regenerate (USER CODE survives).
2. Move the BUSY jumper to PC6.
3. Copy `ereader-tools/ORV_test.erb` to the SD card root, **renamed `ORV.erb`**.
4. Build, flash, watch UART @ 115200.
5. If page 0 is **upside-down**: change the `book.hdr.panel_rotation` argument in
   the `erb_rotate_1bpp(...)` call in `main.c` from the header value (90) to `270`.
   No `.erb` rebuild needed — it's just the 90-vs-270 panel-mounting convention.

---

## Pin map (open_book)

| Signal              | Pin   | Peripheral / mode        |
|---------------------|-------|--------------------------|
| B1 user button      | PC13  | EXTI falling             |
| USART2 TX / RX      | PA2 / PA3 | virtual COM via ST-LINK |
| **E-paper (SPI1)**  |       |                          |
| EP_SCK              | PA5   | SPI1_SCK                 |
| EP_MISO             | PA6   | SPI1_MISO (unused by panel) |
| EP_MOSI             | PA7   | SPI1_MOSI                |
| EP_CS               | PA9   | GPIO output              |
| EP_DC  *(to add)*   | PC7   | GPIO output              |
| EP_RST *(to add)*   | PB6   | GPIO output              |
| EP_PWR *(to add)*   | PA8   | GPIO output              |
| EP_BUSY *(to add)*  | PC6   | GPIO input               |
| **SD card (SPI2 + DMA)** |  |                          |
| SD_SCK              | PB10  | SPI2_SCK                 |
| SD_MISO             | PC2   | SPI2_MISO                |
| SD_MOSI             | PC1   | SPI2_MOSI                |
| SD_CS               | PC3   | GPIO output              |
| SWD                 | PA13/PA14/PB3 | debug            |

SPI1 (e-paper) prescaler /8 (~10.5 MHz). SPI2 (SD) starts /256 for card init, then
`sd_spi.c` bumps to /8 (~5.25 MHz) for data. Clock: HSI→PLL→84 MHz; APB1 42, APB2 84.

---

## The `.erb` book format (rendering pipeline)

All layout/fonts happen on the PC in `../ereader-tools/`; the device only decodes
finished pixels. See `../ereader-tools/format_spec.md` and `Core/Inc/erb_format.h`.

- `ORV.erb` header: **480×800 portrait, RLE (PackBits), panel_rotation=90,
  5918 pages, 48000 B/page.** Page 0 ≈ 10.8 KB compressed; largest page ≈ 18.7 KB.
- Page bits: 1bpp, MSB-first, **bit 1 = white** — exactly the Waveshare convention
  (0xFF = white), so a decoded page is panel-ready with no inversion.
- Decode flow: `erb_render_page_n(page, fb, scratch, cap)` reads the page's table
  entry, reads the compressed blob into `scratch`, PackBits-decodes into `fb`.
  Then `erb_rotate_1bpp(fb, 480, 800, panelbuf, 90)` produces the 800×480 panel
  buffer for `EPD_7IN5_V2_Display(panelbuf)`.

### RAM budget (128 KB) — why only two 48 KB buffers
Three 48 KB framebuffers (logical + panel + scratch) would overflow SRAM. The test
uses **two**: `erb_logical[48000]` and `erb_panel[48000]`. `erb_panel` is passed in
as the RLE *scratch* during decode (free until the rotate step), then receives the
rotated panel-native image. Safe because the biggest compressed page (18.7 KB) is
far under 48 KB. Heap stays unused (no malloc); buffers are `static`, not on the
~1 KB stack.

---

## E-paper API quick reference
Full cheat-sheet: `../EPAPER_API.txt`. Key calls (from `EPD_7in5_V2.h` / `DEV_Config.h`):
`DEV_Module_Init()` → `EPD_7IN5_V2_Init_Fast()` → `EPD_7IN5_V2_Display(buf)` →
`EPD_7IN5_V2_Sleep()`. Init waits on the BUSY line — if the panel is miswired it
blocks there (UART banner still prints first, confirming the MCU is alive).
`GUI_Paint`/`fonts` are NOT used here — the `.erb` page is already rasterized.

## Build
Toolchain `arm-none-eabi-gcc` on PATH, Ninja generator.
`cmake --preset Debug` then `cmake --build --preset Debug` → `build/Debug/open_book.elf`.

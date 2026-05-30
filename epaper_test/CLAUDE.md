# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the firmware for a custom DIY e-reader, currently at the breadboard prototyping stage. The target MCU is the **STM32F446RETx** (Cortex-M4, 84MHz, 512KB Flash, 128KB RAM) on a NUCLEO-F446RE development board.

Final hardware specs (from ProjectSpecs.txt):
- **Display**: 7.5" Waveshare e-paper panel
- **Storage**: 8GB SD card via SPI
- **Buttons**: 4 tactile buttons (power, menu/back, up/forward, down/backward) + optional spin wheel
- **File formats**: EPUB (primary), TXT

## Build System

> **Workflow note for Claude:** The user does the following **manually** and Claude
> must NOT do them unless explicitly asked:
> - **Peripheral / pin / clock configuration in STM32CubeMX.** Do NOT edit the
>   `.ioc` file or hand-edit CubeMX-generated config to add, remove, or reconfigure
>   peripherals, pins, or clocks. When a change needs CubeMX, *describe* the exact
>   settings for the user to apply in the CubeMX GUI and regenerate themselves.
> - **Builds** — do NOT run `cmake`/build.
> - **Flashing** — do NOT flash the board.
> - **All git operations** — do NOT run `git commit`, `push`, `stage`/`add`, etc.
>
> Make the code/doc changes and let the user configure CubeMX, build, flash, and
> commit. You may still *read* git state (e.g. `git status`) only when needed to
> answer a question.

**Toolchain**: `arm-none-eabi-gcc` — must be on `PATH`.
**Build tool**: Ninja (required by CMakePresets.json).

```bash
# Configure (first time or after CMakeLists changes)
cmake --preset Debug

# Build
cmake --build --preset Debug

# Output artifact
build/Debug/epaper_test.elf
```

The `Release` preset uses `-Os` optimization; `Debug` uses `-O0 -g3`.

To flash, convert `.elf` to `.bin` and use STM32CubeProgrammer or OpenOCD with the ST-LINK on the NUCLEO board:
```bash
arm-none-eabi-objcopy -O binary build/Debug/epaper_test.elf build/Debug/epaper_test.bin
```

UART2 (115200 8N1) is connected to the NUCLEO's virtual COM port via ST-LINK — use it for debug output.

## Code Architecture

### STM32CubeMX Code Generation

The project is generated and maintained by **STM32CubeMX** (`Epaper_Files/epaper_test.ioc`). Re-running CubeMX regenerates `Core/Src/main.c`, `Core/Src/stm32f4xx_it.c`, and related files while preserving only content between `/* USER CODE BEGIN <tag> */` and `/* USER CODE END <tag> */` markers. All custom code must live inside those markers.

- `Epaper_Files/epaper_test.ioc` — peripheral configuration source of truth; open in STM32CubeMX to change pinouts, clocks, or enable new peripherals
- `cmake/stm32cubemx/CMakeLists.txt` — CubeMX-managed build config; add new HAL driver `.c` files here under `STM32_Drivers_Src` if enabling new peripherals
- `CMakeLists.txt` — user-editable top-level; add your own source files, include paths, and libraries here

### Pin Assignments (from `Core/Inc/main.h`)

| Signal | Pin | Notes |
|--------|-----|-------|
| B1 (user button) | PC13 | EXTI falling edge |
| LD2 (green LED) | PA5 | GPIO output |
| USART2 TX | PA2 | Virtual COM via ST-LINK |
| USART2 RX | PA3 | |
| SWD SWDIO | PA13 | Debug |
| SWD SWCLK | PA14 | Debug |
| SWO | PB3 | Debug |

### Memory Layout

Defined in `STM32F446XX_FLASH.ld`:
- Flash: 512KB starting at `0x08000000`
- RAM: 128KB starting at `0x20000000`
- Heap: 0x200 (512 bytes) — **very small**, increase before adding dynamic allocation
- Stack: 0x400 (1KB)

### Clock Configuration

84MHz system clock: HSI (16MHz) → PLL (×336, ÷4) → 84MHz SYSCLK. APB1 = 42MHz, APB2 = 84MHz.

## Development Workflow

When adding a new peripheral (SPI for SD card, SPI for e-paper, etc.):
1. Configure it in STM32CubeMX (`.ioc` file) and regenerate
2. Add the corresponding HAL driver `.c` to `STM32_Drivers_Src` in `cmake/stm32cubemx/CMakeLists.txt` if CubeMX doesn't include it
3. Add your driver source files and headers to the user sections of the top-level `CMakeLists.txt`
4. Write driver code inside `/* USER CODE */` blocks or in separate files you own

## Current Project Phase

The repository is in **Phase 3** of the project plan (breadboard prototyping).

**The e-paper display is working — this is the known-good baseline.** `main()` runs a
"hello world" connection test in the `USER CODE BEGIN 2` block: it inits the panel,
draws black text + a border on a white background, does a single fast full refresh,
then sleeps the panel. The wiring (SPI1 + RST/DC/CS/BUSY/PWR control pins) is confirmed
end-to-end on the breadboard.

Baseline display configuration (see `Core/Src/main.c`):
- Driver: Waveshare 7.5" V2 (`EPD_7IN5_V2_*`), native panel resolution 800×480.
- Framebuffer: `static UBYTE epd_image[800*480/8]` (48000 B). **Static, not malloc'd** —
  this deliberately sidesteps the tiny 512 B heap, so the heap was never bumped.
- Orientation: **portrait, `ROTATE_270`**, giving a 480-wide × 800-tall logical canvas
  (right-side-up for how the panel sits in the breadboard). `ROTATE_90` flips it 180°.
- Refresh: `EPD_7IN5_V2_Init_Fast()` + single `EPD_7IN5_V2_Display()` (one quick full
  refresh; the redundant `EPD_7IN5_V2_Clear()` was removed to cut flashing).
- `printf` is retargeted to USART2 via `__io_putchar()` in `USER CODE BEGIN 0`, so the
  test prints progress to the ST-LINK virtual COM port (115200 8N1) — an MCU-alive
  signal independent of the panel wiring.

The earlier "what to fix next" list (F1→F4 HAL includes, missing pin defines, tiny heap)
is **done/resolved**: includes are F4, the `_Pin`/`_GPIO_Port` defines live in `main.h`,
and the heap is avoided via the static framebuffer.

See `EPAPER_API.txt` for a cheat-sheet of the display functions (init, fonts, drawing
text/shapes, orientation, refresh modes).

### Next steps
- Wire up and read from the SD card over SPI (separate SPI peripheral or shared bus).
- Render real text content (page layout, line wrapping) instead of the test strings.
- Bring up partial refresh (`EPD_7IN5_V2_Init_Part` / `Display_Part`) for flicker-free
  page turns, with a periodic full refresh to clear ghosting.
- Validate button input (PC13 user button is wired as EXTI) and basic sleep/wake.



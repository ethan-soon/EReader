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

The repository is in **Phase 3** of the project plan (breadboard prototyping). The main.c is a bare CubeMX scaffold — UART2 and GPIO are initialized but the main loop is empty. The next steps per the project plan are:
- Connect and drive the e-paper display (full refresh, then partial refresh)
- Wire up and read from the SD card over SPI
- Render text to the screen
- Validate button input and basic sleep/wake

## What to Fix Next

1. Wrong HAL includes — DEV_Config.h line 52 and DEV_Config.c line 33 include stm32f1xx_hal.h / stm32f1xx_hal_spi.h. Change those to stm32f4xx_hal.h / stm32f4xx_hal_spi.h.
2. Pin definitions missing — DEV_Config.h references RST_GPIO_Port, DC_GPIO_Port, PWR_GPIO_Port, SPI_CS_GPIO_Port, BUSY_GPIO_Port, DIN_GPIO_Port, SCK_GPIO_Port — these need to be
defined in main.h after you configure SPI1 and the e-paper control pins in STM32CubeMX.
3. Heap too small — the test does malloc(Imagesize) where Imagesize = 800×480/8 = 48,000 bytes. Your current heap is 0x200 (512 bytes). You'll need to increase it in
startup_stm32f446xx.s (look for Heap_Size near the top).

So the order of attack is: configure SPI + GPIO pins in CubeMX → fix the F1→F4 HAL includes → bump the heap → add all those files to CMakeLists.txt → call EPD_test() from main().



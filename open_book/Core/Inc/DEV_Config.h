/*****************************************************************************
* | File      	:   DEV_Config.h
* | Author      :   Waveshare team
* | Function    :   Hardware underlying interface
* | Info        :
*                Used to shield the underlying layers of each master
*                and enhance portability
*----------------
* |	This version:   V2.0
* | Date        :   2018-10-30
*
* NOTE (open_book): adapted from epaper_test for the merged e-reader project.
* The CubeMX user labels changed (now EP_*), so the e-paper GPIO macros below
* point at the EP_* defines generated in main.h. The e-paper uses SPI1 (hspi1);
* the SD card uses SPI2 (hspi2) -- separate buses, no sharing.
******************************************************************************/
#ifndef _DEV_CONFIG_H_
#define _DEV_CONFIG_H_

#include "main.h"
#include "stm32f4xx_hal.h"
#include <stdint.h>
#include <stdio.h>

/**
 * data
**/
#define UBYTE   uint8_t
#define UWORD   uint16_t
#define UDOUBLE uint32_t

/**
 * e-Paper GPIO — expands to (GPIOx, GPIO_Pin) pairs matching HAL signatures.
 * These map to the EP_* labels set in STM32CubeMX (see Core/Inc/main.h):
 *   EP_RST   - GPIO output   (reset)
 *   EP_DC    - GPIO output   (data/command)
 *   EP_PWR   - GPIO output   (panel power rail enable)
 *   EP_CS    - GPIO output   (SPI1 chip select, software NSS)
 *   EP_BUSY  - GPIO input    (panel busy line)
 *   EP_MOSI  - SPI1_MOSI (PA7)
 *   EP_SCK   - SPI1_SCK  (PA5)
**/
#define EPD_RST_PIN     EP_RST_GPIO_Port,  EP_RST_Pin
#define EPD_DC_PIN      EP_DC_GPIO_Port,   EP_DC_Pin
#define EPD_PWR_PIN     EP_PWR_GPIO_Port,  EP_PWR_Pin
#define EPD_CS_PIN      EP_CS_GPIO_Port,   EP_CS_Pin
#define EPD_BUSY_PIN    EP_BUSY_GPIO_Port, EP_BUSY_Pin
#define EPD_MOSI_PIN    EP_MOSI_GPIO_Port, EP_MOSI_Pin
#define EPD_SCLK_PIN    EP_SCK_GPIO_Port,  EP_SCK_Pin

/**
 * GPIO read and write
**/
#define DEV_Digital_Write(_pin, _value) HAL_GPIO_WritePin(_pin, _value == 0 ? GPIO_PIN_RESET : GPIO_PIN_SET)
#define DEV_Digital_Read(_pin) HAL_GPIO_ReadPin(_pin)

/**
 * delay x ms
**/
#define DEV_Delay_ms(__xms) HAL_Delay(__xms)

void DEV_SPI_WriteByte(UBYTE value);
void DEV_SPI_Write_nByte(UBYTE *value, UDOUBLE len);

int  DEV_Module_Init(void);
void DEV_Module_Exit(void);
void DEV_GPIO_Init(void);
void DEV_SPI_Init(void);
void DEV_SPI_SendData(UBYTE Reg);
UBYTE DEV_SPI_ReadData(void);

#endif

#!/usr/bin/env python3
"""
Tiny bidirectional serial console for driving open_book over UART2.

Reads from the board in a background thread (prints everything it sends, e.g.
the boot banner, the "menu: N item box(es)" dump, and the "rx: 0x.." echoes),
and forwards each key you press straight to the board -- no Enter needed.

Keys the firmware understands: w/s = up/down, e or Enter = select, p = power,
c = continue (open ORV page 0).  Press Ctrl-] to quit.

Run it and either accept the defaults or specify parameters interactively,
the same way serial_read.py works.
"""
import sys
import threading

import serial  # pip install pyserial
import serial.tools.list_ports
import msvcrt   # Windows: read one keypress at a time, unbuffered

DEFAULTS = {
    "port": "COM3",
    "baudrate": 115200,
    "bytesize": serial.EIGHTBITS,
    "parity": serial.PARITY_NONE,
    "stopbits": serial.STOPBITS_ONE,
    # Short read timeout keeps the reader thread responsive and the keypress
    # loop snappy; HW flow control stays off (rtscts/dsrdtr default False) so
    # sends never silently block.
    "timeout": 0.05,
    "encoding": "utf-8",
}

BYTESIZE_MAP = {
    "5": serial.FIVEBITS,
    "6": serial.SIXBITS,
    "7": serial.SEVENBITS,
    "8": serial.EIGHTBITS,
}

PARITY_MAP = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
}

STOPBITS_MAP = {
    "1": serial.STOPBITS_ONE,
    "1.5": serial.STOPBITS_ONE_POINT_FIVE,
    "2": serial.STOPBITS_TWO,
}

def list_ports():
    ports = list(serial.tools.list_ports.comports())
    if ports:
        print("\nAvailable ports:")
        for p in ports:
            print(f"  {p.device} - {p.description}")
    else:
        print("\n  No COM ports detected. Is your device plugged in?")
    print()

def prompt(label, default, valid=None):
    hint = f" (default: {default})"
    hint += f", options: {', '.join(valid)}" if valid else ""
    while True:
        val = input(f"  {label}{hint}: ").strip()
        if val == "":
            return default
        if valid and val not in valid:
            print(f"    Invalid option. Choose from: {', '.join(valid)}")
            continue
        return val

def get_config():
    print("\n=== Serial Console ===")
    print("\n[1] Use defaults")
    print("[2] Specify parameters")
    choice = input("\nChoice (1/2): ").strip()

    if choice != "2":
        print(f"\nUsing defaults: {DEFAULTS['port']} @ {DEFAULTS['baudrate']} baud")
        return DEFAULTS.copy()

    list_ports()

    port     = prompt("Port", DEFAULTS["port"])
    baudrate = prompt("Baud rate", DEFAULTS["baudrate"])
    bytesize = prompt("Data bits", "8", list(BYTESIZE_MAP))
    parity   = prompt("Parity (N/E/O)", "N", list(PARITY_MAP))
    stopbits = prompt("Stop bits", "1", list(STOPBITS_MAP))
    encoding = prompt("Encoding (utf-8 / ascii / latin-1)", "utf-8")

    return {
        "port": port,
        "baudrate": int(baudrate),
        "bytesize": BYTESIZE_MAP[bytesize],
        "parity": PARITY_MAP[parity],
        "stopbits": STOPBITS_MAP[stopbits],
        "timeout": DEFAULTS["timeout"],
        "encoding": encoding,
    }

def open_port(config):
    available = [p.device for p in serial.tools.list_ports.comports()]
    if config["port"] not in available:
        print(f"\n  Error: '{config['port']}' was not found.")
        if available:
            print(f"  Available ports: {', '.join(available)}")
            print("  Check your device is plugged in, or update the port name.")
        else:
            print("  No COM ports detected at all.")
            print("  Check your device is plugged in and drivers are installed.")
        print("  Tip: Open Device Manager and look under 'Ports (COM & LPT)'.\n")
        return None

    try:
        ser = serial.Serial(
            port=config["port"],
            baudrate=config["baudrate"],
            bytesize=config["bytesize"],
            parity=config["parity"],
            stopbits=config["stopbits"],
            timeout=config["timeout"],
        )
        return ser
    except serial.SerialException as e:
        print(f"\n  Error opening port: {e}")
        print("  The port may be in use by another application (e.g. Arduino IDE).\n")
        return None

def reader(ser, encoding):
    """Print everything the board sends, as it arrives."""
    while True:
        data = ser.read(ser.in_waiting or 1)
        if data:
            sys.stdout.write(data.decode(encoding, "replace"))
            sys.stdout.flush()

def main():
    config = get_config()
    ser = open_port(config)
    if ser is None:
        return

    print(f"\n[connected {config['port']} @ {config['baudrate']}.  "
          f"w/s=move e=select p=power c=continue.  Ctrl-] quits]\n")

    threading.Thread(target=reader, args=(ser, config["encoding"]), daemon=True).start()

    try:
        while True:
            ch = msvcrt.getch()          # one keypress, no Enter required
            if ch == b"\x1d":            # Ctrl-]  -> quit
                break
            ser.write(ch)                # send the raw byte to the board
            ser.flush()
    finally:
        ser.close()

if __name__ == "__main__":
    main()

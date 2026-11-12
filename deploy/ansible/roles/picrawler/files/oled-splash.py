#!/usr/bin/env python3
"""Show the PiCrawler splash on the OLED during boot and shutdown.

    oled-splash.py splash.bin [--driver ssd1306|sh1106] [--bus 1] [--address 0x3C]
                              [--interval 0.5] [--max-seconds 180]

Standard library only: it runs from the SD card right after the i2c-dev
module is loaded, before the NVMe (venv, luma.oled) is mounted. splash.bin
holds frames of 1024 bytes in SSD1306 page layout, written by
`python -m services.display.splash --export`. One frame: shown, done. Two
frames (state icon on / off): they alternate until SIGTERM or max-seconds,
then the first frame (icon on) stays. The display keeps the picture until
the next writer takes over.
"""
import argparse
import fcntl
import os
import signal
import sys
import time

I2C_SLAVE = 0x0703
CHUNK = 32          # data bytes per I2C write, safe for every controller
FRAME = 1024

# 128x64 init, horizontal addressing mode (SSD1306)
SSD1306_INIT = [
    0xAE,              # display off
    0xD5, 0x80,        # clock divide
    0xA8, 0x3F,        # multiplex 64
    0xD3, 0x00,        # display offset
    0x40,              # start line 0
    0x8D, 0x14,        # charge pump on
    0x20, 0x00,        # horizontal addressing
    0xA1,              # segment remap
    0xC8,              # COM scan direction
    0xDA, 0x12,        # COM pins
    0x81, 0xCF,        # contrast
    0xD9, 0xF1,        # precharge
    0xDB, 0x40,        # VCOM detect
    0xA4,              # follow RAM
    0xA6,              # normal (not inverted)
]
# SH1106: page addressing only, 132 columns with the visible area at 2..129
SH1106_INIT = [0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0xAD, 0x8B, 0xA1, 0xC8,
               0xDA, 0x12, 0x81, 0xCF, 0xD9, 0x22, 0xDB, 0x40, 0xA4, 0xA6]


def split_frames(data):
    if not data or len(data) % FRAME:
        raise ValueError("splash must be a multiple of %d bytes, got %d" % (FRAME, len(data)))
    return [data[i:i + FRAME] for i in range(0, len(data), FRAME)]


def init_commands(driver):
    if driver == "ssd1306":
        return bytes([0x00] + SSD1306_INIT)
    if driver == "sh1106":
        return bytes([0x00] + SH1106_INIT)
    raise ValueError("driver must be ssd1306 or sh1106")


def frame_writes(driver, pages):
    """Writes that put one frame into the display RAM."""
    if len(pages) != FRAME:
        raise ValueError("a frame must be %d bytes, got %d" % (FRAME, len(pages)))
    out = []
    if driver == "ssd1306":
        out.append(bytes([0x00, 0x21, 0, 127, 0x22, 0, 7]))
        for i in range(0, FRAME, CHUNK):
            out.append(b"\x40" + pages[i:i + CHUNK])
    else:
        for page in range(8):
            out.append(bytes([0x00, 0xB0 + page, 0x02, 0x10]))   # page, column 2
            row = pages[page * 128:(page + 1) * 128]
            for i in range(0, 128, CHUNK):
                out.append(b"\x40" + row[i:i + CHUNK])
    return out


def transactions(driver, pages):
    """Init, the first frame and display on: (control byte + payload) each."""
    if len(pages) != FRAME:
        raise ValueError("splash must be 1024 bytes, got %d" % len(pages))
    out = [init_commands(driver)] + frame_writes(driver, pages)
    out.append(b"\x00\xAF")     # display on
    return out


def animate(write, frames, interval, max_seconds, stopping, sleep=time.sleep, clock=time.monotonic):
    """Alternate the frames until stopped or out of time; end on frame 0."""
    deadline = clock() + max_seconds
    i = 0
    while len(frames) > 1 and not stopping() and clock() < deadline:
        sleep(interval)
        i = (i + 1) % len(frames)
        write(frames[i])
    if i != 0:
        write(frames[0])       # leave the state icon on


def open_bus(bus, address, wait_s=5.0):
    """/dev/i2c-N may appear a moment after the module loads."""
    path = "/dev/i2c-%d" % bus
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fd = os.open(path, os.O_RDWR)
            break
        except FileNotFoundError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)
    fcntl.ioctl(fd, I2C_SLAVE, address)
    return fd


def main():
    parser = argparse.ArgumentParser(description="PiCrawler OLED boot splash")
    parser.add_argument("splash")
    parser.add_argument("--driver", default="ssd1306")
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", default="0x3C")
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--max-seconds", type=float, default=180.0)
    args = parser.parse_args()
    with open(args.splash, "rb") as f:
        frames = split_frames(f.read())
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    try:
        fd = open_bus(args.bus, int(args.address, 0))
    except OSError as e:
        # No display connected is not an error worth failing the boot for.
        print("oled-splash: %s" % e, file=sys.stderr)
        return 0

    def write_frame(pages):
        for data in frame_writes(args.driver, pages):
            os.write(fd, data)

    try:
        for data in transactions(args.driver, frames[0]):
            os.write(fd, data)
        animate(write_frame, frames, args.interval, args.max_seconds, lambda: stop["now"])
    except OSError as e:
        print("oled-splash: display not answering at %s: %s" % (args.address, e), file=sys.stderr)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())

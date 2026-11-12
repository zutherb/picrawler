"""Boot/shutdown splash for the OLED: the robot artwork and a state icon.

    python -m services.display.splash --export splash.bin [--state boot]
    python -m services.display.splash --png preview.png [--state shutdown]

--export writes two frames in the SSD1306 page layout (each 1024 bytes: 8
pages of 128 bytes, each byte 8 vertical pixels, top pixel in bit 0): state
icon on, state icon off; the boot script alternates them. The early
boot script (deploy/ansible/.../oled-splash.py) sends it with nothing but
the Python standard library, long before the venv on the NVMe is mounted.
"""
import argparse
import os

WIDTH, HEIGHT = 128, 64


ROBOT = os.path.join(os.path.dirname(__file__), "assets", "robot.png")
BACKGROUND = (80, 49, 153)      # purple of the original artwork


def robot_bitmap(height=62):
    """The robot from the project artwork as a 1-bit picture.

    White shell fully lit, blue parts (ears, joints) dithered, background,
    visor and the pink cheeks dark, so the ring eyes stay clean.
    """
    from PIL import Image, ImageChops

    src = Image.open(ROBOT).convert("RGB")
    # paint the pink cheeks visor-dark at full size, where they are clearly
    # pink; after shrinking they blend with the visor and touch the eyes
    pixels = src.load()
    for y in range(src.height):
        for x in range(src.width):
            r, g, b = pixels[x, y]
            if r > 150 and r - g > 50 and r - b > 30:
                pixels[x, y] = (10, 12, 40)
    small = src.resize((round(src.width * height / src.height), height), Image.LANCZOS)
    background = ImageChops.difference(small, Image.new("RGB", small.size, BACKGROUND)).convert("L")
    keep = background.point(lambda v: 255 if v > 70 else 0)
    gray = Image.composite(small.convert("L"), Image.new("L", small.size, 0), keep)
    return gray.point(lambda v: 255 if v > 170 else (v if v > 80 else 0)).convert("1")


STATES = ("boot", "shutdown")


def draw_state_icon(draw, x, y, state):
    """11x11 icon: circular arrow while booting, power symbol at shutdown."""
    if state == "shutdown":
        draw.arc((x, y + 1, x + 10, y + 11), start=-60, end=240, fill=1, width=2)   # gap at the top
        draw.line((x + 5, y, x + 5, y + 5), fill=1, width=2)
    else:
        draw.arc((x, y, x + 10, y + 10), start=0, end=290, fill=1, width=2)
        draw.polygon([(x + 7, y - 1), (x + 11, y + 2), (x + 7, y + 5)], fill=1)       # arrow head


ROBOT_H = 50      # leaves room for the state icon underneath


def draw_splash(state="boot", image=None, icon=True):
    """Robot from the artwork, centered, with the state icon below it
    (icon=False gives the "off" frame of the blinking animation)."""
    from PIL import Image, ImageDraw

    if state not in STATES:
        raise ValueError("state must be one of %s" % ", ".join(STATES))
    image = image or Image.new("1", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=0)
    robot = robot_bitmap(ROBOT_H)
    image.paste(robot, ((WIDTH - robot.width) // 2, 0))
    if icon:
        draw_state_icon(draw, (WIDTH - 11) // 2, ROBOT_H + 2, state)
    return image


def to_pages(image):
    """1-bit PIL image -> SSD1306/SH1106 page bytes (bit 0 = top pixel)."""
    pixels = image.load()
    out = bytearray()
    for page in range(image.height // 8):
        for x in range(image.width):
            byte = 0
            for bit in range(8):
                if pixels[x, page * 8 + bit]:
                    byte |= 1 << bit
            out.append(byte)
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description="Render the OLED splash")
    parser.add_argument("--state", choices=STATES, default="boot")
    parser.add_argument("--export", help="write SSD1306 page bytes to this file")
    parser.add_argument("--png", help="write a preview PNG")
    args = parser.parse_args()
    image = draw_splash(args.state)
    if args.export:
        with open(args.export, "wb") as f:
            f.write(to_pages(image) + to_pages(draw_splash(args.state, icon=False)))
    if args.png:
        image.save(args.png)
    if not (args.export or args.png):
        parser.error("give --export and/or --png")


if __name__ == "__main__":
    main()

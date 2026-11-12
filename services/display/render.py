"""Draw the status screen into a 128x64 1-bit image.

    ᛒ ▂▄▆█ 192.168.178.142    51°   (temperature inverted when hot)
    ──────────────────────────────
    BAT [█████████░░░░░░]     58%   (with battery data)
    CPU [██████░░░░░░░░░]     37%
    RAM [████░░░░░░░░░░░]     24%
    SD [████░░░] 45  NV [█░░░░░]  8 (split when the NVMe is mounted)

The layout leaves one pixel spare on each axis so the whole picture can be
shifted against OLED burn-in.
"""
from .sysinfo import wifi_bars

WIDTH, HEIGHT = 128, 64
BAR_X, BAR_W = 24, 63
HEADER_H = 11
ICON_W = 9            # threshold icon between bar and number

# Thresholds (percent / degrees C): (warn, critical). Monochrome display, so
# levels show as icons instead of colors: none = ok, outlined triangle =
# warning, triangle flashing filled/outlined = critical (never blank, so a
# critical row never looks ok).
DEFAULT_THRESHOLDS = {
    "cpu": (80, 95), "ram": (80, 92), "sd": (80, 95), "nvme": (80, 95),
    "battery": (20, 5),          # remaining charge: low values are bad
    "temp": (70, 80),
}


def level(metric, value, thresholds=None):
    """'ok', 'warn' or 'crit' for a value against its thresholds."""
    if value is None:
        return "ok"
    warn, crit = (thresholds or DEFAULT_THRESHOLDS)[metric]
    if metric == "battery":
        return "crit" if value <= crit else "warn" if value <= warn else "ok"
    return "crit" if value >= crit else "warn" if value >= warn else "ok"


def draw_level_icon(draw, x, y, lvl, blink=False):
    """Warning triangle with '!'; flashing filled/outlined when critical."""
    if lvl == "ok":
        return
    triangle = [(x + 3, y), (x, y + 7), (x + 6, y + 7)]
    filled = lvl == "crit" and not blink
    draw.polygon(triangle, outline=1, fill=1 if filled else 0)
    mark = 0 if filled else 1
    draw.line((x + 3, y + 3, x + 3, y + 4), fill=mark)
    draw.point((x + 3, y + 6), fill=mark)


def make_image():
    from PIL import Image
    return Image.new("1", (WIDTH, HEIGHT), 0)


def font():
    from PIL import ImageFont
    return ImageFont.load_default()


def draw_wifi(draw, x, y, bars):
    """Four bars, 2 px wide, growing to the right; crossed out without link."""
    for i in range(4):
        h = 3 + 2 * i
        x0, y0 = x + 3 * i, y + 9 - h
        if i < bars:
            draw.rectangle((x0, y0, x0 + 1, y + 9), fill=1)
        else:
            draw.point((x0, y + 9), fill=1)      # dotted base for empty bars
    if bars == 0:
        draw.line((x, y, x + 10, y + 9), fill=1)
        draw.line((x, y + 9, x + 10, y), fill=1)


BT_W = 9


def draw_bluetooth(draw, x, y, state):
    """Bluetooth rune, 5x9; inverted box when connected, crossed out when off."""
    if state == "connected":
        draw.rectangle((x, y - 1, x + BT_W - 1, y + 9), fill=1)
    ink = 0 if state == "connected" else 1
    cx = x + 4
    draw.line((cx, y, cx, y + 8), fill=ink)
    draw.line((cx, y, cx + 2, y + 2), fill=ink)
    draw.line((cx + 2, y + 2, cx - 2, y + 6), fill=ink)
    draw.line((cx, y + 8, cx + 2, y + 6), fill=ink)
    draw.line((cx + 2, y + 6, cx - 2, y + 2), fill=ink)
    if state in ("off", "absent"):
        draw.line((x, y + 9, x + BT_W - 1, y), fill=1)


def draw_bar(draw, fnt, x, y, label, value, height=9, lvl="ok", blink=False):
    draw.text((x, y - 1), label, font=fnt, fill=1)
    draw_level_icon(draw, x + BAR_X + BAR_W + 3, y + 1, lvl, blink)
    draw.rectangle((x + BAR_X, y, x + BAR_X + BAR_W, y + height - 1), outline=1)
    if value is None:
        text = "--"
    else:
        filled = round((BAR_W - 2) * max(0.0, min(100.0, value)) / 100)
        if filled > 0:
            draw.rectangle((x + BAR_X + 1, y + 1, x + BAR_X + filled, y + height - 2), fill=1)
        text = "%d%%" % round(value)
    width = draw.textlength(text, font=fnt)
    draw.text((x + WIDTH - 2 - width, y - 1), text, font=fnt, fill=1)


def draw_half_bar(draw, fnt, x, y, width, label, value, height=9, lvl="ok", blink=False):
    """A bar in half the screen width: label, short bar, number (no % sign,
    there is no room; the label says what it is)."""
    draw.text((x, y - 1), label, font=fnt, fill=1)
    bar_x0 = x + round(draw.textlength("NV", font=fnt)) + 3
    number_x = x + width - round(draw.textlength("100", font=fnt))
    bar_x1 = number_x - 3 - (ICON_W if lvl != "ok" else 0)
    draw_level_icon(draw, bar_x1 + 3, y + 1, lvl, blink)
    draw.rectangle((bar_x0, y, bar_x1, y + height - 1), outline=1)
    if value is None:
        text = "--"
    else:
        filled = round((bar_x1 - bar_x0 - 2) * max(0.0, min(100.0, value)) / 100)
        if filled > 0:
            draw.rectangle((bar_x0 + 1, y + 1, bar_x0 + filled, y + height - 2), fill=1)
        text = "%d" % round(value)
    draw.text((x + width - draw.textlength(text, font=fnt), y - 1), text, font=fnt, fill=1)


def battery(info):
    b = info.get("battery")
    return b if isinstance(b, dict) and b.get("present") else None


def header_text(info):
    """The IP, or a battery warning instead of it."""
    b = battery(info)
    if b and b.get("level") == "critical":
        return "BATT LOW %.1fV" % b["voltage"]
    return info.get("ip") or "no network"


def draw_temp(draw, fnt, right, y, temp, thresholds=None, blink=False):
    """Temperature as digits plus a small degree ring, right-aligned at
    `right`; inverted from the warning level, blinking when critical.
    Returns the x where it starts."""
    digits = "%d" % temp
    width = round(draw.textlength(digits, font=fnt)) + 4
    x = right - width
    lvl = level("temp", temp, thresholds)
    hot = lvl == "warn" or (lvl == "crit" and not blink)
    if hot:
        draw.rectangle((x - 2, y - 1, right, y + 9), fill=1)
    ink = 0 if hot else 1
    draw.text((x, y), digits, font=fnt, fill=ink)
    draw.ellipse((right - 3, y, right - 1, y + 2), outline=ink)
    return x - (2 if lvl != "ok" else 0)


def frame_payload(image):
    """1-bit image -> MQTT payload for the web UI's display mirror.

    PIL packs mode "1" row by row, 8 pixels per byte, leftmost pixel in the
    highest bit, 1 = lit.
    """
    import base64
    return {"w": image.width, "h": image.height, "data": base64.b64encode(image.tobytes()).decode("ascii")}


def render(info, shift=(0, 0), page=0, image=None, blink=False, thresholds=None):
    """info: dict from SysInfo.read(). Returns a 128x64 mode-"1" image.

    blink toggles every second; critical icons and a critical temperature
    disappear on blink frames.
    """
    from PIL import ImageDraw
    image = image or make_image()
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=0)
    fnt = font()
    dx, dy = shift

    bt_state = (info.get("bluetooth") or ("absent", 0))[0]
    draw_bluetooth(draw, dx, dy, bt_state)
    wifi = info.get("wifi")
    draw_wifi(draw, dx + BT_W + 2, dy, wifi_bars(wifi[1] if wifi else None))
    text_x = dx + BT_W + 15
    text = header_text(info)
    temp = info.get("temp")
    if temp is not None:
        right = dx + WIDTH - 2
        width = round(draw.textlength(text, font=fnt))
        temp_w = round(draw.textlength("%d" % temp, font=fnt)) + 6
        hot = level("temp", temp, thresholds) != "ok"
        if text_x + width + 2 <= right - temp_w or page % 2 == 1 or hot:
            # fits side by side; otherwise IP and temperature take turns
            # (a hot CPU always shows)
            limit = draw_temp(draw, fnt, right, dy, temp, thresholds, blink)
            if text_x + width + 2 > limit:
                text = None
    if text:
        draw.text((text_x, dy), text, font=fnt, fill=1)
    draw.line((dx, dy + HEADER_H + 1, dx + WIDTH - 2, dy + HEADER_H + 1), fill=1)

    b = battery(info)
    rows = [("CPU", info.get("cpu"), level("cpu", info.get("cpu"), thresholds)),
            ("RAM", info.get("ram"), level("ram", info.get("ram"), thresholds)),
            ("SD", info.get("sd"), level("sd", info.get("sd"), thresholds))]
    if b:
        # the body's level (voltage based, debounced) wins over the percentage
        bat_level = {"low": "warn", "critical": "crit"}.get(b.get("level"), level("battery", b.get("percent"), thresholds))
        rows.insert(0, ("BAT", b.get("percent"), bat_level))
    top = dy + HEADER_H + 4
    step = (HEIGHT - 1 - top) // len(rows)
    for i, (label, value, lvl) in enumerate(rows):
        y = top + i * step
        if label == "SD" and info.get("nvme") is not None:
            # storage shares the last row: SD on the left, NVMe on the right
            half = (WIDTH - 2) // 2
            draw_half_bar(draw, fnt, dx, y, half - 4, "SD", value, lvl=lvl, blink=blink)
            draw_half_bar(draw, fnt, dx + half + 4, y, half - 4, "NV", info["nvme"],
                          lvl=level("nvme", info["nvme"], thresholds), blink=blink)
        else:
            draw_bar(draw, fnt, dx, y, label, value, lvl=lvl, blink=blink)
    return image

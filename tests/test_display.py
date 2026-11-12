import os
import tempfile
import unittest

from services.display.sysinfo import bluetooth_state, is_separate_mount, parse_wireless, wifi_bars

try:
    import PIL  # noqa: F401
except ImportError:  # pragma: no cover
    PIL = None

WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   58.  -52.  -256        0      0      0      0     20        0
"""


class SysInfoTest(unittest.TestCase):

    def test_parse_wireless(self):
        self.assertEqual(parse_wireless(WIRELESS), ("wlan0", 83, -52.0))
        self.assertIsNone(parse_wireless(WIRELESS, "wlan1"))
        self.assertIsNone(parse_wireless(WIRELESS.splitlines()[0]))   # header only: no link

    def test_bars(self):
        self.assertEqual([wifi_bars(q) for q in (None, 0, 10, 25, 60, 83, 100)], [0, 0, 1, 2, 3, 4, 4])

    def test_bluetooth_state(self):
        with tempfile.TemporaryDirectory() as d:
            bt, rfkill = os.path.join(d, "bt"), os.path.join(d, "rfkill")
            self.assertEqual(bluetooth_state(bt, rfkill), ("absent", 0))
            os.makedirs(os.path.join(bt, "hci0"))
            self.assertEqual(bluetooth_state(bt, rfkill), ("on", 0))
            os.makedirs(os.path.join(bt, "hci0:11"))
            os.makedirs(os.path.join(bt, "hci0:12"))
            self.assertEqual(bluetooth_state(bt, rfkill), ("connected", 2))
            for name, kind, soft in (("rfkill0", "wlan", "1"), ("rfkill1", "bluetooth", "1")):
                os.makedirs(os.path.join(rfkill, name))
                for f, v in (("type", kind), ("soft", soft), ("hard", "0")):
                    with open(os.path.join(rfkill, name, f), "w") as fh:
                        fh.write(v + "\n")
            self.assertEqual(bluetooth_state(bt, rfkill), ("off", 0))

    def test_root_is_not_a_separate_mount(self):
        self.assertFalse(is_separate_mount("/"))
        self.assertFalse(is_separate_mount("/does/not/exist"))


@unittest.skipIf(PIL is None, "Pillow not installed")
class RenderTest(unittest.TestCase):

    INFO = {"cpu": 100, "ram": 0, "sd": 50, "nvme": None, "wifi": ("wlan0", 90, -40), "ip": "10.0.0.2", "temp": 50}

    def test_bars_fill_with_the_value(self):
        from services.display.render import BAR_X, render
        img = render(self.INFO)
        self.assertEqual((img.size, img.mode), ((128, 64), "1"))
        top = 15                                    # first bar row
        mid_y = top + 4
        self.assertTrue(img.getpixel((BAR_X + 40, mid_y)))            # CPU 100 % filled
        step = (63 - top) // 3
        self.assertFalse(img.getpixel((BAR_X + 40, mid_y + step)))       # RAM 0 % empty

    def test_nvme_row_only_when_mounted(self):
        from services.display.render import render
        three = render(self.INFO)
        four = render(dict(self.INFO, nvme=10))
        self.assertNotEqual(list(three.getdata()), list(four.getdata()))

    def test_shift_moves_the_picture(self):
        from services.display.render import render
        a, b = render(self.INFO), render(self.INFO, shift=(1, 1))
        from services.display.render import BT_W
        wifi_x = BT_W + 2
        self.assertTrue(a.getpixel((wifi_x, 9)))      # bottom of the first Wi-Fi bar
        self.assertTrue(b.getpixel((wifi_x + 1, 10)))

    def test_header_fits_longest_ip_next_to_temperature(self):
        from PIL import ImageDraw
        from services.display.render import BT_W, WIDTH, font, render
        draw = ImageDraw.Draw(render(self.INFO))
        text_x = 1 + BT_W + 15                     # worst case: shifted by one pixel
        ip_end = text_x + draw.textlength("255.255.255.255", font=font())
        temp_start = 1 + WIDTH - 2 - (draw.textlength("84", font=font()) + 4) - 2   # inverted (hot) box
        self.assertLess(ip_end + 1, temp_start)

    def test_header_text_and_battery_warning(self):
        from services.display.render import header_text
        info = dict(self.INFO, ip="192.168.178.142")
        self.assertEqual(header_text(info), "192.168.178.142")
        crit = dict(info, battery={"present": True, "voltage": 6.52, "percent": 2, "level": "critical"})
        self.assertEqual(header_text(crit), "BATT LOW 6.5V")
        self.assertEqual(header_text(dict(info, ip=None)), "no network")

    def test_battery_row_and_warnings(self):
        from services.display.render import header_text, render
        bat = {"present": True, "voltage": 7.6, "percent": 58, "level": "ok"}
        info = dict(self.INFO, nvme=8, battery=bat, ip="10.0.0.2")
        self.assertEqual(header_text(info), "10.0.0.2")
        self.assertNotEqual(list(render(info).getdata()), list(render(dict(info, battery=None)).getdata()))
        render(dict(info, battery={"present": False}))

    def test_frame_payload_decodes_like_the_browser(self):
        import base64
        from services.display.render import frame_payload, render
        img = render(self.INFO)
        frame = frame_payload(img)
        self.assertEqual((frame["w"], frame["h"]), (128, 64))
        data = base64.b64decode(frame["data"])
        self.assertEqual(len(data), 128 * 64 // 8)
        row_bytes = 16

        def lit(x, y):      # same bit order as admin.js drawOled
            return bool(data[y * row_bytes + (x >> 3)] & (0x80 >> (x & 7)))

        for x, y in ((0, 9), (40, 19), (127, 0), (64, 40)):
            self.assertEqual(lit(x, y), bool(img.getpixel((x, y))), (x, y))

    def test_levels(self):
        from services.display.render import level
        self.assertEqual([level("cpu", v) for v in (None, 50, 80, 94, 95)], ["ok", "ok", "warn", "warn", "crit"])
        self.assertEqual([level("battery", v) for v in (60, 20, 6, 5, 0)], ["ok", "warn", "warn", "crit", "crit"])
        self.assertEqual(level("ram", 85, {"ram": (90, 99)}), "ok")

    def test_level_icons(self):
        from services.display.render import BAR_W, BAR_X, ICON_W, render
        icon = (BAR_X + BAR_W + 3, BAR_X + BAR_W + 3 + ICON_W)
        cpu_row_y = 15                          # rows: CPU, RAM, SD (no battery in INFO)

        def icon_pixels(img):
            return sum(1 for x in range(*icon) for y in range(cpu_row_y, cpu_row_y + 9) if img.getpixel((x, y)))

        ok = icon_pixels(render(dict(self.INFO, cpu=10)))
        warn = icon_pixels(render(dict(self.INFO, cpu=85)))
        crit_on = icon_pixels(render(dict(self.INFO, cpu=99), blink=False))
        crit_off = icon_pixels(render(dict(self.INFO, cpu=99), blink=True))
        self.assertEqual(ok, 0)
        self.assertGreater(warn, 0)
        self.assertGreater(crit_on, warn)               # filled
        self.assertGreater(crit_off, 0)                 # never blank

    def test_missing_values(self):
        from services.display.render import render
        render({"cpu": None, "ram": None, "sd": None, "wifi": None, "ip": None, "temp": None})


if __name__ == "__main__":
    unittest.main()

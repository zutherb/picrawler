import importlib.util
import os
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "deploy", "ansible", "roles", "picrawler", "files",
                      "oled-splash.py")

try:
    import PIL  # noqa: F401
except ImportError:  # pragma: no cover
    PIL = None


def load_script():
    spec = importlib.util.spec_from_file_location("oled_splash", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootScriptTest(unittest.TestCase):

    def setUp(self):
        self.mod = load_script()
        self.pages = bytes(range(256)) * 4

    def test_ssd1306_sequence(self):
        writes = self.mod.transactions("ssd1306", self.pages)
        self.assertEqual(writes[0][0], 0x00)                     # command stream
        self.assertIn(bytes([0x8D, 0x14]), writes[0])            # charge pump on
        self.assertEqual(writes[1], bytes([0x00, 0x21, 0, 127, 0x22, 0, 7]))   # full-screen window
        data = b"".join(w[1:] for w in writes if w[0] == 0x40)
        self.assertEqual(data, self.pages)
        self.assertTrue(all(len(w) <= self.mod.CHUNK + 1 for w in writes[1:-1]))
        self.assertEqual(writes[-1], b"\x00\xAF")                # display on last

    def test_sh1106_uses_page_addressing_with_offset(self):
        writes = self.mod.transactions("sh1106", self.pages)
        page_cmds = [w for w in writes if w[0] == 0x00 and len(w) == 4]
        self.assertEqual([w[1] for w in page_cmds], [0xB0 + p for p in range(8)])
        self.assertTrue(all(w[2:] == b"\x02\x10" for w in page_cmds))
        self.assertEqual(b"".join(w[1:] for w in writes if w[0] == 0x40), self.pages)

    def test_animation_alternates_and_ends_with_the_icon_on(self):
        on, off = b"\x01" * 1024, b"\x00" * 1024
        written = []
        clock = [0.0]

        def sleep(s):
            clock[0] += s

        self.mod.animate(written.append, [on, off], 0.5, 2.2, lambda: False, sleep=sleep, clock=lambda: clock[0])
        self.assertEqual(written, [off, on, off, on, off, on])     # 2.2 s / 0.5 s, ends on frame 0
        written.clear()
        self.mod.animate(written.append, [on, off], 0.5, 60, lambda: len(written) >= 3,
                         sleep=sleep, clock=lambda: clock[0])
        self.assertEqual(written, [off, on, off, on])              # stopped on "off", icon put back

    def test_single_frame_is_shown_once(self):
        written = []
        self.mod.animate(written.append, [b"x" * 1024], 0.5, 60, lambda: False, sleep=lambda s: None)
        self.assertEqual(written, [])

    def test_split_frames(self):
        self.assertEqual(len(self.mod.split_frames(b"a" * 2048)), 2)
        with self.assertRaises(ValueError):
            self.mod.split_frames(b"a" * 1500)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            self.mod.transactions("ssd1306", b"short")
        with self.assertRaises(ValueError):
            self.mod.transactions("st7789", self.pages)

    def test_no_display_does_not_fail_the_boot(self):
        import tempfile
        from unittest import mock
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(self.pages)
        try:
            with mock.patch.object(self.mod, "open_bus", side_effect=FileNotFoundError("/dev/i2c-1")), \
                    mock.patch("sys.argv", ["oled-splash.py", f.name]):
                self.assertEqual(self.mod.main(), 0)
        finally:
            os.unlink(f.name)


@unittest.skipIf(PIL is None, "Pillow not installed")
class SplashArtTest(unittest.TestCase):

    def test_page_layout(self):
        from PIL import Image
        from services.display.splash import to_pages
        img = Image.new("1", (128, 64), 0)
        img.putpixel((5, 0), 1)       # page 0, bit 0
        img.putpixel((5, 7), 1)       # page 0, bit 7
        img.putpixel((127, 63), 1)    # page 7, last column, bit 7
        pages = to_pages(img)
        self.assertEqual(len(pages), 1024)
        self.assertEqual(pages[5], 0b10000001)
        self.assertEqual(pages[7 * 128 + 127], 0b10000000)
        self.assertEqual(sum(1 for b in pages if b), 2)

    def test_splash_states(self):
        from services.display.splash import draw_splash
        boot, down = draw_splash("boot"), draw_splash("shutdown")
        self.assertGreater(sum(1 for p in boot.getdata() if p), 500)
        below = lambda img: [img.getpixel((x, y)) for x in range(52, 76) for y in range(51, 64)]
        self.assertNotEqual(below(boot), below(down))              # different state icon under the robot
        robot = lambda img: [img.getpixel((x, y)) for x in range(30, 98) for y in range(0, 50)]
        self.assertEqual(robot(boot), robot(down))                 # same robot
        off = draw_splash("boot", icon=False)
        self.assertNotEqual(below(boot), below(off))               # blink frame without the icon
        self.assertEqual(robot(boot), robot(off))
        with self.assertRaises(ValueError):
            draw_splash("dancing")

    def test_export_has_two_frames(self):
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "s.bin")
            subprocess.run([sys.executable, "-m", "services.display.splash", "--state", "shutdown", "--export", out],
                           check=True, cwd=os.path.join(os.path.dirname(__file__), ".."))
            data = open(out, "rb").read()
        self.assertEqual(len(data), 2048)
        self.assertNotEqual(data[:1024], data[1024:])

if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "deploy", "ansible", "roles", "network", "files", "netwatch.py")


def load():
    spec = importlib.util.spec_from_file_location("netwatch", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WatchTest(unittest.TestCase):

    def setUp(self):
        self.mod = load()
        self.w = self.mod.Watch(boot_grace_s=45, lost_s=20, idle_s=120, retry_s=600, probe_s=40, start=0)

    def test_known_network_needs_nothing(self):
        for t in range(0, 1000, 5):
            self.assertIsNone(self.w.decide("client", 0, t))

    def test_hotspot_after_boot_grace(self):
        self.assertIsNone(self.w.decide("none", 0, 10))
        self.assertIsNone(self.w.decide("none", 0, 44))
        self.assertEqual(self.w.decide("none", 0, 46), "hotspot_up")

    def test_lost_network_later_uses_shorter_wait(self):
        self.w.decide("client", 0, 100)
        self.assertIsNone(self.w.decide("none", 0, 300))
        self.assertIsNone(self.w.decide("connecting", 0, 310))
        self.assertEqual(self.w.decide("none", 0, 321), "hotspot_up")

    def test_idle_hotspot_probes_then_comes_back(self):
        self.w.decide("hotspot", 0, 100)
        self.assertIsNone(self.w.decide("hotspot", 0, 699))
        self.assertEqual(self.w.decide("hotspot", 0, 700), "probe")
        self.assertIsNone(self.w.decide("none", 0, 720))          # NetworkManager is looking
        self.assertEqual(self.w.decide("none", 0, 741), "hotspot_up")

    def test_probe_finds_home_network(self):
        self.w.decide("hotspot", 0, 100)
        self.w.decide("hotspot", 0, 700)
        self.assertIsNone(self.w.decide("client", 0, 715))
        self.assertIsNone(self.w.decide("client", 0, 2000))

    def test_connected_phone_keeps_the_hotspot(self):
        self.w.decide("hotspot", 1, 100)
        for t in range(100, 3000, 5):
            self.assertIsNone(self.w.decide("hotspot", 1, t))
        # phone leaves: probe only after idle_s without stations
        self.assertIsNone(self.w.decide("hotspot", 0, 3100))
        self.assertEqual(self.w.decide("hotspot", 0, 3120), "probe")

    def test_mode_and_nmcli_parsing(self):
        self.assertEqual(self.mod.mode_of("connected", "picrawler-hotspot", "picrawler-hotspot"), "hotspot")
        self.assertEqual(self.mod.mode_of("connected", "Home", "picrawler-hotspot"), "client")
        self.assertEqual(self.mod.mode_of("disconnected", None, "picrawler-hotspot"), "none")
        out = "eth0:ethernet:unavailable:\nwlan0:wifi:connected:My:Home\nlo:loopback:connected (externally):lo\n"
        with mock.patch.object(self.mod, "run", return_value=(0, out)):
            self.assertEqual(self.mod.wifi_state("wlan0"), ("connected", "My:Home"))
        with mock.patch.object(self.mod, "run", return_value=(0, "wlan0:wifi:disconnected:\n")):
            self.assertEqual(self.mod.wifi_state("wlan0"), ("disconnected", None))
        dump = "Station aa:bb:cc:dd:ee:01 (on wlan0)\n\tinactive time: 10 ms\nStation aa:bb:cc:dd:ee:02 (on wlan0)\n"
        with mock.patch.object(self.mod, "run", return_value=(0, dump)):
            self.assertEqual(self.mod.station_count("wlan0"), 2)

    def test_status_file_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "network.json")
            self.mod.write_status(path, {"mode": "hotspot", "ip": "10.42.0.1"})
            self.assertEqual(json.load(open(path))["mode"], "hotspot")
            self.assertFalse(os.path.exists(path + ".tmp"))


class DisplayNetworkTest(unittest.TestCase):

    def test_network_status_reader(self):
        from services.display.sysinfo import network_status
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.json")
            self.assertIsNone(network_status(path))
            with open(path, "w") as f:
                f.write("{broken")
            self.assertIsNone(network_status(path))
            with open(path, "w") as f:
                json.dump({"mode": "hotspot", "ip": "10.42.0.1"}, f)
            self.assertEqual(network_status(path)["ip"], "10.42.0.1")

    def test_hotspot_icon_replaces_wifi_bars(self):
        try:
            from services.display.render import render
        except ImportError:  # pragma: no cover
            self.skipTest("Pillow not installed")
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow not installed")
        base = {"cpu": 10, "ram": 10, "sd": 10, "wifi": None, "ip": "10.42.0.1", "temp": 40}
        hotspot = render(dict(base, network={"mode": "hotspot"}))
        client = render(dict(base, network={"mode": "client"}))
        icon = lambda img: [img.getpixel((x, y)) for x in range(10, 22) for y in range(0, 11)]
        self.assertNotEqual(icon(hotspot), icon(client))


if __name__ == "__main__":
    unittest.main()

"""Web service parts that need no web server: auth, store, state, Bluetooth/system parsing."""
import asyncio
import unittest

from services.web import auth, bluetooth, demo, hardware, system
from services.web.sightings import SightingTracker
from services.web.state import LiveState
from services.web.store import LogStore


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class AuthTest(unittest.TestCase):

    def test_hash_and_verify(self):
        stored = auth.hash_password("correct horse")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(auth.verify_password("correct horse", stored))
        self.assertFalse(auth.verify_password("wrong", stored))
        self.assertFalse(auth.verify_password("x", "garbage"))
        self.assertNotEqual(stored, auth.hash_password("correct horse"))   # salted

    def test_users(self):
        users = auth.Users({"anna": {"role": "admin", "password_hash": auth.hash_password("pw-anna-123")}})
        self.assertEqual(users.check("anna", "pw-anna-123"), "admin")
        self.assertEqual(users.check(" Anna ", "pw-anna-123"), "admin")
        self.assertIsNone(users.check("anna", "PW-anna-123"))
        self.assertIsNone(users.check("anna", "nope"))
        self.assertIsNone(users.check("bob", "pw-anna-123"))
        with self.assertRaises(ValueError):
            auth.Users({"x": {"role": "root", "password_hash": auth.hash_password("a")}})
        with self.assertRaises(ValueError):
            auth.Users({"x": {"role": "viewer", "password_hash": "plaintext"}})

    def test_sessions_expire_when_idle(self):
        clock = Clock(0.0)
        sessions = auth.Sessions(idle_s=100, clock=clock)
        token = sessions.create("anna", "viewer")
        clock.t = 99
        self.assertEqual(sessions.get(token)["user"], "anna")
        clock.t = 198
        self.assertIsNotNone(sessions.get(token))     # touched at 99
        clock.t = 299
        self.assertIsNone(sessions.get(token))
        self.assertIsNone(sessions.get(None))

    def test_rate_limiter(self):
        clock = Clock(0.0)
        limiter = auth.RateLimiter(limit=2, window_s=60, clock=clock)
        self.assertEqual([limiter.allow("ip"), limiter.allow("ip"), limiter.allow("ip")], [True, True, False])
        self.assertTrue(limiter.allow("other"))
        clock.t = 61
        self.assertTrue(limiter.allow("ip"))


class StoreAndStateTest(unittest.TestCase):

    def setUp(self):
        self.clock = Clock(1000.0)
        self.store = LogStore(":memory:", retention_days=1, max_entries=5, clock=self.clock)
        self.addCleanup(self.store.close)
        self.pushed = []
        self.state = LiveState(self.store, SightingTracker(), lambda e, d: self.pushed.append((e, d)), wall=self.clock)

    def detections(self, *labels):
        self.state.on_message("sensor/vision/detections",
                              {"objects": [{"label": l, "score": 0.9} for l in labels]})

    def test_detections_become_sightings_in_store(self):
        for _ in range(3):
            self.detections("person")
            self.clock.t += 0.1
        rows = self.store.query()
        self.assertEqual((rows[0]["label"], rows[0]["end"]), ("person", None))
        self.clock.t += 3
        self.state.tick()
        row = self.store.query()[0]
        self.assertAlmostEqual(row["end"], 1000.2)
        self.assertEqual([e for e, _ in self.pushed].count("log"), 2)
        self.assertEqual([e for e, _ in self.pushed].count("detections"), 3)

    def test_vision_offline_closes_sightings(self):
        for _ in range(3):
            self.detections("cat")
        self.state.on_message("sensor/vision/status", {"status": "offline"})
        self.assertIsNotNone(self.store.query()[0]["end"])

    def test_events_are_logged(self):
        self.state.on_message("event/stop", {"reason": "obstacle", "sit": False, "ts": 1001.0})
        self.state.on_message("event/rejected", {"topic": "cmd/action", "reason": "no heartbeat", "ts": 1002.0})
        self.state.on_message("event/stop", {"reason": "old"}, retain=True)
        rows = self.store.query(kind="event")
        self.assertEqual([(r["label"], r["detail"]) for r in rows],
                         [("rejected", "cmd/action: no heartbeat"), ("stop", "obstacle")])

    def test_agent_replies_are_logged_room_talk_is_not(self):
        self.state.on_message("sensor/speech/text", {"text": "private chat in the room"})
        self.state.on_message("agent/reply", {"text": "Found cat.", "request": "such die Katze", "brain": "litert-lm",
                                              "seconds": 6.2, "ts": 1001.0})
        rows = self.store.query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["detail"], "such die Katze → Found cat. [litert-lm, 6.2 s]")
        self.assertIn("agent", [e for e, _ in self.pushed])

    def test_display_frame_kept_and_pushed(self):
        frame = {"w": 128, "h": 64, "data": "AAAA", "ts": 1.0}
        self.state.on_message("display/frame", frame, retain=True)
        self.assertEqual(self.state.display_frame, frame)
        self.assertIn(("display", frame), self.pushed)
        self.assertNotIn("display", self.state.snapshot)      # kept out of the status snapshot

    def test_local_map_kept_and_pushed(self):
        scan = {"status": "done", "samples": [], "grid": [], "ts": 1.0}
        self.state.on_message("map/local", scan, retain=True)
        self.assertEqual(self.state.local_map, scan)
        self.assertIn(("map", scan), self.pushed)

    def test_status_snapshot_pushed_once_per_tick(self):
        self.state.on_message("status", {"status": "online"})
        self.state.on_message("sensor/gamepad/status", {"status": "online", "connected": True})
        self.state.on_message("sensor/ultrasonic/distance", {"cm": 12.5})
        self.state.tick()
        self.state.tick()
        statuses = [d for e, d in self.pushed if e == "status"]
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0]["sensors"]["gamepad"]["connected"], True)
        self.assertEqual(statuses[0]["distance_cm"], 12.5)

    def test_query_filters_csv_and_prune(self):
        for i in range(7):
            self.store.add_event("stop", "n%d" % i, 1000 + i)
        self.assertEqual(len(self.store.query(since=1004)), 3)
        self.assertEqual(len(self.store.query(label="nothing")), 0)
        self.assertIn("label", self.store.to_csv(self.store.query()).splitlines()[0])
        self.store.add_event("stop", "ancient", 1000 - 2 * 86400)
        self.store.prune()
        rows = self.store.query()
        self.assertEqual(len(rows), 5)
        self.assertNotIn("ancient", [r["detail"] for r in rows])


class BluetoothParsingTest(unittest.TestCase):

    def test_parse_devices_and_info(self):
        out = "\x1b[0;94m[bluetooth]\x1b[0m# Device E4:17:D8:00:11:22 SteelSeries Nimbus+\nDevice 11:22:33:44:55:66\nnoise\n"
        self.assertEqual(bluetooth.parse_devices(out), [
            {"mac": "E4:17:D8:00:11:22", "name": "SteelSeries Nimbus+"},
            {"mac": "11:22:33:44:55:66", "name": "11:22:33:44:55:66"}])
        info = bluetooth.parse_info("\tName: Pad\n\tIcon: input-gaming\n\tPaired: yes\n\tConnected: no\n"
                                    "\tBattery Percentage: 0x50 (80)\n")
        self.assertEqual(info, {"name": "Pad", "icon": "input-gaming", "paired": True, "connected": False, "battery": 80})

    def test_mac_validation_blocks_injection(self):
        self.assertEqual(bluetooth.check_mac("e4:17:d8:00:11:22"), "E4:17:D8:00:11:22")
        for bad in ("E4:17:D8:00:11", "E4:17:D8:00:11:22\nremove", "; reboot", None):
            with self.assertRaises(bluetooth.BluetoothError):
                bluetooth.check_mac(bad)

    def test_gamepad_detection(self):
        self.assertTrue(bluetooth.is_gamepad({"icon": "input-gaming", "name": "x"}))
        self.assertTrue(bluetooth.is_gamepad({"name": "SteelSeries Nimbus+"}))
        self.assertFalse(bluetooth.is_gamepad({"icon": "audio-card", "name": "Speaker"}))

    def test_fake_backend_flow(self):
        async def flow():
            bt = bluetooth.FakeBluetooth(delay=0)
            await bt.scan()
            new = [d for d in await bt.devices() if not d["paired"] and d["gamepad"]][0]
            result = await bt.pair(new["mac"])
            devices = {d["mac"]: d for d in await bt.devices()}
            return result, devices[new["mac"]]
        result, device = asyncio.run(flow())
        self.assertTrue(result["ok"])
        self.assertTrue(device["connected"] and device["trusted"])


class SystemTest(unittest.TestCase):

    def test_allow_list(self):
        system.System(["picrawler-body.service", "picrawler-sensor@gamepad.service"])
        for bad in ("ssh.service", "picrawler-body.service; reboot", "../picrawler-x.service"):
            with self.assertRaises(ValueError):
                system.System([bad])

    def test_unit_outside_list_rejected(self):
        s = system.System(["picrawler-body.service"])
        with self.assertRaises(system.SystemActionError):
            asyncio.run(s.restart("picrawler-web.service"))

    def test_parsers(self):
        self.assertEqual(system.parse_show("ActiveState=active\nSubState=running\n"),
                         {"ActiveState": "active", "SubState": "running"})
        flags = system.parse_throttled("throttled=0x50005")
        self.assertTrue(flags["under_voltage"] and flags["throttled"] and flags["under_voltage_since_boot"])
        self.assertIsNone(system.parse_throttled("error"))


if __name__ == "__main__":
    unittest.main()


class HardwareTest(unittest.TestCase):

    LOCAL = {"i2c_bus": True, "tpu_pci": 1, "tpu_devices": ["apex_0"], "cameras": ["ov5647"],
             "bluetooth": ["hci0"], "audio": {"playback": ["Headphones"], "capture": ["USB Mic"]}}

    def facts(self, **extra):
        now = 1000.0
        base = {
            "status": {"status": "online"},
            "state/battery": {"present": True, "voltage": 7.6, "percent": 60, "level": "ok", "ts": now - 5},
            "sensor/ultrasonic/status": {"status": "online"},
            "sensor/ultrasonic/distance": {"cm": 42.0, "ts": now - 1},
            "sensor/vision/status": {"status": "online", "tpu": True, "fps": 15.0},
            "sensor/gamepad/status": {"status": "online", "connected": True, "name": "Nimbus+"},
            "sensor/speech/status": {"status": "online"},
            "voice/status": {"status": "online"},
            "display/status": {"status": "online"},
            "display/frame": {"ts": now - 2},
        }
        base.update(extra)
        return {k: (v, now) for k, v in base.items() if v is not None}

    def status(self, local, facts):
        return {c["name"]: c["status"] for c in hardware.assess(local, facts, 1000.0)}

    def test_everything_fine(self):
        result = self.status(self.LOCAL, self.facts())
        self.assertEqual(set(result.values()), {"ok"}, result)
        self.assertEqual(len(result), 10)

    def test_problems(self):
        local = dict(self.LOCAL, cameras=[], tpu_devices=[], audio={"playback": [], "capture": ["USB Mic"]})
        facts = self.facts(**{"state/battery": {"present": True, "voltage": 6.9, "level": "low", "ts": 999.0},
                              "sensor/ultrasonic/distance": {"cm": None, "ts": 999.0},
                              "sensor/gamepad/status": {"status": "online", "connected": False},
                              "display/frame": {"ts": 100.0}})
        result = self.status(local, facts)
        self.assertEqual(result["Camera"], "bad")
        self.assertEqual(result["Coral Edge TPU"], "bad")                # on PCIe, no driver node
        self.assertEqual(result["Speaker"], "bad")
        self.assertEqual(result["Battery"], "warn")
        self.assertEqual(result["Ultrasonic"], "warn")                   # no echo
        self.assertEqual(result["Gamepad"], "warn")
        self.assertEqual(result["OLED display"], "warn")                 # stale picture
        self.assertEqual(result["Microphone"], "ok")

    def test_services_offline(self):
        facts = self.facts(status={"status": "offline"}, **{"sensor/vision/status": {"status": "offline"}})
        result = self.status(self.LOCAL, facts)
        self.assertEqual(result["Robot HAT & servos"], "bad")
        self.assertEqual(result["Camera"], "warn")
        self.assertEqual(result["Coral Edge TPU"], "warn")
        nothing = self.status({}, {})
        self.assertEqual(nothing["Battery"], "unknown")
        self.assertEqual(nothing["Gamepad"], "unknown")

    def test_parsers(self):
        self.assertEqual(hardware.parse_cameras("Available cameras\n-----------------\n"
                                                "0 : ov5647 [2592x1944 10-bit GBRG] (/base/axi/pcie@120000/rp1/i2c@88000/ov5647@36)\n"
                                                "    Modes: 'SGBRG10_CSI2P' : 640x480 [58.92 fps - (16, 0)/2560x1920 crop]\n"),
                         ["ov5647"])
        self.assertEqual(hardware.parse_cameras("No cameras available!\n"), [])
        pcm = ("00-00: bcm2835 Headphones : bcm2835 Headphones : playback 8\n"
               "01-00: USB Audio : USB Audio : playback 1 : capture 1\n")
        self.assertEqual(hardware.parse_pcm(pcm), {"playback": ["bcm2835 Headphones", "USB Audio"],
                                                    "capture": ["USB Audio"]})

    def test_probe_local_reads_sysfs_and_dev(self):
        import os
        import tempfile
        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root)

        def touch(path, text=""):
            full = os.path.join(root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(text)

        touch("dev/i2c-1")
        touch("dev/apex_0")
        touch("sys/bus/pci/devices/0000:03:00.0/vendor", "0x1ac1\n")
        touch("sys/bus/pci/devices/0000:01:00.0/vendor", "0x144d\n")
        os.makedirs(os.path.join(root, "sys/class/bluetooth/hci0"))
        touch("proc/asound/pcm", "00-00: USB Audio : USB Audio : capture 1\n")

        async def run(*args, timeout=10):
            raise FileNotFoundError(args[0])

        local = asyncio.run(hardware.probe_local(run, root=root))
        self.assertEqual(local, {"i2c_bus": True, "tpu_pci": 1, "tpu_devices": ["apex_0"], "cameras": None,
                                 "bluetooth": ["hci0"], "audio": {"playback": [], "capture": ["USB Audio"]}})


class DemoPlayerTest(unittest.TestCase):

    def test_replays_frames_and_messages_in_a_loop(self):
        import json
        import os
        import tempfile
        import zipfile
        path = os.path.join(tempfile.mkdtemp(), "demo.zip")
        self.addCleanup(os.remove, path)
        events = [{"t": i / 10, "frame": i, "detections": [{"label": "chair", "score": 0.9, "box": [0, 0, 1, 1]}],
                   "ultrasonic_cm": 40.0 + i, "motion": {"status": "running"},
                   "map": {"status": "live", "message": "m", "grid": ["?"]}} for i in range(2)]
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("meta.json", json.dumps({"fps": 100, "frames": 2}))
            z.writestr("events.jsonl", "\n".join(json.dumps(e) for e in events))
            for i in range(2):
                z.writestr("frames/%05d.jpg" % i, b"jpeg%d" % i)
        player = demo.DemoPlayer(path)
        sent = []

        async def play():
            task = asyncio.ensure_future(player.run(lambda suffix, payload, retain=False: sent.append((suffix, payload))))
            await asyncio.sleep(0.045)
            task.cancel()

        asyncio.run(play())
        suffixes = [s for s, _ in sent]
        self.assertGreaterEqual(suffixes.count("map/local"), 3)                 # looped past the 2 frames
        self.assertIn("sensor/vision/detections", suffixes)
        maps = [p for s, p in sent if s == "map/local"]
        self.assertTrue(maps[2]["message"].startswith("Replay (loop 2)"))
        self.assertIn(player.frame(), (b"jpeg0", b"jpeg1"))


class MetricsTest(unittest.TestCase):

    def test_meminfo(self):
        from services.common.metrics import parse_meminfo
        m = parse_meminfo("MemTotal:  8000000 kB\nMemFree:  1000 kB\nMemAvailable:  6000000 kB\n")
        self.assertEqual((m["total"], m["used"], m["percent"]), (8000000 * 1024, 2000000 * 1024, 25.0))
        self.assertIsNone(parse_meminfo("garbage"))

    def test_cpu_sampler(self):
        import os
        import tempfile
        from services.common.metrics import CpuSampler
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3\n")
        try:
            sampler = CpuSampler(f.name)
            self.assertIsNone(sampler.sample())               # first call: no delta yet
            with open(f.name, "w") as g:
                g.write("cpu  150 0 150 900 0 0 0 0 0 0\n")    # +100 busy, +100 idle
            self.assertEqual(sampler.sample(), 50.0)
        finally:
            os.unlink(f.name)


class MonitorTest(unittest.TestCase):

    def test_records_stats_and_filters(self):
        from services.web.monitor import MqttMonitor
        pushed = []
        m = MqttMonitor(pushed.append)
        m.record("sensor/ultrasonic/distance", b'{"cm": 12}', False, 0, 1.0)
        m.record("sensor/ultrasonic/status", b'{"status": "online"}', True, 1, 2.0)
        m.record("cmd/stop", b"not json", False, 1, 3.0)
        m.record("sensor/vision/raw", b"\xff\xfe", False, 0, 4.0)
        self.assertEqual([t["topic"] for t in m.topic_list()],
                         ["cmd/stop", "sensor/ultrasonic/distance", "sensor/ultrasonic/status", "sensor/vision/raw"])
        self.assertEqual(len(pushed), 4)
        self.assertEqual([x["topic"] for x in m.messages("sensor/ultrasonic")],
                         ["sensor/ultrasonic/status", "sensor/ultrasonic/distance"])
        self.assertEqual(len(m.messages("sensor/ultrasonic/status", subtree=False)), 1)
        self.assertEqual(m.messages("sensor/ultra"), [])            # no partial name matches
        stop = m.messages("cmd/stop")[0]
        self.assertEqual((stop["json"], stop["payload"], stop["qos"]), (False, "not json", 1))
        self.assertTrue(m.messages("sensor/ultrasonic/status")[0]["retain"])
        self.assertIn("binary", m.messages("sensor/vision/raw")[0]["payload"])
        m.clear()
        self.assertEqual((m.topic_list(), m.messages()), ([], []))

    def test_truncates_large_payloads(self):
        from services.web.monitor import MAX_PAYLOAD, MqttMonitor
        m = MqttMonitor()
        msg = m.record("state/legs", b"x" * (MAX_PAYLOAD + 100), False, 0, 1.0)
        self.assertLess(len(msg["payload"]), MAX_PAYLOAD + 50)
        self.assertEqual(msg["size"], MAX_PAYLOAD + 100)

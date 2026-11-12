import threading
import unittest

from services.body.battery import BatteryMonitor, adc_to_volts, percent


class BatteryMonitorTest(unittest.TestCase):

    def test_conversion_and_percent(self):
        self.assertAlmostEqual(adc_to_volts(4095), 9.9)
        self.assertAlmostEqual(adc_to_volts(3102), 7.5, places=2)
        self.assertEqual([percent(v) for v in (8.5, 8.4, 7.55, 7.0, 6.4, 5.0)], [100, 100, 50, 8, 0, 0])
        self.assertTrue(all(percent(a) >= percent(b) for a, b in zip((8.2, 7.8, 7.4, 7.0), (7.8, 7.4, 7.0, 6.6))))

    def test_level_needs_to_hold(self):
        m = BatteryMonitor(low_v=7.0, critical_v=6.6, hold_s=20, window=1)
        self.assertIsNone(m.feed(6.9, 0))           # low candidate
        self.assertIsNone(m.feed(6.9, 10))
        self.assertEqual(m.feed(6.9, 21), "low")
        self.assertIsNone(m.feed(7.05, 30))         # hysteresis: still low just above 7.0
        self.assertEqual(m.level, "low")
        m.feed(7.3, 40)
        self.assertEqual(m.feed(7.3, 61), "ok")

    def test_short_sag_while_walking_is_ignored(self):
        m = BatteryMonitor(hold_s=20, window=5)
        for t, v in enumerate([7.6, 7.6, 6.4, 6.4, 7.6, 7.6, 7.6]):
            self.assertIsNone(m.feed(v, t * 5))
        self.assertEqual(m.state()["level"], "ok")

    def test_no_battery(self):
        m = BatteryMonitor()
        self.assertIsNone(m.feed(0.2, 0))
        self.assertEqual(m.state(), {"present": False})


class BodyBatteryTest(unittest.TestCase):

    def setUp(self):
        from tests.test_body_adapter import Clock, FakeExecutor
        from services.body.adapter import BodyAdapter
        self.ex = FakeExecutor()
        self.sent = []
        self.mono = Clock(0.0)
        self.body = BodyAdapter(self.ex, lambda *m: self.sent.append(m),
                                {"battery": {"low_v": 7.0, "critical_v": 6.6, "hold_s": 10}},
                                wall=Clock(1_762_900_000.0), mono=self.mono)
        self.body.battery._readings = __import__("collections").deque(maxlen=1)

    def test_publishes_state_and_sits_when_critical(self):
        self.body.on_battery(7.6)
        topic, payload, qos, retain = self.sent[-1]
        self.assertEqual((topic, payload["percent"], payload["level"], retain), ("state/battery", 55, "ok", True))
        self.body.on_battery(6.5)
        self.mono.t = 11
        self.body.on_battery(6.5)
        self.assertEqual(self.ex.stops[-1], (2, True))         # SAFETY, sit down
        self.assertIn("event/battery", [m[0] for m in self.sent])

    def test_motion_refused_while_critical(self):
        self.body.on_battery(6.5)
        self.mono.t = 11
        self.body.on_battery(6.5)
        self.body.on_message("cmd/heartbeat", {})
        self.body.on_message("cmd/action", {"id": "a", "name": "forward", "ts": 1_762_900_000.0, "priority": "reflex"})
        self.assertEqual(self.ex.submitted, [])
        reasons = [m[1]["reason"] for m in self.sent if m[0] == "event/rejected"]
        self.assertEqual(reasons, ["battery critical, charge first"])
        self.body.on_message("cmd/stop", {})                    # stop still works
        self.assertEqual(len(self.ex.stops), 2)


class ExecutorIoLockTest(unittest.TestCase):

    def test_frames_hold_the_io_lock(self):
        from unittest import mock
        from tests import fake_robot_hat
        fake_robot_hat.install()
        from picrawler import Picrawler
        from picrawler.executor import Command, MotionExecutor
        with mock.patch("time.sleep"):
            crawler = Picrawler()
        lock = threading.Lock()
        held = []
        original = crawler.do_step

        def do_step(frame, speed=50):
            held.append(lock.locked())
            return original(frame, speed)

        crawler.do_step = do_step
        ex = MotionExecutor(crawler, io_lock=lock)
        ex.start()
        ex.submit(Command("frame", frame=[[50, 50, -50]] * 4))
        self.assertTrue(ex.wait_idle(2))
        ex.shutdown(sit=False)
        self.assertEqual(held, [True])


if __name__ == "__main__":
    unittest.main()

"""Sensor -> arbiter -> body -> executor -> servos, with an in-memory broker."""
import threading
import time
import unittest
from unittest import mock

from tests import fake_robot_hat

fake_robot_hat.install()

from picrawler import Picrawler  # noqa: E402
from picrawler.executor import MotionExecutor  # noqa: E402
from services.arbiter.arbiter import Arbiter  # noqa: E402
from services.body.adapter import BodyAdapter  # noqa: E402

BODY_TOPICS = ("cmd/",)
ARBITER_TOPICS = ("sensor/", "state/motion", "status", "event/rejected")


class Broker:
    """Delivers synchronously, like a broker with zero latency."""

    def __init__(self):
        self.subscribers = []
        self.retained = {}
        self.log = []
        self.lock = threading.RLock()

    def subscribe(self, prefixes, handler):
        self.subscribers.append((prefixes, handler))

    def publish(self, suffix, payload, qos=0, retain=False):
        with self.lock:
            self.log.append((suffix, payload))
            if retain:
                self.retained[suffix] = payload
            for prefixes, handler in self.subscribers:
                if any(suffix == p or suffix.startswith(p) for p in prefixes if p.endswith("/")) \
                        or suffix in prefixes:
                    handler(suffix, payload, False)


class SlowCrawler(Picrawler):
    """Each frame takes a little time, like real servos."""
    frame_s = 0.02

    def do_step(self, _step, speed=50, israise=False):
        time.sleep(self.frame_s)
        return super().do_step(_step, speed, israise)


class IntegrationTest(unittest.TestCase):

    def setUp(self):
        with mock.patch("time.sleep"):
            self.crawler = SlowCrawler()
        self.broker = Broker()
        self.body = None
        self.executor = MotionExecutor(self.crawler, on_state=lambda s: self.body and self.body.on_state(s),
                                       lockout_s=1.0)
        self.body = BodyAdapter(self.executor, self.broker.publish)
        self.arbiter = Arbiter(self.broker.publish, {
            "rule": [{"name": "obstacle_stop", "when": "sensor/ultrasonic/distance",
                      "condition": "cm < 15", "then": {"kind": "stop", "reason": "obstacle"},
                      "priority": "safety"}],
        })
        self.broker.subscribe(BODY_TOPICS, self.body.on_message)
        self.broker.subscribe(ARBITER_TOPICS, self.arbiter.on_message)
        self.executor.start()
        self.body.on_connect()

    def tearDown(self):
        self.executor.shutdown(sit=False, timeout=2)

    def distance(self, cm):
        self.broker.publish("sensor/ultrasonic/distance", {"cm": cm, "ts": time.time()})

    def test_obstacle_stops_walking_within_a_frame(self):
        self.arbiter.tick()   # heartbeat, so the body accepts goal commands
        self.arbiter.send_action("forward", steps=10, speed=70)
        while len(self.crawler.moves) < 3:
            time.sleep(0.005)

        for _ in range(3):
            self.distance(5)
        stopped_at = len(self.crawler.moves)
        self.assertTrue(self.executor.wait_idle(1))

        self.assertLessEqual(len(self.crawler.moves) - stopped_at, 1)
        self.assertLess(len(self.crawler.moves), 30)
        self.assertEqual(self.broker.retained["state/motion"]["status"], "idle")
        self.assertIn("obstacle", [p.get("reason") for s, p in self.broker.log if s == "cmd/stop"])

        # forward is refused right after the stop (lockout) ...
        self.arbiter.tick()
        self.arbiter.send_action("forward", steps=1)
        rejected = [p["reason"] for s, p in self.broker.log if s == "event/rejected"]
        self.assertEqual(rejected, ["busy or locked out by a higher priority"])

    def test_body_ignores_goal_without_arbiter_heartbeat(self):
        self.arbiter.send_action("forward")
        self.assertTrue(self.executor.wait_idle(1))
        self.assertEqual(self.crawler.moves, [])


if __name__ == "__main__":
    unittest.main()

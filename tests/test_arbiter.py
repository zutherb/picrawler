import unittest

from services.arbiter.arbiter import Arbiter, parse_condition

NOW = 1_762_900_000.0

OBSTACLE_RULE = {
    "name": "obstacle_stop",
    "when": "sensor/ultrasonic/distance",
    "condition": "cm < 15",
    "then": {"kind": "stop", "reason": "obstacle"},
    "priority": "safety",
    "cooldown_s": 1.0,
}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class ArbiterTestBase(unittest.TestCase):

    def make(self, cfg):
        self.sent = []
        self.mono = Clock(100.0)
        self.arbiter = Arbiter(lambda *m: self.sent.append(m), cfg, wall=Clock(NOW), mono=self.mono)

    def distance(self, cm):
        self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": cm, "ts": NOW})

    def topics(self, name):
        return [m for m in self.sent if m[0] == name]


class RuleTest(ArbiterTestBase):

    def setUp(self):
        self.make({"rule": [OBSTACLE_RULE]})

    def test_parse_condition(self):
        field, compare, value = parse_condition("cm <= 12.5")
        self.assertEqual((field, value), ("cm", 12.5))
        self.assertTrue(compare(12.5, value))
        for bad in ("cm < 15; import os", "__import__('os')", "cm < x", "", None):
            with self.assertRaises(ValueError):
                parse_condition(bad)

    def test_stop_after_median_of_three(self):
        self.distance(10)
        self.distance(10)
        self.assertEqual(self.topics("cmd/stop"), [])
        self.distance(10)
        stop = self.topics("cmd/stop")
        self.assertEqual(len(stop), 1)
        self.assertEqual(stop[0][1]["reason"], "obstacle")
        self.assertEqual(stop[0][1]["sit"], False)
        self.assertEqual(stop[0][2:], (1, False))

    def test_single_outlier_does_not_stop(self):
        for cm in (50, 5, 50, 50, 4, 50):
            self.distance(cm)
        self.assertEqual(self.topics("cmd/stop"), [])

    def test_cooldown(self):
        for _ in range(5):
            self.distance(5)
        self.assertEqual(len(self.topics("cmd/stop")), 1)
        self.mono.t += 1.1
        self.distance(5)
        self.assertEqual(len(self.topics("cmd/stop")), 2)

    def test_offline_sensor_resets_window(self):
        self.distance(5)
        self.distance(5)
        self.arbiter.on_message("sensor/ultrasonic/status", {"status": "offline"})
        self.distance(5)
        self.assertEqual(self.topics("cmd/stop"), [])

    def test_retained_reading_is_ignored(self):
        for _ in range(3):
            self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": 5}, retain=True)
        self.assertEqual(self.topics("cmd/stop"), [])

    def test_garbage_reading_is_ignored(self):
        for payload in (None, {"cm": "near"}, {"cm": True}, {}):
            self.arbiter.on_message("sensor/ultrasonic/distance", payload)
        self.assertEqual(self.topics("cmd/stop"), [])

    def test_heartbeat_at_4_hz(self):
        for _ in range(20):          # 1 s in 50 ms ticks
            self.arbiter.tick()
            self.mono.t += 0.05
        self.assertEqual(len(self.topics("cmd/heartbeat")), 4)

    def test_invalid_rules_are_refused(self):
        for bad in ({"then": {"kind": "action", "name": "forward"}},
                    {"priority": "panic"},
                    {"when": "state/motion"},
                    {"then": {"kind": "explode"}}):
            with self.assertRaises(ValueError):
                Arbiter(lambda *m: None, {"rule": [dict(OBSTACLE_RULE, **bad)]})

    def test_action_rule(self):
        self.make({"rule": [{
            "name": "back_off", "when": "sensor/ultrasonic/distance", "condition": "cm < 8",
            "then": {"kind": "action", "name": "backward", "steps": 1, "speed": 80},
            "priority": "reflex", "median_of": 1,
        }]})
        self.distance(5)
        action = self.topics("cmd/action")[0][1]
        self.assertEqual((action["name"], action["priority"], action["speed"]), ("backward", "reflex", 80))


class WanderTest(ArbiterTestBase):

    def setUp(self):
        self.make({"rule": [OBSTACLE_RULE], "wander": {"enabled": True, "clear_cm": 25}})
        self.arbiter.on_message("status", {"status": "online"})
        self.arbiter.on_message("state/motion", {"status": "idle", "queue_len": 0})

    def actions(self):
        return [m[1] for m in self.topics("cmd/action")]

    def test_walks_forward_when_clear_and_waits_for_completion(self):
        self.distance(80)
        self.arbiter.tick()
        self.arbiter.tick()
        self.assertEqual([a["name"] for a in self.actions()], ["forward"])
        cmd_id = self.actions()[0]["id"]
        self.arbiter.on_message("state/motion", {"status": "running", "command_id": cmd_id})
        self.arbiter.tick()
        self.arbiter.on_message("state/motion", {"status": "idle", "command_id": cmd_id, "queue_len": 0})
        self.distance(80)
        self.arbiter.tick()
        self.assertEqual([a["name"] for a in self.actions()], ["forward", "forward"])

    def test_turns_when_something_is_close(self):
        self.distance(20)
        self.arbiter.tick()
        self.assertEqual(self.actions()[0]["name"], "turn left")

    def test_backs_off_after_rejection(self):
        self.distance(20)
        self.arbiter.tick()
        self.arbiter.on_message("event/rejected", {"id": self.actions()[0]["id"], "reason": "locked out"})
        self.arbiter.tick()
        self.assertEqual(len(self.actions()), 1)
        self.mono.t += 0.6
        self.distance(20)
        self.arbiter.tick()
        self.assertEqual(len(self.actions()), 2)

    def test_does_nothing_without_fresh_sensor_or_body(self):
        self.arbiter.tick()
        self.assertEqual(self.actions(), [])          # no reading yet
        self.distance(80)
        self.mono.t += 1.5
        self.arbiter.tick()
        self.assertEqual(self.actions(), [])          # reading too old
        self.arbiter.on_message("status", {"status": "offline"})
        self.distance(80)
        self.arbiter.tick()
        self.assertEqual(self.actions(), [])          # body offline

    def test_off_by_default(self):
        self.make({"rule": [OBSTACLE_RULE]})
        self.arbiter.on_message("status", {"status": "online"})
        self.distance(80)
        self.arbiter.tick()
        self.assertEqual(self.actions(), [])


if __name__ == "__main__":
    unittest.main()

import unittest

from services.arbiter.arbiter import Arbiter
from services.sensor_gamepad.gamepad import PadState, normalize

NOW = 1_762_900_000.0


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class NormalizeTest(unittest.TestCase):

    def test_range_and_deadzone(self):
        self.assertEqual(normalize(0, 0, 255, 0.15), -1.0)
        self.assertEqual(normalize(255, 0, 255, 0.15), 1.0)
        self.assertEqual(normalize(130, 0, 255, 0.15), 0.0)
        self.assertEqual(normalize(-32768, -32768, 32767, 0.1), -1.0)
        self.assertGreater(normalize(200, 0, 255, 0.15), 0.4)
        self.assertEqual(normalize(5, 0, 0, 0.1), 0.0)

    def test_pad_state(self):
        pad = PadState(axis_x=0, axis_y=1)
        pad.set_range(0, 0, 255)
        pad.set_range(1, 0, 255)
        self.assertTrue(pad.on_axis(1, 0))
        self.assertFalse(pad.on_axis(1, 0))
        self.assertFalse(pad.on_axis(5, 0))         # unknown axis
        self.assertTrue(pad.on_button("BTN_SOUTH", True))
        payload = pad.payload()
        self.assertEqual((payload["x"], payload["y"], payload["buttons"]), (0.0, -1.0, ["BTN_SOUTH"]))


class ManualTest(unittest.TestCase):

    def setUp(self):
        self.sent = []
        self.mono = Clock(100.0)
        self.arbiter = Arbiter(lambda *m: self.sent.append(m), {
            "rule": [{"name": "obstacle_stop", "when": "sensor/ultrasonic/distance", "condition": "cm < 15",
                      "then": {"kind": "stop", "reason": "obstacle"}, "priority": "safety",
                      "when_motion": ["forward"], "median_of": 1}],
            "wander": {"enabled": True},
            "manual": {"buttons": {"BTN_NORTH": "wave"}},
        }, wall=Clock(NOW), mono=self.mono)
        self.arbiter.on_message("status", {"status": "online"})
        self.arbiter.on_message("state/motion", {"status": "idle", "queue_len": 0})

    def pad(self, x=0.0, y=0.0, buttons=()):
        self.arbiter.on_message("sensor/gamepad/state", {"x": x, "y": y, "buttons": list(buttons), "ts": NOW})

    def actions(self):
        return [m[1]["name"] for m in self.sent if m[0] == "cmd/action"]

    def finish_last(self):
        last = [m[1] for m in self.sent if m[0] == "cmd/action"][-1]
        self.arbiter.on_message("state/motion", {"status": "idle", "command_id": last["id"], "queue_len": 0})

    def test_stick_directions(self):
        for (x, y), expected in (((0, -1), "forward"), ((0, 1), "backward"),
                                 ((-1, 0.2), "turn left"), ((0.9, 0), "turn right")):
            self.pad(x, y)
            self.arbiter.tick()
            self.assertEqual(self.actions()[-1], expected)
            self.finish_last()

    def test_center_sends_nothing(self):
        self.pad(0.3, -0.2)
        self.arbiter.tick()
        self.assertEqual(self.actions(), [])

    def test_button_edge_and_stop_button(self):
        self.pad(buttons=["BTN_NORTH"])
        self.arbiter.tick()
        self.finish_last()
        self.pad(buttons=["BTN_NORTH"])              # still held: no repeat
        self.arbiter.tick()
        self.assertEqual(self.actions(), ["wave"])
        self.pad(buttons=["BTN_SELECT"])
        self.assertEqual([m[1]["reason"] for m in self.sent if m[0] == "cmd/stop"], ["gamepad"])

    def test_manual_pauses_wander(self):
        self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": 80})
        self.pad(0, 0.9)                             # backward
        self.arbiter.tick()
        self.finish_last()
        self.pad()                                   # released, still within hold_s
        self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": 80})
        self.mono.t += 2
        self.pad()
        self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": 80})
        self.arbiter.tick()
        self.assertEqual(self.actions(), ["backward"])
        self.mono.t += 1.5                           # hold_s (3 s) over: wander resumes
        self.pad()
        self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": 80})
        self.arbiter.tick()
        self.assertEqual(self.actions(), ["backward", "forward"])

    def test_obstacle_stop_only_while_walking_forward(self):
        self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": 5})
        self.assertEqual([m for m in self.sent if m[0] == "cmd/stop"], [])
        self.arbiter.on_message("state/motion", {"status": "running", "name": "turn left"})
        self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": 5})
        self.assertEqual([m for m in self.sent if m[0] == "cmd/stop"], [])
        self.arbiter.on_message("state/motion", {"status": "running", "name": "forward"})
        self.arbiter.on_message("sensor/ultrasonic/distance", {"cm": 5})
        self.assertEqual(len([m for m in self.sent if m[0] == "cmd/stop"]), 1)

    def test_garbage_pad_payload(self):
        for payload in ({"x": "left"}, {"x": True, "y": 0}, {"buttons": "BTN_NORTH"}, None):
            self.arbiter.on_message("sensor/gamepad/state", payload)
        self.arbiter.tick()
        self.assertEqual(self.actions(), [])


if __name__ == "__main__":
    unittest.main()


class AgentPausesWanderTest(unittest.TestCase):

    def test_wander_waits_while_agent_busy(self):
        sent = []
        arbiter = Arbiter(lambda *m: sent.append(m), {"wander": {"enabled": True}}, wall=Clock(NOW), mono=Clock(100.0))
        arbiter.on_message("status", {"status": "online"})
        arbiter.on_message("state/motion", {"status": "idle", "queue_len": 0})
        arbiter.on_message("agent/status", {"status": "online", "busy": True})
        arbiter.on_message("sensor/ultrasonic/distance", {"cm": 80})
        arbiter.tick()
        self.assertEqual([m for m in sent if m[0] == "cmd/action"], [])
        arbiter.on_message("agent/status", {"status": "online", "busy": False})
        arbiter.tick()
        self.assertEqual(len([m for m in sent if m[0] == "cmd/action"]), 1)

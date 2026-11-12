import unittest

from picrawler.executor import ExecutorState, Priority
from services.body.adapter import BodyAdapter

NOW = 1_762_900_000.0


class FakeExecutor:
    def __init__(self):
        self.submitted = []
        self.stops = []
        self.accept = True
        self.state = ExecutorState("idle")

    def submit(self, cmd):
        if self.accept:
            self.submitted.append(cmd)
        return self.accept

    def stop(self, priority=Priority.SAFETY, sit=False, speed=40):
        self.stops.append((priority, sit))


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class BodyAdapterTest(unittest.TestCase):

    def setUp(self):
        self.ex = FakeExecutor()
        self.sent = []
        self.wall = Clock(NOW)
        self.mono = Clock(500.0)
        cfg = {"watchdog": {"heartbeat_timeout_s": 1.0, "sit_after_disconnect_s": 5.0}}
        self.body = BodyAdapter(self.ex, lambda *m: self.sent.append(m), cfg, wall=self.wall, mono=self.mono)
        self.body.on_connect()
        self.sent.clear()

    def action(self, **kw):
        payload = {"id": "a1", "name": "forward", "steps": 2, "speed": 70, "ts": NOW}
        payload.update(kw)
        return payload

    def rejected(self):
        return [m[1]["reason"] for m in self.sent if m[0] == "event/rejected"]

    def test_connect_announces_online_and_state(self):
        self.sent.clear()
        self.body.on_connect()
        self.assertEqual(self.sent[0][0], "status")
        self.assertEqual(self.sent[0][1]["status"], "online")
        self.assertEqual(self.sent[0][2:], (1, True))
        self.assertEqual(self.sent[1][0], "state/motion")

    def test_goal_action_needs_heartbeat(self):
        self.body.on_message("cmd/action", self.action())
        self.assertEqual(self.ex.submitted, [])
        self.assertIn("no heartbeat from a controller", self.rejected())

        self.body.on_message("cmd/heartbeat", {"ts": NOW})
        self.body.on_message("cmd/action", self.action(id="a2"))
        cmd = self.ex.submitted[0]
        self.assertEqual((cmd.kind, cmd.name, cmd.steps, cmd.speed, cmd.priority, cmd.id),
                         ("action", "forward", 2, 70, Priority.GOAL, "a2"))

    def test_reflex_does_not_need_heartbeat(self):
        self.body.on_message("cmd/action", self.action(priority="reflex"))
        self.assertEqual(self.ex.submitted[0].priority, Priority.REFLEX)

    def test_frame(self):
        self.body.on_message("cmd/frame", {"id": "f", "frame": [[50, 50, -50]] * 4, "priority": "reflex", "ts": NOW})
        self.assertEqual(self.ex.submitted[0].frame, [[50, 50, -50]] * 4)

    def test_retained_commands_are_ignored(self):
        self.body.on_message("cmd/stop", {"sit": True}, retain=True)
        self.body.on_message("cmd/action", self.action(priority="reflex"), retain=True)
        self.assertEqual((self.ex.stops, self.ex.submitted), ([], []))
        self.assertEqual(self.rejected(), ["retained command ignored"] * 2)

    def test_invalid_and_duplicate_are_reported(self):
        self.body.on_message("cmd/action", self.action(name="rm -rf", priority="reflex"))
        self.body.on_message("cmd/action", self.action(priority="reflex"))
        self.body.on_message("cmd/action", self.action(priority="reflex"))
        self.assertEqual(len(self.ex.submitted), 1)
        self.assertEqual(self.rejected(), ["action not allowed", "duplicate id"])

    def test_executor_refusal_is_reported(self):
        self.ex.accept = False
        self.body.on_message("cmd/action", self.action(priority="reflex"))
        self.assertIn("busy or locked out by a higher priority", self.rejected())

    def test_stop_always_goes_through(self):
        self.body.on_message("cmd/stop", None)
        self.body.on_message("cmd/stop", {"sit": True, "ts": 0})
        self.assertEqual(self.ex.stops, [(Priority.SAFETY, False), (Priority.SAFETY, True)])

    def test_watchdog_stops_goal_motion_without_heartbeat(self):
        self.body.on_message("cmd/heartbeat", {})
        self.ex.state = ExecutorState("running", priority=Priority.GOAL)
        self.mono.t += 0.9
        self.body.tick()
        self.assertEqual(self.ex.stops, [])
        self.mono.t += 0.2
        self.body.tick()
        self.assertEqual(self.ex.stops, [(Priority.SAFETY, False)])
        self.assertIn("event/watchdog", [m[0] for m in self.sent])

    def test_watchdog_leaves_reflex_alone(self):
        self.ex.state = ExecutorState("running", priority=Priority.REFLEX)
        self.mono.t += 5
        self.body.tick()
        self.assertEqual(self.ex.stops, [])

    def test_disconnect_stops_then_sits_once(self):
        self.body.on_disconnect()
        self.assertEqual(self.ex.stops, [(Priority.SAFETY, False)])
        self.mono.t += 4.9
        self.body.tick()
        self.assertEqual(len(self.ex.stops), 1)
        self.mono.t += 0.2
        self.body.tick()
        self.body.tick()
        self.assertEqual(self.ex.stops, [(Priority.SAFETY, False), (Priority.SAFETY, True)])
        self.body.on_connect()
        self.mono.t += 10
        self.body.tick()
        self.assertEqual(len(self.ex.stops), 2)

    def test_state_is_published(self):
        self.body.on_state(ExecutorState("idle", command_id="a", name="forward", priority=Priority.GOAL,
                                         legs=[[60, 0, -30]] * 4))
        topics = {m[0]: m for m in self.sent}
        self.assertEqual(topics["state/motion"][1]["priority"], "goal")
        self.assertEqual(topics["state/motion"][2:], (1, True))
        self.assertEqual(topics["state/legs"][1]["legs"], [[60, 0, -30]] * 4)

    def test_unrelated_topics_ignored(self):
        self.body.on_message("sensor/ultrasonic/distance", {"cm": 3})
        self.assertEqual((self.sent, self.ex.stops), ([], []))


class InterlockTest(unittest.TestCase):

    def setUp(self):
        self.ex = FakeExecutor()
        self.sent = []
        self.wall = Clock(NOW)
        self.mono = Clock(500.0)
        self.body = BodyAdapter(self.ex, lambda *m: self.sent.append(m), {}, wall=self.wall, mono=self.mono)
        self.body.on_message("cmd/heartbeat", {"ts": NOW})

    def distance(self, *values):
        for cm in values:
            self.body.on_message("sensor/ultrasonic/distance", {"cm": cm, "ts": NOW})

    def forward(self, cmd_id="a1", name="forward"):
        self.body.on_message("cmd/action", {"id": cmd_id, "name": name, "ts": NOW})

    def rejected(self):
        return [m[1]["reason"] for m in self.sent if m[0] == "event/rejected"]

    def test_subscribes_to_the_sensor(self):
        self.assertIn("sensor/ultrasonic/distance", self.body.topics)
        off = BodyAdapter(self.ex, lambda *m: None, {"interlock": {"enabled": False}})
        self.assertNotIn("sensor/ultrasonic/distance", off.topics)

    def test_refuses_forward_but_not_turning_near_an_obstacle(self):
        self.distance(10, 11, 40)                     # median 11
        self.forward("a1")
        self.forward("a2", name="turn left")
        self.assertEqual(self.rejected(), ["obstacle 11 cm ahead"])
        self.assertEqual([c.name for c in self.ex.submitted], ["turn left"])

    def test_stops_a_running_forward_once(self):
        self.ex.state = ExecutorState("running", command_id="a1", name="forward", priority=Priority.GOAL)
        self.distance(30, 12, 12, 12)
        self.assertEqual(self.ex.stops, [(Priority.SAFETY, False)])
        stops = [m[1]["reason"] for m in self.sent if m[0] == "event/stop"]
        self.assertEqual(stops, ["obstacle 12 cm (body interlock)"])

    def test_leaves_other_actions_alone(self):
        self.ex.state = ExecutorState("running", command_id="a1", name="backward", priority=Priority.GOAL)
        self.distance(5, 5, 5)
        self.assertEqual(self.ex.stops, [])

    def test_stale_or_missing_readings_block_nothing(self):
        self.forward("a1")
        self.distance(5, 5, 5)
        self.mono.t += 1.5
        self.body.on_message("cmd/heartbeat", {"ts": NOW})
        self.forward("a2")
        self.body.on_message("sensor/ultrasonic/distance", {"cm": 5}, retain=True)
        self.forward("a3")
        self.assertEqual(self.rejected(), [])
        self.assertEqual(len(self.ex.submitted), 3)


if __name__ == "__main__":
    unittest.main()

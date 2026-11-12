import unittest

from services.arbiter import scan as localmap
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


class AgentGoalTest(ArbiterTestBase):

    def setUp(self):
        self.make({"rule": [OBSTACLE_RULE], "wander": {"enabled": True}})
        self.arbiter.on_message("status", {"status": "online"})
        self.arbiter.on_message("state/motion", {"status": "idle", "queue_len": 0})

    def goal(self, cmd_id="g1", name="forward", steps=3, speed=60):
        self.arbiter.on_message("agent/goal", {"id": cmd_id, "name": name, "steps": steps, "speed": speed, "ts": NOW})

    def rejected(self):
        return [(m[1]["id"], m[1]["reason"]) for m in self.topics("arbiter/rejected")]

    def test_goal_becomes_command_with_the_agents_id(self):
        self.goal()
        self.arbiter.tick()
        action = self.topics("cmd/action")[0][1]
        self.assertEqual((action["id"], action["name"], action["steps"], action["speed"], action["priority"]),
                         ("g1", "forward", 3, 60, "goal"))

    def test_waits_for_idle_body_and_gives_up(self):
        self.arbiter.on_message("state/motion", {"status": "running", "command_id": "other", "queue_len": 0})
        self.goal()
        self.arbiter.tick()
        self.assertEqual(self.topics("cmd/action"), [])
        self.mono.t += 6
        self.arbiter.tick()
        self.assertEqual(self.rejected(), [("g1", "body busy or offline")])

    def test_manual_control_wins(self):
        self.arbiter.on_message("remote/state", {"x": 0, "y": -1})
        self.goal()
        self.arbiter.tick()
        self.assertEqual(self.rejected(), [("g1", "manual control active")])
        self.assertEqual([m[1]["name"] for m in self.topics("cmd/action")], ["forward"])
        self.assertNotEqual(self.topics("cmd/action")[0][1]["id"], "g1")

    def test_invalid_and_replaced_goals(self):
        self.arbiter.on_message("agent/goal", {"id": "bad", "name": "forward", "steps": 99, "ts": NOW})
        self.arbiter.on_message("state/motion", {"status": "running", "command_id": "x", "queue_len": 0})
        self.goal("g1")
        self.goal("g2")
        self.assertEqual(self.rejected(), [("bad", "invalid goal"), ("g1", "replaced by a newer goal")])

    def test_wander_pauses_while_a_goal_waits_or_runs(self):
        self.distance(80)
        self.goal()
        self.arbiter.tick()
        names = [m[1]["id"] for m in self.topics("cmd/action")]
        self.assertEqual(names, ["g1"])
        self.arbiter.on_message("state/motion", {"status": "running", "command_id": "g1", "queue_len": 0})
        self.arbiter.on_message("state/motion", {"status": "idle", "command_id": "g1", "queue_len": 0})
        self.distance(80)
        self.arbiter.tick()
        self.assertEqual(len(self.topics("cmd/action")), 2)   # wander resumes


class ScanTest(ArbiterTestBase):

    def setUp(self):
        self.make({"rule": [OBSTACLE_RULE], "wander": {"enabled": True},
                   "scan": {"turns": 4, "turn_deg": 90, "settle_s": 0.4, "readings": 3}})
        self.arbiter.on_message("status", {"status": "online"})
        self.arbiter.on_message("state/motion", {"status": "idle", "queue_len": 0})
        self.distance(100)

    def maps(self):
        return [m[1] for m in self.topics("map/local")]

    def turns(self):
        return [m[1] for m in self.topics("cmd/action")]

    def measure(self, cm):
        self.mono.t += 0.5                            # settled
        for _ in range(3):
            self.distance(cm)
        self.arbiter.tick()

    def finish_turn(self):
        self.arbiter.tick()                           # sends the turn
        turn = self.turns()[-1]
        self.arbiter.on_message("state/motion", {"status": "running", "command_id": turn["id"], "queue_len": 0})
        self.arbiter.on_message("state/motion", {"status": "idle", "command_id": turn["id"], "queue_len": 0})
        self.arbiter.tick()                           # back to measuring

    def test_full_sweep(self):
        self.arbiter.on_message("map/scan", {"id": "s1", "user": "admin", "ts": NOW})
        self.assertEqual(self.maps()[-1]["status"], "scanning")
        for i, cm in enumerate((80, 30, 120, 50)):
            self.measure(cm)
            if i < 3:
                self.finish_turn()
        result = self.maps()[-1]
        self.assertEqual(result["status"], "done")
        self.assertEqual([(s["heading_deg"], s["cm"]) for s in result["samples"]],
                         [(0, 80), (90, 30), (180, 120), (270, 50)])
        self.assertEqual(result["nearest"], (90, 30))
        self.assertEqual(result["clearest_deg"], 180)
        self.assertEqual([t["name"] for t in self.turns()][:3], ["turn left"] * 3)   # then wander resumes
        self.assertEqual(len(result["grid"]), 60)
        self.assertEqual(self.topics("map/local")[-1][2:], (1, True))   # retained

    def test_waits_for_the_body_to_settle(self):
        self.arbiter.on_message("map/scan", {"id": "s1"})
        for _ in range(3):
            self.distance(40)                         # too early, body may still sway
        self.arbiter.tick()
        self.assertEqual(self.maps()[-1]["progress"], [0, 4])
        self.measure(40)
        self.assertEqual(self.maps()[-1]["progress"], [1, 4])

    def test_no_echo_counts_as_free(self):
        self.arbiter.on_message("map/scan", {"id": "s1"})
        self.mono.t += 0.5
        self.arbiter.tick()
        self.mono.t += 1.5
        self.distance(100)                            # keeps the sensor fresh, too few readings
        self.mono.t += 0.7
        self.arbiter.tick()
        self.assertEqual(self.maps()[-1]["samples"], [{"heading_deg": 0, "cm": None}])

    def test_manual_control_aborts_and_others_wait(self):
        self.arbiter.on_message("map/scan", {"id": "s1"})
        self.arbiter.on_message("agent/goal", {"id": "g1", "name": "forward", "ts": NOW})
        self.arbiter.tick()
        self.assertEqual([m[1]["reason"] for m in self.topics("arbiter/rejected")], ["scan in progress"])
        self.assertEqual(self.turns(), [])            # wander waits too
        self.arbiter.on_message("remote/state", {"x": 0, "y": -1})
        self.arbiter.tick()
        self.assertEqual((self.maps()[-1]["status"], self.maps()[-1]["message"]), ("aborted", "manual control"))

    def test_refused_without_sensor_or_body(self):
        self.mono.t += 2
        self.arbiter.on_message("map/scan", {"id": "s1"})
        self.assertEqual((self.maps()[-1]["status"], self.maps()[-1]["message"]),
                         ("refused", "no ultrasonic readings"))

    def test_rejected_turn_aborts(self):
        self.arbiter.on_message("map/scan", {"id": "s1"})
        self.measure(60)
        self.arbiter.tick()
        self.arbiter.on_message("event/rejected", {"id": self.turns()[-1]["id"], "reason": "battery critical"})
        self.assertEqual(self.maps()[-1]["message"], "body refused the turn: battery critical")
        self.assertFalse(self.arbiter.scan.active())


class OccupancyTest(unittest.TestCase):

    def test_echo_marks_free_then_occupied(self):
        grid = localmap.occupancy([(0, 50)], cell_cm=10, radius_cm=100, beam_deg=0.1)
        column = "".join(row[10] for row in grid)     # straight ahead, from the far end to the robot
        self.assertEqual(column[:5], "?????")
        self.assertEqual(column[5], "#")
        self.assertEqual(column[6:10], "....")

    def test_no_echo_is_free_to_the_edge(self):
        grid = localmap.occupancy([(90, None)], cell_cm=10, radius_cm=100, beam_deg=0.1, max_cm=200)
        self.assertEqual(grid[9][:10], "." * 10)      # to the left
        self.assertEqual(grid[9][11:], "?" * 9)


class ScanWithCameraTest(ArbiterTestBase):

    def setUp(self):
        self.make({"scan": {"turns": 2, "turn_deg": 90, "settle_s": 0.4, "readings": 3}})
        self.arbiter.on_message("status", {"status": "online"})
        self.arbiter.on_message("state/motion", {"status": "idle", "queue_len": 0})
        self.distance(100)
        self.floor([])                                # vision is alive

    def floor(self, bands):
        self.arbiter.on_message("sensor/vision/floor", {"bands": bands, "max_cm": 150, "ts": NOW})

    def last_map(self):
        return [m[1] for m in self.sent if m[0] == "map/local"][-1]

    def test_waits_for_a_settled_frame_and_uses_it(self):
        self.arbiter.on_message("map/scan", {"id": "s1"})
        self.mono.t += 0.5
        for _ in range(3):
            self.distance(70)
        self.arbiter.tick()
        self.assertEqual(self.last_map()["progress"], [0, 2])       # no camera frame yet
        self.floor([{"bearing_deg": 20, "cm": 60.0}, {"bearing_deg": -20, "cm": None}])
        self.arbiter.on_message("sensor/vision/detections", {"objects": [
            {"label": "chair", "score": 0.8, "bearing_deg": -15, "floor_cm": 55.0},
            {"label": "cat", "score": 0.6, "bearing_deg": 3, "floor_cm": None},
            {"label": "old", "score": 0.9}]})
        self.arbiter.tick()
        m = self.last_map()
        self.assertEqual(m["floor"], [{"heading_deg": 20, "cm": 60.0}, {"heading_deg": 340, "cm": None}])
        self.assertEqual([(o["label"], o["heading_deg"], o["cm"]) for o in m["objects"]],
                         [("cat", 3, 70.0), ("chair", 345, 55.0)])  # cat: centered, ultrasonic distance
        self.assertEqual(m["nearest"], (20, 60.0))                  # the camera saw it closer than the sonar

    def test_goes_on_without_camera_after_the_timeout(self):
        self.arbiter.on_message("map/scan", {"id": "s1"})
        self.mono.t += 0.5
        for _ in range(3):
            self.distance(70)
        self.arbiter.tick()
        self.mono.t += 2.0
        self.distance(70)
        self.arbiter.tick()
        self.assertEqual(self.last_map()["progress"], [1, 2])
        self.assertEqual(self.last_map()["floor"], [])


class OccupancyWithFloorTest(unittest.TestCase):

    def test_floor_edge_marks_obstacle_the_ultrasonic_missed(self):
        grid = localmap.occupancy([(0, None)], cell_cm=10, radius_cm=100, beam_deg=0.1, max_cm=200,
                                  floor=[(0, 40.0)], floor_beam_deg=0.1)
        column = "".join(row[10] for row in grid)
        self.assertEqual(column[6], "#")              # 40 cm ahead
        self.assertEqual(column[:6], "......")        # ultrasonic: no echo, free beyond

    def test_objects_are_merged_per_label_and_direction(self):
        merged = localmap.merge_objects([
            {"label": "chair", "heading_deg": 350, "cm": 50, "score": 0.5},
            {"label": "chair", "heading_deg": 5, "cm": 52, "score": 0.9},
            {"label": "chair", "heading_deg": 180, "cm": 80, "score": 0.7},
            {"label": "cat", "heading_deg": 0, "cm": None, "score": 0.6}])
        self.assertEqual([(o["label"], o["heading_deg"]) for o in merged],
                         [("cat", 0), ("chair", 5), ("chair", 180)])


if __name__ == "__main__":
    unittest.main()

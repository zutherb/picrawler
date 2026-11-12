import unittest

from services.common import schema

NOW = 1_762_900_000.0
LIMITS = schema.Limits()


def action(**kw):
    payload = {"id": "a1", "name": "forward", "steps": 2, "speed": 70, "priority": "goal", "ts": NOW}
    payload.update(kw)
    return payload


def frame(**kw):
    payload = {"id": "f1", "frame": [[45, 45, -50], [45, 0, -50], [45, 0, -50], [45, 45, -50]], "ts": NOW}
    payload.update(kw)
    return payload


class SchemaTest(unittest.TestCase):

    def assertRejected(self, fn, payload, fragment, **kw):
        with self.assertRaises(schema.Rejected) as cm:
            fn(payload, NOW, LIMITS, **kw)
        self.assertIn(fragment, str(cm.exception))

    def test_valid_action(self):
        self.assertEqual(schema.action(action(), NOW, LIMITS),
                         {"id": "a1", "name": "forward", "steps": 2, "speed": 70, "priority": "goal"})

    def test_action_defaults(self):
        v = schema.action({"id": "x", "name": "sit", "ts": NOW}, NOW, LIMITS)
        self.assertEqual((v["steps"], v["speed"], v["priority"]), (1, 50, "goal"))

    def test_action_rejections(self):
        self.assertRejected(schema.action, action(name="__import__"), "not allowed")
        self.assertRejected(schema.action, action(name="turn left angle"), "not allowed")
        self.assertRejected(schema.action, action(steps=0), "steps")
        self.assertRejected(schema.action, action(steps=11), "steps")
        self.assertRejected(schema.action, action(steps=2.0), "steps")
        self.assertRejected(schema.action, action(speed=101), "speed")
        self.assertRejected(schema.action, action(speed=True), "speed")
        self.assertRejected(schema.action, action(priority="safety"), "priority")
        self.assertRejected(schema.action, action(ts=NOW - 3), "ts")
        self.assertRejected(schema.action, action(ts=NOW + 3), "ts")
        self.assertRejected(schema.action, action(ts="now"), "ts")
        self.assertRejected(schema.action, action(id=""), "id")
        self.assertRejected(schema.action, action(id=None), "id")
        self.assertRejected(schema.action, ["forward"], "JSON object")
        self.assertRejected(schema.action, None, "JSON object")

    def test_duplicate_id_within_window(self):
        dedupe = schema.Dedupe()
        schema.action(action(), NOW, LIMITS, dedupe)
        self.assertRejected(schema.action, action(), "duplicate", dedupe=dedupe)
        later = NOW + 61
        schema.action(action(ts=later), later, LIMITS, dedupe)

    def test_frame(self):
        self.assertEqual(len(schema.frame(frame(), NOW, LIMITS)["frame"]), 4)
        self.assertRejected(schema.frame, frame(frame=[[45, 45, -50]] * 3), "4 legs")
        self.assertRejected(schema.frame, frame(frame=[[45, 45]] * 4), "[x, y, z]")
        self.assertRejected(schema.frame, frame(frame=[[45, 45, -50]] * 3 + [[121, 0, 0]]), "x out of range")
        self.assertRejected(schema.frame, frame(frame=[[45, 45, float("nan")]] * 4), "number")
        self.assertRejected(schema.frame, frame(frame=[[45, "1", -50]] * 4), "number")

    def test_custom_limits(self):
        limits = schema.Limits(actions=["wave"], steps_max=3, bounds={"z": [-60, 0]})
        schema.action(action(name="wave", steps=3), NOW, limits)
        with self.assertRaises(schema.Rejected):
            schema.action(action(steps=1), NOW, limits)
        with self.assertRaises(schema.Rejected):
            schema.frame(frame(), NOW, limits.__class__(bounds={"z": (-40, 0)}))

    def test_stop_never_rejects(self):
        self.assertEqual(schema.stop({"sit": True, "reason": "obstacle", "id": "s"}),
                         {"id": "s", "sit": True, "reason": "obstacle"})
        self.assertEqual(schema.stop(None), {"id": None, "sit": False, "reason": ""})
        self.assertEqual(schema.stop({"sit": "yes", "reason": 5}), {"id": None, "sit": False, "reason": ""})


if __name__ == "__main__":
    unittest.main()

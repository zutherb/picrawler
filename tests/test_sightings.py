import unittest

from services.web.sightings import SightingTracker


def person(score=0.9):
    return {"label": "person", "score": score, "box": [0, 0, 1, 1]}


class SightingTrackerTest(unittest.TestCase):

    def setUp(self):
        self.t = SightingTracker(min_score=0.5, start_frames=3, end_after_s=2.0)

    def feed(self, objects, now):
        return [(what, s.label) for what, s in self.t.feed(objects, now)]

    def test_starts_after_three_frames_with_time_of_first(self):
        self.assertEqual(self.feed([person()], 10.0), [])
        self.assertEqual(self.feed([person()], 10.1), [])
        self.assertEqual(self.feed([person()], 10.2), [("start", "person")])
        self.assertEqual(self.t.active["person"].start, 10.0)

    def test_streak_broken_by_missing_frame(self):
        self.feed([person()], 10.0)
        self.feed([person()], 10.1)
        self.feed([], 10.2)
        self.assertEqual(self.feed([person()], 10.3), [])
        self.assertNotIn("person", self.t.active)

    def test_low_score_does_not_count(self):
        for i in range(5):
            self.feed([person(0.4)], 10 + i * 0.1)
        self.assertEqual(self.t.active, {})

    def test_short_gap_extends_and_long_gap_ends(self):
        for i in range(3):
            self.feed([person()], 10 + i * 0.1)
        self.assertEqual(self.feed([], 11.5), [])
        self.feed([person(0.95)], 12.0)              # gap of 1.8 s: same sighting
        self.assertEqual(self.feed([], 13.9), [])
        events = self.t.feed([], 14.1)               # 2.1 s since last seen
        self.assertEqual([(w, s.label) for w, s in events], [("end", "person")])
        sighting = events[0][1]
        self.assertEqual((sighting.start, sighting.end), (10.0, 12.0))
        self.assertEqual(sighting.max_score, 0.95)

    def test_max_count_and_separate_labels(self):
        cat = {"label": "cat", "score": 0.7}
        for i in range(3):
            self.feed([person(), person(0.8), cat], 10 + i * 0.1)
        self.assertEqual(self.t.active["person"].max_count, 2)
        self.assertEqual(set(self.t.active), {"person", "cat"})

    def test_expire_without_frames_and_close_all(self):
        for i in range(3):
            self.feed([person()], 10 + i * 0.1)
        self.assertEqual(self.t.expire(11.0), [])
        self.assertEqual([w for w, _ in self.t.expire(12.5)], ["end"])
        for i in range(3):
            self.feed([person()], 20 + i * 0.1)
        self.assertEqual([w for w, _ in self.t.close_all()], ["end"])
        self.assertEqual(self.t.active, {})

    def test_garbage_is_ignored(self):
        for i in range(3):
            self.feed([None, {"label": 3, "score": 1}, {"label": "x", "score": True}, "y"], 10 + i)
        self.feed(None, 14)
        self.assertEqual(self.t.active, {})


if __name__ == "__main__":
    unittest.main()

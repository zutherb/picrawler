import unittest

from picrawler import MoveList

GAITS = ["stand", "sit", "forward", "backward", "turn left", "turn right",
         "turn left angle", "turn right angle", "wave", "push up", "dance",
         "look left", "look right", "look up", "look down"]


class GaitsTest(unittest.TestCase):

    def test_every_gait_yields_four_leg_frames(self):
        moves = MoveList()
        for name in GAITS:
            frames = moves[name]
            self.assertTrue(frames, name)
            for frame in frames:
                self.assertEqual(len(frame), 4, name)
                for coord in frame:
                    self.assertEqual(len(coord), 3, name)

    def test_walking_gait_prepends_stand_when_sitting(self):
        moves = MoveList()
        self.assertTrue(moves.is_sit())
        with_stand = len(moves["forward"])
        self.assertTrue(moves.is_stand())
        self.assertGreater(with_stand, len(moves["forward"]))

    def test_stand_position_swaps_legs(self):
        moves = MoveList()
        moves["stand"]
        first = moves["forward"]
        moves.stand_position = 1
        swapped = moves["forward"]
        self.assertEqual([[f[1], f[0], f[3], f[2]] for f in first], swapped)

    def test_lookup_rejects_non_gait_names(self):
        moves = MoveList()
        for name in ["__class__", "_x", "nope", "__import__('os')"]:
            with self.assertRaises(AttributeError):
                moves[name]


if __name__ == "__main__":
    unittest.main()

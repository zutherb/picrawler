import math
import unittest

from services.vision.geometry import CameraGeometry, locate

try:
    import numpy as np
except ImportError:
    np = None


class GeometryTest(unittest.TestCase):

    def setUp(self):
        self.g = CameraGeometry(300, 300, hfov_deg=60, vfov_deg=40, height_cm=10, tilt_deg=10)

    def test_center_column_is_straight_ahead(self):
        cm, bearing = self.g.ground(150, 250)
        self.assertAlmostEqual(bearing, 0)
        # ray 10° (tilt) + atan(100/fy) below the horizon
        depression = math.radians(10) + math.atan(100 / self.g.fy)
        self.assertAlmostEqual(cm, 10 / math.tan(depression), delta=0.6)

    def test_left_is_positive_and_lower_rows_are_closer(self):
        self.assertGreater(self.g.bearing(0), 0)
        self.assertLess(self.g.bearing(299), 0)
        self.assertLess(self.g.ground(150, 299)[0], self.g.ground(150, 200)[0])

    def test_above_the_horizon_is_none(self):
        self.assertIsNone(self.g.ground(150, 0))


@unittest.skipIf(np is None, "numpy not installed")
class FloorTest(unittest.TestCase):

    def setUp(self):
        self.g = CameraGeometry(300, 300, height_cm=9, tilt_deg=8)
        rng = np.random.default_rng(1)
        floor = np.full((300, 300, 3), (120, 110, 100), np.float32)
        self.img = (floor + rng.normal(0, 6, floor.shape)).clip(0, 255).astype(np.uint8)

    def row_at(self, u, cm):
        return next(v for v in range(300) if self.g.ground(u, v) and self.g.ground(u, v)[0] <= cm)

    def test_wall_and_box(self):
        from services.vision.floor import boundaries
        self.img[:self.row_at(150, 60), :] = (60, 80, 160)          # wall 60 cm ahead
        self.img[:self.row_at(260, 40), 220:] = (200, 60, 50)       # box on the right, 40 cm
        bands = boundaries(self.img, self.g, bins=12)
        ahead = [cm for bearing, cm in bands if abs(bearing) < 5]
        right = [cm for bearing, cm in bands if bearing < -18]
        self.assertTrue(all(55 <= cm <= 66 for cm in ahead), bands)
        self.assertTrue(all(35 <= cm <= 45 for cm in right), bands)
        self.assertEqual([b for b, _ in bands], sorted((b for b, _ in bands), reverse=True))

    def test_open_floor_is_free(self):
        from services.vision.floor import boundaries
        self.assertTrue(all(cm is None for _, cm in boundaries(self.img, self.g, max_cm=50)))


class LocateTest(unittest.TestCase):

    def test_bearing_and_floor_distance_from_the_box(self):
        g = CameraGeometry(300, 300, height_cm=9, tilt_deg=8)
        objects = locate([{"label": "chair", "box": [0.6, 0.2, 0.9, 0.7]},
                          {"label": "person", "box": [0.1, 0.0, 0.4, 1.0]},
                          {"label": "odd"}], g, 300, 300)
        chair, person, odd = objects
        self.assertLess(chair["bearing_deg"], 0)                    # right of center
        self.assertAlmostEqual(chair["floor_cm"], g.ground(225, 210)[0], delta=0.2)
        self.assertGreater(person["bearing_deg"], 0)
        self.assertIsNone(person["floor_cm"])                       # feet cut off
        self.assertNotIn("bearing_deg", odd)


if __name__ == "__main__":
    unittest.main()

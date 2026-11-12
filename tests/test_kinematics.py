import unittest

from picrawler import kinematics


class KinematicsTest(unittest.TestCase):

    def test_round_trip_reachable_points(self):
        for coord in ([60, 0, -30], [45, 45, -50], [45, 0, -50], [70, 0, -30], [50, 20, -40]):
            angles = kinematics.leg_servo_angles(coord)
            back = kinematics.polar2coord(angles)
            for expected, actual in zip(coord, back):
                self.assertAlmostEqual(expected, actual, delta=0.01, msg=coord)

    def test_leg_servo_angles_reorders_coord2polar(self):
        alpha, beta, gamma = kinematics.coord2polar([45, 45, -50])
        self.assertEqual(kinematics.leg_servo_angles([45, 45, -50]), [beta, alpha, gamma])

    def test_gamma_is_zero_on_the_diagonal(self):
        self.assertAlmostEqual(kinematics.coord2polar([50, 50, -40])[2], 0)

    def test_unreachable_points_do_not_raise(self):
        for coord in ([0, 0, 0], [500, 0, 0], [0, 0, -500], [1, 1, 1]):
            self.assertEqual(len(kinematics.coord2polar(coord)), 3)

    def test_clamp_coord_scales_into_shell(self):
        far = kinematics.clamp_coord([1000, 0, 0])
        self.assertAlmostEqual(far[0], kinematics.A + kinematics.B + kinematics.C)
        near = kinematics.clamp_coord([1, 0, 0])
        self.assertAlmostEqual(near[0], kinematics.C)

    def test_limit_angle(self):
        self.assertEqual(kinematics.limit_angle([10, 20, 30]), (False, [10, 20, 30]))
        self.assertEqual(kinematics.limit_angle([-100, -20, 70]), (True, [-90, -10, 60]))


if __name__ == "__main__":
    unittest.main()

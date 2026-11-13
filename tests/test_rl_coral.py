"""rl_coral data contract: observation layout, CPG, frames, IK -> URDF joints."""
import math
import pathlib
import unittest
import xml.etree.ElementTree as ET

try:
    import numpy as np
    from rl_coral import common
    from rl_coral import occupancy_mapper_2d as om
except ImportError:
    np = None

from picrawler import kinematics

URDF = pathlib.Path(__file__).resolve().parent.parent / "rl_coral" / "picrawler.urdf"


def urdf_foot(leg, joints):
    """Foot position (body frame, mm, relative to the torso) from URDF joint angles."""
    sx, sy = common.LEG_SIGNS[leg]
    hip, femur, knee = joints
    direction = math.atan2(sy, sx) + hip
    reach = kinematics.C + kinematics.A * math.cos(femur) + kinematics.B * math.cos(femur - knee)
    z = kinematics.A * math.sin(femur) + kinematics.B * math.sin(femur - knee)
    return np.array([common.HIP_POS_MM[leg][0] + reach * math.cos(direction),
                     common.HIP_POS_MM[leg][1] + reach * math.sin(direction), z])


@unittest.skipIf(np is None, "numpy not installed")
class ContractTest(unittest.TestCase):

    def test_observation_layout(self):
        last = np.linspace(-1, 1, 12)
        prev = -last
        obs = common.build_observation(math.pi / 2, 0.5, -0.25, [0.1, -0.2, 3.0, 0.4], last, prev)
        self.assertEqual(obs.shape, (32,))
        self.assertEqual(obs.dtype, np.float32)
        np.testing.assert_allclose(obs[0:4], [1.0, 0.0, 0.5, -0.25], atol=1e-6)
        np.testing.assert_allclose(obs[4:8], [0.1, -0.2, 1.0, 0.4], atol=1e-6)    # flow clipped to 1
        np.testing.assert_allclose(obs[8:20], last, atol=1e-6)
        np.testing.assert_allclose(obs[20:32], prev, atol=1e-6)
        self.assertTrue(np.all(np.abs(obs) <= 1.0))

    def test_zero_action_standing_is_the_base_stance(self):
        self.assertEqual(common.foot_targets(0.0, 0.0, 0.0, np.zeros(12)), [[60.0, 60.0, -50.0]] * 4)

    def test_body_offsets_map_to_leg_frames(self):
        action = np.zeros((4, 3))
        action[:, 1] = 1.0                                      # every foot 15 mm forward
        legs = common.foot_targets(0.0, 0.0, 0.0, action)
        self.assertEqual([leg[1] for leg in legs], [75.0, 75.0, 45.0, 45.0])   # front away, rear closer
        action[:] = 0.0
        action[:, 0] = 1.0                                      # every foot 15 mm to the right
        legs = common.foot_targets(0.0, 0.0, 0.0, action)
        self.assertEqual([leg[0] for leg in legs], [75.0, 45.0, 45.0, 75.0])

    def test_trot(self):
        for phase in np.linspace(0, 2 * math.pi, 17):
            offsets = common.cpg_body(phase, 1.0, 0.0)
            np.testing.assert_allclose(offsets[0], offsets[2])  # RF with LR
            np.testing.assert_allclose(offsets[1], offsets[3])  # LF with RR
            swing = math.sin(phase) > 1e-9
            self.assertEqual(offsets[0, 2] > 1e-6, swing)
            self.assertTrue(offsets[0, 2] == 0 or offsets[1, 2] == 0)   # never both pairs in the air
        # a swinging foot moves forward, a supporting one backward
        self.assertLess(common.cpg_body(0.1, 1.0, 0.0)[0, 1], common.cpg_body(1.0, 1.0, 0.0)[0, 1])
        self.assertGreater(common.cpg_body(math.pi + 0.1, 1.0, 0.0)[0, 1], common.cpg_body(math.pi + 1.0, 1.0, 0.0)[0, 1])
        np.testing.assert_allclose(common.cpg_body(1.0, 0.0, 0.0), 0.0)      # no command: stand still

    def test_turning_moves_swinging_feet_counter_clockwise(self):
        swing_rf = common.cpg_body(math.pi / 2, 0.0, 1.0)[0]     # RF mid-swing, moving through 0
        later = common.cpg_body(math.pi / 2 + 0.3, 0.0, 1.0)[0]
        motion = later[:2] - swing_rf[:2]
        hip = common.HIP_POS_MM[0]
        self.assertGreater(hip[0] * motion[1] - hip[1] * motion[0], 0)     # cross product > 0: CCW

    def test_obstacle_slows_only_forward_commands(self):
        self.assertEqual(common.effective_vx(1.0, 1.0), 1.0)
        self.assertEqual(common.effective_vx(1.0, 0.1), 0.0)
        self.assertAlmostEqual(common.effective_vx(1.0, 0.275), 0.5)
        self.assertEqual(common.effective_vx(-0.5, 0.0), -0.5)

    def test_urdf_joints_reproduce_the_ik_targets(self):
        rng = np.random.default_rng(3)
        for _ in range(50):
            phase = rng.uniform(0, 2 * math.pi)
            action = rng.uniform(-1, 1, 12)
            targets = common.foot_targets(phase, rng.uniform(-1, 1), rng.uniform(-1, 1), action)
            servo = common.servo_angles(targets)
            joints = common.joint_angles(servo)
            for leg in range(4):
                x, y, z = targets[leg]
                reach = math.hypot(z, math.hypot(x, y) - kinematics.C)
                if not 30 <= reach <= 91.58 or kinematics.limit_angle(kinematics.leg_servo_angles(targets[leg]))[0]:
                    continue                                    # shortened by coord2polar or the servo limits
                sx, sy = common.LEG_SIGNS[leg]
                body = np.array([targets[leg][0] * sx, targets[leg][1] * sy, targets[leg][2]])
                expected = np.array([*common.HIP_POS_MM[leg], 0.0]) + body
                np.testing.assert_allclose(urdf_foot(leg, joints[leg]), expected, atol=0.5)

    def test_urdf_matches_the_kinematics(self):
        root = ET.parse(URDF).getroot()
        joints = {j.get("name"): j for j in root.iter("joint")}
        x = lambda name: float(joints[name].find("origin").get("xyz").split()[0]) * 1000
        for leg in ("rf", "lf", "lr", "rr"):
            self.assertAlmostEqual(x("%s_femur" % leg), kinematics.C)
            self.assertAlmostEqual(x("%s_knee" % leg), kinematics.A)
            self.assertAlmostEqual(x("%s_foot_fixed" % leg), kinematics.B)
        self.assertEqual(len([j for j in joints.values() if j.get("type") == "revolute"]), 12)

    def test_quantization_round_trip(self):
        x = np.linspace(-1, 1, 32, dtype=np.float32)
        q = common.quantize(x, 1 / 127.5, -1)
        self.assertEqual(q.dtype, np.int8)
        np.testing.assert_allclose(common.dequantize(q, 1 / 127.5, -1), x, atol=1 / 127.5)


@unittest.skipIf(np is None, "numpy not installed")
class MapperTest(unittest.TestCase):

    def walk(self, mapper, vx, wz, seconds, flow_yaw=0.0, fresh=False):
        for step in range(int(seconds / common.DT)):
            mapper.update_odometry(common.phase_at(step), vx, wz, np.zeros(12), flow_yaw, fresh)

    def test_bresenham(self):
        for x1, y1 in [(7, 3), (-5, 9), (0, -6), (-8, -8), (4, 0)]:
            xs, ys = om.bresenham(0, 0, x1, y1)
            self.assertEqual((xs[0], ys[0], xs[-1], ys[-1]), (0, 0, x1, y1))
            steps = np.abs(np.diff(np.stack([xs, ys]), axis=1))
            self.assertTrue(np.all(steps.max(axis=0) == 1))          # 8-connected, no gaps or repeats
        self.assertEqual(om.bresenham(2, 2, 2, 2)[0].tolist(), [2])

    def test_odometry_forward_and_turning(self):
        mapper = om.OccupancyMapper2D(slip=1.0)
        self.walk(mapper, 1.0, 0.0, 2.0)
        x, y, theta = mapper.pose
        self.assertAlmostEqual(y, common.MAX_VX * 2.0, delta=0.03)  # about 16 cm forward
        self.assertAlmostEqual(x, 0.0, delta=0.005)
        self.assertAlmostEqual(theta, 0.0, delta=0.01)
        mapper = om.OccupancyMapper2D(slip=1.0)
        self.walk(mapper, 0.0, 1.0, 2.0)
        self.assertGreater(mapper.pose[2], 0.5)                      # counter-clockwise
        self.assertLess(np.hypot(*mapper.pose[:2]), 0.02)            # on the spot

    def test_flow_yaw_corrects_the_heading(self):
        mapper = om.OccupancyMapper2D(slip=1.0, flow_weight=1.0)
        self.walk(mapper, 0.0, 1.0, 2.0, flow_yaw=0.2, fresh=True)
        self.assertAlmostEqual(mapper.pose[2], 0.4, delta=0.02)      # heading from the camera only

    def test_wall_ahead(self):
        mapper = om.OccupancyMapper2D(fov_deg=60)
        bands = [(b, 50.0) for b in np.linspace(30, -30, 31)]        # floor ends 50 cm ahead everywhere
        for _ in range(3):
            self.assertTrue(mapper.update_scan(bands))
        prob = mapper.probability()
        wall = mapper.world_to_cell(0.0, om.CAMERA_FORWARD_M + 0.5)
        free = mapper.world_to_cell(0.0, om.CAMERA_FORWARD_M + 0.25)
        behind = mapper.world_to_cell(0.0, -0.3)
        self.assertGreater(prob[wall[1], wall[0]], 0.9)
        self.assertLess(prob[free[1], free[0]], 0.25)                # 3 x -0.4 log-odds
        self.assertAlmostEqual(prob[behind[1], behind[0]], 0.5)
        self.assertTrue(np.all(np.abs(mapper.log_odds) <= om.L_MAX))

    def test_ultrasonic_fills_the_center_and_tilt_skips(self):
        mapper = om.OccupancyMapper2D(fov_deg=60)
        scan = mapper.virtual_scan([(b, None) for b in np.linspace(30, -30, 31)], ultrasonic_cm=40.0)
        hits = [(round(np.degrees(b)), d) for b, d, hit in scan if hit]
        self.assertEqual({d for _, d in hits}, {0.4})
        self.assertTrue(all(abs(b) <= om.ULTRASONIC_HALF_BEAM_DEG for b, _ in hits))
        before = mapper.log_odds.copy()
        self.assertFalse(mapper.update_scan([(0.0, 30.0)], vis_pitch=0.2))
        np.testing.assert_array_equal(mapper.log_odds, before)

    def test_probability_map_image(self):
        mapper = om.OccupancyMapper2D()
        img = mapper.get_probability_map()
        self.assertEqual((img.shape, img.dtype), ((200, 200, 3), np.uint8))
        self.assertEqual(tuple(img[0, 0]), (127, 127, 127))          # unknown
        self.assertEqual(tuple(img[100 - 3, 100]), (240, 140, 30))   # heading points up


if __name__ == "__main__":
    unittest.main()

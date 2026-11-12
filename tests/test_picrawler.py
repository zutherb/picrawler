import contextlib
import io
import unittest
from unittest import mock

from tests import fake_robot_hat

fake_robot_hat.install()

from picrawler import Picrawler  # noqa: E402


def make_crawler():
    with mock.patch("time.sleep"):
        return Picrawler()


class PicrawlerTest(unittest.TestCase):

    def test_do_step_sends_twelve_angles_and_returns_them(self):
        crawler = make_crawler()
        angles = crawler.do_step([[45, 45, -50]] * 4, 60)
        self.assertEqual(len(angles), 12)
        self.assertEqual(crawler.moves[-1], (angles, 60))
        self.assertEqual(crawler.current_step_all_leg_value(), [[45, 45, -50]] * 4)

    def test_named_step(self):
        crawler = make_crawler()
        crawler.do_step("stand", 40)
        self.assertEqual(len(crawler.moves), len(crawler.step_list["stand"]))

    def test_do_action_alternates_stand_position(self):
        crawler = make_crawler()
        crawler.do_action("forward", 1, 70)
        self.assertEqual(crawler.stand_position, 1)
        crawler.do_action("forward", 1, 70)
        self.assertEqual(crawler.stand_position, 0)
        crawler.do_action("wave", 1, 70)
        self.assertEqual(crawler.stand_position, 0)

    def test_unknown_action_does_not_move(self):
        crawler = make_crawler()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            crawler.do_action("__import__('os').system('true')")
        self.assertEqual(crawler.moves, [])
        self.assertIn("No such action", out.getvalue())

    def test_custom_action(self):
        crawler = make_crawler()
        crawler.add_action("hop", [[[50, 50, -50]] * 4, [[50, 50, -40]] * 4])
        crawler.do_action("hop", 2, 30)
        self.assertEqual(len(crawler.moves), 4)

    def test_out_of_range_is_clamped_or_raises(self):
        crawler = make_crawler()
        crawler.do_step([[45, 45, 120]] * 4, 50)
        self.assertEqual(len(crawler.moves), 1)
        with self.assertRaises(ValueError):
            crawler.do_step([[45, 45, 120]] * 4, 50, israise=True)

    def test_state_getters_return_copies(self):
        crawler = make_crawler()
        crawler.do_step("sit", 40)
        sit_frames = repr(crawler.step_list["sit"])
        coords = crawler.current_step_all_leg_value()
        coords[0][0] += 10
        crawler.do_single_leg(1, [60, 10, -40], 70)
        self.assertEqual(repr(crawler.step_list["sit"]), sit_frames)
        self.assertNotEqual(crawler.current_step_leg_value(0), coords[0])
        self.assertEqual(crawler.current_step_leg_value(1), [60, 10, -40])

    def test_cali_helper_web(self):
        crawler = make_crawler()
        crawler.cali_helper_web(1, "up", 0)
        self.assertAlmostEqual(crawler.current_step_leg_value(0)[1], 0.2)
        crawler.cali_helper_web(1, "up", 1)
        self.assertEqual(crawler.current_step_leg_value(0), [60, 0, -30])
        self.assertNotEqual(crawler.offset[0:3], [0, 0, 0])


if __name__ == "__main__":
    unittest.main()

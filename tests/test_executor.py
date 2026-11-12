import threading
import unittest
from unittest import mock

from tests import fake_robot_hat

fake_robot_hat.install()

from picrawler import Picrawler  # noqa: E402
from picrawler.executor import Command, MotionExecutor, Priority  # noqa: E402


class GatedCrawler:
    """Wraps a Picrawler so each frame blocks until the test releases it."""

    def __init__(self):
        with mock.patch("time.sleep"):
            self.real = Picrawler()
        self.step_list = self.real.step_list
        self.frames = []
        self.entered = threading.Semaphore(0)
        self.release = threading.Semaphore(0)
        self.fail = False

    def iter_action(self, name, steps):
        return self.real.iter_action(name, steps)

    def do_step(self, frame, speed=50):
        self.entered.release()
        self.release.acquire()
        if self.fail:
            raise RuntimeError("servo bus error")
        self.frames.append((frame, speed))
        return self.real.do_step(frame, speed)

    def current_step_all_leg_value(self):
        return self.real.current_step_all_leg_value()

    def wait_frame(self):
        assert self.entered.acquire(timeout=2), "no frame started"

    def run_frames(self, n):
        for _ in range(n):
            self.release.release()


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class ExecutorTest(unittest.TestCase):

    def setUp(self):
        self.crawler = GatedCrawler()
        self.clock = FakeClock()
        self.states = []
        self.ex = MotionExecutor(self.crawler, on_state=self.states.append, queue_max=3,
                                 lockout_s=1.0, clock=self.clock)
        quiet = mock.patch("picrawler.executor.traceback.print_exc")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.ex.start()

    def tearDown(self):
        self.crawler.run_frames(100)
        self.ex.shutdown(sit=False, timeout=2)

    def finish(self):
        self.crawler.run_frames(100)
        self.assertTrue(self.ex.wait_idle(2))

    def test_runs_all_frames_of_an_action_in_order(self):
        expected = list(self.make_reference().iter_action("forward", 2))
        self.assertTrue(self.ex.submit(Command("action", "forward", steps=2, speed=70, id="a")))
        self.finish()
        self.assertEqual([f for f, _ in self.crawler.frames], expected)
        self.assertEqual({s for _, s in self.crawler.frames}, {70})
        self.assertEqual(self.ex.state.status, "idle")
        self.assertIn("running", [s.status for s in self.states])

    def test_same_priority_is_queued(self):
        self.ex.submit(Command("frame", frame=[[50, 50, -50]] * 4, id="1"))
        self.ex.submit(Command("frame", frame=[[60, 40, -50]] * 4, id="2"))
        self.finish()
        self.assertEqual([f for f, _ in self.crawler.frames], [[[50, 50, -50]] * 4, [[60, 40, -50]] * 4])

    def test_queue_limit(self):
        self.ex.submit(Command("action", "forward", id="running"))
        self.crawler.wait_frame()
        results = [self.ex.submit(Command("action", "forward", id=str(i))) for i in range(4)]
        self.assertEqual(results, [True, True, True, False])

    def test_higher_priority_preempts_between_frames_and_drops_lower(self):
        self.ex.submit(Command("action", "forward", steps=3, id="goal"))
        self.ex.submit(Command("action", "backward", id="goal2"))
        self.crawler.wait_frame()
        self.assertTrue(self.ex.submit(Command("action", "wave", priority=Priority.REFLEX, id="reflex")))
        self.crawler.run_frames(1)           # finish the frame that was in progress
        self.crawler.wait_frame()            # next frame already belongs to the reflex
        self.assertEqual(self.ex.state.command_id, "reflex")
        self.finish()
        ran = [s.command_id for s in self.states if s.status == "running"]
        self.assertEqual(sorted(set(ran)), ["goal", "reflex"])
        self.assertNotIn("goal2", [s.command_id for s in self.states])

    def test_stop_aborts_and_locks_out_lower_priorities(self):
        self.ex.submit(Command("action", "forward", steps=5, id="goal"))
        self.crawler.wait_frame()
        self.ex.stop()
        self.crawler.run_frames(1)
        self.assertTrue(self.ex.wait_idle(2))
        self.assertEqual(len(self.crawler.frames), 1)
        self.assertIn("stopping", [s.status for s in self.states])

        self.assertFalse(self.ex.submit(Command("action", "forward", id="too-early")))
        self.assertFalse(self.ex.submit(Command("action", "wave", priority=Priority.REFLEX)))
        self.clock.t += 1.1
        self.assertTrue(self.ex.submit(Command("action", "forward", id="later")))

    def test_lower_priority_rejected_while_higher_runs(self):
        self.ex.submit(Command("action", "wave", priority=Priority.REFLEX))
        self.crawler.wait_frame()
        self.assertFalse(self.ex.submit(Command("action", "forward")))

    def test_stop_with_sit(self):
        self.ex.stop(sit=True)
        self.finish()
        self.assertEqual([f for f, _ in self.crawler.frames], self.crawler.step_list["sit"])

    def test_error_is_reported_and_worker_survives(self):
        self.crawler.fail = True
        self.ex.submit(Command("action", "forward", id="bad"))
        self.ex.submit(Command("action", "forward", id="dropped"))
        self.crawler.run_frames(1)
        self.assertTrue(self.ex.wait_idle(2))
        errors = [s for s in self.states if s.status == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].command_id, "bad")
        self.assertIn("servo bus error", errors[0].error)

        self.crawler.fail = False
        self.assertTrue(self.ex.submit(Command("frame", frame=[[50, 50, -50]] * 4)))
        self.finish()
        self.assertEqual(len(self.crawler.frames), 1)

    def test_unknown_action_is_an_error_not_a_crash(self):
        self.ex.submit(Command("action", "__import__", id="x"))
        self.assertTrue(self.ex.wait_idle(2))
        self.assertTrue(any(s.status == "error" and "KeyError" in s.error for s in self.states))

    def test_shutdown_sits_and_reports_offline(self):
        self.ex.submit(Command("action", "forward", steps=5))
        self.crawler.wait_frame()
        self.crawler.run_frames(100)
        self.ex.shutdown(sit=True, timeout=2)
        self.assertEqual(self.crawler.frames[-len(self.crawler.step_list["sit"]):],
                         [(f, 40) for f in self.crawler.step_list["sit"]])
        self.assertEqual(self.ex.state.status, "offline")
        self.assertFalse(self.ex.submit(Command("action", "forward")))

    def test_legs_and_finished_command_reported(self):
        self.ex.submit(Command("frame", frame=[[50, 50, -50]] * 4, id="f"))
        self.finish()
        self.assertEqual(self.ex.state.legs, [[50, 50, -50]] * 4)
        self.assertEqual((self.ex.state.status, self.ex.state.command_id), ("idle", "f"))

    @staticmethod
    def make_reference():
        with mock.patch("time.sleep"):
            return Picrawler()


class IterActionTest(unittest.TestCase):

    def setUp(self):
        with mock.patch("time.sleep"):
            self.a = Picrawler()
            self.b = Picrawler()

    def test_do_action_matches_iter_action(self):
        frames = list(self.a.iter_action("forward", 3))
        self.b.do_action("forward", 3, 60)
        self.assertEqual(len(self.b.moves), len(frames))
        self.assertEqual(self.a.stand_position, self.b.stand_position)

    def test_unknown_name_raises_immediately(self):
        with self.assertRaises(KeyError):
            self.a.iter_action("_private")
        with self.assertRaises(KeyError):
            self.a.iter_action("nope")


if __name__ == "__main__":
    unittest.main()

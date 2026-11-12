"""In-memory stand-in for robot_hat so the framework runs off-hardware."""
import sys
import types


class FakeRobot:
    def __init__(self, pin_list, db=None, name=None, init_angles=None, **kwargs):
        self.pin_list = pin_list
        self.offset = [0] * len(pin_list)
        self.servo_positions = [0] * len(pin_list)
        self.moves = []

    def servo_move(self, targets, speed=50, bpm=None):
        self.moves.append((list(targets), speed))
        self.servo_positions = list(targets)

    def set_offset(self, offset):
        self.offset = list(offset)


def install():
    module = types.ModuleType("robot_hat")
    module.Robot = FakeRobot
    module.utils = types.SimpleNamespace(reset_mcu=lambda: None)
    sys.modules["robot_hat"] = module

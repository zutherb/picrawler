"""Read a gamepad via evdev and turn it into normalized state.

Kept separate from __main__ so the normalization is testable without evdev.
"""
import time


def normalize(value, lo, hi, deadzone):
    """Map a raw axis value to -1..1 with a dead zone around the center."""
    if hi <= lo:
        return 0.0
    v = (2.0 * (value - lo) / (hi - lo)) - 1.0
    v = max(-1.0, min(1.0, v))
    if abs(v) < deadzone:
        return 0.0
    # rescale so the output starts at 0 right outside the dead zone
    return round((abs(v) - deadzone) / (1.0 - deadzone) * (1 if v > 0 else -1), 3)


class PadState:
    """Accumulates evdev events into {x, y, buttons}."""

    def __init__(self, axis_x, axis_y, deadzone=0.15):
        self.axis_x = axis_x
        self.axis_y = axis_y
        self.deadzone = deadzone
        self.ranges = {}            # axis code -> (min, max)
        self.x = 0.0
        self.y = 0.0
        self.buttons = set()

    def set_range(self, code, lo, hi):
        self.ranges[code] = (lo, hi)

    def on_axis(self, code, value):
        if code not in (self.axis_x, self.axis_y) or code not in self.ranges:
            return False
        v = normalize(value, *self.ranges[code], self.deadzone)
        if code == self.axis_x:
            changed, self.x = v != self.x, v
        else:
            changed, self.y = v != self.y, v
        return changed

    def on_button(self, name, pressed):
        before = set(self.buttons)
        if pressed:
            self.buttons.add(name)
        else:
            self.buttons.discard(name)
        return before != self.buttons

    def payload(self):
        return {"x": self.x, "y": self.y, "buttons": sorted(self.buttons), "ts": time.time()}


class FakePad:
    """Walks in a square and presses a button now and then (for --fake)."""

    name = "Fake gamepad"

    def __init__(self):
        self._t0 = time.monotonic()

    def read(self):
        t = (time.monotonic() - self._t0) % 16
        state = {"x": 0.0, "y": 0.0, "buttons": []}
        if t < 4:
            state["y"] = -1.0
        elif t < 6:
            state["x"] = 1.0
        elif 10 < t < 10.3:
            state["buttons"] = ["BTN_NORTH"]
        return state

"""The agent's view of the robot and the blocking actions its tools use.

MQTT messages arrive on the network thread (on_message); tools run on the
agent's worker thread and wait for the body to finish a command. Movement
goes to the arbiter as agent/goal; it decides when (and whether) the body
gets it, so manual control, the obstacle stop and the body's lockout win.
Stop goes straight to the body as cmd/stop.
"""
import threading
import time
import uuid

ACTION_TIMEOUT_S = 30.0
STALE_S = 2.0


def new_id():
    return uuid.uuid4().hex[:16]


def position(box):
    """Normalized box -> 'left' / 'center' / 'right' and a rough distance word."""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    side = "left" if cx < 0.4 else "right" if cx > 0.6 else "center"
    height = y1 - y0
    near = "close" if height > 0.5 else "far away" if height < 0.15 else "at medium distance"
    return side, near


class World:

    def __init__(self, publish, wall=time.time, mono=time.monotonic):
        self.publish = publish            # fn(suffix, payload, qos, retain)
        self._wall = wall
        self._mono = mono
        self._lock = threading.Lock()
        self._waiters = {}                # command id -> [Event, result]
        self.detections = []
        self.detections_at = None
        self.distance_cm = None
        self.distance_at = None
        self.body_online = False
        self.arbiter_online = False
        self.motion = {}

    # --- incoming (network thread) ---

    def on_message(self, suffix, payload, retain=False):
        if not isinstance(payload, dict):
            return
        if suffix == "status":
            self.body_online = payload.get("status") == "online"
        elif suffix == "arbiter/status":
            self.arbiter_online = payload.get("status") == "online"
        elif suffix == "state/motion":
            self.motion = payload
            if payload.get("status") in ("idle", "error"):
                self._resolve(payload.get("command_id"), "error" if payload.get("status") == "error" else "done",
                              payload.get("error"))
        elif suffix in ("event/rejected", "arbiter/rejected"):
            self._resolve(payload.get("id"), "rejected", payload.get("reason"))
        elif suffix == "event/stop":
            # a stop aborts whatever we were waiting for
            with self._lock:
                waiting = list(self._waiters)
            for cmd_id in waiting:
                self._resolve(cmd_id, "stopped", payload.get("reason"))
        elif suffix == "sensor/vision/detections" and not retain:
            objects = payload.get("objects")
            self.detections = objects if isinstance(objects, list) else []
            self.detections_at = self._mono()
        elif suffix == "sensor/ultrasonic/distance" and not retain:
            cm = payload.get("cm")
            if isinstance(cm, (int, float)) and not isinstance(cm, bool):
                self.distance_cm, self.distance_at = cm, self._mono()

    def _resolve(self, cmd_id, outcome, detail):
        with self._lock:
            waiter = self._waiters.get(cmd_id)
        if waiter is not None:
            waiter[1] = (outcome, detail)
            waiter[0].set()

    # --- actions (worker thread) ---

    def act(self, name, steps=1, speed=60, timeout=ACTION_TIMEOUT_S):
        """Ask the arbiter for an action and wait for it. Returns (outcome, detail)."""
        if not self.body_online:
            return "failed", "the body service is offline"
        if not self.arbiter_online:
            return "failed", "the arbiter is offline"
        cmd_id = new_id()
        waiter = [threading.Event(), ("timeout", None)]
        with self._lock:
            self._waiters[cmd_id] = waiter
        try:
            self.publish("agent/goal", {"id": cmd_id, "name": name, "steps": steps, "speed": speed,
                                        "ts": self._wall()}, 1, False)
            waiter[0].wait(timeout)
            return waiter[1]
        finally:
            with self._lock:
                self._waiters.pop(cmd_id, None)

    def stop(self, reason, sit=False):
        self.publish("cmd/stop", {"id": new_id(), "sit": sit, "reason": reason[:64], "ts": self._wall()}, 1, False)

    # --- perception ---

    def fresh_detections(self):
        if self.detections_at is None or self._mono() - self.detections_at > STALE_S:
            return None
        return self.detections

    def fresh_distance(self):
        if self.distance_at is None or self._mono() - self.distance_at > STALE_S:
            return None
        return self.distance_cm

    def describe(self):
        """Short text of what the camera sees right now, for the model."""
        objects = self.fresh_detections()
        if objects is None:
            return "The camera reports nothing (vision service offline or detection off)."
        if not objects:
            return "Nothing recognized in view."
        parts = []
        for o in objects[:5]:
            box = o.get("box")
            if not (isinstance(box, list) and len(box) == 4):
                continue
            side, near = position(box)
            parts.append("%s on the %s, %s (score %.2f)" % (o.get("label", "?"), side, near, o.get("score", 0)))
        return "I see: " + "; ".join(parts) + "." if parts else "Nothing recognized in view."

    def find(self, label):
        """Best fresh detection with this label, or None."""
        for o in self.fresh_detections() or []:
            if o.get("label") == label:
                return o
        return None

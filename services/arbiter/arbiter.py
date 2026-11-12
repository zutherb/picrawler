"""Turn sensor facts into commands (docs/target-architecture.md, section 6).

Transport-agnostic like the body adapter: feed messages to on_message(),
call tick() periodically, outgoing messages go through publish().
"""
import collections
import operator
import re
import statistics
import time
import uuid

from . import scan as localmap

HEARTBEAT_S = 0.25
STALE_S = 1.0
PENDING_TIMEOUT_S = 5.0
RETRY_AFTER_REJECT_S = 0.5
GOAL_WAIT_S = 5.0                 # an agent goal not started by then is rejected

OPERATORS = {
    "<": operator.lt, "<=": operator.le, ">": operator.gt,
    ">=": operator.ge, "==": operator.eq, "!=": operator.ne,
}
CONDITION = re.compile(r"^\s*([A-Za-z_]\w*)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$")
RULE_PRIORITIES = ("goal", "reflex", "safety")


def new_id():
    return uuid.uuid4().hex[:16]


def parse_condition(text):
    """'cm < 15' -> (field, compare, value). No eval, only this one form."""
    m = CONDITION.match(text or "")
    if not m:
        raise ValueError("condition must look like 'field < number': %r" % (text,))
    return m.group(1), OPERATORS[m.group(2)], float(m.group(3))


def sensor_of(suffix):
    parts = suffix.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "sensor" else None


class Rule:

    def __init__(self, spec):
        self.name = spec["name"]
        self.when = spec["when"]
        self.sensor = sensor_of(self.when)
        if self.sensor is None:
            raise ValueError("rule %s: 'when' must be a sensor topic" % self.name)
        self.field, self.compare, self.value = parse_condition(spec["condition"])
        self.then = dict(spec["then"])
        if self.then.get("kind") not in ("stop", "action"):
            raise ValueError("rule %s: then.kind must be stop or action" % self.name)
        self.priority = spec.get("priority", "goal")
        if self.priority not in RULE_PRIORITIES:
            raise ValueError("rule %s: unknown priority %r" % (self.name, self.priority))
        if self.priority == "safety" and self.then["kind"] != "stop":
            raise ValueError("rule %s: only stop can have priority safety" % self.name)
        self.cooldown_s = spec.get("cooldown_s", 1.0)
        # Only fire while one of these actions is running, e.g. ["forward"]
        # for the obstacle stop, so the crawler can still turn or back away.
        self.when_motion = tuple(spec.get("when_motion", ()))
        self.window = collections.deque(maxlen=spec.get("median_of", 3))
        self.last_fired = None

    def motion_matches(self, motion):
        if not self.when_motion:
            return True
        return motion.get("status") == "running" and motion.get("name") in self.when_motion

    def feed(self, payload):
        """Add a reading; True if the debounced condition holds."""
        value = payload.get(self.field) if isinstance(payload, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        self.window.append(value)
        if len(self.window) < self.window.maxlen:
            return False
        return self.compare(statistics.median(self.window), self.value)

    def reset(self):
        self.window.clear()


class Arbiter:

    def __init__(self, publish, cfg=None, wall=time.time, mono=time.monotonic):
        cfg = cfg or {}
        self.publish = publish            # fn(suffix, payload, qos, retain)
        self.rules = [Rule(spec) for spec in cfg.get("rule", [])]
        self._wall = wall
        self._mono = mono
        self._last_heartbeat = None
        self._sensor_seen = {}            # sensor -> mono time of last reading
        self._sensor_offline = set()
        self.body_online = False
        self.agent_busy = False           # the language model agent is acting
        self.motion = {}                  # last state/motion payload
        # Order is precedence: manual control, a requested scan, the agent, wander.
        self.manual = Manual(self, cfg.get("manual", {}))
        self.scan = Scan(self, cfg.get("scan", {}))
        self.agent = AgentGoals(self)
        self.wander = Wander(self, cfg.get("wander", {}))
        self.behaviors = (self.manual, self.scan, self.agent, self.wander)

    # --- incoming ---

    def on_message(self, suffix, payload, retain=False):
        if suffix == "status":
            self.body_online = isinstance(payload, dict) and payload.get("status") == "online"
            return
        if suffix == "state/motion":
            if isinstance(payload, dict):
                self.motion = payload
                for behavior in self.behaviors:
                    behavior.on_motion(payload)
            return
        if suffix == "event/rejected":
            if isinstance(payload, dict):
                for behavior in self.behaviors:
                    behavior.on_rejected(payload)
            return
        if suffix == "agent/status":
            self.agent_busy = isinstance(payload, dict) and payload.get("status") == "online" and bool(payload.get("busy"))
            return
        if suffix == AgentGoals.topic:
            if not retain:
                self.agent.on_goal(payload)
            return
        if suffix == Scan.request_topic:
            if not retain:
                self.scan.request(payload)
            return
        if suffix == self.manual.remote_topic:
            if not retain:
                self.manual.on_reading(suffix, payload)
            return
        sensor = sensor_of(suffix)
        if sensor is None:
            return
        if suffix == "sensor/%s/status" % sensor:
            self._on_sensor_status(sensor, payload)
            return
        if retain:
            return  # an old reading is not a fact about now
        self._sensor_seen[sensor] = self._mono()
        for rule in self.rules:
            if rule.when == suffix and rule.feed(payload):
                self._fire(rule)
        for behavior in self.behaviors:
            behavior.on_reading(suffix, payload)

    def _on_sensor_status(self, sensor, payload):
        online = isinstance(payload, dict) and payload.get("status") == "online"
        if online:
            self._sensor_offline.discard(sensor)
            return
        self._sensor_offline.add(sensor)
        for rule in self.rules:
            if rule.sensor == sensor:
                rule.reset()

    def sensor_fresh(self, sensor):
        seen = self._sensor_seen.get(sensor)
        return (sensor not in self._sensor_offline and seen is not None
                and self._mono() - seen <= STALE_S)

    def body_idle(self):
        return self.motion.get("status", "idle") == "idle" and not self.motion.get("queue_len", 0)

    # --- rules ---

    def _fire(self, rule):
        now = self._mono()
        if not rule.motion_matches(self.motion):
            return
        if rule.last_fired is not None and now - rule.last_fired < rule.cooldown_s:
            return
        rule.last_fired = now
        then = rule.then
        if then["kind"] == "stop":
            self.send_stop(reason=then.get("reason", rule.name), sit=bool(then.get("sit", False)))
        else:
            self.send_action(then["name"], steps=then.get("steps", 1), speed=then.get("speed", 50),
                             priority=rule.priority)

    # --- outgoing ---

    def send_stop(self, reason, sit=False):
        self.publish("cmd/stop", {"id": new_id(), "sit": sit, "reason": reason, "ts": self._wall()}, 1, False)

    def send_action(self, name, steps=1, speed=50, priority="goal", cmd_id=None):
        cmd_id = cmd_id or new_id()
        self.publish("cmd/action", {"id": cmd_id, "name": name, "steps": steps, "speed": speed,
                                    "priority": priority, "ts": self._wall()}, 1, False)
        return cmd_id

    # --- periodic ---

    def tick(self):
        now = self._mono()
        if self._last_heartbeat is None or now - self._last_heartbeat >= HEARTBEAT_S:
            self._last_heartbeat = now
            self.publish("cmd/heartbeat", {"ts": self._wall()}, 0, False)
        for rule in self.rules:
            if not self.sensor_fresh(rule.sensor):
                rule.reset()
        for behavior in self.behaviors:
            behavior.tick()


class Behavior:
    """A goal source that keeps at most one command in flight."""

    def __init__(self, arbiter):
        self.arbiter = arbiter
        self.pending = None               # (command id, mono time sent)
        self.next_send = 0.0

    def on_reading(self, suffix, payload):
        pass

    def on_motion(self, payload):
        if self.pending and payload.get("command_id") == self.pending[0] \
                and payload.get("status") in ("idle", "error"):
            self.pending = None

    def on_rejected(self, payload):
        # e.g. still locked out after a safety stop: back off a little
        if self.pending and payload.get("id") == self.pending[0]:
            self.pending = None
            self.next_send = self.arbiter._mono() + RETRY_AFTER_REJECT_S

    def ready(self):
        """True if a new command may be sent now."""
        a = self.arbiter
        now = a._mono()
        if not a.body_online or now < self.next_send:
            return False
        if self.pending:
            if now - self.pending[1] < PENDING_TIMEOUT_S:
                return False
            self.pending = None
        return a.body_idle()

    def send(self, name, speed, steps=1, cmd_id=None):
        a = self.arbiter
        self.pending = (a.send_action(name, steps=steps, speed=speed, priority="goal", cmd_id=cmd_id), a._mono())

    def tick(self):
        pass


class Manual(Behavior):
    """Manual control: the Bluetooth gamepad and the web remote.

    Both publish stick facts ({"x", "y"} in -1..1, up is negative): the pad
    on sensor/gamepad/state with button names, the web remote on
    remote/state with an optional one-shot "action" and a "speed". The
    freshest source drives; a source that has been silent for INPUT_STALE_S
    counts as centered (dead man: a closed browser tab stops the robot after
    the current step). Active while used within hold_s; wander pauses
    meanwhile. Commands are plain goals, so the safety stop still wins.
    """

    DEFAULT_BUTTONS = {"BTN_SOUTH": "stand", "BTN_EAST": "sit", "BTN_NORTH": "wave"}
    REMOTE_ACTIONS = ("stand", "sit", "wave", "dance", "push up", "look left", "look right", "look up", "look down")
    INPUT_STALE_S = 1.0

    def __init__(self, arbiter, cfg):
        super().__init__(arbiter)
        self.enabled = cfg.get("enabled", True)
        self.topic = cfg.get("topic", "sensor/gamepad/state")
        self.remote_topic = cfg.get("remote_topic", "remote/state")
        self.speed = cfg.get("speed", 70)
        self.threshold = cfg.get("threshold", 0.5)
        self.hold_s = cfg.get("hold_s", 3.0)
        self.buttons = dict(cfg.get("buttons", self.DEFAULT_BUTTONS))
        self.stop_button = cfg.get("stop_button", "BTN_SELECT")
        self.inputs = {}                  # topic -> (x, y, speed or None, mono time)
        self.pressed = set()
        self.queued_button = None
        self.last_input = None

    def _fresh_input(self):
        now = self.arbiter._mono()
        fresh = [i for i in self.inputs.values() if now - i[3] <= self.INPUT_STALE_S]
        return max(fresh, key=lambda i: i[3]) if fresh else None

    @property
    def stick(self):
        current = self._fresh_input()
        return (current[0], current[1]) if current else (0.0, 0.0)

    def active(self):
        return (self.enabled and self.last_input is not None and self._fresh_input() is not None
                and self.arbiter._mono() - self.last_input <= self.hold_s)

    def on_reading(self, suffix, payload):
        if not self.enabled or suffix not in (self.topic, self.remote_topic) or not isinstance(payload, dict):
            return
        x, y = payload.get("x", 0.0), payload.get("y", 0.0)
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
            return
        x, y = max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y))
        now = self.arbiter._mono()
        speed = payload.get("speed")
        speed = int(speed) if isinstance(speed, int) and not isinstance(speed, bool) and 1 <= speed <= 100 else None
        self.inputs[suffix] = (x, y, speed, now)
        moving = max(abs(x), abs(y)) >= self.threshold
        if suffix == self.remote_topic:
            action = payload.get("action")
            if action in self.REMOTE_ACTIONS:
                self.queued_button = action
            if moving or action:
                self.last_input = now
            return
        buttons = payload.get("buttons", [])
        pressed = {b for b in buttons if isinstance(b, str)} if isinstance(buttons, list) else set()
        new = pressed - self.pressed
        self.pressed = pressed
        if moving or pressed:
            self.last_input = now
        if self.stop_button in new:
            self.queued_button = None
            self.arbiter.send_stop(reason="gamepad")
            return
        for button in sorted(new):
            if button in self.buttons:
                self.queued_button = self.buttons[button]

    def stick_action(self):
        x, y = self.stick
        if max(abs(x), abs(y)) < self.threshold:
            return None
        if abs(y) >= abs(x):
            return "forward" if y < 0 else "backward"   # evdev: up is negative
        return "turn right" if x > 0 else "turn left"

    def tick(self):
        if not self.active() or not self.ready():
            return
        name = self.queued_button or self.stick_action()
        if name is None:
            return
        self.queued_button = None
        current = self._fresh_input()
        self.send(name, current[2] if current and current[2] else self.speed)


class Scan(Behavior):
    """Look around: turn on the spot and measure after every step.

    Requested on map/scan (web UI). Publishes the growing map, retained,
    on map/local after every reading (see scan.py). Waits settle_s after
    each turn so the body stands still, then takes the median of
    `readings` ultrasonic values. Manual control, a rejected turn or a
    lost body or sensor aborts it; agent goals and wander wait meanwhile.
    """

    request_topic = "map/scan"
    map_topic = "map/local"
    READING_TIMEOUT_S = 2.0

    def __init__(self, arbiter, cfg):
        super().__init__(arbiter)
        self.turns = cfg.get("turns", 16)
        self.turn_deg = cfg.get("turn_deg", 22.5)        # calibrate: steps for a full turn
        self.turn = cfg.get("turn", "turn left")
        self.speed = cfg.get("speed", 60)
        self.settle_s = cfg.get("settle_s", 0.4)
        self.readings_needed = cfg.get("readings", 3)
        self.topic = cfg.get("topic", "sensor/ultrasonic/distance")
        self.sensor = sensor_of(self.topic)
        self.grid_cfg = {k: cfg[k] for k in ("cell_cm", "radius_cm", "beam_deg", "max_cm") if k in cfg}
        self.phase = None                 # None | "measure" | "turn"
        self.samples = []
        self.readings = []
        self.measure_from = 0.0
        self.sent = False
        self.requested_by = None

    def active(self):
        return self.phase is not None

    def request(self, payload):
        if self.active():
            return
        a = self.arbiter
        user = payload.get("user") if isinstance(payload, dict) else None
        self.requested_by = user if isinstance(user, str) else None
        if a.manual.active():
            self._publish("refused", "manual control active")
            return
        if not a.body_online:
            self._publish("refused", "body offline")
            return
        if not a.sensor_fresh(self.sensor):
            self._publish("refused", "no ultrasonic readings")
            return
        self.samples = []
        self._measure()
        self._publish("scanning")

    def _measure(self):
        self.phase = "measure"
        self.readings = []
        self.measure_from = self.arbiter._mono() + self.settle_s

    def on_reading(self, suffix, payload):
        if self.phase != "measure" or suffix != self.topic or self.arbiter._mono() < self.measure_from:
            return
        cm = payload.get("cm") if isinstance(payload, dict) else None
        if isinstance(cm, (int, float)) and not isinstance(cm, bool):
            self.readings.append(float(cm))

    def on_rejected(self, payload):
        if self.pending and payload.get("id") == self.pending[0]:
            self.pending = None
            self._abort("body refused the turn: %s" % payload.get("reason", "?"))

    def _abort(self, reason):
        self.phase = None
        self.pending = None
        self._publish("aborted", reason)

    def tick(self):
        if not self.active():
            return
        a = self.arbiter
        now = a._mono()
        if a.manual.active():
            self._abort("manual control")
            return
        if not a.body_online:
            self._abort("body offline")
            return
        if self.phase == "turn":
            if not self.sent:
                if self.ready():
                    self.send(self.turn, self.speed)
                    self.sent = True
            elif self.pending is None and a.body_idle():
                self._measure()
            return
        # measure: the body has to stand still for settle_s first
        if not a.body_idle():
            self.measure_from = now + self.settle_s
            self.readings = []
            return
        if len(self.readings) >= self.readings_needed:
            cm = statistics.median(self.readings)
        elif now - self.measure_from > self.READING_TIMEOUT_S:
            if not a.sensor_fresh(self.sensor):
                self._abort("ultrasonic sensor stopped")
                return
            cm = None                     # sensor alive but no echo: nothing in range
        else:
            return
        self.samples.append((round(len(self.samples) * self.turn_deg, 1), None if cm is None else round(cm, 1)))
        if len(self.samples) >= self.turns:
            self.phase = None
            self._publish("done")
            return
        self._publish("scanning")
        self.phase = "turn"
        self.sent = False

    def _publish(self, status, message=None):
        payload = localmap.map_payload(self.samples, status, self.turns, self.turn_deg, self.arbiter._wall(),
                                       message=message, user=self.requested_by, **self.grid_cfg)
        self.arbiter.publish(self.map_topic, payload, 1, True)


class AgentGoals(Behavior):
    """Goals from the language model agent (agent/goal).

    The agent asks, the arbiter decides: a goal waits until the body is
    idle and is passed on as cmd/action with the agent's id, so the agent
    sees the body's state/motion and event/rejected for it. Manual control
    wins: while it is active, goals are refused on arbiter/rejected, as are
    goals that could not start within GOAL_WAIT_S. One goal at a time; a
    newer one replaces a waiting one.
    """

    topic = "agent/goal"

    def __init__(self, arbiter):
        super().__init__(arbiter)
        self.waiting = None               # (goal dict, mono time received)

    def on_goal(self, payload):
        goal = self._valid(payload)
        if goal is None:
            cmd_id = payload.get("id") if isinstance(payload, dict) else None
            self.reject(cmd_id if isinstance(cmd_id, str) else None, "invalid goal")
            return
        if self.waiting:
            self.reject(self.waiting[0]["id"], "replaced by a newer goal")
        self.waiting = (goal, self.arbiter._mono())

    @staticmethod
    def _valid(payload):
        if not isinstance(payload, dict):
            return None
        cmd_id, name = payload.get("id"), payload.get("name")
        steps, speed = payload.get("steps", 1), payload.get("speed", 50)
        if not (isinstance(cmd_id, str) and 0 < len(cmd_id) <= 64 and isinstance(name, str) and name):
            return None
        for value, hi in ((steps, 10), (speed, 100)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= hi:
                return None
        return {"id": cmd_id, "name": name, "steps": steps, "speed": speed}

    def active(self):
        return self.waiting is not None or self.pending is not None

    def reject(self, cmd_id, reason):
        self.arbiter.publish("arbiter/rejected", {"id": cmd_id, "reason": reason, "ts": self.arbiter._wall()}, 1, False)

    def tick(self):
        if not self.waiting:
            return
        goal, received = self.waiting
        if self.arbiter.manual.active() or self.arbiter.scan.active():
            self.waiting = None
            self.reject(goal["id"], "manual control active" if self.arbiter.manual.active() else "scan in progress")
            return
        if self.ready():
            self.waiting = None
            self.send(goal["name"], goal["speed"], steps=goal["steps"], cmd_id=goal["id"])
        elif self.arbiter._mono() - received > GOAL_WAIT_S:
            self.waiting = None
            self.reject(goal["id"], "body busy or offline")


class Wander(Behavior):
    """Optional demo goal: walk forward, turn away from obstacles.

    Off by default. Only acts on fresh distance readings while the body is
    online and idle, one command at a time, and pauses while the gamepad
    or the agent is in use. The safety stop rule still does the actual emergency stop.
    """

    def __init__(self, arbiter, cfg):
        super().__init__(arbiter)
        self.enabled = cfg.get("enabled", False)
        self.topic = cfg.get("topic", "sensor/ultrasonic/distance")
        self.sensor = sensor_of(self.topic)
        self.clear_cm = cfg.get("clear_cm", 25.0)
        self.speed = cfg.get("speed", 70)
        self.turn = cfg.get("turn", "turn left")
        self.distance = None

    def on_reading(self, suffix, payload):
        if suffix == self.topic and isinstance(payload, dict):
            cm = payload.get("cm")
            if isinstance(cm, (int, float)) and not isinstance(cm, bool):
                self.distance = cm

    def tick(self):
        a = self.arbiter
        if not self.enabled or self.distance is None or not a.sensor_fresh(self.sensor):
            return
        if a.manual.active() or a.scan.active() or a.agent_busy or a.agent.active() or not self.ready():
            return
        self.send("forward" if self.distance > self.clear_cm else self.turn, self.speed)

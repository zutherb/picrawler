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

HEARTBEAT_S = 0.25
STALE_S = 1.0
PENDING_TIMEOUT_S = 5.0
RETRY_AFTER_REJECT_S = 0.5

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
        self.window = collections.deque(maxlen=spec.get("median_of", 3))
        self.last_fired = None

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
        self.wander = Wander(self, cfg.get("wander", {}))
        self._wall = wall
        self._mono = mono
        self._last_heartbeat = None
        self._sensor_seen = {}            # sensor -> mono time of last reading
        self._sensor_offline = set()
        self.body_online = False
        self.motion = {}                  # last state/motion payload

    # --- incoming ---

    def on_message(self, suffix, payload, retain=False):
        if suffix == "status":
            self.body_online = isinstance(payload, dict) and payload.get("status") == "online"
            return
        if suffix == "state/motion":
            if isinstance(payload, dict):
                self.motion = payload
                self.wander.on_motion(payload)
            return
        if suffix == "event/rejected":
            if isinstance(payload, dict):
                self.wander.on_rejected(payload)
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
        self.wander.on_reading(suffix, payload)

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

    # --- rules ---

    def _fire(self, rule):
        now = self._mono()
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

    def send_action(self, name, steps=1, speed=50, priority="goal"):
        cmd_id = new_id()
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
        self.wander.tick()


class Wander:
    """Optional demo goal: walk forward, turn away from obstacles.

    Off by default. Only acts on fresh distance readings while the body is
    online and idle, one command at a time. The safety stop rule still
    does the actual emergency stop.
    """

    def __init__(self, arbiter, cfg):
        self.arbiter = arbiter
        self.enabled = cfg.get("enabled", False)
        self.topic = cfg.get("topic", "sensor/ultrasonic/distance")
        self.sensor = sensor_of(self.topic)
        self.clear_cm = cfg.get("clear_cm", 25.0)
        self.speed = cfg.get("speed", 70)
        self.turn = cfg.get("turn", "turn left")
        self.distance = None
        self.pending = None               # (command id, mono time sent)
        self.next_send = 0.0

    def on_reading(self, suffix, payload):
        if suffix == self.topic and isinstance(payload, dict):
            cm = payload.get("cm")
            if isinstance(cm, (int, float)) and not isinstance(cm, bool):
                self.distance = cm

    def on_motion(self, payload):
        if self.pending and payload.get("command_id") == self.pending[0] \
                and payload.get("status") in ("idle", "error"):
            self.pending = None

    def on_rejected(self, payload):
        # e.g. still locked out after a safety stop: back off a little
        if self.pending and payload.get("id") == self.pending[0]:
            self.pending = None
            self.next_send = self.arbiter._mono() + RETRY_AFTER_REJECT_S

    def tick(self):
        a = self.arbiter
        if not self.enabled or not a.body_online or not a.sensor_fresh(self.sensor):
            return
        if self.distance is None:
            return
        now = a._mono()
        if now < self.next_send:
            return
        if self.pending:
            if now - self.pending[1] < PENDING_TIMEOUT_S:
                return
            self.pending = None
        if a.motion.get("status", "idle") != "idle" or a.motion.get("queue_len", 0):
            return
        name = "forward" if self.distance > self.clear_cm else self.turn
        self.pending = (a.send_action(name, steps=1, speed=self.speed, priority="goal"), now)

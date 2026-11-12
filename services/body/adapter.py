"""MQTT <-> MotionExecutor glue, with validation and watchdog.

Transport-agnostic: the caller feeds messages in via on_message() and
calls tick() periodically; outgoing messages go through publish().
See docs/target-architecture.md, sections 4 and 5.
"""
import collections
import statistics
import time

from picrawler.executor import Command, Priority
from services.body.battery import BatteryMonitor
from services.common import schema

PRIORITY = {"goal": Priority.GOAL, "reflex": Priority.REFLEX}
COMMAND_TOPICS = ("cmd/action", "cmd/frame", "cmd/stop", "cmd/heartbeat")


class Interlock:
    """Obstacle interlock in the body itself, independent of the arbiter.

    The arbiter's obstacle rule is the configurable behaviour; this is the
    backstop that needs no other process: below min_cm (median of the last
    readings) a running forward action is stopped and new ones are refused.
    Only fresh readings count; without a sensor nothing is blocked.
    """

    def __init__(self, cfg, mono):
        self.enabled = cfg.get("enabled", True)
        self.topic = cfg.get("topic", "sensor/ultrasonic/distance")
        self.field = cfg.get("field", "cm")
        self.min_cm = cfg.get("min_cm", 15.0)
        self.stale_s = cfg.get("stale_s", 1.0)
        self.actions = tuple(cfg.get("actions", ("forward",)))
        self.window = collections.deque(maxlen=cfg.get("median_of", 3))
        self._mono = mono
        self._last = None

    def feed(self, payload):
        value = payload.get(self.field) if isinstance(payload, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        self.window.append(value)
        self._last = self._mono()

    def distance(self):
        """Median distance if fresh and complete, else None."""
        if self._last is None or self._mono() - self._last > self.stale_s:
            self.window.clear()
            return None
        if len(self.window) < self.window.maxlen:
            return None
        return statistics.median(self.window)

    def blocks(self, name):
        """Distance in cm if this action must not run now, else None."""
        if not self.enabled or name not in self.actions:
            return None
        cm = self.distance()
        return cm if cm is not None and cm < self.min_cm else None


class BodyAdapter:

    def __init__(self, executor, publish, cfg=None, wall=time.time, mono=time.monotonic):
        cfg = cfg or {}
        watchdog = cfg.get("watchdog", {})
        self.executor = executor
        self.publish = publish            # fn(suffix, payload, qos, retain)
        self.limits = schema.Limits.from_config(cfg)
        self.dedupe = schema.Dedupe()
        self.heartbeat_timeout_s = watchdog.get("heartbeat_timeout_s", 1.0)
        self.sit_after_disconnect_s = watchdog.get("sit_after_disconnect_s", 5.0)
        self._wall = wall
        self._mono = mono
        self._last_heartbeat = None
        self._disconnected_since = None
        self._sat_after_disconnect = False
        battery = cfg.get("battery", {})
        self.battery = BatteryMonitor(low_v=battery.get("low_v", 7.0), critical_v=battery.get("critical_v", 6.6),
                                      hold_s=battery.get("hold_s", 20.0))
        self.interlock = Interlock(cfg.get("interlock", {}), mono)
        self._interlock_stopped = None

    @property
    def topics(self):
        """Everything the body subscribes to."""
        return COMMAND_TOPICS + ((self.interlock.topic,) if self.interlock.enabled else ())

    # --- connection ---

    def on_connect(self):
        self._disconnected_since = None
        self._sat_after_disconnect = False
        self.publish("status", {"status": "online", "ts": self._wall()}, 1, True)
        self.on_state(self.executor.state)

    def on_disconnect(self):
        if self._disconnected_since is None:
            self._disconnected_since = self._mono()
            self.executor.stop(Priority.SAFETY)

    # --- incoming ---

    def on_message(self, suffix, payload, retain=False):
        if self.interlock.enabled and suffix == self.interlock.topic:
            if not retain:
                self.interlock.feed(payload)
                self._check_interlock()
            return
        if suffix not in COMMAND_TOPICS:
            return
        if retain:
            self._reject(suffix, payload, "retained command ignored")
            return
        if suffix == "cmd/stop":
            stop = schema.stop(payload)
            self.executor.stop(Priority.SAFETY, sit=stop["sit"])
            self.publish("event/stop", {"reason": stop["reason"], "sit": stop["sit"], "ts": self._wall()}, 1, False)
            return
        if suffix == "cmd/heartbeat":
            self._last_heartbeat = self._mono()
            return

        now = self._wall()
        try:
            if suffix == "cmd/action":
                v = schema.action(payload, now, self.limits, self.dedupe)
                cmd = Command("action", name=v["name"], steps=v["steps"], speed=v["speed"],
                              priority=PRIORITY[v["priority"]], id=v["id"])
            else:
                v = schema.frame(payload, now, self.limits, self.dedupe)
                cmd = Command("frame", frame=v["frame"], speed=v["speed"],
                              priority=PRIORITY[v["priority"]], id=v["id"])
        except schema.Rejected as e:
            self._reject(suffix, payload, str(e))
            return

        if self.battery.level == "critical":
            self._reject(suffix, payload, "battery critical, charge first")
            return
        if cmd.kind == "action":
            cm = self.interlock.blocks(cmd.name)
            if cm is not None:
                self._reject(suffix, payload, "obstacle %.0f cm ahead" % cm)
                return
        if cmd.priority == Priority.GOAL and not self._heartbeat_fresh():
            self._reject(suffix, payload, "no heartbeat from a controller")
            return
        if not self.executor.submit(cmd):
            self._reject(suffix, payload, "busy or locked out by a higher priority")

    def _check_interlock(self):
        state = self.executor.state
        if state.status != "running" or state.name is None:
            return
        cm = self.interlock.blocks(state.name)
        if cm is not None and state.command_id != self._interlock_stopped:
            self._interlock_stopped = state.command_id   # once per command, not per reading
            self.executor.stop(Priority.SAFETY)
            reason = "obstacle %.0f cm (body interlock)" % cm
            self.publish("event/stop", {"reason": reason, "sit": False, "ts": self._wall()}, 1, False)

    # --- periodic ---

    def tick(self):
        now = self._mono()
        if self._disconnected_since is not None:
            if not self._sat_after_disconnect and now - self._disconnected_since >= self.sit_after_disconnect_s:
                self._sat_after_disconnect = True
                self.executor.stop(Priority.SAFETY, sit=True)
            return
        state = self.executor.state
        if state.status == "running" and state.priority == Priority.GOAL and not self._heartbeat_fresh():
            self.executor.stop(Priority.SAFETY)
            self._event("watchdog", "heartbeat lost, stopped")

    def _heartbeat_fresh(self):
        return self._last_heartbeat is not None and self._mono() - self._last_heartbeat <= self.heartbeat_timeout_s

    def on_battery(self, volts):
        """A raw voltage reading (from the battery thread)."""
        changed = self.battery.feed(volts, self._mono())
        state = self.battery.state()
        if not state["present"]:
            return
        self.publish("state/battery", dict(state, ts=self._wall()), 1, True)
        if changed is None:
            return
        self._event("battery", "battery %s at %.2f V" % (changed, state["voltage"]))
        if changed == "critical":
            # Sit down before the cells cut out and the robot collapses.
            self.executor.stop(Priority.SAFETY, sit=True)

    # --- outgoing ---

    def on_state(self, state):
        """Executor callback (worker thread)."""
        self.publish("state/motion", {
            "status": state.status,
            "command_id": state.command_id,
            "name": state.name,
            "priority": state.priority.name.lower() if state.priority is not None else None,
            "queue_len": state.queue_len,
            "error": state.error,
            "ts": self._wall(),
        }, 1, True)
        if state.legs is not None and state.status in ("idle", "error"):
            self.publish("state/legs", {"legs": state.legs, "ts": self._wall()}, 0, True)

    def _reject(self, suffix, payload, reason):
        cmd_id = payload.get("id") if isinstance(payload, dict) else None
        self.publish("event/rejected", {
            "topic": suffix,
            "id": cmd_id if isinstance(cmd_id, str) else None,
            "reason": reason,
            "ts": self._wall(),
        }, 1, False)

    def _event(self, kind, message):
        self.publish("event/" + kind, {"message": message, "ts": self._wall()}, 1, False)

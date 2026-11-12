"""MQTT <-> MotionExecutor glue, with validation and watchdog.

Transport-agnostic: the caller feeds messages in via on_message() and
calls tick() periodically; outgoing messages go through publish().
See docs/target-architecture.md, sections 4 and 5.
"""
import time

from picrawler.executor import Command, Priority
from services.common import schema

PRIORITY = {"goal": Priority.GOAL, "reflex": Priority.REFLEX}
COMMAND_TOPICS = ("cmd/action", "cmd/frame", "cmd/stop", "cmd/heartbeat")


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
        if suffix not in COMMAND_TOPICS:
            return
        if retain:
            self._reject(suffix, payload, "retained command ignored")
            return
        if suffix == "cmd/stop":
            stop = schema.stop(payload)
            self.executor.stop(Priority.SAFETY, sit=stop["sit"])
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

        if cmd.priority == Priority.GOAL and not self._heartbeat_fresh():
            self._reject(suffix, payload, "no heartbeat from a controller")
            return
        if not self.executor.submit(cmd):
            self._reject(suffix, payload, "busy or locked out by a higher priority")

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

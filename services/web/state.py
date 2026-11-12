"""What the web UI knows about the robot, fed from MQTT.

Transport-agnostic: on_message() takes MQTT messages, tick() runs
periodically, push(event, data) sends to all browsers (SSE). Everything
runs on the web server's event loop thread.
"""
import time

LOGGED_EVENTS = ("event/stop", "event/watchdog", "event/rejected", "event/battery")
PRUNE_EVERY_S = 3600.0


class LiveState:

    def __init__(self, store, tracker, push, wall=time.time):
        self.store = store
        self.tracker = tracker
        self.push = push
        self._wall = wall
        self.snapshot = {
            "body": None, "motion": None, "arbiter": None, "vision": None,
            "sensors": {}, "gamepad": None, "distance_cm": None, "agent": None, "voice": None, "battery": None,
        }
        self.vision_mode = "detect"
        self.display_frame = None         # latest OLED picture (display/frame)
        self.local_map = None             # latest scan (map/local)
        self._dirty = True
        self._last_prune = None

    # --- incoming ---

    def on_message(self, suffix, payload, retain=False):
        if not isinstance(payload, dict):
            return
        if suffix == "status":
            self._set("body", payload)
        elif suffix == "state/motion":
            self._set("motion", payload)
        elif suffix == "state/battery":
            self._set("battery", payload)
        elif suffix == "arbiter/status":
            self._set("arbiter", payload)
        elif suffix == "agent/status":
            self._set("agent", payload)
        elif suffix == "voice/status":
            self._set("voice", payload)
        elif suffix == "display/frame":
            self.display_frame = payload
            self.push("display", payload)
        elif suffix == "map/local":
            self.local_map = payload
            self.push("map", payload)
        elif suffix == "agent/reply" and not retain:
            self._log_agent(payload)
        elif suffix == "sensor/vision/status":
            self._set("vision", payload)
            self.vision_mode = payload.get("mode", self.vision_mode)
            if payload.get("status") != "online" or payload.get("mode") != "detect":
                self._handle(self.tracker.close_all())
        elif suffix == "sensor/vision/detections":
            if retain:
                return
            self.push("detections", payload)
            self._handle(self.tracker.feed(payload.get("objects"), self._wall(), self.vision_mode))
        elif suffix == "sensor/gamepad/state":
            self.snapshot["gamepad"] = payload
            self.push("gamepad", payload)
        elif suffix == "sensor/ultrasonic/distance":
            if self.snapshot["distance_cm"] != payload.get("cm"):
                self.snapshot["distance_cm"] = payload.get("cm")
                self._dirty = True
        elif suffix.startswith("sensor/") and suffix.endswith("/status"):
            self.snapshot["sensors"][suffix.split("/")[1]] = payload
            self._dirty = True
        elif suffix in LOGGED_EVENTS and not retain:
            self._log_event(suffix.split("/", 1)[1], payload)

    def _set(self, key, payload):
        self.snapshot[key] = payload
        self._dirty = True

    def _log_event(self, kind, payload):
        if kind == "stop":
            detail = payload.get("reason") or "manual"
            if payload.get("sit"):
                detail += ", sit down"
        elif kind == "rejected":
            detail = "%s: %s" % (payload.get("topic", "?"), payload.get("reason", ""))
        else:
            detail = str(payload.get("message", ""))
        ts = payload.get("ts") if isinstance(payload.get("ts"), (int, float)) else self._wall()
        row_id = self.store.add_event(kind, detail, ts)
        self.push("log", {"id": row_id, "kind": "event", "label": kind, "start": ts, "end": None,
                          "max_score": None, "max_count": None, "mode": None, "detail": detail})

    def _log_agent(self, payload):
        # Only requests the agent acted on; plain room talk is never stored.
        request, reply = str(payload.get("request", ""))[:200], str(payload.get("text", ""))[:300]
        detail = "%s → %s" % (request, reply)
        if payload.get("brain"):
            detail += " [%s%s]" % (payload["brain"], ", %.1f s" % payload["seconds"]
                                   if isinstance(payload.get("seconds"), (int, float)) else "")
        ts = payload.get("ts") if isinstance(payload.get("ts"), (int, float)) else self._wall()
        row_id = self.store.add_event("agent", detail, ts)
        row = {"id": row_id, "kind": "event", "label": "agent", "start": ts, "end": None, "max_score": None,
               "max_count": None, "mode": None, "detail": detail}
        self.push("log", row)
        self.push("agent", payload)

    def _handle(self, events):
        for what, sighting in events:
            if what == "start":
                self.store.add_sighting(sighting)
            else:
                self.store.finish_sighting(sighting)
            self.push("log", sighting.as_dict())

    # --- periodic ---

    def tick(self):
        now = self._wall()
        self._handle(self.tracker.expire(now))
        if self._last_prune is None or now - self._last_prune >= PRUNE_EVERY_S:
            self._last_prune = now
            self.store.prune()
        if self._dirty:
            self._dirty = False
            self.push("status", self.snapshot)

"""Validation of command payloads (docs/target-architecture.md, section 4.3).

Every function returns the cleaned values or raises Rejected with a short
reason that the body reports on event/rejected.
"""
import math

DEFAULT_ACTIONS = ("stand", "sit", "forward", "backward", "turn left", "turn right", "wave")
DEFAULT_BOUNDS = {"x": (0, 120), "y": (-120, 150), "z": (-100, 130)}
PRIORITIES = ("goal", "reflex")
MAX_AGE_S = 2.0
DEDUPE_WINDOW_S = 60.0


class Rejected(Exception):
    pass


class Limits:
    def __init__(self, actions=DEFAULT_ACTIONS, steps_max=10, bounds=None, max_age_s=MAX_AGE_S):
        self.actions = tuple(actions)
        self.steps_max = steps_max
        self.bounds = dict(DEFAULT_BOUNDS, **(bounds or {}))
        self.max_age_s = max_age_s

    @classmethod
    def from_config(cls, cfg):
        limits = cfg.get("limits", {})
        return cls(
            actions=limits.get("actions", DEFAULT_ACTIONS),
            steps_max=limits.get("steps_max", 10),
            bounds={k: tuple(v) for k, v in limits.get("bounds", {}).items()},
            max_age_s=limits.get("max_age_s", MAX_AGE_S),
        )


class Dedupe:
    """Remembers command ids for a while (QoS 1 may deliver twice)."""

    def __init__(self, window_s=DEDUPE_WINDOW_S):
        self.window_s = window_s
        self._seen = {}

    def check(self, cmd_id, now):
        for old, t in list(self._seen.items()):
            if now - t > self.window_s:
                del self._seen[old]
        if cmd_id in self._seen:
            raise Rejected("duplicate id")
        self._seen[cmd_id] = now


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Rejected("%s must be a number" % name)
    return value


def _int_in(payload, name, lo, hi, default):
    value = payload.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise Rejected("%s must be an integer in %d..%d" % (name, lo, hi))
    return value


def common(payload, now, limits):
    if not isinstance(payload, dict):
        raise Rejected("payload is not a JSON object")
    ts = _number(payload.get("ts"), "ts")
    if abs(now - ts) > limits.max_age_s:
        raise Rejected("stale or future ts (clocks in sync?)")
    cmd_id = payload.get("id")
    if not isinstance(cmd_id, str) or not 0 < len(cmd_id) <= 64:
        raise Rejected("id must be a non-empty string")
    return cmd_id


def _remember(dedupe, cmd_id, now):
    # Only after full validation, so a rejected message does not use up
    # its id and block a corrected resend.
    if dedupe is not None:
        dedupe.check(cmd_id, now)


def priority(payload):
    value = payload.get("priority", "goal")
    if value not in PRIORITIES:
        raise Rejected("priority must be one of %s" % ", ".join(PRIORITIES))
    return value


def action(payload, now, limits, dedupe=None):
    cmd_id = common(payload, now, limits)
    name = payload.get("name")
    if name not in limits.actions:
        raise Rejected("action not allowed")
    result = {
        "id": cmd_id,
        "name": name,
        "steps": _int_in(payload, "steps", 1, limits.steps_max, 1),
        "speed": _int_in(payload, "speed", 1, 100, 50),
        "priority": priority(payload),
    }
    _remember(dedupe, cmd_id, now)
    return result


def frame(payload, now, limits, dedupe=None):
    cmd_id = common(payload, now, limits)
    legs = payload.get("frame")
    if not isinstance(legs, list) or len(legs) != 4:
        raise Rejected("frame must be 4 legs")
    cleaned = []
    for leg in legs:
        if not isinstance(leg, list) or len(leg) != 3:
            raise Rejected("each leg must be [x, y, z]")
        coord = []
        for axis, value in zip("xyz", leg):
            value = _number(value, axis)
            lo, hi = limits.bounds[axis]
            if not lo <= value <= hi:
                raise Rejected("%s out of range %s..%s" % (axis, lo, hi))
            coord.append(value)
        cleaned.append(coord)
    result = {
        "id": cmd_id,
        "frame": cleaned,
        "speed": _int_in(payload, "speed", 1, 100, 50),
        "priority": priority(payload),
    }
    _remember(dedupe, cmd_id, now)
    return result


def stop(payload):
    """Never rejects: an extra stop is harmless, a dropped one is not.

    No ts check and no dedupe either. Malformed fields fall back to a
    plain stop without sitting down.
    """
    if not isinstance(payload, dict):
        payload = {}
    cmd_id = payload.get("id")
    reason = payload.get("reason")
    return {
        "id": cmd_id[:64] if isinstance(cmd_id, str) else None,
        "sit": payload.get("sit") is True,
        "reason": reason[:64] if isinstance(reason, str) else "",
    }

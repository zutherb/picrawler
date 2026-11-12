"""Keeps recent MQTT traffic for the debug page.

Every message under the robot's prefix is recorded: per-topic statistics,
the last messages per topic and a global ring buffer. Payloads are kept as
text (JSON pretty-printing happens in the browser) and truncated.
"""
import collections
import json

PER_TOPIC = 50
GLOBAL = 2000
MAX_PAYLOAD = 4096


def decode(raw):
    """Bytes -> (text, is_json)."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return "<%d bytes binary>" % len(raw), False
    else:
        text = raw if isinstance(raw, str) else json.dumps(raw)
    if len(text) > MAX_PAYLOAD:
        return text[:MAX_PAYLOAD] + " …(%d bytes)" % len(text), False
    try:
        json.loads(text)
        return text, True
    except ValueError:
        return text, False


class MqttMonitor:

    def __init__(self, push=None):
        self.push = push or (lambda message: None)
        self.topics = {}                      # suffix -> stats
        self._recent = collections.deque(maxlen=GLOBAL)
        self._per_topic = {}
        self._seq = 0

    def record(self, suffix, raw, retain, qos, ts):
        text, is_json = decode(raw)
        self._seq += 1
        message = {"seq": self._seq, "topic": suffix, "payload": text, "json": is_json,
                   "retain": bool(retain), "qos": int(qos), "ts": ts, "size": len(raw) if raw is not None else 0}
        stats = self.topics.setdefault(suffix, {"topic": suffix, "count": 0, "first": ts})
        stats.update(count=stats["count"] + 1, last=ts, retain=bool(retain), size=message["size"])
        self._recent.append(message)
        self._per_topic.setdefault(suffix, collections.deque(maxlen=PER_TOPIC)).append(message)
        self.push(message)
        return message

    def topic_list(self):
        return sorted(self.topics.values(), key=lambda t: t["topic"])

    def messages(self, topic="", subtree=True, limit=200):
        """Newest first. Empty topic = everything."""
        limit = max(1, min(int(limit), GLOBAL))
        if topic and not subtree:
            source = self._per_topic.get(topic, ())
        else:
            prefix = topic.rstrip("/") + "/" if topic else ""
            source = (m for m in self._recent if not topic or m["topic"] == topic or m["topic"].startswith(prefix))
        return list(reversed(list(source)))[:limit]

    def clear(self):
        self.topics.clear()
        self._recent.clear()
        self._per_topic.clear()

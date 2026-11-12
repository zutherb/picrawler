"""Agent: requests in (speech or web), tool-using brain, replies out.

- Speech must start with a wake word ("Crawler, geh vorwärts"); typed
  requests from the web UI need none.
- "Stopp"/"halt" is handled at once, without the model, and also cancels
  queued requests.
- One request at a time on a worker thread. While busy, agent/status says
  so; the arbiter pauses wander meanwhile.
- Brains are tried in order (e.g. Mac server, then Gemma on the Pi), the
  keyword parser last, so the robot always understands simple commands.
"""
import queue
import re
import threading
import time
import traceback

from .brains import BrainUnavailable
from .world import World

STOP_WORDS = re.compile(r"\b(stopp?|halt|anhalten|stehen ?bleiben|stop)\b", re.I)
QUEUE_MAX = 3
# Fixed replies the robot speaks, by [agent] language (data, not code).
MESSAGES = {
    "de": {"stopping": "Ich stoppe.", "busy": "Ich bin noch beschäftigt.", "done": "Erledigt.",
           "failed": "Ich konnte das gerade nicht ausführen."},
    "en": {"stopping": "Stopping.", "busy": "I am still busy.", "done": "Done.",
           "failed": "I could not do that right now."},
}


class Agent:

    def __init__(self, publish, brains, cfg=None, world=None, wall=time.time):
        cfg = cfg or {}
        self.publish = publish
        self.world = world or World(publish)
        self.brains = list(brains)
        self.wake_words = [w.lower() for w in cfg.get("wake_words", ["crawler", "krabbler", "roboter"])]
        self.require_wake_word = cfg.get("require_wake_word", True)
        self.messages = MESSAGES.get(cfg.get("language", "de"), MESSAGES["en"])
        self._wall = wall
        self._queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stopping = threading.Event()
        self.busy = False
        self.last_brain = None

    # --- incoming (network thread) ---

    def on_message(self, suffix, payload, retain=False):
        self.world.on_message(suffix, payload, retain)
        if retain or not isinstance(payload, dict):
            return
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        if suffix == "sensor/speech/text":
            self.handle(text, source="speech")
        elif suffix == "agent/ask":
            self.handle(text, source=payload.get("user") or "web")

    def handle(self, text, source):
        text = text.strip()[:500]
        if source == "speech":
            command = self.strip_wake_word(text)
            if command is None:
                return
            text = command
        if STOP_WORDS.search(text):
            self.world.stop("voice" if source == "speech" else "agent")
            self._drain()
            self.reply(self.messages["stopping"], text, source, "fast path")
            return
        try:
            self._queue.put_nowait((text, source))
        except queue.Full:
            self.reply(self.messages["busy"], text, source, "busy")

    def strip_wake_word(self, text):
        """Command without the wake word, or None if speech was not for us."""
        if not self.require_wake_word:
            return text
        words = re.findall(r"[\wäöüß]+", text.lower())
        for i, word in enumerate(words[:3]):
            if word in self.wake_words:
                rest = text.split(None, i + 1)[-1] if len(text.split()) > i + 1 else ""
                rest = rest.lstrip(",.!? ")
                return rest or None
        return None

    def _drain(self):
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # --- worker ---

    def run(self):
        self.status()
        while not self._stopping.is_set():
            try:
                text, source = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self.busy = True
            self.status()
            started = time.monotonic()
            reply, used = self.think(text)
            self.busy = False
            self.reply(reply, text, source, used, time.monotonic() - started)
            self.status()

    def think(self, text):
        context = "Robot state: %s Obstacle ahead: %s." % (
            self.world.describe(),
            "%.0f cm" % self.world.fresh_distance() if self.world.fresh_distance() is not None else "unknown")
        for brain in self.brains:
            try:
                reply = brain.ask(text, context)
                self.last_brain = brain.name
                return reply or self.messages["done"], brain.name
            except BrainUnavailable as e:
                print("agent: %s unavailable: %s" % (brain.name, e))
            except Exception:
                traceback.print_exc()
        return self.messages["failed"], "none"

    def stop(self):
        self._stopping.set()

    # --- outgoing ---

    def reply(self, text, request, source, brain, seconds=None):
        payload = {"text": text, "request": request, "source": source, "brain": brain, "ts": self._wall()}
        if seconds is not None:
            payload["seconds"] = round(seconds, 1)
        self.publish("agent/reply", payload, 1, False)

    def status(self):
        self.publish("agent/status", {"status": "online", "busy": self.busy,
                                      "brains": [b.name for b in self.brains], "last_brain": self.last_brain,
                                      "ts": self._wall()}, 1, True)

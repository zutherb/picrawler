"""Voice service: speaks the agent's replies with Piper (offline TTS).

    python -m services.voice --config /etc/picrawler/voice.toml [--fake]

Subscribes agent/reply, synthesizes with the `piper` binary and plays the
WAV with `aplay`. Publishes voice/speaking {"speaking": bool} so the speech
sensor ignores the robot's own voice. Replies are spoken one after another;
when several queue up, older ones are dropped.
"""
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time

from services.common import config
from services.common.mqtt import make_bus

MAX_CHARS = 300


def enable_robot_hat_speaker():
    """The Robot HAT amplifier is switched by a GPIO; robot_hat knows which."""
    try:
        from robot_hat.utils import enable_speaker
        enable_speaker()
        return True
    except Exception as e:   # not on the robot, or robot_hat without this helper
        print("voice: could not enable the Robot HAT speaker: %s" % e)
        return False


class Piper:
    def __init__(self, binary, model, device=""):
        self.binary = binary
        self.model = model
        self.device = device

    def say(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "say.wav")
            subprocess.run([self.binary, "--model", self.model, "--output_file", wav],
                           input=text.encode("utf-8"), check=True, capture_output=True, timeout=60)
            play = ["aplay", "-q"] + (["-D", self.device] if self.device else []) + [wav]
            subprocess.run(play, check=True, capture_output=True, timeout=60)


class FakeVoice:
    def say(self, text):
        print("voice: (would say) %s" % text)
        time.sleep(min(3.0, 0.06 * len(text)))


def main():
    args = config.arg_parser("PiCrawler voice").parse_args()
    cfg = config.load(args.config)
    v = cfg.get("voice", {})

    bus = make_bus(cfg, "picrawler-voice", "voice/status", fake=args.fake)
    if args.fake:
        voice = FakeVoice()
    else:
        if v.get("robot_hat_speaker", True):
            enable_robot_hat_speaker()
        voice = Piper(v.get("piper", "piper"), v["model"], v.get("device", ""))
    replies = queue.Queue(maxsize=2)

    def on_message(suffix, payload, retain):
        if suffix != "agent/reply" or retain or not isinstance(payload, dict):
            return
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        while True:
            try:
                replies.put_nowait(text.strip()[:MAX_CHARS])
                return
            except queue.Full:
                try:
                    replies.get_nowait()      # drop the oldest
                except queue.Empty:
                    pass

    bus.subscribe("agent/reply")
    bus.on_message = on_message
    bus.on_connect = lambda: bus.publish_status("online")

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    bus.start()
    print("voice: ready%s" % (" (fake)" if args.fake else ""))
    while not stopping.is_set():
        try:
            text = replies.get(timeout=0.5)
        except queue.Empty:
            continue
        bus.publish("voice/speaking", {"speaking": True, "ts": time.time()})
        try:
            voice.say(text)
        except (OSError, subprocess.SubprocessError) as e:
            print("voice: %s" % e)
        finally:
            bus.publish("voice/speaking", {"speaking": False, "ts": time.time()})
    bus.stop()


if __name__ == "__main__":
    main()

"""Speech sensor: USB microphone -> utterances -> text.

    python -m services.sensor_speech --config /etc/picrawler/sensor-speech.toml [--fake]

Publishes sensor/speech/text {"text", "language", "duration_s", "asr_s", "ts"}
and a retained sensor/speech/status. Every utterance is transcribed; the
agent decides whether it was meant for the robot (wake word). While the
robot's own voice is playing (voice/speaking), the microphone is ignored.
"""
import signal
import threading
import time

from services.common import config
from services.common.mqtt import make_bus

from .asr import AsrError, FakeAsr, WhisperServer
from .audio import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, UtteranceDetector, to_wav

TEXT_TOPIC = "sensor/speech/text"
STATUS_TOPIC = "sensor/speech/status"


class Microphone:
    """16 kHz mono capture via PyAudio (python3-pyaudio from apt)."""

    def __init__(self, device_name=""):
        import pyaudio
        self._pa = pyaudio.PyAudio()
        index = None
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0 and (not device_name or device_name.lower() in info["name"].lower()):
                index = i
                self.name = info["name"]
                break
        if index is None:
            raise RuntimeError("no input device%s" % (" matching %r" % device_name if device_name else ""))
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE, input=True,
                                     input_device_index=index, frames_per_buffer=FRAME_SAMPLES)

    def read(self):
        return self._stream.read(FRAME_SAMPLES, exception_on_overflow=False)

    def close(self):
        self._stream.stop_stream()
        self._stream.close()
        self._pa.terminate()


class FakeMicrophone:
    """Silence with a burst of 'speech' every 12 s (for --fake)."""

    name = "fake microphone"

    def __init__(self):
        import array
        import math
        self._t0 = time.monotonic()
        self._silence = bytes(FRAME_BYTES)
        tone = array.array("h", (int(4000 * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE))
                                 for i in range(FRAME_SAMPLES)))
        self._voice = tone.tobytes()

    def read(self):
        time.sleep(FRAME_SAMPLES / SAMPLE_RATE)
        t = (time.monotonic() - self._t0) % 12
        return self._voice if 3 < t < 4.5 else self._silence

    def close(self):
        pass


def main():
    args = config.arg_parser("PiCrawler speech sensor").parse_args()
    cfg = config.load(args.config)
    s = cfg.get("sensor", {})

    bus = make_bus(cfg, "picrawler-sensor-speech", STATUS_TOPIC, fake=args.fake)
    mic = FakeMicrophone() if args.fake else Microphone(s.get("device_name", ""))
    asr = FakeAsr() if args.fake else WhisperServer(s.get("whisper_url", "http://127.0.0.1:8082/inference"),
                                                     s.get("language", "de"))
    detector = UtteranceDetector(ratio=s.get("vad_ratio", 3.0), min_level=s.get("vad_min_level", 300.0),
                                 end_ms=s.get("end_ms", 700))
    muted_until = [0.0]
    state = {"error": None}

    def status():
        bus.publish_status("online", microphone=mic.name, asr=asr.name, error=state["error"])

    def on_message(suffix, payload, retain):
        # Ignore our own voice while the speaker talks (plus a short echo tail).
        if suffix == "voice/speaking" and isinstance(payload, dict):
            if payload.get("speaking"):
                muted_until[0] = float("inf")
            else:
                muted_until[0] = time.monotonic() + 0.5

    bus.subscribe("voice/speaking", qos=0)
    bus.on_message = on_message
    bus.on_connect = status

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    bus.start()
    print("sensor-speech: listening on %s, asr %s" % (mic.name, asr.name))
    while not stopping.is_set():
        frame = mic.read()
        if time.monotonic() < muted_until[0]:
            continue
        pcm = detector.feed(frame)
        if pcm is None:
            continue
        started = time.monotonic()
        try:
            text = asr.transcribe(to_wav(pcm))
            state["error"] = None
        except AsrError as e:
            state["error"] = str(e)
            print("sensor-speech: %s" % e)
            status()
            continue
        if text:
            bus.publish(TEXT_TOPIC, {"text": text, "language": s.get("language", "de"),
                                     "duration_s": round(len(pcm) / 2 / SAMPLE_RATE, 2),
                                     "asr_s": round(time.monotonic() - started, 2), "ts": time.time()}, 1)
    mic.close()
    bus.stop()


if __name__ == "__main__":
    main()

"""Utterance detection on 16 kHz mono 16-bit audio, without extra libraries.

An energy-based voice activity detector with an adaptive noise floor: good
enough to cut utterances out of a USB microphone stream so that whisper only
runs when someone actually speaks.
"""
import array
import io
import math
import wave

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


def rms(frame):
    samples = array.array("h")
    samples.frombytes(frame)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def to_wav(pcm, rate=SAMPLE_RATE):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class UtteranceDetector:
    """Feed 30 ms frames; returns the PCM of a finished utterance or None.

    Speech starts when the level stays `ratio` times above the noise floor
    for `start_ms`; it ends after `end_ms` of silence. Utterances with less
    than `min_ms` of voiced audio are dropped, longer than `max_ms` are cut.
    """

    def __init__(self, ratio=3.0, min_level=300.0, start_ms=150, end_ms=700, min_ms=400, max_ms=10000,
                 pre_roll_ms=300):
        self.ratio = ratio
        self.min_level = min_level
        self.start_frames = max(1, start_ms // FRAME_MS)
        self.end_frames = max(1, end_ms // FRAME_MS)
        self.min_frames = max(1, min_ms // FRAME_MS)
        self.max_frames = max(1, max_ms // FRAME_MS)
        self.pre_roll = max(0, pre_roll_ms // FRAME_MS)
        self.noise = None
        self._history = []        # recent frames before speech (pre-roll)
        self._voiced_run = 0
        self._silent_run = 0
        self._voiced = 0          # voiced frames in the current utterance
        self._speech = None       # frames of the current utterance

    @property
    def speaking(self):
        return self._speech is not None

    def threshold(self):
        return max(self.min_level, (self.noise or self.min_level) * self.ratio)

    def feed(self, frame):
        level = rms(frame)
        voiced = level > self.threshold()
        if self._speech is None:
            # learn the noise floor while nobody speaks; voiced frames only
            # nudge it, so a loud room still adapts but speech does not
            # raise the threshold before it is detected
            alpha = 0.002 if voiced else 0.05
            self.noise = level if self.noise is None else (1 - alpha) * self.noise + alpha * level
            self._history.append(frame)
            del self._history[:-max(self.pre_roll, self.start_frames)]
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            if self._voiced_run >= self.start_frames:
                self._speech = list(self._history)
                self._silent_run = 0
                self._voiced = self._voiced_run
            return None
        self._speech.append(frame)
        self._silent_run = 0 if voiced else self._silent_run + 1
        self._voiced += 1 if voiced else 0
        if self._silent_run >= self.end_frames or len(self._speech) >= self.max_frames:
            speech, self._speech = self._speech, None
            self._history, self._voiced_run = [], 0
            if self._silent_run >= self.end_frames:
                speech = speech[:len(speech) - self._silent_run + 2]   # keep a little tail
            if self._voiced < self.min_frames:
                return None
            return b"".join(speech)
        return None

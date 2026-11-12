"""Camera sources: picamera2 on the Pi, a test card for --fake.

Both deliver JPEG frames for the stream into a FrameBuffer and small RGB
arrays (the "lores" stream) for inference.
"""
import io
import os
import threading
import time

TESTCARD = os.path.join(os.path.dirname(__file__), "testcard.jpg")


class FrameBuffer:
    """Holds only the newest JPEG; readers wait for the next one."""

    def __init__(self):
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self.frames = 0

    def put(self, jpeg):
        with self._cond:
            self._frame = jpeg
            self._seq += 1
            self.frames += 1
            self._cond.notify_all()

    def wait(self, after_seq, timeout=2.0):
        """Return (seq, jpeg) newer than after_seq, or (after_seq, None) on timeout."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq != after_seq, timeout)
            if self._seq == after_seq:
                return after_seq, None
            return self._seq, self._frame

    def latest(self):
        with self._cond:
            return self._frame


class _JpegSink(io.BufferedIOBase):
    """picamera2 FileOutput target: every write() is one complete JPEG."""

    def __init__(self, buffer):
        self._buffer = buffer

    def writable(self):
        return True

    def write(self, data):
        self._buffer.put(bytes(data))
        return len(data)


class PiCamera:
    def __init__(self, buffer, size=(960, 540), lores=(300, 300), fps=15, quality=70):
        from picamera2 import Picamera2
        from picamera2.encoders import JpegEncoder
        from picamera2.outputs import FileOutput

        self._cam = Picamera2()
        # Pi 5 can deliver lores as 24-bit color directly, no YUV conversion needed.
        config = self._cam.create_video_configuration(
            main={"size": tuple(size), "format": "RGB888"},
            lores={"size": tuple(lores), "format": "RGB888"},
            controls={"FrameRate": fps},
        )
        self._cam.configure(config)
        # The Pi 5 has no hardware JPEG/H.264 encoder: software JPEG it is.
        self._encoder = JpegEncoder(q=quality)
        self._output = FileOutput(_JpegSink(buffer))

    def start(self):
        self._cam.start_recording(self._encoder, self._output)

    def capture_lores(self):
        """Blocks until the next frame; returns an HxWx3 uint8 RGB array."""
        import numpy as np
        # picamera2's "RGB888" is stored as B, G, R in memory
        return np.ascontiguousarray(self._cam.capture_array("lores")[..., ::-1])

    def stop(self):
        self._cam.stop_recording()
        self._cam.close()


class FakeCamera:
    def __init__(self, buffer, fps=10, **_):
        self._buffer = buffer
        self._period = 1.0 / fps
        with open(TESTCARD, "rb") as f:
            self._jpeg = f.read()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while not self._stopping.wait(self._period):
            self._buffer.put(self._jpeg)

    def capture_lores(self):
        time.sleep(self._period)
        return None

    def stop(self):
        self._stopping.set()

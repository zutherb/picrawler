"""Vision service: owns the camera and the Coral TPU.

    python -m services.vision --config /etc/picrawler/vision.toml [--fake]

Streams MJPEG on 127.0.0.1 (the web service proxies it with login) and
publishes facts under sensor/vision/*: detections (with bearing and floor
distance per object) and, independent of the mode, the free floor ahead
(sensor/vision/floor). Modes: detect, off. pose and imprint are planned
(docs/target-architecture.md, section 7).
"""
import signal
import threading
import time

from services.common import config
from services.common.mqtt import make_bus

from . import floor
from .camera import FakeCamera, FrameBuffer, PiCamera
from .geometry import CameraGeometry, locate
from .inference import DetectorUnavailable, FakeDetector, TpuDetector
from .stream import StreamServer

MODES = ("detect", "off")
PLANNED_MODES = ("pose", "imprint")
STATUS_TOPIC = "sensor/vision/status"
STATUS_EVERY_S = 2.0


class Vision:
    def __init__(self, bus, camera, buffer, detector_factory, cfg, floor_cfg=None, geometry_cfg=None):
        self.bus = bus
        self.camera = camera
        self.buffer = buffer
        self.detector_factory = detector_factory
        self.max_rate_hz = cfg.get("max_rate_hz", 10)
        self.mode = cfg.get("mode", "detect")
        self.detector = None
        self.error = None
        self.inference_ms = None
        self._lock = threading.Lock()
        self._last_published = 0.0
        self._fps_frames = 0
        self._fps_t = time.monotonic()
        self.fps = 0.0
        floor_cfg = floor_cfg or {}
        self.floor_enabled = floor_cfg.get("enabled", True)
        self.floor_every_s = 1.0 / floor_cfg.get("rate_hz", 2.0)
        self.floor_bins = floor_cfg.get("bins", 24)
        self.floor_max_cm = floor_cfg.get("max_cm", 150.0)
        self._floor_at = 0.0
        self._geometry_cfg = geometry_cfg or {}
        self._geometry = None             # built for the first frame's size

    # --- mode ---

    def set_mode(self, mode):
        with self._lock:
            if mode in PLANNED_MODES:
                self.error = "mode %s is not implemented yet" % mode
                return False
            if mode not in MODES:
                self.error = "unknown mode %r" % (mode,)
                return False
            self.mode = mode
            self.error = None
            if mode == "detect" and self.detector is None:
                self._load_detector()
        self.publish_status()
        return True

    def _load_detector(self):
        try:
            self.detector = self.detector_factory()
            print("vision: detector ready (%s)" % self.detector.backend)
        except (DetectorUnavailable, OSError, ValueError) as e:
            self.detector = None
            self.error = "no TPU detector: %s" % e
            print("vision: %s" % self.error)

    def on_message(self, suffix, payload, retain=False):
        if suffix != "vision/mode" or retain or not isinstance(payload, dict):
            return
        self.set_mode(payload.get("mode"))

    # --- status ---

    def publish_status(self):
        now = time.monotonic()
        frames = self.buffer.frames
        if now - self._fps_t >= 1.0:
            self.fps = round((frames - self._fps_frames) / (now - self._fps_t), 1)
            self._fps_frames, self._fps_t = frames, now
        self.bus.publish_status(
            "online", mode=self.mode, tpu=bool(self.detector and self.detector.tpu),
            fps=self.fps, inference_ms=self.inference_ms, error=self.error,
        )

    # --- inference loop ---

    def geometry(self, rgb):
        h, w = rgb.shape[:2]
        if self._geometry is None or (self._geometry.width, self._geometry.height) != (w, h):
            self._geometry = CameraGeometry.from_config(self._geometry_cfg, w, h)
        return self._geometry

    def publish_floor(self, rgb):
        now = time.monotonic()
        if not self.floor_enabled or rgb is None or now - self._floor_at < self.floor_every_s:
            return
        self._floor_at = now
        geometry = self.geometry(rgb)
        bands = floor.boundaries(rgb, geometry, bins=self.floor_bins, max_cm=self.floor_max_cm)
        self.bus.publish("sensor/vision/floor", {
            "frame_ts": time.time(), "max_cm": self.floor_max_cm,
            "near_cm": round(geometry.nearest_floor_cm() or 0, 1),
            "bands": [{"bearing_deg": b, "cm": cm} for b, cm in bands]})

    def run_inference(self, stopping):
        while not stopping.is_set():
            rgb = self.camera.capture_lores()
            try:
                self.publish_floor(rgb)
            except Exception as e:      # a bad frame must not stop detection
                self.error = "floor estimate failed: %s" % e
            with self._lock:
                detector = self.detector if self.mode == "detect" else None
            if detector is None:
                if rgb is None:
                    stopping.wait(0.1)
                continue
            frame_ts = time.time()
            try:
                objects, ms = detector.detect(rgb)
            except Exception as e:
                self.error = "inference failed: %s" % e
                stopping.wait(1.0)
                continue
            self.inference_ms = round(ms, 1)
            if rgb is not None:
                locate(objects, self.geometry(rgb), rgb.shape[1], rgb.shape[0])
            now = time.monotonic()
            if now - self._last_published >= 1.0 / self.max_rate_hz:
                self._last_published = now
                self.bus.publish("sensor/vision/detections", {"frame_ts": frame_ts, "objects": objects})


def main():
    args = config.arg_parser("PiCrawler vision service").parse_args()
    cfg = config.load(args.config)
    cam_cfg = cfg.get("camera", {})
    det_cfg = cfg.get("detect", {})
    stream_cfg = cfg.get("stream", {})

    buffer = FrameBuffer()
    if args.fake:
        camera = FakeCamera(buffer)
        factory = FakeDetector
    else:
        camera = PiCamera(buffer, size=cam_cfg.get("size", [960, 540]), lores=cam_cfg.get("lores", [300, 300]),
                          fps=cam_cfg.get("fps", 15), quality=cam_cfg.get("quality", 70))

        def factory():
            return TpuDetector(det_cfg["model"], det_cfg["labels"], det_cfg.get("threshold", 0.4))

    bus = make_bus(cfg, "picrawler-vision", STATUS_TOPIC, fake=args.fake)
    vision = Vision(bus, camera, buffer, factory, cfg.get("vision", {}), cfg.get("floor", {}), cfg.get("geometry", {}))
    bus.subscribe("vision/mode")
    bus.on_message = vision.on_message
    bus.on_connect = vision.publish_status

    server = StreamServer(buffer, stream_cfg.get("host", "127.0.0.1"), stream_cfg.get("port", 8081))
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    camera.start()
    server.start()
    vision.set_mode(vision.mode)
    bus.start()
    worker = threading.Thread(target=vision.run_inference, args=(stopping,), daemon=True)
    worker.start()
    print("vision: streaming on http://%s:%d/stream.mjpg, mode %s"
          % (stream_cfg.get("host", "127.0.0.1"), server.port, vision.mode))
    while not stopping.wait(STATUS_EVERY_S):
        vision.publish_status()
    server.stop()
    camera.stop()
    bus.stop()


if __name__ == "__main__":
    main()

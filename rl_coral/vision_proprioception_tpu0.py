"""Visual proprioception and obstacle distance from the camera, detector on TPU 0.

    python -m rl_coral.vision_proprioception_tpu0 --model ssd.tflite --labels coco_labels.txt

VisionStateTracker runs in a daemon thread and keeps the newest
[vis_roll, vis_pitch, flow_yaw, obstacle_dist, vx_cmd, wz_cmd], plus the
floor edge per column band and the ultrasonic reading for the 2D mapper:

- Optical flow on a 64x48 grayscale copy of every frame (LK features +
  cv2.estimateAffinePartial2D): frame rotation -> vis_roll, vertical shift
  -> vis_pitch, horizontal shift -> flow_yaw. Roll and pitch are rates
  integrated with a leak (VISION_TAU_S), the same high-pass the simulator
  applies to the true angles; the camera has no absolute horizon.
- An SSD detector on the Edge TPU at pci:0 locates objects on the floor
  (services/vision/geometry.py); together with the floor edge
  (services/vision/floor.py) and, optionally, the ultrasonic sensor it gives
  obstacle_dist = free distance ahead / 1 m, clipped to [0, 1].
- A target label (default "sports ball") is followed: wz_cmd steers towards
  it, vx_cmd approaches it until TARGET_STOP_CM. No target: both 0.

Sign conventions match rl_coral/sim_env.py: roll positive with the right
side down, pitch positive nose up, yaw rate positive counter-clockwise.
Check them once on the robot (tilt it by hand, watch the printout).

The camera, the ultrasonic sensor and the TPU are used exclusively: stop the
picrawler-vision and ultrasonic services first.
"""
import argparse
import math
import threading
import time

import numpy as np

from rl_coral import common
from services.vision.geometry import CameraGeometry, locate
from services.vision.inference import load_labels, split_ssd_outputs

EDGETPU_LIB = "libedgetpu.so.1"
FLOW_SIZE = (64, 48)
FRAME_SIZE = (320, 240)
FPS = 30
FLOOR_BANDS = 31            # floor edge per image column band, the mapper's ray count
CENTER_DEG = 15.0           # objects / floor bands within this bearing count as "ahead"
TARGET_STOP_CM = 25.0
TARGET_SLOW_CM = 75.0
TARGET_LOST_S = 1.0
STALE_S = 0.5


class FlowEstimator:
    """Rotation and shift between consecutive frames on a 64x48 grayscale image."""

    def __init__(self, hfov_deg, vfov_deg, size=FLOW_SIZE):
        import cv2
        self.cv2 = cv2
        self.size = size
        self.rad_per_px = (math.radians(hfov_deg) / size[0], math.radians(vfov_deg) / size[1])
        self.prev = None
        self.prev_points = None

    def update(self, bgr):
        """Returns (rotation rad, dx rad, dy rad, ms); zeros on the first frame or too few features."""
        cv2 = self.cv2
        started = time.perf_counter()
        gray = cv2.cvtColor(cv2.resize(bgr, self.size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        result = (0.0, 0.0, 0.0)
        if self.prev is not None and self.prev_points is not None and len(self.prev_points) >= 6:
            points, status, _ = cv2.calcOpticalFlowPyrLK(self.prev, gray, self.prev_points, None,
                                                         winSize=(9, 9), maxLevel=1)
            good = status.reshape(-1) == 1
            if good.sum() >= 6:
                matrix, _ = cv2.estimateAffinePartial2D(self.prev_points[good], points[good],
                                                        method=cv2.RANSAC, ransacReprojThreshold=1.0)
                if matrix is not None:
                    angle = math.atan2(matrix[1, 0], matrix[0, 0])      # image y points down
                    result = (angle, matrix[0, 2] * self.rad_per_px[0], matrix[1, 2] * self.rad_per_px[1])
        self.prev = gray
        self.prev_points = cv2.goodFeaturesToTrack(gray, maxCorners=40, qualityLevel=0.01, minDistance=4)
        return result + ((time.perf_counter() - started) * 1000,)


class EdgeTpuDetector:
    """SSD postprocess model on one Edge TPU via ai_edge_litert."""

    def __init__(self, model_path, labels_path, device="pci:0", threshold=0.4):
        from ai_edge_litert.interpreter import Interpreter, load_delegate
        delegate = load_delegate(EDGETPU_LIB, {"device": device})
        self.interpreter = Interpreter(model_path=model_path, experimental_delegates=[delegate])
        self.interpreter.allocate_tensors()
        inp = self.interpreter.get_input_details()[0]
        self.input_index = inp["index"]
        self.input_size = (int(inp["shape"][2]), int(inp["shape"][1]))
        self.labels = load_labels(labels_path)
        self.threshold = threshold
        self.device = device

    def detect(self, rgb):
        import cv2
        started = time.perf_counter()
        image = cv2.resize(rgb, self.input_size, interpolation=cv2.INTER_LINEAR)
        self.interpreter.set_tensor(self.input_index, np.expand_dims(image, 0))
        self.interpreter.invoke()
        outputs = [self.interpreter.get_tensor(d["index"]) for d in self.interpreter.get_output_details()]
        boxes, classes, scores, count = split_ssd_outputs(outputs)
        objects = []
        for i in range(min(count, len(scores))):
            if float(scores[i]) < self.threshold:
                continue
            ymin, xmin, ymax, xmax = (min(1.0, max(0.0, float(v))) for v in boxes[i])
            objects.append({"label": self.labels.get(int(classes[i]), str(int(classes[i]))),
                            "score": float(scores[i]), "box": [xmin, ymin, xmax, ymax]})
        return objects, (time.perf_counter() - started) * 1000


class UltrasonicReader(threading.Thread):
    """Ultrasonic fallback at 10 Hz in its own thread (a reading blocks up to 20 ms)."""

    def __init__(self, trig="D2", echo="D3"):
        super().__init__(daemon=True, name="ultrasonic")
        from robot_hat import Pin, Ultrasonic
        self.sonar = Ultrasonic(Pin(trig), Pin(echo), timeout=0.02)
        self.cm = None
        self.ts = 0.0
        self.running = True

    def run(self):
        while self.running:
            cm = self.sonar.read()
            if cm is not None and 2 <= cm <= 400:
                self.cm, self.ts = float(cm), time.monotonic()
            time.sleep(0.1)

    def latest(self, max_age=0.5):
        return self.cm if time.monotonic() - self.ts <= max_age else None


class Camera:
    """320x240 BGR at 30 fps from picamera2, or cv2.VideoCapture as fallback."""

    def __init__(self, size=FRAME_SIZE, fps=FPS, index=0):
        self.picam = None
        try:
            from picamera2 import Picamera2
            self.picam = Picamera2()
            # "RGB888" is stored as B, G, R per pixel, i.e. OpenCV's BGR.
            config = self.picam.create_video_configuration(
                main={"size": tuple(size), "format": "RGB888"},
                controls={"FrameDurationLimits": (int(1e6 / fps), int(1e6 / fps))}, buffer_count=4)
            self.picam.configure(config)
            self.picam.start()
        except ImportError:
            import cv2
            self.cap = cv2.VideoCapture(index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
            self.cap.set(cv2.CAP_PROP_FPS, fps)
            if not self.cap.isOpened():
                raise RuntimeError("no camera (picamera2 missing, cv2.VideoCapture(%d) failed)" % index)

    def read(self):
        if self.picam is not None:
            return self.picam.capture_array("main")
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("camera read failed")
        return frame

    def close(self):
        if self.picam is not None:
            self.picam.stop()
            self.picam.close()
        else:
            self.cap.release()


class VisionStateTracker(threading.Thread):
    """Camera thread; snapshot() is safe to call from the control loop."""

    def __init__(self, detector=None, ultrasonic=None, target="sports ball", camera=None,
                 hfov_deg=53.5, vfov_deg=41.4, height_cm=9.0, tilt_deg=8.0, floor_edge=True):
        super().__init__(daemon=True, name="vision")
        self.detector = detector
        self.ultrasonic = ultrasonic
        self.target = target
        self.camera = camera
        self.geometry = CameraGeometry(FRAME_SIZE[0], FRAME_SIZE[1], hfov_deg=hfov_deg, vfov_deg=vfov_deg,
                                       height_cm=height_cm, tilt_deg=tilt_deg)
        self.flow = FlowEstimator(hfov_deg, vfov_deg)
        self.floor_edge = floor_edge
        self._lock = threading.Lock()
        self._state = {"features": np.array([0.0, 0.0, 0.0, 1.0], np.float32), "cmd": (0.0, 0.0),
                       "bands": [], "ultrasonic_cm": None,
                       "ts": 0.0, "flow_ms": 0.0, "detect_ms": 0.0, "fps": 0.0, "error": None}
        self._stop = threading.Event()
        self.roll = self.pitch = 0.0
        self.target_seen = 0.0
        self.target_cmd = (0.0, 0.0)

    def snapshot(self):
        """Copy of the newest state plus its age in seconds."""
        with self._lock:
            state = dict(self._state)
            state["features"] = state["features"].copy()
        state["age"] = time.monotonic() - state["ts"] if state["ts"] else float("inf")
        state["stale"] = state["age"] > STALE_S
        return state

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            if self.camera is None:
                self.camera = Camera()
            last = None
            frames, fps_t0 = 0, time.monotonic()
            fps = 0.0
            while not self._stop.is_set():
                bgr = self.camera.read()
                now = time.monotonic()
                dt = (now - last) if last else 1.0 / FPS
                last = now
                features, cmd, bands, sonar, flow_ms, detect_ms = self._process(bgr, dt, now)
                frames += 1
                if now - fps_t0 >= 1.0:
                    fps, frames, fps_t0 = frames / (now - fps_t0), 0, now
                with self._lock:
                    self._state.update(features=features, cmd=cmd, bands=bands, ultrasonic_cm=sonar, ts=now,
                                       flow_ms=flow_ms, detect_ms=detect_ms, fps=fps)
        except Exception as e:                      # surfaced via snapshot(); the loop goes stale
            with self._lock:
                self._state["error"] = "%s: %s" % (type(e).__name__, e)
        finally:
            if self.camera is not None:
                self.camera.close()

    def _process(self, bgr, dt, now):
        rotation, dx, dy, flow_ms = self.flow.update(bgr)
        decay = common.leak(dt)
        self.roll = decay * self.roll - rotation        # scene turns counter-clockwise when rolling right
        self.pitch = decay * self.pitch + dy            # scene moves down when the nose goes up
        flow_yaw = dx / max(dt, 1e-3)                   # scene moves right when turning left

        distances = []
        bands = []
        detect_ms = 0.0
        rgb = bgr[..., ::-1]
        if self.floor_edge:
            from services.vision.floor import boundaries
            bands = boundaries(np.ascontiguousarray(rgb), self.geometry, bins=FLOOR_BANDS,
                               max_cm=common.OBSTACLE_RANGE_M * 100)
            distances += [cm for bearing, cm in bands if cm is not None and abs(bearing) <= CENTER_DEG]
        if self.detector is not None:
            objects, detect_ms = self.detector.detect(np.ascontiguousarray(rgb))
            locate(objects, self.geometry, FRAME_SIZE[0], FRAME_SIZE[1])
            distances += [o["floor_cm"] for o in objects
                          if o.get("floor_cm") is not None and abs(o["bearing_deg"]) <= CENTER_DEG]
            self._follow(objects, now)
        sonar = self.ultrasonic.latest() if self.ultrasonic is not None else None
        if sonar is not None:
            distances.append(sonar)
        free_m = min(distances) / 100.0 if distances else common.OBSTACLE_RANGE_M
        obstacle = min(1.0, max(0.0, free_m / common.OBSTACLE_RANGE_M))

        cmd = self.target_cmd if now - self.target_seen <= TARGET_LOST_S else (0.0, 0.0)
        features = np.array([self.roll, self.pitch, flow_yaw, obstacle], np.float32)
        return features, cmd, bands, sonar, flow_ms, detect_ms

    def _follow(self, objects, now):
        targets = [o for o in objects if o["label"] == self.target]
        if not targets:
            return
        best = max(targets, key=lambda o: o["score"])
        wz = max(-1.0, min(1.0, best["bearing_deg"] / 30.0))
        cm = best.get("floor_cm")
        if cm is None:                                  # bottom edge cut off: it is close
            vx = 0.0
        else:
            vx = max(0.0, min(1.0, (cm - TARGET_STOP_CM) / (TARGET_SLOW_CM - TARGET_STOP_CM)))
        self.target_cmd = (vx * (1.0 - 0.5 * abs(wz)), wz)
        self.target_seen = now


def make_tracker(args):
    detector = None
    if args.model:
        detector = EdgeTpuDetector(args.model, args.labels, device=args.vision_device, threshold=args.threshold)
    ultrasonic = UltrasonicReader() if args.ultrasonic else None
    if ultrasonic is not None:
        ultrasonic.start()
    return VisionStateTracker(detector=detector, ultrasonic=ultrasonic, target=args.target,
                              hfov_deg=args.hfov, vfov_deg=args.vfov, height_cm=args.camera_height,
                              tilt_deg=args.camera_tilt)


def add_arguments(parser):
    parser.add_argument("--model", help="SSD *_edgetpu.tflite for TPU 0 (omit: flow and floor edge only)")
    parser.add_argument("--labels", help="label file for --model")
    parser.add_argument("--vision-device", default="pci:0")
    parser.add_argument("--threshold", type=float, default=0.4)
    parser.add_argument("--target", default="sports ball", help="label to walk towards")
    parser.add_argument("--ultrasonic", action="store_true", help="also use the ultrasonic sensor (D2/D3)")
    parser.add_argument("--hfov", type=float, default=53.5, help="camera field of view at 4:3, degrees")
    parser.add_argument("--vfov", type=float, default=41.4)
    parser.add_argument("--camera-height", type=float, default=9.0, help="cm above the floor")
    parser.add_argument("--camera-tilt", type=float, default=8.0, help="degrees down")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print the vision features (for checking signs and timing).")
    add_arguments(parser)
    args = parser.parse_args(argv)
    if args.model and not args.labels:
        parser.error("--model needs --labels")
    tracker = make_tracker(args)
    tracker.start()
    try:
        while tracker.is_alive():
            time.sleep(0.2)
            s = tracker.snapshot()
            roll, pitch, yaw, obstacle = s["features"]
            print("roll %+.3f  pitch %+.3f  yaw %+.2f rad/s  obstacle %.2f  cmd %+.2f %+.2f  "
                  "flow %.2f ms  detect %.1f ms  %.0f fps%s"
                  % (roll, pitch, yaw, obstacle, s["cmd"][0], s["cmd"][1], s["flow_ms"], s["detect_ms"], s["fps"],
                     "  STALE" if s["stale"] else ""))
        print("vision thread ended: %s" % tracker.snapshot()["error"])
    except KeyboardInterrupt:
        pass
    finally:
        tracker.stop()


if __name__ == "__main__":
    main()

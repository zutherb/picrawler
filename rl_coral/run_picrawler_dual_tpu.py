"""50 Hz gait control on the robot: vision on TPU 0, gait policy on TPU 1.

    sudo systemctl stop picrawler.target        # body, vision and sensors own the hardware otherwise
    python -m rl_coral.run_picrawler_dual_tpu \
        --policy rl_coral/models/gait_policy_int8_edgetpu.tflite \
        --model ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite --labels coco_labels.txt

Every 20 ms: newest vision features and command -> 32-dim observation ->
int8 -> policy on pci:1 -> 12 offsets (+-15 mm) on top of the CPG trot ->
Picrawler.do_step(). Ctrl-C or SIGTERM brings the feet back to the stance
and sits down.

This bypasses the MQTT stack (arbiter, body interlock, heartbeat). It keeps
its own guards: forward commands are scaled down to zero within 15 cm of an
obstacle, the robot stops when the camera reports a strong tilt or the
vision thread goes stale, and it refuses to start while the PiCrawler
services are running.

With only one Edge TPU visible (the HatDrive passes one PCIe lane, see
spikes/coral), use --policy-device cpu with gait_policy_int8.tflite: the
policy is 7k parameters and runs in well under a millisecond on the CPU.
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time

import numpy as np

from rl_coral import common
from rl_coral.vision_proprioception_tpu0 import add_arguments, make_tracker

EDGETPU_LIB = "libedgetpu.so.1"
HARDWARE_UNITS = ("picrawler-body.service", "picrawler-vision.service", "picrawler-sensor@ultrasonic.service")
TILT_STOP = 0.45                 # rad of high-passed roll/pitch, held for TILT_HOLD_S
TILT_HOLD_S = 0.2
EASE_S = 1.0
SERVO_SPEED = 100                # robot_hat servo_move: one interpolation step (~10 ms)


class GaitPolicy:
    """int8 TFLite policy; quantizes the observation and dequantizes the action."""

    def __init__(self, path, device="pci:1"):
        from ai_edge_litert.interpreter import Interpreter, load_delegate
        delegates = [] if device == "cpu" else [load_delegate(EDGETPU_LIB, {"device": device})]
        self.interpreter = Interpreter(model_path=path, experimental_delegates=delegates)
        self.interpreter.allocate_tensors()
        inp = self.interpreter.get_input_details()[0]
        out = self.interpreter.get_output_details()[0]
        if inp["dtype"] != np.int8 or out["dtype"] != np.int8:
            raise ValueError("%s is not an int8 model (%s -> %s)" % (path, inp["dtype"], out["dtype"]))
        if tuple(inp["shape"]) != (1, common.OBS_DIM) or tuple(out["shape"]) != (1, common.ACT_DIM):
            raise ValueError("%s has shape %s -> %s, expected (1, 32) -> (1, 12)"
                             % (path, tuple(inp["shape"]), tuple(out["shape"])))
        self.input_index, self.output_index = inp["index"], out["index"]
        self.in_q, self.out_q = inp["quantization"], out["quantization"]
        self.device = device

    def __call__(self, obs):
        """float32 (32,) -> float32 (12,) in [-1, 1] (x OFFSET_MM = mm)."""
        self.interpreter.set_tensor(self.input_index, common.quantize(obs.reshape(1, -1), *self.in_q))
        self.interpreter.invoke()
        action = common.dequantize(self.interpreter.get_tensor(self.output_index), *self.out_q)
        return np.clip(action.reshape(-1), -1.0, 1.0)


class StaticTracker:
    """Stands in for the camera thread with --no-camera: level, nothing ahead."""

    def __init__(self):
        self.ts = time.monotonic()

    def start(self):
        pass

    def stop(self):
        pass

    def snapshot(self):
        return {"features": np.array([0.0, 0.0, 0.0, 1.0], np.float32), "cmd": (0.0, 0.0), "age": 0.0,
                "stale": False, "error": None, "flow_ms": 0.0, "detect_ms": 0.0, "fps": 0.0,
                "bands": [], "ultrasonic_cm": None, "ts": self.ts}


def services_running():
    try:
        result = subprocess.run(["systemctl", "is-active", *HARDWARE_UNITS], capture_output=True, text=True)
    except FileNotFoundError:
        return []
    return [unit for unit, state in zip(HARDWARE_UNITS, result.stdout.split()) if state == "active"]


def ease(crawler, target, seconds=EASE_S):
    """Move all feet linearly from where they are to target (leg frame)."""
    start = np.array(crawler.current_coord, np.float32)
    end = np.array(target, np.float32)
    steps = max(1, int(seconds / common.DT))
    for i in range(1, steps + 1):
        frame = start + (end - start) * i / steps
        crawler.do_step([[round(float(v), 2) for v in leg] for leg in frame], speed=SERVO_SPEED)
        time.sleep(common.DT)


class ControlLoop:

    def __init__(self, crawler, policy, tracker, fixed_cmd=None, log_every_s=5.0, mapper=None):
        self.crawler = crawler
        self.mapper = mapper
        self.policy = policy
        self.tracker = tracker
        self.fixed_cmd = fixed_cmd
        self.log_every_s = log_every_s
        self.stop_event = threading.Event()
        self.reason = None

    def stop(self, reason):
        if self.reason is None:
            self.reason = reason
        self.stop_event.set()

    def run(self, max_seconds=None):
        last_action = np.zeros(common.ACT_DIM, np.float32)
        prev_action = np.zeros(common.ACT_DIM, np.float32)
        tilted_since = None
        step = 0
        stats = {"invoke": [], "servo": [], "map": [], "overruns": 0}
        last_frame = None
        started = next_tick = log_t = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            if max_seconds is not None and now - started >= max_seconds:
                self.stop("time limit")
                break
            snap = self.tracker.snapshot()
            if snap.get("error"):
                self.stop("vision failed: %s" % snap["error"])
                break
            vision = snap["features"]
            if snap["stale"]:
                vision = np.array([0.0, 0.0, 0.0, 0.0], np.float32)     # unknown ahead: no forward motion
                cmd = (0.0, 0.0)
            else:
                cmd = self.fixed_cmd if self.fixed_cmd is not None else snap["cmd"]
            if max(abs(vision[0]), abs(vision[1])) > TILT_STOP:
                tilted_since = tilted_since or now
                if now - tilted_since >= TILT_HOLD_S:
                    self.stop("tilt roll %.2f pitch %.2f rad" % (vision[0], vision[1]))
                    break
            else:
                tilted_since = None

            phase = common.phase_at(step)
            vx = common.effective_vx(cmd[0], float(vision[3]))
            wz = cmd[1]
            obs = common.build_observation(phase, vx, wz, vision, last_action, prev_action)
            t0 = time.perf_counter()
            action = self.policy(obs)
            t1 = time.perf_counter()
            self.crawler.do_step(common.foot_targets(phase, vx, wz, action), speed=SERVO_SPEED)
            t2 = time.perf_counter()
            if self.mapper is not None:
                self.mapper.update_odometry(phase, vx, wz, action, float(vision[2]), not snap["stale"])
                if not snap["stale"] and snap.get("ts") != last_frame:
                    last_frame = snap.get("ts")
                    self.mapper.update_scan(snap.get("bands") or [], snap.get("ultrasonic_cm"),
                                            float(vision[0]), float(vision[1]))
                stats["map"].append((time.perf_counter() - t2) * 1000)
            stats["invoke"].append((t1 - t0) * 1000)
            stats["servo"].append((t2 - t1) * 1000)
            prev_action, last_action = last_action, action
            step += 1

            next_tick += common.DT
            delay = next_tick - time.monotonic()
            if delay > 0:
                self.stop_event.wait(delay)
            else:
                stats["overruns"] += 1
                if delay < -5 * common.DT:              # far behind: drop the backlog, keep the rhythm
                    next_tick = time.monotonic()
            if time.monotonic() - log_t >= self.log_every_s:
                elapsed = time.monotonic() - log_t
                print("%.1f Hz  invoke %.2f ms  servo %.1f ms (max %.1f)  overruns %d  cmd %+.2f %+.2f  "
                      "vision %s age %.0f ms  obstacle %.2f"
                      % (len(stats["invoke"]) / elapsed, np.mean(stats["invoke"]), np.mean(stats["servo"]),
                         max(stats["servo"]), stats["overruns"], vx, wz,
                         np.round(vision[:3], 3).tolist(), snap["age"] * 1000, vision[3]), flush=True)
                if self.mapper is not None:
                    x, y, theta = self.mapper.pose
                    print("  map %.2f ms  pose x %+.2f y %+.2f m theta %+.0f deg  scans %d skipped %d"
                          % (np.mean(stats["map"]), x, y, np.degrees(theta), self.mapper.scans,
                             self.mapper.skipped), flush=True)
                stats = {"invoke": [], "servo": [], "map": [], "overruns": 0}
                log_t = time.monotonic()
        return step


def main(argv=None):
    parser = argparse.ArgumentParser(description="50 Hz RL gait on the PiCrawler (vision TPU 0, policy TPU 1).")
    parser.add_argument("--policy", default=os.path.join(os.path.dirname(__file__), "models",
                                                         "gait_policy_int8_edgetpu.tflite"))
    parser.add_argument("--policy-device", default="pci:1", help='"pci:1", or "cpu" for the plain int8 model')
    parser.add_argument("--cmd", type=float, nargs=2, metavar=("VX", "WZ"),
                        help="fixed command in [-1, 1] instead of following --target")
    parser.add_argument("--no-camera", action="store_true", help="no vision thread (level, free ahead): bench test only")
    parser.add_argument("--seconds", type=float, help="stop after this long")
    parser.add_argument("--force", action="store_true", help="start even if PiCrawler services are active")
    parser.add_argument("--dry-run", action="store_true", help="fake robot_hat, no servos (for a laptop)")
    parser.add_argument("--map", action="store_true", help="build the 2D occupancy grid while walking")
    parser.add_argument("--map-host", default="127.0.0.1")
    parser.add_argument("--map-port", type=int, default=8090, help="/map.png and /map.mjpg (0: no server)")
    parser.add_argument("--map-save", help="write the final map as PNG here on exit")
    add_arguments(parser)
    args = parser.parse_args(argv)
    if args.model and not args.labels:
        parser.error("--model needs --labels")

    running = services_running()
    if running and not args.force:
        sys.exit("refusing to start, these own the hardware: %s\n  sudo systemctl stop picrawler.target"
                 % ", ".join(running))
    if args.dry_run:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
        import fake_robot_hat
        fake_robot_hat.install()

    policy = GaitPolicy(args.policy, args.policy_device)
    print("policy %s on %s" % (os.path.basename(args.policy), args.policy_device))
    tracker = StaticTracker() if args.no_camera else make_tracker(args)
    from picrawler import Picrawler
    crawler = Picrawler()
    mapper = server = None
    if args.map or args.map_save:
        from rl_coral.occupancy_mapper_2d import MapStreamServer, OccupancyMapper2D
        mapper = OccupancyMapper2D(fov_deg=args.hfov)
        if args.map_port:
            server = MapStreamServer(mapper, args.map_host, args.map_port)
            server.start()
            print("map on http://%s:%d/map.mjpg" % (args.map_host, args.map_port))
    loop = ControlLoop(crawler, policy, tracker, fixed_cmd=tuple(args.cmd) if args.cmd else None, mapper=mapper)

    def on_signal(signum, _frame):
        loop.stop(signal.Signals(signum).name)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    tracker.start()
    stance = common.foot_targets(0.0, 0.0, 0.0, np.zeros(common.ACT_DIM))
    try:
        crawler.do_action("stand", speed=60)
        ease(crawler, stance)
        if not args.no_camera:
            deadline = time.monotonic() + 5.0
            while tracker.snapshot()["stale"] and time.monotonic() < deadline and not loop.stop_event.is_set():
                time.sleep(0.05)
        steps = loop.run(max_seconds=args.seconds)
        print("stopped after %d steps: %s" % (steps, loop.reason))
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)     # finish sitting down even on a second Ctrl-C
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        tracker.stop()
        if server is not None:
            server.stop()
        if mapper is not None and args.map_save:
            mapper.save_png(args.map_save)
            print("map saved to %s" % args.map_save)
        try:
            ease(crawler, stance, seconds=0.5)
            crawler.do_step("sit", speed=40)
        except Exception as e:
            print("could not sit down: %s" % e, file=sys.stderr)


if __name__ == "__main__":
    main()

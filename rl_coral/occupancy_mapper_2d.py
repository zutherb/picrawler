"""2D log-odds occupancy grid from leg odometry, optical flow, floor edge and ultrasonic.

    mapper = OccupancyMapper2D()
    mapper.update_odometry(phase, vx, wz, action, flow_yaw, vision_fresh)   # every control step
    mapper.update_scan(bands, ultrasonic_cm, vis_roll, vis_pitch)           # every new camera frame
    mapper.get_probability_map()                                            # RGB uint8 for PNG / MJPEG

Pose: SE(2) [x, y, theta] in m / rad. The map starts with the crawler in
the middle, facing up (+y); theta is counter-clockwise.

Odometry: the stance feet are fixed on the floor, so their motion in the
body frame between two control steps (from what was actually commanded,
CPG + policy offsets) is the body's motion reversed. A 2D rigid fit over
the feet that were on the ground in both steps gives dx, dy, dtheta; the
slip factors scale translation and rotation (feet slide, about 0.8 and 0.9
in simulation; calibrate on the real floor). dtheta is blended with flow_yaw * dt (complementary filter), because leg
slip is worst while turning.

Scan: the floor edge per camera band (services/vision/floor.py via the
VisionStateTracker) and the ultrasonic distance become a 31-ray fan across
the camera's field of view. Each ray clears the cells on its way
(log-odds -0.4) and marks the hit (+0.85), clamped to [-4, 4]. Frames
taken while the body rocks (|roll| or |pitch| > 0.12 rad) are skipped:
a tilted camera sees the floor edge at the wrong distance.

It is dead reckoning: without loop closure the map drifts with the pose
(see docs/target-architecture.md, local map). Good for the last few metres.
"""
import http.server
import io
import math
import socketserver
import threading
import time

import numpy as np

from rl_coral import common

CELLS = 200
RESOLUTION_M = 0.02
L_FREE = -0.4
L_OCC = 0.85
L_MIN, L_MAX = -4.0, 4.0
RAYS = 31
TILT_GATE = 0.12                    # rad
SCAN_RANGE_M = 1.0                  # floor edge is trusted this far (OBSTACLE_RANGE_M)
ULTRASONIC_RANGE_M = 1.5
ULTRASONIC_HALF_BEAM_DEG = 7.5
CAMERA_FORWARD_M = 0.065            # camera / sonar in front of the body center
SLIP = 0.8
TURN_SLIP = 0.9                     # commanded vs actual rotation in simulation
FLOW_WEIGHT = 0.7                   # share of flow_yaw in the heading update
FOOTPRINT_M = 0.11                  # torso


def bresenham(x0, y0, x1, y1):
    """Integer cells from (x0, y0) to (x1, y1), both included, 8-connected.

    Bresenham's choice of cells, computed for all steps at once: along the
    major axis every cell, on the minor axis the integer nearest to the
    ideal line (ties rounded up, like the classic error-term version).
    """
    dx, dy = int(x1) - int(x0), int(y1) - int(y0)
    n = max(abs(dx), abs(dy))
    if n == 0:
        return np.array([x0], np.int64), np.array([y0], np.int64)
    i = np.arange(n + 1, dtype=np.int64)
    return int(x0) + (2 * i * dx + n) // (2 * n), int(y0) + (2 * i * dy + n) // (2 * n)


def rigid_fit_2d(p, q):
    """R (angle) and t with q ~ R p + t, least squares (Kabsch in 2D)."""
    pc, qc = p.mean(axis=0), q.mean(axis=0)
    a, b = p - pc, q - qc
    angle = math.atan2(float(np.sum(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])),
                       float(np.sum(a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1])))
    c, s = math.cos(angle), math.sin(angle)
    t = qc - np.array([c * pc[0] - s * pc[1], s * pc[0] + c * pc[1]])
    return angle, t


class OccupancyMapper2D:

    def __init__(self, cells=CELLS, resolution=RESOLUTION_M, fov_deg=60.0, rays=RAYS, slip=SLIP,
                 flow_weight=FLOW_WEIGHT, turn_slip=TURN_SLIP):
        self.cells = cells
        self.resolution = resolution
        self.log_odds = np.zeros((cells, cells), np.float32)      # [row = y, col = x], row 0 = -y edge
        self.pose = np.zeros(3)                                   # x, y (m), theta (rad)
        self.ray_bearings = np.radians(np.linspace(-fov_deg / 2.0, fov_deg / 2.0, rays))
        self.slip = slip
        self.turn_slip = turn_slip
        self.flow_weight = flow_weight
        self._last_feet = None
        self._lock = threading.Lock()
        self.scans = 0
        self.skipped = 0
        self.path = []

    # --- pose ---

    def update_odometry(self, phase, vx_cmd, wz_cmd, action, flow_yaw=0.0, vision_fresh=False, dt=common.DT):
        """Advance the pose by one control step from the commanded feet."""
        feet = common.body_coords(phase, vx_cmd, wz_cmd, action)[:, :2] + common.HIP_POS_MM
        stance = common.stance_mask(phase, vx_cmd, wz_cmd)
        last, self._last_feet = self._last_feet, (feet, stance)
        if last is None:
            return self.pose.copy()
        both = stance & last[1]
        if both.sum() >= 2:
            angle, t = rigid_fit_2d(last[0][both], feet[both])
            # feet moved by (angle, t) in the body frame -> the body moved by the inverse
            c, s = math.cos(-angle), math.sin(-angle)
            move = -np.array([c * t[0] - s * t[1], s * t[0] + c * t[1]]) / 1000.0 * self.slip
            d_theta = -angle * self.turn_slip
        else:
            move, d_theta = np.zeros(2), 0.0
        if vision_fresh:
            d_theta = (1.0 - self.flow_weight) * d_theta + self.flow_weight * flow_yaw * dt
        with self._lock:
            x, y, theta = self.pose
            mid = theta + d_theta / 2.0
            c, s = math.cos(mid), math.sin(mid)
            # body x is right, y forward; at theta = 0 forward is world +y
            self.pose = np.array([x + c * move[0] - s * move[1], y + s * move[0] + c * move[1], theta + d_theta])
            if not self.path or np.hypot(*(self.pose[:2] - self.path[-1])) > self.resolution:
                self.path.append(self.pose[:2].copy())
            return self.pose.copy()

    # --- scan ---

    def virtual_scan(self, bands, ultrasonic_cm=None):
        """[(bearing rad, distance m, hit)] for the 31 rays.

        bands: [(bearing_deg, cm or None)] from floor.boundaries (None = free
        to SCAN_RANGE_M). A ray takes the nearest band; rays within the
        ultrasonic beam also take the sonar reading if it is closer.
        """
        band_bearings = np.radians([b for b, _ in bands]) if bands else np.array([])
        scan = []
        for bearing in self.ray_bearings:
            dist, hit = SCAN_RANGE_M, False
            if len(band_bearings):
                cm = bands[int(np.argmin(np.abs(band_bearings - bearing)))][1]
                if cm is not None and cm / 100.0 < SCAN_RANGE_M:
                    dist, hit = cm / 100.0, True
            if ultrasonic_cm is not None and abs(math.degrees(bearing)) <= ULTRASONIC_HALF_BEAM_DEG:
                sonar = ultrasonic_cm / 100.0
                if sonar < min(dist, ULTRASONIC_RANGE_M) or (not hit and sonar <= ULTRASONIC_RANGE_M):
                    dist, hit = sonar, True
            scan.append((float(bearing), float(dist), hit))
        return scan

    def update_scan(self, bands, ultrasonic_cm=None, vis_roll=0.0, vis_pitch=0.0):
        """Raycast one frame into the grid. Returns False if the tilt gate skipped it."""
        if abs(vis_pitch) > TILT_GATE or abs(vis_roll) > TILT_GATE:
            self.skipped += 1
            return False
        with self._lock:
            x, y, theta = self.pose
        ox, oy = x - math.sin(theta) * CAMERA_FORWARD_M, y + math.cos(theta) * CAMERA_FORWARD_M
        origin = self.world_to_cell(ox, oy)
        free, occupied = [], []
        for bearing, dist, hit in self.virtual_scan(bands, ultrasonic_cm):
            d = dist * math.cos(vis_pitch)                       # horizontal distance
            heading = theta + bearing                            # forward is +y at theta = 0
            end = self.world_to_cell(ox - math.sin(heading) * d, oy + math.cos(heading) * d)
            cols, rows = bresenham(origin[0], origin[1], end[0], end[1])
            inside = (cols >= 0) & (cols < self.cells) & (rows >= 0) & (rows < self.cells)
            flat = rows * self.cells + cols
            if hit:
                free.append(flat[:-1][inside[:-1]])
                if inside[-1]:
                    occupied.append(flat[-1:])
            else:
                free.append(flat[inside])
        occupied = np.unique(np.concatenate(occupied)) if occupied else np.array([], np.int64)
        free = np.setdiff1d(np.unique(np.concatenate(free)) if free else np.array([], np.int64), occupied)
        with self._lock:
            grid = self.log_odds.reshape(-1)
            grid[free] += L_FREE
            grid[occupied] += L_OCC
            np.clip(self.log_odds, L_MIN, L_MAX, out=self.log_odds)
            self.scans += 1
        return True

    def world_to_cell(self, x, y):
        half = self.cells // 2
        return int(math.floor(x / self.resolution)) + half, int(math.floor(y / self.resolution)) + half

    # --- output ---

    def probability(self):
        with self._lock:
            return 1.0 / (1.0 + np.exp(-self.log_odds))

    def get_probability_map(self):
        """(cells, cells, 3) uint8 RGB, +y up: free light, walls dark, unknown grey.

        The crawler's footprint is drawn in teal with its heading in orange,
        the path so far in blue.
        """
        p = self.probability()
        gray = (255 * (1.0 - p)).astype(np.uint8)
        img = np.repeat(gray[::-1, :, None], 3, axis=2)          # flip: row 0 = top = +y
        with self._lock:
            x, y, theta = self.pose
            path = list(self.path)

        def put(cx, cy, color):
            col, row = cx, self.cells - 1 - cy
            if 0 <= col < self.cells and 0 <= row < self.cells:
                img[row, col] = color

        for px, py in path:
            put(*self.world_to_cell(px, py), (70, 130, 220))
        half = FOOTPRINT_M / 2.0
        c, s = math.cos(theta), math.sin(theta)
        corners = [(x + c * bx - s * by, y + s * bx + c * by)
                   for bx, by in ((-half, -half), (half, -half), (half, half), (-half, half))]
        for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
            a, b = self.world_to_cell(ax, ay), self.world_to_cell(bx, by)
            for cx, cy in zip(*bresenham(a[0], a[1], b[0], b[1])):
                put(cx, cy, (20, 160, 160))
        tip = self.world_to_cell(x - s * 0.12, y + c * 0.12)
        start = self.world_to_cell(x, y)
        for cx, cy in zip(*bresenham(start[0], start[1], tip[0], tip[1])):
            put(cx, cy, (240, 140, 30))
        return img

    def encode(self, fmt=".png", scale=3):
        """The map as PNG or JPEG bytes (needs OpenCV), scaled up for viewing."""
        import cv2
        img = self.get_probability_map()
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        ok, data = cv2.imencode(fmt, img[..., ::-1])
        if not ok:
            raise RuntimeError("could not encode the map")
        return data.tobytes()

    def save_png(self, path, scale=3):
        with open(path, "wb") as f:
            f.write(self.encode(".png", scale))


class MapStreamServer(threading.Thread):
    """GET /map.png (snapshot) and /map.mjpg (live, 5 fps) for debugging.

    Binds to 127.0.0.1 by default; use an SSH tunnel to look at it, as with
    the vision service's stream.
    """

    def __init__(self, mapper, host="127.0.0.1", port=8090, fps=5.0):
        super().__init__(daemon=True, name="map-stream")
        mapper_ref, period = mapper, 1.0 / fps

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/map.png"):
                    body = mapper_ref.encode(".png")
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/map.mjpg"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    try:
                        while True:
                            jpeg = mapper_ref.encode(".jpg")
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                                             % len(jpeg) + jpeg + b"\r\n")
                            time.sleep(period)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self.send_error(404)

            def log_message(self, *args):
                pass

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server((host, port), Handler)

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()

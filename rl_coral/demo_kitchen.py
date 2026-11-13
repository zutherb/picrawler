"""Kitchen demo: camera floor edge, ultrasonic and detected objects build the 2D map.

    python -m rl_coral.demo_kitchen                       # -> rl_coral/models/kitchen_demo.zip + .mp4
    python -m services.web --config web.toml --fake --demo rl_coral/models/kitchen_demo.zip

The crawler walks a simulated kitchen (PyBullet) with the trained gait
policy and a waypoint follower. Every 0.1 s the recording stores what the
robot would publish: the camera picture, detections, the ultrasonic
distance and a robot-centred view of the rolling map (map/local format plus
coloured rays). The web mock replays it in the real UI; the MP4 shows camera,
map and the true kitchen side by side.

Sensors are simulated the way the robot sees them: the floor edge per
column band from rays just above the floor, the ultrasonic sensor as a
+-7.5 degree cone 5 cm up (it passes over low things), detections only for
COCO classes, boxed from the segmentation mask (so occluded parts do not
count), and their distance from the box's bottom edge via
services/vision/geometry.locate, like on the robot.

Needs requirements-train.txt plus Pillow; the MP4 needs ffmpeg.
"""
import argparse
import io
import json
import math
import os
import shutil
import subprocess
import zipfile

import numpy as np

from rl_coral import common
from rl_coral.occupancy_mapper_2d import CAMERA_FORWARD_M, ULTRASONIC_HALF_BEAM_DEG, OccupancyMapper2D
from services.vision.geometry import CameraGeometry, locate

HERE = os.path.dirname(os.path.abspath(__file__))
FPS = 10
CAM_W, CAM_H_PX = 640, 360                      # 16:9 like the robot's stream
HFOV, VFOV, TILT_DEG, CAM_HEIGHT_M = 53.5, 31.0, 8.0, 0.09
FLOOR_RAY_M, SONAR_HEIGHT_M = 0.015, 0.05
CAM_RANGE_M, SONAR_RANGE_M = 1.0, 1.5
MAP_RADIUS_CM, MAP_CELL_CM = 150, 5
MIN_BOX_PX = 400
OBJECT_MERGE_M = 0.5            # box bottoms jump between legs of big objects
OBJECT_MERGE_LARGE_M = {"dining table": 0.8, "refrigerator": 0.8}
WAYPOINTS = [(0.0, 0.45), (-0.55, 0.4), (-0.55, -0.45), (0.0, -0.3), (0.6, -0.2), (0.62, 0.42), (0.2, 0.42)]
BAND_BEARINGS = np.linspace(HFOV / 2, -HFOV / 2, 31)


class Kitchen:
    """The room, built in the crawler's start frame (x right, y forward, m)."""

    def __init__(self, env):
        import pybullet as p
        self.p, self.env, self.c = p, env, env.client
        pos, orn = p.getBasePositionAndOrientation(env.robot, physicsClientId=self.c)
        self.origin = np.array(pos[:2])
        self.yaw0 = p.getEulerFromQuaternion(orn)[2]
        self.rot = np.array([[math.cos(self.yaw0), -math.sin(self.yaw0)], [math.sin(self.yaw0), math.cos(self.yaw0)]])
        self.items = []             # (label, kind, geometry, rgb) for the top view
        self.classes = {}           # body id -> (coco label, instance)
        wall = [0.85, 0.85, 0.8]
        wood = [0.55, 0.42, 0.3]
        for x0, y0, x1, y1 in ((-1.55, 1.4, 1.55, 1.45), (-1.55, -1.05, 1.55, -1.0),
                               (-1.55, -1.05, -1.5, 1.45), (1.5, -1.05, 1.55, 1.45)):
            self.box(None, x0, y0, x1, y1, 0.6, wall)
        self.box("Arbeitsplatte", -1.5, 0.8, 0.6, 1.4, 0.9, wood)
        self.box("Kühlschrank", 0.75, 0.75, 1.5, 1.4, 1.2, [0.9, 0.92, 0.95], coco=("refrigerator", 1))
        self.box("Unterschrank", -1.5, -1.0, -0.95, 0.8, 0.9, wood)
        self.legged("Tisch", ("dining table", 1), 0.6, -0.2, 0.8, 0.6, 0.72, 0.035, [0.75, 0.6, 0.4])
        self.legged("Stuhl", ("chair", 1), 1.22, -0.2, 0.36, 0.36, 0.45, 0.022, [0.4, 0.45, 0.6])
        self.legged("Stuhl", ("chair", 2), 0.6, -0.78, 0.36, 0.36, 0.45, 0.022, [0.4, 0.45, 0.6])
        self.cylinder("Napf", ("bowl", 1), -0.72, -0.72, 0.08, 0.035, [0.85, 0.3, 0.3])
        self.cylinder("Mülleimer", None, 1.25, -0.75, 0.14, 0.5, [0.3, 0.3, 0.32])      # no COCO class

    def to_world(self, x, y):
        return (self.rot @ np.array([x, y]) + self.origin).tolist()

    def to_local(self, xy):
        return self.rot.T @ (np.array(xy[:2]) - self.origin)

    def box(self, label, x0, y0, x1, y1, h, color, z0=0.0, coco=None, show=True):
        p = self.p
        half = [(x1 - x0) / 2, (y1 - y0) / 2, h / 2]
        body = p.createMultiBody(
            0, p.createCollisionShape(p.GEOM_BOX, halfExtents=half, physicsClientId=self.c),
            p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=[*color, 1], physicsClientId=self.c),
            self.to_world((x0 + x1) / 2, (y0 + y1) / 2) + [z0 + h / 2],
            p.getQuaternionFromEuler([0, 0, self.yaw0]), physicsClientId=self.c)
        if coco:
            self.classes[body] = coco
        if show:
            self.items.append((label, "box", (x0, y0, x1, y1), color))

    def cylinder(self, label, coco, x, y, r, h, color):
        p = self.p
        body = p.createMultiBody(
            0, p.createCollisionShape(p.GEOM_CYLINDER, radius=r, height=h, physicsClientId=self.c),
            p.createVisualShape(p.GEOM_CYLINDER, radius=r, length=h, rgbaColor=[*color, 1], physicsClientId=self.c),
            self.to_world(x, y) + [h / 2], physicsClientId=self.c)
        if coco:
            self.classes[body] = coco
        self.items.append((label, "circle", (x, y, r), color))

    def legged(self, label, coco, cx, cy, w, d, h, leg, color):
        self.box(label, cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2, 0.03, color, z0=h - 0.03, coco=coco)
        for sx in (-1, 1):
            for sy in (-1, 1):
                lx, ly = cx + sx * (w / 2 - leg), cy + sy * (d / 2 - leg)
                g = (lx - leg / 2, ly - leg / 2, lx + leg / 2, ly + leg / 2)
                self.box(None, *g, h - 0.03, [v * 0.7 for v in color], coco=coco, show=False)
                self.items.append((None, "leg", g, color))


class Robot:
    """Simulated sensors of the crawler in the kitchen."""

    def __init__(self, env, kitchen, rng):
        self.p, self.env, self.k, self.rng = kitchen.p, env, kitchen, rng
        self.geometry = CameraGeometry(CAM_W, CAM_H_PX, hfov_deg=HFOV, vfov_deg=VFOV,
                                       height_cm=CAM_HEIGHT_M * 100, tilt_deg=TILT_DEG)

    def pose(self):
        pos, orn = self.p.getBasePositionAndOrientation(self.env.robot, physicsClientId=self.env.client)
        x, y = self.k.to_local(pos)
        return x, y, self.p.getEulerFromQuaternion(orn)[2] - self.k.yaw0

    def _camera_frame(self):
        st = self.env._state()
        eye = st["pos"] + st["rot"] @ np.array([0, CAMERA_FORWARD_M, 0])
        fwd = st["rot"][:, 1].copy()
        fwd[2] = 0
        return eye, fwd / np.linalg.norm(fwd)

    def cast(self, height, bearings, max_range):
        eye, fwd = self._camera_frame()
        start = eye.copy()
        start[2] = height
        froms, tos = [], []
        for b in bearings:
            a = math.radians(b)
            d = np.array([fwd[0] * math.cos(a) - fwd[1] * math.sin(a), fwd[0] * math.sin(a) + fwd[1] * math.cos(a), 0])
            froms.append(start.tolist())
            tos.append((start + d * max_range).tolist())
        hits = self.p.rayTestBatch(froms, tos, physicsClientId=self.env.client)
        return [None if h[0] < 0 or h[0] == self.env.robot else h[2] * max_range for h in hits]

    def floor_and_sonar(self):
        floor = self.cast(FLOOR_RAY_M, BAND_BEARINGS, CAM_RANGE_M)
        bands = [(float(b), None if d is None else float(d * 100 + self.rng.normal(0, 1.5)))
                 for b, d in zip(BAND_BEARINGS, floor)]
        cone = [d for d in self.cast(SONAR_HEIGHT_M, np.linspace(-7.5, 7.5, 7), SONAR_RANGE_M) if d is not None]
        sonar = float(min(cone) * 100 + self.rng.normal(0, 1.0)) if cone else None
        return bands, sonar

    def camera(self):
        """(RGB uint8 HxWx3, detections) from the robot's camera."""
        p = self.p
        eye, fwd = self._camera_frame()
        eye[2] = CAM_HEIGHT_M
        t = math.radians(TILT_DEG)
        target = eye + np.array([fwd[0] * math.cos(t), fwd[1] * math.cos(t), -math.sin(t)])
        view = p.computeViewMatrix(eye.tolist(), target.tolist(), [0, 0, 1], physicsClientId=self.env.client)
        proj = p.computeProjectionMatrixFOV(VFOV, CAM_W / CAM_H_PX, 0.01, 6.0, physicsClientId=self.env.client)
        w, h, rgba, _, seg = p.getCameraImage(CAM_W, CAM_H_PX, view, proj, renderer=p.ER_TINY_RENDERER,
                                              physicsClientId=self.env.client)
        rgb = np.reshape(rgba, (h, w, 4))[:, :, :3].astype(np.uint8)
        seg = np.reshape(seg, (h, w))
        instances = {}
        for body, key in self.k.classes.items():
            mask = seg == body
            if mask.any():
                instances.setdefault(key, []).append(mask)
        objects = []
        for (label, _), masks in instances.items():
            mask = np.logical_or.reduce(masks)
            area = int(mask.sum())
            if area < MIN_BOX_PX:
                continue
            rows, cols = np.nonzero(mask)
            score = min(0.97, 0.55 + 0.4 * min(1.0, area / 25000) + self.rng.normal(0, 0.03))
            objects.append({"label": label, "score": round(float(score), 3),
                            "box": [round(cols.min() / w, 4), round(rows.min() / h, 4),
                                    round((cols.max() + 1) / w, 4), round((rows.max() + 1) / h, 4)]})
        locate(objects, self.geometry, CAM_W, CAM_H_PX)
        return rgb, objects


class ObjectMemory:
    """Detected objects pinned into the map frame, merged per label."""

    def __init__(self):
        self.objects = []           # {"label", "x", "y", "n", "score"}

    def add(self, detections, pose):
        x, y, theta = pose
        for o in detections:
            if o.get("floor_cm") is None:
                continue
            d = o["floor_cm"] / 100.0 + 0.03
            a = theta + math.radians(o["bearing_deg"])
            cx, cy = x - math.sin(theta) * CAMERA_FORWARD_M, y + math.cos(theta) * CAMERA_FORWARD_M
            ox, oy = cx - math.sin(a) * d, cy + math.cos(a) * d
            near = [m for m in self.objects if m["label"] == o["label"]
                    and math.hypot(m["x"] - ox, m["y"] - oy) < OBJECT_MERGE_LARGE_M.get(o["label"], OBJECT_MERGE_M)]
            if near:
                m = near[0]
                m["n"] += 1
                k = 1.0 / min(m["n"], 20)
                m["x"] += k * (ox - m["x"])
                m["y"] += k * (oy - m["y"])
                m["score"] = max(m["score"], o["score"])
            else:
                self.objects.append({"label": o["label"], "x": ox, "y": oy, "n": 1, "score": o["score"]})

    def confirmed(self):
        return [m for m in self.objects if m["n"] >= 3]


def local_grid(mapper):
    """Robot-centred, heading-up view of the rolling map in map/local format."""
    n = 2 * MAP_RADIUS_CM // MAP_CELL_CM + 1
    half = n // 2
    x, y, theta = mapper.pose
    cols, rows = np.meshgrid(np.arange(n), np.arange(n))
    bx = (cols - half) * MAP_CELL_CM / 100.0
    by = (half - rows) * MAP_CELL_CM / 100.0
    c, s = math.cos(theta), math.sin(theta)
    wx, wy = x + c * bx - s * by, y + s * bx + c * by
    cx = np.floor(wx / mapper.resolution).astype(int) + mapper.cells // 2
    cy = np.floor(wy / mapper.resolution).astype(int) + mapper.cells // 2
    inside = (cx >= 0) & (cx < mapper.cells) & (cy >= 0) & (cy < mapper.cells)
    odds = np.zeros((n, n), np.float32)
    odds[inside] = mapper.log_odds[cy[inside], cx[inside]]
    chars = np.full((n, n), "?")
    chars[odds < -0.4] = "."
    chars[odds > 0.4] = "#"
    return ["".join(row) for row in chars]


def relative(mapper, wx, wy):
    """(heading_deg, cm) of a map point seen from the crawler."""
    x, y, theta = mapper.pose
    dx, dy = wx - x, wy - y
    heading = math.degrees(math.atan2(-dx, dy) - theta)
    return (heading + 180) % 360 - 180, math.hypot(dx, dy) * 100


def map_message(mapper, scan, sonar, memory, t, ts):
    rays = []
    for bearing, dist, hit in scan:
        sonar_ray = (sonar is not None and abs(math.degrees(bearing)) <= ULTRASONIC_HALF_BEAM_DEG
                     and abs(dist - sonar / 100.0) < 0.005)
        rays.append({"heading_deg": round(math.degrees(bearing), 1), "cm": round(dist * 100, 1), "hit": hit,
                     "source": "ultrasonic" if sonar_ray else "camera"})
    objects = []
    for m in memory.confirmed():
        heading, cm = relative(mapper, m["x"], m["y"])
        if cm <= MAP_RADIUS_CM:
            objects.append({"label": m["label"], "heading_deg": round(heading, 1), "cm": round(cm, 1)})
    hits = [r for r in rays if r["hit"]]
    nearest = min(hits, key=lambda r: r["cm"]) if hits else None
    return {
        "status": "live", "message": "Kitchen demo · %.0f s · map %d cells known · %d objects"
        % (t, int((np.abs(mapper.log_odds) > 0.4).sum()), len(memory.confirmed())),
        "progress": [0, 0], "turn_deg": 0, "samples": [{"heading_deg": r["heading_deg"],
                                                       "cm": r["cm"] if r["hit"] else None} for r in rays],
        "rays": rays, "floor": [], "objects": objects, "grid": local_grid(mapper), "cell_cm": MAP_CELL_CM,
        "radius_cm": MAP_RADIUS_CM, "nearest": [nearest["heading_deg"], nearest["cm"]] if nearest else None,
        "clearest_deg": None, "user": "demo", "ts": ts,
    }


class VideoPanels:
    """Camera | map | true kitchen, as in the MP4."""

    PANEL = 480
    TEAL, ORANGE, WHITE, BG = (40, 200, 190), (245, 150, 40), (240, 240, 240), (22, 27, 34)

    def __init__(self, kitchen, robot, mapper, memory):
        from PIL import ImageFont
        self.k, self.robot, self.mapper, self.memory = kitchen, robot, mapper, memory
        try:
            face = "/System/Library/Fonts/Helvetica.ttc"
            self.font, self.small, self.big = (ImageFont.truetype(face, s) for s in (17, 13, 21))
        except OSError:
            self.font = self.small = self.big = ImageFont.load_default()

    def frame(self, rgb, detections, bands, sonar, scan, true_path, est_path, t, cmd):
        from PIL import Image, ImageDraw
        P = self.PANEL
        frame = Image.new("RGB", (3 * P, P + 50), self.BG)
        frame.paste(self.camera(rgb, detections, bands, sonar), (0, 50))
        frame.paste(self.map(scan, sonar), (P, 50))
        frame.paste(self.truth(true_path, est_path), (2 * P, 50))
        d = ImageDraw.Draw(frame)
        d.text((14, 14), "PiCrawler-Simulation: Kamera, Ultraschall und erkannte Objekte bauen die 2D-Karte",
               fill=self.WHITE, font=self.big)
        d.text((3 * P - 14, 18), "%4.1f s   Befehl vx %+.1f  wz %+.1f" % (t, cmd[0], cmd[1]),
               fill=(190, 190, 190), font=self.font, anchor="ra")
        return np.asarray(frame)

    def camera(self, rgb, detections, bands, sonar):
        from PIL import Image, ImageDraw
        P = self.PANEL
        img = Image.fromarray(rgb).resize((P, 270))
        d = ImageDraw.Draw(img)
        for b, cm in bands:
            if cm is None:
                continue
            hit = self.robot.geometry
            u = hit.cx - hit.fx * math.tan(math.radians(b))
            v = hit.cy + hit.fy * math.tan(math.atan2(CAM_HEIGHT_M * 100 - FLOOR_RAY_M * 100, cm)
                                           - math.radians(TILT_DEG))
            u, v = u * P / CAM_W, v * 270 / CAM_H_PX
            if 0 <= v < 270:
                d.ellipse([u - 4, v - 4, u + 4, v + 4], outline=self.TEAL, width=2)
        placed = []
        for o in detections:
            x0, y0, x1, y1 = o["box"]
            d.rectangle([x0 * P, y0 * 270, x1 * P, y1 * 270], outline=(255, 220, 60), width=2)
        for o in detections:
            x0, y0, x1, y1 = o["box"]
            text = "%s %.0f%%" % (o["label"], o["score"] * 100)
            if o.get("floor_cm"):
                text += " · %.0f cm" % o["floor_cm"]
            tx, ty = x0 * P + 3, max(2, y0 * 270 - 18)
            box = d.textbbox((tx, ty), text, font=self.small)
            while any(box[0] < b[2] + 4 and b[0] < box[2] + 4 and box[1] < b[3] + 3 and b[1] < box[3] + 3
                      for b in placed):
                ty += 19
                box = d.textbbox((tx, ty), text, font=self.small)
            placed.append(box)
            d.rectangle([box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2], fill=(255, 220, 60))
            d.text((tx, ty), text, fill=(20, 20, 20), font=self.small)
        out = Image.new("RGB", (P, P), self.BG)
        out.paste(img, (0, 60))
        d = ImageDraw.Draw(out)
        d.text((12, 10), "Kamera (Roboter-Sicht) + Detektor", fill=self.WHITE, font=self.font)
        d.text((12, 360), "Kreise = Bodenkante · gelb = erkanntes Objekt (COCO) mit Entfernung", fill=self.TEAL,
               font=self.small)
        d.text((12, 382), "Kamera: %d/31 Spalten mit Hindernis bis 1 m · %d Objekte im Bild"
               % (sum(cm is not None for _, cm in bands), len(detections)), fill=self.WHITE, font=self.small)
        d.text((12, 404), "Ultraschall: %s (Kegel ±7,5°, 5 cm hoch, bis 1,5 m)"
               % ("%.0f cm" % sonar if sonar else "frei"), fill=self.ORANGE, font=self.small)
        return out

    def map(self, scan, sonar):
        from PIL import Image, ImageDraw
        P, m = self.PANEL, self.mapper
        crop, size, left = 160, self.PANEL - 80, 40
        img = Image.fromarray(m.get_probability_map()).crop((20, 20, 20 + crop, 20 + crop))
        img = img.resize((size, size), Image.NEAREST)
        out = Image.new("RGB", (P, P), self.BG)
        out.paste(img, (left, 30))
        d = ImageDraw.Draw(out)
        scale = size / (crop * m.resolution)
        ox, oy = left + (100 - 20) * size / crop, 30 + (100 - 20) * size / crop

        def px(x, y):
            return ox + x * scale, oy - y * scale

        x, y, th = m.pose
        cx, cy = x - math.sin(th) * CAMERA_FORWARD_M, y + math.cos(th) * CAMERA_FORWARD_M
        for bearing, dist, hit in scan:
            a = th + bearing
            sonar_ray = (sonar is not None and abs(math.degrees(bearing)) <= ULTRASONIC_HALF_BEAM_DEG
                         and abs(dist - sonar / 100) < 0.005)
            col = self.ORANGE if sonar_ray else self.TEAL
            end = px(cx - math.sin(a) * dist, cy + math.cos(a) * dist)
            d.line([px(cx, cy), end], fill=col, width=1)
            if hit:
                d.ellipse([end[0] - 2.5, end[1] - 2.5, end[0] + 2.5, end[1] + 2.5], fill=col)
        for o in self.memory.confirmed():
            mx, my = px(o["x"], o["y"])
            d.ellipse([mx - 6, my - 6, mx + 6, my + 6], fill=(255, 220, 60), outline=(20, 20, 20))
            box = d.textbbox((mx + 9, my - 8), o["label"], font=self.small)
            d.rectangle([box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1], fill=(20, 24, 30))
            d.text((mx + 9, my - 8), o["label"], fill=(255, 220, 60), font=self.small)
        d.text((12, 6), "2D-Karte (Schätzung, mitlaufend)", fill=self.WHITE, font=self.font)
        d.text((12, P - 30), "hell = frei · dunkel = belegt · grau = unbekannt · gelb = Objekt",
               fill=(190, 190, 190), font=self.small, anchor="ls")
        d.text((12, P - 10), "Strahlen: türkis = Kamera-Bodenkante", fill=self.TEAL, font=self.small, anchor="ls")
        d.text((262, P - 10), "orange = Ultraschall", fill=self.ORANGE, font=self.small, anchor="ls")
        return out

    def truth(self, true_path, est_path):
        from PIL import Image, ImageDraw
        P = self.PANEL
        out = Image.new("RGB", (P, P), self.BG)
        d = ImageDraw.Draw(out)
        scale = (P - 40) / 3.3
        ox, oy = P / 2, 30 + 1.55 * scale

        def px(x, y):
            return ox + x * scale, oy - y * scale

        d.rectangle([px(-1.5, 1.4), px(1.5, -1.0)], fill=(60, 64, 70))
        for label, kind, g, color in self.k.items:
            col = tuple(int(v * 255) for v in color)
            if kind in ("box", "leg"):
                (a, b), (e, f) = px(g[0], g[3]), px(g[2], g[1])
                d.rectangle([a, b, e, f], fill=col if kind == "leg" else None, outline=col, width=2)
            else:
                (a, b), r = px(g[0], g[1]), g[2] * scale
                d.ellipse([a - r, b - r, a + r, b + r], outline=col, width=2)
            if label:
                lx, ly = px((g[0] + g[2]) / 2, (g[1] + g[3]) / 2) if kind == "box" else px(g[0], g[1] - g[2] - 0.04)
                d.text((lx, ly), label, fill=(235, 235, 235), font=self.small, anchor="mm")
        if len(true_path) > 1:
            d.line([px(*q) for q in true_path], fill=self.WHITE, width=2)
        if len(est_path) > 1:
            d.line([px(*q) for q in est_path], fill=self.TEAL, width=2)
        x, y, th = self.robot.pose()
        tri = [(x + math.cos(th) * bx - math.sin(th) * by, y + math.sin(th) * bx + math.cos(th) * by)
               for bx, by in ((0, 0.09), (-0.06, -0.06), (0.06, -0.06))]
        d.polygon([px(*q) for q in tri], fill=self.ORANGE)
        ex, ey, eth = self.mapper.pose
        dth = (math.degrees(eth - th) + 180) % 360 - 180
        d.text((12, 6), "Wirklichkeit: Küche von oben", fill=self.WHITE, font=self.font)
        d.text((12, P - 22), "— weiß echter Weg   — türkis geschätzt   Abweichung %.0f cm / %.0f°"
               % (math.hypot(ex - x, ey - y) * 100, abs(dth)), fill=(190, 190, 190), font=self.small)
        return out


def jpeg(rgb, quality=80):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def run(policy, out_zip, out_mp4=None, seconds=62.0, seed=11):
    from stable_baselines3 import PPO
    from rl_coral.sim_env import PiCrawlerVisionEnv

    model = PPO.load(policy, device="cpu")
    env = PiCrawlerVisionEnv(randomize=True, obstacles=False)
    obs, _ = env.reset(seed=seed)
    rng = np.random.default_rng(seed)
    kitchen = Kitchen(env)
    robot = Robot(env, kitchen, rng)
    mapper = OccupancyMapper2D(fov_deg=HFOV)
    memory = ObjectMemory()
    panels = VideoPanels(kitchen, robot, mapper, memory) if out_mp4 else None
    ffmpeg = None
    if out_mp4:
        if shutil.which("ffmpeg") is None:
            print("ffmpeg not found, skipping the MP4")
        else:
            ffmpeg = subprocess.Popen(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "-s", "%dx%d" % (3 * VideoPanels.PANEL, VideoPanels.PANEL + 50), "-r", str(FPS), "-i", "-",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", out_mp4], stdin=subprocess.PIPE)

    archive = zipfile.ZipFile(out_zip, "w", zipfile.ZIP_STORED)
    events = []
    wp = 0
    true_path, est_path = [], []
    bands, sonar = robot.floor_and_sonar()
    scan = mapper.virtual_scan(bands, sonar)
    detections = []
    steps_per_frame = int(round(1 / (FPS * common.DT)))
    frame_no = 0
    vx = wz = 0.0
    for step in range(int(seconds / common.DT)):
        x, y, th = robot.pose()
        if wp < len(WAYPOINTS):
            tx, ty = WAYPOINTS[wp]
            if math.hypot(tx - x, ty - y) < 0.06:
                wp += 1
            err = (math.atan2(-(tx - x), ty - y) - th + math.pi) % (2 * math.pi) - math.pi
            vx, wz = (0.0, max(-1, min(1, 2.5 * err))) if abs(err) > 0.35 else (1.0, max(-1, min(1, 2.0 * err)))
        else:
            vx, wz = 0.0, 0.0
        env.cmd = (vx, wz)
        obs = env._observe()
        phase = env.phase
        vx_used, wz_used = env.cmd_used
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, _, _ = env.step(action)
        mapper.update_odometry(phase, vx_used, wz_used, np.tanh(action), float(obs[6]), True)
        if step % 2 == 0:                                   # camera at 25 Hz
            bands, sonar = robot.floor_and_sonar()
            scan = mapper.virtual_scan(bands, sonar)
            mapper.update_scan(bands, sonar, float(obs[4]), float(obs[5]))
        if step % 5 == 0:
            true_path.append(robot.pose()[:2])
            est_path.append(tuple(mapper.pose[:2]))
        if terminated:
            print("fell at %.1f s" % (step * common.DT))
            break
        if step % steps_per_frame == 0:
            t = step * common.DT
            rgb, detections = robot.camera()
            memory.add(detections, mapper.pose)
            archive.writestr("frames/%05d.jpg" % frame_no, jpeg(rgb))
            events.append({
                "t": round(t, 2), "frame": frame_no,
                "detections": [{k: o[k] for k in ("label", "score", "box", "bearing_deg", "floor_cm")}
                               for o in detections],
                "ultrasonic_cm": None if sonar is None else round(sonar, 1),
                "motion": {"status": "running" if (vx or wz) else "idle",
                           "name": "rl gait vx %+.1f wz %+.1f" % (vx, wz), "priority": "goal", "queue_len": 0},
                "map": map_message(mapper, scan, sonar, memory, t, 0),
            })
            if ffmpeg:
                ffmpeg.stdin.write(panels.frame(rgb, detections, bands, sonar, scan, true_path, est_path, t,
                                                (vx, wz)).tobytes())
            frame_no += 1
    archive.writestr("events.jsonl", "\n".join(json.dumps(e, separators=(",", ":")) for e in events) + "\n")
    archive.writestr("meta.json", json.dumps({"name": "kitchen", "fps": FPS, "frames": frame_no,
                                              "camera": [CAM_W, CAM_H_PX], "seed": seed}))
    archive.close()
    if ffmpeg:
        ffmpeg.stdin.close()
        ffmpeg.wait()
    x, y, _ = robot.pose()
    print("%d frames, waypoints %d/%d, drift %.0f cm, objects %s -> %s%s"
          % (frame_no, wp, len(WAYPOINTS), 100 * math.hypot(mapper.pose[0] - x, mapper.pose[1] - y),
             sorted({m["label"] for m in memory.confirmed()}), out_zip, " + " + out_mp4 if ffmpeg else ""))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record the kitchen mapping demo.")
    parser.add_argument("--policy", default=os.path.join(HERE, "models", "gait_policy_ppo.zip"))
    parser.add_argument("--out", default=os.path.join(HERE, "models", "kitchen_demo.zip"),
                        help="recording for: python -m services.web --fake --demo <file>")
    parser.add_argument("--video", default=os.path.join(HERE, "models", "kitchen_demo.mp4"),
                        help="MP4 with camera, map and the true kitchen ('' to skip)")
    parser.add_argument("--seconds", type=float, default=62.0)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    run(args.policy, args.out, args.video or None, args.seconds, args.seed)


if __name__ == "__main__":
    main()

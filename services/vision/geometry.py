"""Where a pixel lands on the floor, for a camera at a known height and tilt.

Pinhole model per axis, so it also holds for the lores stream, which is
the full field of view scaled to a square. Bearings are degrees, positive
to the left (counter-clockwise, like the arbiter's headings); distances
are cm on the floor from the point below the camera.

Calibrate once on the robot: put tape on the floor 30 cm and 60 cm in
front of the standing crawler and adjust tilt_deg (and height_cm) until
the reported floor distances match. Defaults are for the original camera
module (OV5647, 16:9 mode) on the PiCrawler's head.
"""
import math


class CameraGeometry:

    def __init__(self, width, height, hfov_deg=53.5, vfov_deg=31.0, height_cm=9.0, tilt_deg=8.0):
        self.width, self.height = width, height
        self.height_cm = height_cm
        self.tilt = math.radians(tilt_deg)          # positive: looking down
        self.fx = (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
        self.fy = (height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
        self.cx, self.cy = width / 2.0, height / 2.0

    @classmethod
    def from_config(cls, cfg, width, height):
        return cls(width, height, hfov_deg=cfg.get("hfov_deg", 53.5), vfov_deg=cfg.get("vfov_deg", 31.0),
                   height_cm=cfg.get("height_cm", 9.0), tilt_deg=cfg.get("tilt_deg", 8.0))

    def bearing(self, u):
        """Bearing of image column u (pixels)."""
        return -math.degrees(math.atan((u - self.cx) / self.fx))

    def ground(self, u, v):
        """(cm, bearing_deg) where pixel (u, v) meets the floor, or None above the horizon."""
        x = (u - self.cx) / self.fx                 # camera frame: x right, y down, z forward
        y = (v - self.cy) / self.fy
        down = y * math.cos(self.tilt) + math.sin(self.tilt)
        forward = -y * math.sin(self.tilt) + math.cos(self.tilt)
        if down <= 1e-6:
            return None
        scale = self.height_cm / down
        gx, gz = scale * x, scale * forward
        return math.hypot(gx, gz), math.degrees(math.atan2(-gx, gz))

    def nearest_floor_cm(self):
        """Closest floor point visible (bottom row, image center)."""
        hit = self.ground(self.cx, self.height - 0.5)
        return hit[0] if hit else None


def locate(objects, geometry, width, height):
    """Add bearing_deg and floor_cm (from the box's bottom edge) to detections.

    floor_cm is None when the box touches the image bottom (the object's
    foot is not visible) or the bottom edge is above the horizon.
    """
    for o in objects:
        box = o.get("box")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        x0, _, x1, y1 = box
        u = (x0 + x1) / 2.0 * width
        o["bearing_deg"] = round(geometry.bearing(u), 1)
        hit = geometry.ground(u, y1 * height) if y1 < 0.98 else None
        o["floor_cm"] = round(hit[0], 1) if hit else None
    return objects

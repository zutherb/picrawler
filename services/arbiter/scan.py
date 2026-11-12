"""Local 2D map from a sweep on the spot: ultrasonic plus camera.

The crawler turns step by step and measures after each step:

- ultrasonic: one reading as a cone (the sensor's beam). Cells closer
  than the echo are free, the arc at the echo is occupied. No echo means
  free up to max_cm.
- camera floor edge (sensor/vision/floor): one narrow ray per image band,
  free up to where the floor ends, occupied there. Sharper and wider than
  the ultrasonic beam, but blind to things that do not touch the floor.
- detected objects: label, heading and distance, shown as markers.

The map is robot-centric and only valid where the scan was made: legged
steps slip, so there is no position to merge several scans.

Headings are degrees counter-clockwise from the direction the crawler
faced when the scan started (turning left is positive).
"""
import math

UNKNOWN, FREE, OCCUPIED = "?", ".", "#"


def occupancy(samples, cell_cm=5.0, radius_cm=150.0, beam_deg=15.0, max_cm=200.0,
              floor=(), floor_beam_deg=2.5, floor_max_cm=150.0):
    """Grid as a list of strings, first row = straight ahead at the far end.

    samples (ultrasonic) and floor (camera): iterables of
    (heading_deg, cm or None). Cell characters are UNKNOWN, FREE and
    OCCUPIED; occupied wins over free.
    """
    n = int(round(2 * radius_cm / cell_cm))
    grid = [[UNKNOWN] * n for _ in range(n)]
    half = n / 2.0

    def cell(dist, angle):
        # angle 0 = up (row 0), counter-clockwise positive = to the left
        col = int(math.floor(half - dist * math.sin(angle) / cell_cm))
        row = int(math.floor(half - dist * math.cos(angle) / cell_cm))
        return (row, col) if 0 <= row < n and 0 <= col < n else None

    occupied = set()

    def cone(heading, cm, beam, limit):
        rays = max(3, int(math.ceil(beam / 2.0)) + 1)
        hit = cm is not None and cm <= limit
        reach = min(cm if hit else limit, radius_cm)
        for i in range(rays):
            angle = math.radians(heading - beam / 2.0 + beam * i / (rays - 1))
            d = 0.0
            while d < reach:
                rc = cell(d, angle)
                if rc and grid[rc[0]][rc[1]] == UNKNOWN:
                    grid[rc[0]][rc[1]] = FREE
                d += cell_cm / 2.0
            if hit and cm <= radius_cm:
                rc = cell(cm, angle)
                if rc:
                    occupied.add(rc)

    for heading, cm in samples:
        cone(heading, cm, beam_deg, max_cm)
    for heading, cm in floor:
        cone(heading, cm, floor_beam_deg, floor_max_cm)
    for row, col in occupied:
        grid[row][col] = OCCUPIED
    return ["".join(row) for row in grid]


def merge_objects(objects, within_deg=20.0):
    """One marker per label and direction: the best-scoring sighting wins."""
    kept = []
    for o in sorted(objects, key=lambda o: -o.get("score", 0)):
        if not any(k["label"] == o["label"] and angle_between(k["heading_deg"], o["heading_deg"]) < within_deg
                   for k in kept):
            kept.append(o)
    return sorted(kept, key=lambda o: o["heading_deg"])


def angle_between(a, b):
    return abs((a - b + 180) % 360 - 180)


def map_payload(samples, status, turns, turn_deg, ts, message=None, user=None, floor=(), objects=(),
                **grid_cfg):
    """The map/local message: readings, grid and a short summary."""
    floor = list(floor)
    return {
        "status": status,                 # scanning | done | aborted | refused
        "message": message,
        "progress": [len(samples), turns],
        "turn_deg": turn_deg,
        "samples": [{"heading_deg": h, "cm": cm} for h, cm in samples],
        "floor": [{"heading_deg": h, "cm": cm} for h, cm in floor],
        "objects": merge_objects(list(objects)),
        "grid": occupancy(samples, floor=floor, **grid_cfg),
        "cell_cm": grid_cfg.get("cell_cm", 5.0),
        "radius_cm": grid_cfg.get("radius_cm", 150.0),
        "nearest": nearest(list(samples) + floor),     # ultrasonic or camera, whichever is closer
        "clearest_deg": clearest(samples),
        "user": user,
        "ts": ts,
    }


def nearest(samples):
    """(heading_deg, cm) of the closest echo, or None."""
    hits = [(cm, heading) for heading, cm in samples if cm is not None]
    if not hits:
        return None
    cm, heading = min(hits)
    return heading, cm


def clearest(samples):
    """Heading with the most room (no echo counts as farthest), or None."""
    if not samples:
        return None
    return max(samples, key=lambda s: math.inf if s[1] is None else s[1])[0]

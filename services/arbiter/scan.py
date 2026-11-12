"""Local 2D map from an ultrasonic sweep on the spot.

The crawler turns step by step and measures the distance after each
step. Each reading is a cone (the sensor's beam): cells closer than the
echo are free, the arc at the echo is occupied. No echo means free up to
max_cm. The map is robot-centric and only valid where the scan was made:
legged steps slip, so there is no position to merge several scans.

Headings are degrees counter-clockwise from the direction the crawler
faced when the scan started (turning left is positive).
"""
import math

UNKNOWN, FREE, OCCUPIED = "?", ".", "#"


def occupancy(samples, cell_cm=5.0, radius_cm=150.0, beam_deg=15.0, max_cm=200.0):
    """Grid as a list of strings, first row = straight ahead at the far end.

    samples: iterable of (heading_deg, cm or None). Cell characters are
    UNKNOWN, FREE and OCCUPIED; occupied wins over free.
    """
    n = int(round(2 * radius_cm / cell_cm))
    grid = [[UNKNOWN] * n for _ in range(n)]
    half = n / 2.0

    def cell(dist, angle):
        # angle 0 = up (row 0), counter-clockwise positive = to the left
        col = int(math.floor(half - dist * math.sin(angle) / cell_cm))
        row = int(math.floor(half - dist * math.cos(angle) / cell_cm))
        return (row, col) if 0 <= row < n and 0 <= col < n else None

    rays = max(3, int(math.ceil(beam_deg / 2.0)) + 1)
    occupied = set()
    for heading, cm in samples:
        hit = cm is not None and cm <= max_cm
        reach = min(cm if hit else max_cm, radius_cm)
        for i in range(rays):
            angle = math.radians(heading - beam_deg / 2.0 + beam_deg * i / (rays - 1))
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
    for row, col in occupied:
        grid[row][col] = OCCUPIED
    return ["".join(row) for row in grid]


def map_payload(samples, status, turns, turn_deg, ts, message=None, user=None, **grid_cfg):
    """The map/local message: readings, grid and a short summary."""
    return {
        "status": status,                 # scanning | done | aborted | refused
        "message": message,
        "progress": [len(samples), turns],
        "turn_deg": turn_deg,
        "samples": [{"heading_deg": h, "cm": cm} for h, cm in samples],
        "grid": occupancy(samples, **grid_cfg),
        "cell_cm": grid_cfg.get("cell_cm", 5.0),
        "radius_cm": grid_cfg.get("radius_cm", 150.0),
        "nearest": nearest(samples),
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

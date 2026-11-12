"""Free floor ahead from one camera frame, without a depth sensor.

The strip right in front of the crawler is taken as the floor's look
(color and brightness). Each image column band is followed upwards until
the picture stops looking like that floor; the geometry turns that row
into a distance. Works on plain, even floors; strong patterns, reflections
or a floor in the wall's color confuse it, so the arbiter's map treats it
as one source next to the ultrasonic sensor.
"""
import numpy as np

SAMPLE_ROWS = 0.12          # bottom share of the image used as the floor sample
SAMPLE_COLS = (0.3, 0.7)
THRESHOLD = 3.0             # distance from the floor's look, in (floored) standard deviations
CHROMA_EPS = 0.02
LIGHT_EPS = 12.0
RUN = 3                     # rows of non-floor in a row that end the floor


def floor_mask(rgb):
    """Bool array, True where a pixel looks like the floor sample."""
    img = rgb.astype(np.float32)
    total = img.sum(axis=2) + 1e-3
    chroma = np.stack([img[..., 0] / total, img[..., 1] / total], axis=-1)
    light = total / 3.0
    h, w = light.shape
    rows = slice(int(h * (1 - SAMPLE_ROWS)), h)
    cols = slice(int(w * SAMPLE_COLS[0]), int(w * SAMPLE_COLS[1]))
    c_mean = chroma[rows, cols].reshape(-1, 2).mean(axis=0)
    c_std = np.maximum(chroma[rows, cols].reshape(-1, 2).std(axis=0), CHROMA_EPS)
    l_mean = light[rows, cols].mean()
    l_std = max(float(light[rows, cols].std()), LIGHT_EPS)
    d2 = (((chroma - c_mean) / c_std) ** 2).sum(axis=-1) + ((light - l_mean) / l_std) ** 2
    return d2 < THRESHOLD ** 2


def boundaries(rgb, geometry, bins=24, max_cm=150.0, step=2):
    """[(bearing_deg, cm or None)] per column band; None = floor free to max_cm.

    rgb is the frame the geometry was made for (same width and height);
    step subsamples pixels for speed.
    """
    mask = floor_mask(rgb[::step, ::step])
    h, w = mask.shape
    edges = np.linspace(0, w, bins + 1).astype(int)
    result = []
    for b in range(bins):
        band = mask[:, edges[b]:max(edges[b] + 1, edges[b + 1])].mean(axis=1) >= 0.5
        u = (edges[b] + edges[b + 1]) / 2.0 * step
        bearing = geometry.bearing(u)
        boundary = None
        run = 0
        for row in range(h - 1, -1, -1):
            v = (row + 0.5) * step
            hit = geometry.ground(u, v)
            if hit is None or hit[0] > max_cm:
                break                               # free as far as we trust it
            if band[row]:
                run = 0
                continue
            run += 1
            if run >= RUN:
                # edge between the last floor row and the first obstacle row
                boundary = geometry.ground(u, min(row + RUN, h) * step - 0.5)
                break
        result.append((round(bearing, 1), None if boundary is None else round(boundary[0], 1)))
    return result

"""Turn per-frame detections (up to 10 Hz) into sightings for the log.

A sighting starts once a label is seen in `start_frames` consecutive frames
with at least `min_score`, and ends after the label has not been seen for
`end_after_s`. Short gaps inside that time extend the sighting. Objects with
the same label are one sighting; `max_count` records how many were seen at
once. There is no tracking of individual objects.
"""


class Sighting:
    __slots__ = ("label", "start", "last_seen", "end", "max_score", "max_count", "mode", "row_id")

    def __init__(self, label, start, mode):
        self.label = label
        self.start = start
        self.last_seen = start
        self.end = None
        self.max_score = 0.0
        self.max_count = 0
        self.mode = mode
        self.row_id = None

    def as_dict(self):
        return {"id": self.row_id, "kind": "sighting", "label": self.label, "start": self.start,
                "end": self.end, "max_score": round(self.max_score, 3), "max_count": self.max_count,
                "mode": self.mode}


class SightingTracker:

    def __init__(self, min_score=0.5, start_frames=3, end_after_s=2.0):
        self.min_score = min_score
        self.start_frames = start_frames
        self.end_after_s = end_after_s
        self._streak = {}        # label -> (consecutive frames, time of first, best score, best count)
        self.active = {}         # label -> Sighting

    def feed(self, objects, now, mode="detect"):
        """Process one frame. Returns [("start"|"end", Sighting), ...]."""
        events = self.expire(now)
        seen = {}
        for obj in objects or []:
            if not isinstance(obj, dict):
                continue
            label, score = obj.get("label"), obj.get("score")
            if not isinstance(label, str) or isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            if score < self.min_score:
                continue
            best, count = seen.get(label, (0.0, 0))
            seen[label] = (max(best, score), count + 1)

        for label in list(self._streak):
            if label not in seen:
                del self._streak[label]

        for label, (score, count) in seen.items():
            sighting = self.active.get(label)
            if sighting is not None:
                sighting.last_seen = now
                sighting.max_score = max(sighting.max_score, score)
                sighting.max_count = max(sighting.max_count, count)
                continue
            frames, first, best, most = self._streak.get(label, (0, now, 0.0, 0))
            frames, best, most = frames + 1, max(best, score), max(most, count)
            if frames >= self.start_frames:
                sighting = Sighting(label, first, mode)
                sighting.last_seen = now
                sighting.max_score, sighting.max_count = best, most
                self.active[label] = sighting
                self._streak.pop(label, None)
                events.append(("start", sighting))
            else:
                self._streak[label] = (frames, first, best, most)
        return events

    def expire(self, now):
        """End sightings not seen for end_after_s. Call regularly too."""
        events = []
        for label, sighting in list(self.active.items()):
            if now - sighting.last_seen > self.end_after_s:
                sighting.end = sighting.last_seen
                del self.active[label]
                events.append(("end", sighting))
        return events

    def close_all(self):
        """End every active sighting now (e.g. vision went offline)."""
        events = []
        for sighting in self.active.values():
            sighting.end = sighting.last_seen
            events.append(("end", sighting))
        self.active.clear()
        self._streak.clear()
        return events

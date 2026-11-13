"""Replay a recorded demo (rl_coral/demo_kitchen.py) in the web mock.

    python -m services.web --config web.toml --fake --demo rl_coral/models/kitchen_demo.zip

The recording holds camera JPEGs and, per frame, what the robot would
publish: detections, the ultrasonic distance, the motion state and the
rolling map in map/local format. The player feeds them to the live state
as if they came from MQTT and serves the pictures as the video stream, in a
loop. Standard library only, so the mock needs no simulator.
"""
import asyncio
import json
import time
import zipfile


class DemoPlayer:

    def __init__(self, path):
        with zipfile.ZipFile(path) as archive:
            self.meta = json.loads(archive.read("meta.json"))
            self.events = [json.loads(line) for line in archive.read("events.jsonl").decode().splitlines() if line]
            self.frames = [archive.read("frames/%05d.jpg" % e["frame"]) for e in self.events]
        if not self.events:
            raise ValueError("%s has no frames" % path)
        self.fps = float(self.meta.get("fps", 10))
        self.index = 0
        self.loops = 0

    def frame(self):
        """The current camera JPEG (for the MJPEG stream)."""
        return self.frames[self.index]

    async def run(self, inject):
        """inject(suffix, payload, retain=False) for every message, forever."""
        period = 1.0 / self.fps
        start = time.monotonic()
        tick = 0
        while True:
            self.index = tick % len(self.events)
            if self.index == 0 and tick:
                self.loops += 1
            event = self.events[self.index]
            now = time.time()
            inject("sensor/vision/detections", {"frame_ts": now, "objects": event["detections"]})
            inject("sensor/ultrasonic/distance", {"cm": event["ultrasonic_cm"], "ts": now})
            inject("state/motion", event["motion"])
            m = dict(event["map"], ts=now)
            m["message"] = "Replay (loop %d) · %s" % (self.loops + 1, m.get("message", ""))
            inject("map/local", m, retain=True)
            tick += 1
            await asyncio.sleep(max(0.0, start + tick * period - time.monotonic()))

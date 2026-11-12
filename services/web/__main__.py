"""Web service: login, live video, object log, emergency stop, admin.

    python -m services.web --config /etc/picrawler/web.toml [--fake]

--fake without an [mqtt] section runs without broker, Bluetooth and
systemd: random detections, a fake gamepad and a test card stream, so the
UI can be developed on a laptop.
"""
import asyncio
import ipaddress
import random
import signal
import ssl
import time

from aiohttp import web

from services.common import config
from services.common.mqtt import encode, make_bus
from services.vision.inference import FakeDetector

from .app import Hub, WebApp
from .auth import RateLimiter, Sessions, Users
from .bluetooth import Bluetoothctl, FakeBluetooth
from .monitor import MqttMonitor
from .sightings import SightingTracker
from .state import LiveState
from .store import LogStore
from .system import FakeSystem, System

# Everything under the robot's prefix: the state needs a few topics, the
# MQTT debug page shows all of them (read-only).
SUBSCRIPTIONS = ("#",)
TICK_S = 0.2


def check_listen(http_cfg):
    host, _, port = http_cfg.get("listen", "127.0.0.1:8080").rpartition(":")
    host = host.strip("[]") or "0.0.0.0"
    local = host in ("localhost",) or (host != "0.0.0.0" and ipaddress.ip_address(host).is_loopback)
    if not local and not http_cfg.get("cert") and not http_cfg.get("allow_plaintext", False):
        raise SystemExit("web: refusing to serve passwords over plain HTTP on %s. "
                         "Set http.cert/http.key, or http.allow_plaintext = true to accept it." % host)
    return host, int(port)


class FakeFeed:
    """Plays the rest of the robot for --fake runs without a broker."""

    def __init__(self, bus, state, monitor):
        self.bus = bus
        self.state = state
        self.monitor = monitor
        self.detector = FakeDetector()
        self.mode = "detect"
        bus.publish = self.publish

    def publish(self, suffix, payload, qos=0, retain=False):
        self.monitor.record(suffix, encode(payload).encode(), retain, qos, time.time())
        if suffix == "cmd/stop":
            self.inject("event/stop", {"reason": payload.get("reason"), "sit": payload.get("sit"), "ts": time.time()})
        elif suffix == "remote/state":
            self.remote_step(payload)
        elif suffix == "agent/ask":
            self.inject("agent/reply", {"text": "(fake) Okay: %s" % payload.get("text"), "request": payload.get("text"),
                                        "source": payload.get("user"), "brain": "fake", "seconds": 0.1,
                                        "ts": time.time()})
        elif suffix == "vision/mode":
            if payload.get("mode") in ("detect", "off"):
                self.mode = payload["mode"]
            self.vision_status(error=None if payload.get("mode") in ("detect", "off")
                               else "mode %s is not implemented yet" % payload.get("mode"))

    def remote_step(self, payload):
        """Pretend the arbiter and body ran one step for the remote (mock only)."""
        name = payload.get("action")
        x, y = payload.get("x", 0), payload.get("y", 0)
        if not name and max(abs(x), abs(y)) >= 0.5:
            name = ("forward" if y < 0 else "backward") if abs(y) >= abs(x) else ("turn right" if x > 0 else "turn left")
        if name:
            self.inject("state/motion", {"status": "running", "name": name, "priority": "goal", "queue_len": 0})
            asyncio.get_running_loop().call_later(
                0.6, self.inject, "state/motion", {"status": "idle", "name": name, "priority": "goal", "queue_len": 0})

    def inject(self, suffix, payload, retain=False):
        self.monitor.record(suffix, encode(payload).encode(), retain, 0, time.time())
        self.state.on_message(suffix, payload, retain)

    def vision_status(self, error=None):
        self.inject("sensor/vision/status", {"status": "online", "mode": self.mode, "tpu": True, "fps": 10.0,
                                             "inference_ms": 9.1, "error": error})

    def display_frame(self, t):
        try:
            from services.display.render import frame_payload, render
        except ImportError:
            return
        battery = self.state.snapshot.get("battery")
        # CPU sweeps up to 99 % so the warning/critical icons show up too
        info = {"cpu": 20 + (t // 10 * 9) % 80, "ram": 31.5, "sd": 42.0, "nvme": 9.0, "wifi": ("wlan0", 78, -55),
                "ip": "192.168.178.42", "temp": 52, "bluetooth": ("connected", 1), "battery": battery}
        try:
            image = render(info, page=t // 30, blink=(t // 10) % 2 == 1)
        except Exception:      # Pillow missing in this environment
            return
        self.inject("display/frame", dict(frame_payload(image), ts=time.time()), retain=True)

    async def run(self):
        self.inject("status", {"status": "online"}, retain=True)
        self.inject("arbiter/status", {"status": "online"}, retain=True)
        self.inject("state/motion", {"status": "idle", "queue_len": 0})
        self.inject("sensor/ultrasonic/status", {"status": "online"})
        self.inject("sensor/gamepad/status", {"status": "online", "connected": True, "name": "Fake gamepad"})
        self.inject("agent/status", {"status": "online", "busy": False, "brains": ["fake"]})
        self.vision_status()
        t = 0
        while True:
            await asyncio.sleep(0.1)
            t += 1
            if self.mode == "detect":
                objects, _ = self.detector.detect(None)
                self.inject("sensor/vision/detections", {"frame_ts": time.time(), "objects": objects})
            if t % 50 == 0:
                volts = round(max(6.5, 8.1 - t / 3000), 2)
                self.inject("state/battery", {"present": True, "voltage": volts, "percent": round((volts - 6.4) / 2 * 100),
                                              "level": "ok" if volts >= 7.0 else "low"}, retain=True)
            if t % 10 == 0:
                self.display_frame(t)
            if t % 5 == 0:
                self.inject("sensor/ultrasonic/distance", {"cm": round(40 + 25 * random.random(), 1)})
                self.inject("sensor/gamepad/state", {"x": 0.0, "y": 0.0, "buttons": []})


async def serve(cfg, fake):
    http_cfg = cfg.get("http", {})
    host, port = check_listen(http_cfg)
    users = Users(cfg.get("users", {}))
    if not len(users):
        raise SystemExit("web: no users configured ([users.<name>] with role and password_hash)")
    log_cfg = cfg.get("log", {})
    store = LogStore(log_cfg.get("path", "/var/lib/picrawler/web/log.db"),
                     retention_days=log_cfg.get("retention_days", 7), max_entries=log_cfg.get("max_entries", 10000))
    tracker = SightingTracker(min_score=log_cfg.get("min_score", 0.5), start_frames=log_cfg.get("start_frames", 3),
                              end_after_s=log_cfg.get("end_after_s", 2.0))
    hub = Hub()
    debug_hub = Hub()
    state = LiveState(store, tracker, hub.push)
    monitor = MqttMonitor(lambda message: debug_hub.push("mqtt", message))
    units = cfg.get("system", {}).get("units", [])

    bus = make_bus(cfg, "picrawler-web", "web/status", fake=fake)
    feed = FakeFeed(bus, state, monitor) if fake and "mqtt" not in cfg else None
    loop = asyncio.get_running_loop()
    for topic in SUBSCRIPTIONS:
        bus.subscribe(topic, qos=0)
    bus.on_message = lambda suffix, payload, retain: loop.call_soon_threadsafe(state.on_message, suffix, payload, retain)
    bus.on_raw = lambda suffix, raw, retain, qos: loop.call_soon_threadsafe(
        monitor.record, suffix, raw, retain, qos, time.time())
    bus.on_connect = lambda: bus.publish_status("online")

    webapp = WebApp(cfg, users, Sessions(), RateLimiter(), state, hub, bus,
                    FakeBluetooth() if fake else Bluetoothctl(),
                    FakeSystem(units) if fake else System(units), store, fake=fake,
                    monitor=monitor, debug_hub=debug_hub)
    runner = web.AppRunner(webapp.build(), access_log=None)
    await runner.setup()
    ssl_ctx = None
    if http_cfg.get("cert"):
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(http_cfg["cert"], http_cfg.get("key"))
    await web.TCPSite(runner, host, port, ssl_context=ssl_ctx).start()

    bus.start()
    tasks = [asyncio.ensure_future(feed.run())] if feed else []
    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    print("web: listening on %s://%s:%d%s" % ("https" if ssl_ctx else "http", host, port, " (fake)" if fake else ""))
    while not stopping.is_set():
        state.tick()
        try:
            await asyncio.wait_for(stopping.wait(), TICK_S)
        except asyncio.TimeoutError:
            pass
    for task in tasks:
        task.cancel()
    # stop deliveries first, let already scheduled ones run, then close the store
    bus.on_message = None
    bus.on_raw = None
    bus.stop()
    await asyncio.sleep(0.1)
    await runner.cleanup()
    store.close()


def main():
    args = config.arg_parser("PiCrawler web service").parse_args()
    asyncio.run(serve(config.load(args.config), args.fake))


if __name__ == "__main__":
    main()

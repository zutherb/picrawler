"""Ultrasonic sensor node: publishes distance readings as facts.

    python -m services.sensor_ultrasonic --config /etc/picrawler/sensor-ultrasonic.toml [--fake]

Publishes sensor/ultrasonic/distance {"cm": 12.4, "ts": ...} and a retained
sensor/ultrasonic/status (LWT offline). Never publishes commands.
"""
import math
import signal
import threading
import time

from services.common import config
from services.common.mqtt import Bus, now

TOPIC = "sensor/ultrasonic/distance"
MAX_CM = 400.0


class RealSonar:
    def __init__(self, trig="D2", echo="D3", timeout=0.02):
        from robot_hat import Pin, Ultrasonic
        self._sonar = Ultrasonic(Pin(trig), Pin(echo), timeout=timeout)

    def read(self):
        return self._sonar.read()


class FakeSonar:
    """Slowly approaches an obstacle and backs off again (for testing)."""

    def __init__(self):
        self._t0 = time.monotonic()

    def read(self):
        t = time.monotonic() - self._t0
        return 40 + 30 * math.sin(t / 3)


def valid(cm):
    # robot_hat returns -1/-2 on timeouts; very large values are echoes lost.
    return isinstance(cm, (int, float)) and 0 < cm <= MAX_CM


def main():
    args = config.arg_parser("PiCrawler ultrasonic sensor").parse_args()
    cfg = config.load(args.config)
    sensor_cfg = cfg.get("sensor", {})
    rate_hz = sensor_cfg.get("rate_hz", 10)

    sonar = FakeSonar() if args.fake else RealSonar(
        sensor_cfg.get("trig_pin", "D2"), sensor_cfg.get("echo_pin", "D3"))
    bus = Bus(cfg, client_id="picrawler-sensor-ultrasonic", status_topic="sensor/ultrasonic/status")
    bus.on_connect = lambda: bus.publish_status("online")

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    bus.start()
    print("sensor-ultrasonic: publishing at %s Hz%s" % (rate_hz, " (fake)" if args.fake else ""))
    period = 1.0 / rate_hz
    while not stopping.is_set():
        started = time.monotonic()
        try:
            cm = sonar.read()
        except Exception as e:
            print("read failed: %s" % e)
            cm = None
        if valid(cm):
            bus.publish(TOPIC, {"cm": round(float(cm), 1), "ts": now()})
        stopping.wait(max(0.0, period - (time.monotonic() - started)))
    bus.stop()


if __name__ == "__main__":
    main()

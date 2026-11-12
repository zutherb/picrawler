"""Arbiter service: sensor facts in, prioritized commands out.

    python -m services.arbiter --config /etc/picrawler/arbiter.toml
"""
import signal
import threading

from services.common import config
from services.common.mqtt import Bus

from .arbiter import Arbiter

TICK_S = 0.05
SUBSCRIPTIONS = ("sensor/#", "state/motion", "status", "event/rejected", "agent/status", "agent/goal",
                 "remote/state", "map/scan")


def main():
    args = config.arg_parser("PiCrawler arbiter").parse_args()
    cfg = config.load(args.config)

    bus = Bus(cfg, client_id="picrawler-arbiter", status_topic="arbiter/status")
    arbiter = Arbiter(bus.publish, cfg)
    lock = threading.Lock()   # paho delivers on its own thread, tick runs here

    def on_message(suffix, payload, retain):
        with lock:
            arbiter.on_message(suffix, payload, retain)

    for topic in SUBSCRIPTIONS:
        bus.subscribe(topic)
    bus.on_message = on_message
    bus.on_connect = lambda: bus.publish_status("online")

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    bus.start()
    print("arbiter: running with %d rule(s), wander %s"
          % (len(arbiter.rules), "on" if arbiter.wander.enabled else "off"))
    while not stopping.wait(TICK_S):
        with lock:
            arbiter.tick()
    bus.stop()


if __name__ == "__main__":
    main()

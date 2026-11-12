"""Body service: the only process that drives the servos.

    python -m services.body --config /etc/picrawler/body.toml [--fake]
"""
import signal
import threading

from services.common import config
from services.common.mqtt import Bus

from .adapter import COMMAND_TOPICS, BodyAdapter

TICK_S = 0.1


def make_crawler(fake):
    if fake:
        from tests import fake_robot_hat
        fake_robot_hat.install()
    from picrawler import Picrawler
    return Picrawler()


def main():
    args = config.arg_parser("PiCrawler body service").parse_args()
    cfg = config.load(args.config)
    executor_cfg = cfg.get("executor", {})

    from picrawler.executor import MotionExecutor

    bus = Bus(cfg, client_id="picrawler-body")
    adapter = None
    executor = MotionExecutor(
        make_crawler(args.fake),
        on_state=lambda state: adapter and adapter.on_state(state),
        queue_max=executor_cfg.get("queue_max", 8),
        lockout_s=executor_cfg.get("lockout_s", 1.0),
    )
    adapter = BodyAdapter(executor, bus.publish, cfg)

    for topic in COMMAND_TOPICS:
        bus.subscribe(topic, qos=0 if topic == "cmd/heartbeat" else 1)
    bus.on_connect = adapter.on_connect
    bus.on_disconnect = adapter.on_disconnect
    bus.on_message = adapter.on_message

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    executor.start()
    bus.start()
    print("body: running, waiting for commands (no movement at start)")
    while not stopping.wait(TICK_S):
        adapter.tick()

    print("body: shutting down, sitting down")
    executor.shutdown(sit=True)
    bus.stop()


if __name__ == "__main__":
    main()

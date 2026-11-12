"""OLED status display (128x64, I2C): Wi-Fi quality and usage bars.

    python -m services.display --config /etc/picrawler/display.toml [--fake]

Works without any other service. With an [mqtt] section it also shows
the battery from the body service (state/battery); the body measures it so
the HAT's I2C microcontroller is never read from two processes. --fake
writes the picture to a PNG (default /tmp/picrawler-display.png).
"""
import signal
import threading
import time

from services.common import config
from services.common.mqtt import Bus

from .render import DEFAULT_THRESHOLDS, frame_payload, render
from .splash import draw_splash
from .sysinfo import SysInfo

SHIFT_EVERY_S = 60
PAGE_EVERY_S = 3       # header alternates between IP and temperature
SHIFTS = ((0, 0), (1, 0), (1, 1), (0, 1))   # against burn-in


def open_device(cfg):
    from luma.core.interface.serial import i2c
    from luma.oled.device import sh1106, ssd1306

    drivers = {"ssd1306": ssd1306, "sh1106": sh1106}
    driver = cfg.get("driver", "ssd1306")
    if driver not in drivers:
        raise SystemExit("display: driver must be one of %s" % ", ".join(drivers))
    serial = i2c(port=cfg.get("bus", 1), address=int(str(cfg.get("address", "0x3C")), 0))
    device = drivers[driver](serial, width=128, height=64, rotate=cfg.get("rotate", 0))
    device.contrast(cfg.get("contrast", 128))
    # keep the last picture on exit (luma would clear and switch off)
    device.persist = True
    return device


class PngDevice:
    def __init__(self, path):
        self.path = path

    def display(self, image):
        image.save(self.path)



def main():
    args = config.arg_parser("PiCrawler OLED display").parse_args()
    full_cfg = config.load(args.config)
    cfg = full_cfg.get("display", {})
    device = PngDevice(cfg.get("png", "/tmp/picrawler-display.png")) if args.fake else open_device(cfg)
    info = SysInfo(cfg.get("wifi_interface") or None, cfg.get("nvme_path", "/mnt/picrawler-data"))
    interval = cfg.get("interval_s", 1.0)
    thresholds = dict(DEFAULT_THRESHOLDS, **{k: tuple(v) for k, v in cfg.get("thresholds", {}).items()})
    latest = {"battery": None}
    bus = None
    if "mqtt" in full_cfg:
        bus = Bus(full_cfg, client_id="picrawler-display", status_topic="display/status")

        def on_message(suffix, payload, retain):
            if suffix == "state/battery" and isinstance(payload, dict):
                latest["battery"] = payload

        bus.subscribe("state/battery")
        bus.on_message = on_message
        bus.on_connect = lambda: bus.publish_status("online")
        bus.start()

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    print("display: running%s" % (" (fake, %s)" % device.path if args.fake else ""))
    started = time.monotonic()
    while not stopping.is_set():
        elapsed = time.monotonic() - started
        shift = SHIFTS[int(elapsed // SHIFT_EVERY_S) % len(SHIFTS)]
        data = info.read()
        data["battery"] = latest["battery"]
        image = render(data, shift, page=int(elapsed // PAGE_EVERY_S), blink=int(elapsed) % 2 == 1,
                       thresholds=thresholds)
        device.display(image)
        if bus:
            # mirror for the web UI, only when the picture changed
            pixels = image.tobytes()
            if pixels != latest.get("pixels"):
                latest["pixels"] = pixels
                bus.publish("display/frame", dict(frame_payload(image), ts=time.time()), 0, True)
        stopping.wait(interval)
    # Leave the logo on: the OLED keeps it while powered. At system shutdown
    # picrawler-splash-shutdown.service takes over and makes the icon blink.
    device.display(draw_splash("shutdown"))
    if bus:
        bus.stop()


if __name__ == "__main__":
    main()

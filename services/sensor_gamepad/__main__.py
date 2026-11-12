"""Gamepad sensor node: publishes stick and button state as facts.

    python -m services.sensor_gamepad --config /etc/picrawler/sensor-gamepad.toml [--fake]

Publishes sensor/gamepad/state {"x", "y", "buttons", "ts"} (x/y in -1..1,
up is negative) at up to rate_hz while a pad is connected, and a retained
sensor/gamepad/status with "connected" and the device name. Pairing is done
in the web UI; this service only picks up whatever pad the kernel exposes.
Never publishes commands, the arbiter decides (see [manual] there).
"""
import select
import signal
import threading
import time

from services.common import config
from services.common.mqtt import make_bus

from .gamepad import FakePad, PadState

STATE_TOPIC = "sensor/gamepad/state"
STATUS_TOPIC = "sensor/gamepad/status"
RESCAN_S = 2.0
IDLE_REPEAT_S = 0.5


def is_pad(caps, ecodes):
    """Any device with two stick axes and joystick/gamepad buttons.

    Deliberately loose: generic Bluetooth pads often report BTN_TRIGGER,
    BTN_THUMB, ... (joystick range) instead of BTN_SOUTH.
    """
    axes = {code for code, _ in caps.get(ecodes.EV_ABS, [])}
    keys = set(caps.get(ecodes.EV_KEY, []))
    has_buttons = any(ecodes.BTN_JOYSTICK <= k < ecodes.BTN_DIGI for k in keys)
    return {ecodes.ABS_X, ecodes.ABS_Y} <= axes and has_buttons


def find_device(name_filter):
    import evdev
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        matches = is_pad(dev.capabilities(), evdev.ecodes)
        if matches and name_filter:
            matches = name_filter.lower() in dev.name.lower()
        if matches:
            return dev
        dev.close()
    return None


def button_name(code):
    import evdev
    names = evdev.ecodes.BTN.get(code) or evdev.ecodes.KEY.get(code)
    if isinstance(names, (list, tuple)):
        # several aliases (e.g. BTN_A/BTN_GAMEPAD/BTN_SOUTH): prefer BTN_SOUTH style
        for preferred in ("BTN_SOUTH", "BTN_EAST", "BTN_NORTH", "BTN_WEST"):
            if preferred in names:
                return preferred
        return names[0]
    return names or "BTN_%d" % code


class Publisher:
    def __init__(self, bus, rate_hz):
        self.bus = bus
        self.period = 1.0 / rate_hz
        self.last = 0.0
        self.last_payload = None
        self.connected = (False, None)

    def status(self, connected, name=None):
        self.connected = (connected, name)
        self.republish()

    def republish(self):
        connected, name = self.connected
        self.bus.publish_status("online", connected=connected, name=name)

    def state(self, payload, force=False):
        now = time.monotonic()
        changed = payload != self.last_payload
        if (changed or force) and now - self.last >= self.period:
            self.bus.publish(STATE_TOPIC, dict(payload, ts=time.time()))
            self.last, self.last_payload = now, payload


def run_real(bus, pub, cfg, stopping):
    import evdev
    ecodes = evdev.ecodes
    axis_x = getattr(ecodes, cfg.get("axis_x", "ABS_X"))
    axis_y = getattr(ecodes, cfg.get("axis_y", "ABS_Y"))
    while not stopping.is_set():
        dev = find_device(cfg.get("device_name", ""))
        if dev is None:
            pub.status(False)
            stopping.wait(RESCAN_S)
            continue
        print("gamepad: using %s (%s)" % (dev.name, dev.path))
        pub.status(True, dev.name)
        pad = PadState(axis_x, axis_y, cfg.get("deadzone", 0.15))
        for code in (axis_x, axis_y):
            info = dev.absinfo(code)
            pad.set_range(code, info.min, info.max)
        try:
            while not stopping.is_set():
                readable, _, _ = select.select([dev.fd], [], [], 0.05)
                if readable:
                    for event in dev.read():
                        if event.type == ecodes.EV_ABS:
                            pad.on_axis(event.code, event.value)
                        elif event.type == ecodes.EV_KEY:
                            pad.on_button(button_name(event.code), event.value != 0)
                # repeat now and then, so the arbiter's view stays fresh while a stick is held
                pub.state({k: v for k, v in pad.payload().items() if k != "ts"},
                          force=time.monotonic() - pub.last >= IDLE_REPEAT_S)
        except OSError:
            print("gamepad: disconnected")
        finally:
            try:
                dev.close()
            except OSError:
                pass
        pub.status(False)


def run_fake(pub, stopping):
    pad = FakePad()
    pub.status(True, pad.name)
    while not stopping.wait(0.05):
        pub.state(pad.read(), force=time.monotonic() - pub.last >= IDLE_REPEAT_S)


def main():
    args = config.arg_parser("PiCrawler gamepad sensor").parse_args()
    cfg = config.load(args.config)
    sensor_cfg = cfg.get("sensor", {})

    bus = make_bus(cfg, "picrawler-sensor-gamepad", STATUS_TOPIC, fake=args.fake)
    pub = Publisher(bus, sensor_cfg.get("rate_hz", 10))
    bus.on_connect = pub.republish

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    bus.start()
    print("sensor-gamepad: running%s" % (" (fake)" if args.fake else ""))
    if args.fake:
        run_fake(pub, stopping)
    else:
        run_real(bus, pub, sensor_cfg, stopping)
    bus.stop()


if __name__ == "__main__":
    main()

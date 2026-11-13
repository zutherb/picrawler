"""Hardware check for the admin page.

Two sources, combined per component by assess():
- local, passive probes on the Pi (probe_local): device nodes, sysfs, ALSA
  and the camera list. Nothing touches I2C or GPIO: the web user has no
  access to them on purpose (it could drive the servos past the arbiter).
- live MQTT facts the web service sees anyway (LiveState.facts): the
  battery voltage comes through the Robot HAT's ADC, distance readings from
  the ultrasonic sensor, frames from the OLED and the camera services.
  Fresh data there is the proof that the part works.
"""
import glob
import os
import re

CORAL_PCI_VENDOR = "0x1ac1"
FRESH_S = {"state/battery": 120, "sensor/ultrasonic/distance": 10, "display/frame": 60}
CAMERA_LINE = re.compile(r"^\s*(\d+)\s*:\s*(\S+)")
PCM_LINE = re.compile(r"^(\d+)-(\d+):\s*([^:]*?)\s*:\s*([^:]*?)\s*:(.*)$")


def parse_cameras(text):
    """rpicam-hello --list-cameras -> ["ov5647", ...]."""
    return [m.group(2) for m in map(CAMERA_LINE.match, text.splitlines()) if m]


def parse_pcm(text):
    """/proc/asound/pcm -> {"playback": [names], "capture": [names]}."""
    found = {"playback": [], "capture": []}
    for line in text.splitlines():
        m = PCM_LINE.match(line)
        if not m:
            continue
        name = m.group(3) or m.group(4)
        for kind in found:
            if kind in m.group(5) and name not in found[kind]:
                found[kind].append(name)
    return found


async def probe_local(run, root="/"):
    """Facts from this machine. run(*args, timeout) -> (code, output) like system._run."""
    def path(p):
        return os.path.join(root, p.lstrip("/"))

    def read(full):
        try:
            with open(full) as f:
                return f.read()
        except OSError:
            return None

    pci = [d for d in glob.glob(path("/sys/bus/pci/devices/*"))
           if (read(os.path.join(d, "vendor")) or "").strip() == CORAL_PCI_VENDOR]
    cameras = None
    for tool in ("rpicam-hello", "libcamera-hello"):
        try:
            code, out = await run(tool, "--list-cameras", timeout=8)
        except Exception:                       # tool missing or timed out
            continue
        cameras = parse_cameras(out)
        break
    pcm = read(path("/proc/asound/pcm"))
    return {
        "i2c_bus": os.path.exists(path("/dev/i2c-1")),
        "tpu_pci": len(pci),
        "tpu_devices": sorted(os.path.basename(p) for p in glob.glob(path("/dev/apex_*"))),
        "cameras": cameras,
        "bluetooth": sorted(os.path.basename(p) for p in glob.glob(path("/sys/class/bluetooth/hci*"))),
        "audio": parse_pcm(pcm) if pcm is not None else None,
    }


def _fact(facts, suffix, now):
    """(payload, age_s) or (None, None). Age from the payload's ts, else from receipt."""
    entry = facts.get(suffix)
    if entry is None:
        return None, None
    payload, received = entry
    ts = payload.get("ts") if isinstance(payload.get("ts"), (int, float)) else received
    return payload, max(0.0, now - ts)


def _online(facts, suffix, now):
    payload, _ = _fact(facts, suffix, now)
    return payload is not None and payload.get("status") == "online", payload


def _fresh(facts, suffix, now):
    payload, age = _fact(facts, suffix, now)
    return payload if age is not None and age <= FRESH_S[suffix] else None


def component(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def assess(local, facts, now):
    """[{name, status: ok|warn|bad|unknown, detail}] for the admin page."""
    out = []

    body_on, _ = _online(facts, "status", now)
    battery = _fresh(facts, "state/battery", now)
    if not local.get("i2c_bus", True):
        out.append(component("Robot HAT & servos", "bad", "no I2C bus (/dev/i2c-1): enable I2C"))
    elif not body_on:
        out.append(component("Robot HAT & servos", "bad", "body service offline: servos not driven"))
    elif battery is None or not battery.get("present"):
        out.append(component("Robot HAT & servos", "warn", "body online, but no battery reading from the HAT"))
    else:
        out.append(component("Robot HAT & servos", "ok", "body online, HAT answers (battery ADC)"))

    if battery is None:
        out.append(component("Battery", "unknown", "no reading"))
    elif not battery.get("present"):
        out.append(component("Battery", "bad", "not detected"))
    else:
        level = battery.get("level", "ok")
        text = "%.2f V" % battery["voltage"] if isinstance(battery.get("voltage"), (int, float)) else "?"
        if battery.get("percent") is not None:
            text += " · %s %%" % battery["percent"]
        out.append(component("Battery", {"ok": "ok", "low": "warn"}.get(level, "bad"),
                             text if level == "ok" else "%s (%s)" % (text, level)))

    sonar_on, _ = _online(facts, "sensor/ultrasonic/status", now)
    reading = _fresh(facts, "sensor/ultrasonic/distance", now)
    if not sonar_on:
        out.append(component("Ultrasonic", "bad", "sensor service offline"))
    elif reading is None:
        out.append(component("Ultrasonic", "warn", "online, but no readings"))
    elif reading.get("cm") is None:
        out.append(component("Ultrasonic", "warn", "no echo: unplugged, or nothing within range"))
    else:
        out.append(component("Ultrasonic", "ok", "%.0f cm" % reading["cm"]))

    vision_on, vision = _online(facts, "sensor/vision/status", now)
    cameras = local.get("cameras")
    if cameras == []:
        out.append(component("Camera", "bad", "no camera detected (ribbon cable, 22-to-15-pin adapter)"))
    elif not vision_on:
        name = ", ".join(cameras) if cameras else "camera"
        out.append(component("Camera", "warn", "%s found, vision service offline" % name))
    else:
        fps = vision.get("fps")
        out.append(component("Camera", "ok", "%s · streaming%s" % (", ".join(cameras or ["camera"]),
                                                                    " %.0f fps" % fps if fps else "")))

    pci, nodes = local.get("tpu_pci"), local.get("tpu_devices") or []
    tpu_used = bool(vision and vision.get("tpu"))
    if pci == 0 and not nodes:
        out.append(component("Coral Edge TPU", "bad", "not on PCIe (check dtparam=pciex1 and the M.2 card)"))
    elif pci and not nodes:
        out.append(component("Coral Edge TPU", "bad", "on PCIe, but no /dev/apex_* (gasket driver, 4K page kernel)"))
    else:
        count = "%d TPU%s" % (len(nodes), "" if len(nodes) == 1 else "s") if nodes else "TPU"
        if tpu_used:
            out.append(component("Coral Edge TPU", "ok", "%s · detector running" % count))
        else:
            out.append(component("Coral Edge TPU", "warn", "%s · not used by the vision service" % count))

    display_on, _ = _online(facts, "display/status", now)
    frame = _fresh(facts, "display/frame", now)
    if frame is not None:
        out.append(component("OLED display", "ok", "drawing"))
    elif display_on:
        out.append(component("OLED display", "warn", "service online, no recent picture"))
    else:
        out.append(component("OLED display", "bad" if "display/status" in facts else "unknown",
                             "display service offline"))

    adapters = local.get("bluetooth")
    pad_on, pad = _online(facts, "sensor/gamepad/status", now)
    if adapters == []:
        out.append(component("Bluetooth", "bad", "no adapter"))
    else:
        out.append(component("Bluetooth", "ok", ", ".join(adapters) if adapters else "adapter"))
    if pad_on and pad.get("connected"):
        out.append(component("Gamepad", "ok", pad.get("name") or "connected"))
    elif pad_on:
        out.append(component("Gamepad", "warn", "not connected (switch it on or pair it)"))
    else:
        out.append(component("Gamepad", "unknown", "gamepad service offline"))

    audio = local.get("audio")
    for kind, name, service, label in (("capture", "Microphone", "sensor/speech/status", "speech"),
                                       ("playback", "Speaker", "voice/status", "voice")):
        devices = (audio or {}).get(kind)
        running, _ = _online(facts, service, now)
        if audio is not None and not devices:
            out.append(component(name, "bad", "no %s device" % kind))
        elif running:
            out.append(component(name, "ok", "%s · %s service online" % (", ".join(devices or [kind]), label)))
        else:
            out.append(component(name, "warn" if devices else "unknown",
                                 "%s · %s service offline" % (", ".join(devices), label) if devices
                                 else "%s service offline" % label))
    return out

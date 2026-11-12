"""Bluetooth controller management through bluetoothctl (BlueZ).

Commands are passed as argument lists, never through a shell, and MAC
addresses are validated first. Pairing runs one interactive bluetoothctl
session with a NoInputNoOutput agent ("just works"), which is what common
gamepads use. The service user needs the `bluetooth` group (BlueZ D-Bus policy).
"""
import asyncio
import re

MAC = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x01\x02]")
GAMEPAD_HINT = re.compile(r"controller|gamepad|joystick|nimbus|xbox|dualshock|dualsense|8bitdo|pro con", re.I)
SCAN_MAX_S = 30


class BluetoothError(Exception):
    pass


def check_mac(mac):
    mac = (mac or "").upper()
    if not MAC.match(mac):
        raise BluetoothError("invalid MAC address")
    return mac


DEVICE_LINE = re.compile(r"\bDevice ([0-9A-F]{2}(?::[0-9A-F]{2}){5})(?: (.*))?$")


def parse_devices(text):
    """'Device <MAC> <name>' lines, also behind prompts like '[bluetooth]# '."""
    devices, seen = [], set()
    for line in ANSI.sub("", text).splitlines():
        m = DEVICE_LINE.search(line.strip())
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            devices.append({"mac": m.group(1), "name": (m.group(2) or "").strip() or m.group(1)})
    return devices


def parse_info(text):
    info = {}
    for line in ANSI.sub("", text).splitlines():
        key, sep, value = line.strip().partition(":")
        if not sep:
            continue
        value = value.strip()
        if key in ("Paired", "Trusted", "Connected", "Blocked"):
            info[key.lower()] = value == "yes"
        elif key in ("Name", "Alias", "Icon"):
            info[key.lower()] = value
        elif key == "Battery Percentage":
            m = re.search(r"\((\d+)\)", value)
            if m:
                info["battery"] = int(m.group(1))
    return info


def is_gamepad(device):
    return device.get("icon") == "input-gaming" or bool(GAMEPAD_HINT.search(device.get("name", "")))


class Bluetoothctl:

    def __init__(self, command="bluetoothctl"):
        self.command = command
        self.scanning = False
        self._lock = asyncio.Lock()

    async def _run(self, *args, timeout=10):
        proc = await asyncio.create_subprocess_exec(
            self.command, *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise BluetoothError("bluetoothctl %s timed out" % args[0])
        return out.decode("utf-8", "replace")

    async def devices(self):
        listed = parse_devices(await self._run("devices"))
        result = []
        for dev in listed:
            info = parse_info(await self._run("info", dev["mac"]))
            dev = dict(dev, **{k: v for k, v in info.items() if k != "alias"})
            dev["gamepad"] = is_gamepad(dev)
            result.append(dev)
        result.sort(key=lambda d: (not d.get("connected"), not d.get("paired"), not d["gamepad"], d["name"]))
        return result

    async def scan(self, seconds=20):
        seconds = max(5, min(int(seconds), SCAN_MAX_S))
        if self.scanning:
            raise BluetoothError("a scan is already running")
        self.scanning = True
        try:
            await self._run("--timeout", str(seconds), "scan", "on", timeout=seconds + 5)
        finally:
            self.scanning = False

    async def disconnect(self, mac):
        return await self._simple("disconnect", mac, ("Successful disconnected", "not connected"))

    async def remove(self, mac):
        return await self._simple("remove", mac, ("Device has been removed", "not available"))

    async def connect(self, mac):
        mac = check_mac(mac)
        return await self._session([
            ("connect %s" % mac, ("Connection successful",), ("Failed to connect", "not available"), 20, True),
        ])

    async def pair(self, mac):
        """Pair, trust and connect. The pad must be in pairing mode."""
        mac = check_mac(mac)
        return await self._session([
            ("power on", ("succeeded",), (), 3, False),
            ("agent NoInputNoOutput", ("Agent registered", "already registered"), (), 3, False),
            ("default-agent", ("Default agent request successful",), (), 3, False),
            ("scan on", ("Device %s" % mac,), (), 15, False),
            ("pair %s" % mac, ("Pairing successful", "AlreadyExists"), ("Failed to pair", "not available"), 30, True),
            ("trust %s" % mac, ("trust succeeded",), ("Failed",), 5, True),
            ("scan off", ("Discovery stopped", "succeeded"), (), 3, False),
            ("connect %s" % mac, ("Connection successful",), ("Failed to connect",), 20, True),
        ])

    async def _simple(self, verb, mac, ok_markers):
        mac = check_mac(mac)
        out = await self._run(verb, mac)
        if any(marker.lower() in out.lower() for marker in ok_markers):
            return {"ok": True}
        raise BluetoothError(ANSI.sub("", out).strip().splitlines()[-1] if out.strip() else "%s failed" % verb)

    async def _session(self, steps):
        """Feed commands to one bluetoothctl process, waiting for markers.

        steps: (command, success markers, failure markers, timeout s, required)
        """
        async with self._lock:
            proc = await asyncio.create_subprocess_exec(
                self.command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            log = []
            try:
                for command, ok, fail, timeout, required in steps:
                    proc.stdin.write((command + "\n").encode())
                    await proc.stdin.drain()
                    result = await self._wait_for(proc, ok, fail, timeout)
                    log.append("%s: %s" % (command, result))
                    if result != "ok" and required:
                        return {"ok": False, "error": "%s: %s" % (command, result), "log": log}
                return {"ok": True, "log": log}
            finally:
                if proc.returncode is None:
                    try:
                        proc.stdin.write(b"quit\n")
                        await proc.stdin.drain()
                        await asyncio.wait_for(proc.wait(), 3)
                    except (asyncio.TimeoutError, ConnectionError):
                        proc.kill()
                        await proc.wait()

    @staticmethod
    async def _wait_for(proc, ok, fail, timeout):
        text = ""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return "timeout"
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), remaining)
            except asyncio.TimeoutError:
                return "timeout"
            if not chunk:
                return "bluetoothctl exited"
            text += ANSI.sub("", chunk.decode("utf-8", "replace"))
            for marker in fail:
                if marker in text:
                    return "failed (%s)" % marker
            for marker in ok:
                if marker in text:
                    return "ok"


class FakeBluetooth:
    """In-memory stand-in for --fake runs and tests."""

    def __init__(self, delay=0.5):
        self.delay = delay
        self.scanning = False
        self._devices = {
            "AA:BB:CC:00:00:01": {"name": "SteelSeries Nimbus+", "icon": "input-gaming", "paired": True,
                                  "trusted": True, "connected": True, "battery": 80},
        }

    async def devices(self):
        result = []
        for mac, dev in self._devices.items():
            dev = dict(dev, mac=mac)
            dev["gamepad"] = is_gamepad(dev)
            result.append(dev)
        return result

    async def scan(self, seconds=20):
        self.scanning = True
        await asyncio.sleep(self.delay)
        self._devices.setdefault("AA:BB:CC:00:00:02", {"name": "Wireless Controller", "icon": "input-gaming",
                                                      "paired": False, "trusted": False, "connected": False})
        self._devices.setdefault("AA:BB:CC:00:00:03", {"name": "Some Speaker", "icon": "audio-card",
                                                      "paired": False, "trusted": False, "connected": False})
        self.scanning = False

    async def pair(self, mac):
        dev = self._get(mac)
        await asyncio.sleep(self.delay)
        dev.update(paired=True, trusted=True, connected=True)
        return {"ok": True, "log": ["pair: ok", "trust: ok", "connect: ok"]}

    async def connect(self, mac):
        self._get(mac)["connected"] = True
        return {"ok": True}

    async def disconnect(self, mac):
        self._get(mac)["connected"] = False
        return {"ok": True}

    async def remove(self, mac):
        self._get(mac)
        del self._devices[check_mac(mac)]
        return {"ok": True}

    def _get(self, mac):
        mac = check_mac(mac)
        if mac not in self._devices:
            raise BluetoothError("device not found")
        return self._devices[mac]

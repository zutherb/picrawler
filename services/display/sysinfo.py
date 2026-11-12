"""What the OLED shows: Wi-Fi quality, IP, temperature, usage bars.

Read straight from /proc and /sys so the display works without any other
service running.
"""
import json
import os
import re
import shutil
import socket

from services.common.metrics import CpuSampler, parse_meminfo

WIRELESS = "/proc/net/wireless"
NETWORK_STATUS = "/run/picrawler/network.json"    # written by picrawler-netwatch
BLUETOOTH = "/sys/class/bluetooth"
RFKILL = "/sys/class/rfkill"
# The kernel adds hci0:<handle> for every open Bluetooth connection.
CONNECTION = re.compile(r"^(hci\d+):\d+$")
THERMAL = "/sys/class/thermal/thermal_zone0/temp"
LINK_QUALITY_MAX = 70.0     # what Linux drivers report as full link quality


def parse_wireless(text, interface=None):
    """/proc/net/wireless -> (interface, quality 0..100, signal dBm) or None."""
    for line in text.splitlines()[2:]:
        name, sep, rest = line.partition(":")
        if not sep:
            continue
        name = name.strip()
        if interface and name != interface:
            continue
        fields = rest.split()
        try:
            quality = float(fields[1].rstrip("."))
            signal = float(fields[2].rstrip("."))
        except (IndexError, ValueError):
            continue
        return name, max(0, min(100, round(100 * quality / LINK_QUALITY_MAX))), signal
    return None


def wifi_bars(quality):
    """0 = no link, 1..4 bars."""
    if quality is None or quality <= 0:
        return 0
    return 1 + min(3, int(quality // 25))


def bluetooth_state(sys_bt=BLUETOOTH, sys_rfkill=RFKILL):
    """'absent', 'off' (blocked), 'on' or 'connected', plus connection count."""
    try:
        entries = os.listdir(sys_bt)
    except OSError:
        return "absent", 0
    adapters = [e for e in entries if re.match(r"^hci\d+$", e)]
    if not adapters:
        return "absent", 0
    connections = sum(1 for e in entries if CONNECTION.match(e))
    blocked = False
    try:
        for name in os.listdir(sys_rfkill):
            base = os.path.join(sys_rfkill, name)
            with open(os.path.join(base, "type")) as f:
                if f.read().strip() != "bluetooth":
                    continue
            for flag in ("soft", "hard"):
                with open(os.path.join(base, flag)) as f:
                    blocked = blocked or f.read().strip() == "1"
    except OSError:
        pass
    if blocked:
        return "off", 0
    return ("connected" if connections else "on"), connections


def network_status(path=NETWORK_STATUS):
    """Hotspot watcher state, or None when the watcher does not run."""
    try:
        with open(path) as f:
            status = json.load(f)
    except (OSError, ValueError):
        return None
    return status if isinstance(status, dict) else None


def local_ip():
    """Address of the interface with the default route; sends no packets."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def percent_used(path):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return round(100 * usage.used / usage.total, 1)


def is_separate_mount(path):
    """True if path is a mounted filesystem other than the root one."""
    try:
        return os.path.ismount(path) and os.stat(path).st_dev != os.stat("/").st_dev
    except OSError:
        return False


class SysInfo:

    def __init__(self, interface=None, nvme_path="/mnt/picrawler-data", network_path=NETWORK_STATUS):
        self.interface = interface
        self.nvme_path = nvme_path
        self.network_path = network_path
        self.cpu = CpuSampler()
        self.cpu.sample()   # first call only sets the baseline

    def read(self):
        info = {"cpu": self.cpu.sample(), "ram": None, "sd": percent_used("/"), "nvme": None,
                "wifi": None, "ip": local_ip(), "temp": None, "bluetooth": bluetooth_state(),
                "network": network_status(self.network_path)}
        if info["network"] and info["network"].get("mode") == "hotspot":
            info["ip"] = info["network"].get("ip") or info["ip"]   # no default route in hotspot mode
        try:
            with open("/proc/meminfo") as f:
                mem = parse_meminfo(f.read())
            info["ram"] = mem["percent"] if mem else None
        except OSError:
            pass
        if self.nvme_path and is_separate_mount(self.nvme_path):
            info["nvme"] = percent_used(self.nvme_path)
        try:
            with open(WIRELESS) as f:
                info["wifi"] = parse_wireless(f.read(), self.interface)
        except OSError:
            pass
        try:
            with open(THERMAL) as f:
                info["temp"] = round(int(f.read().strip()) / 1000)
        except (OSError, ValueError):
            pass
        return info

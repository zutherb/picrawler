#!/usr/bin/env python3
"""Fall back to the PiCrawler hotspot when no known Wi-Fi is around.

    netwatch.py --hotspot picrawler-hotspot [--status /run/picrawler/network.json]

Standard library plus nmcli/iw. Rules:
- Connected to a known network: nothing to do.
- Not connected for `boot_grace_s` after start (or later for `lost_s`):
  bring the hotspot up.
- Hotspot active and nobody connected to it for `idle_s`: every `retry_s`
  take it down and let NetworkManager try the known networks for
  `probe_s`; if that fails, the hotspot comes back. With a phone connected
  the hotspot stays, so the connection never drops under you.
The state goes to a JSON file for the OLED and the web UI.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HOTSPOT_IP = "10.42.0.1"


def run(*args, timeout=20):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def wifi_state(interface):
    """('connected' | 'connecting' | 'disconnected' | 'unavailable', connection name)."""
    code, out = run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device")
    if code != 0:
        return "unavailable", None
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[0] == interface and parts[1] == "wifi":
            state = parts[2]
            connection = ":".join(parts[3:]) or None
            if state.startswith("connected"):
                return "connected", connection
            if state.startswith("connecting"):
                return "connecting", connection
            return ("unavailable" if state == "unavailable" else "disconnected"), None
    return "unavailable", None


def station_count(interface):
    code, out = run("iw", "dev", interface, "station", "dump")
    return sum(1 for line in out.splitlines() if line.startswith("Station")) if code == 0 else 0


def mode_of(state, connection, hotspot):
    if state == "connected":
        return "hotspot" if connection == hotspot else "client"
    return "connecting" if state == "connecting" else "none"


class Watch:
    """The decisions, without side effects (testable)."""

    def __init__(self, boot_grace_s=45, lost_s=20, idle_s=120, retry_s=600, probe_s=40, start=0.0):
        self.boot_grace_s = boot_grace_s
        self.lost_s = lost_s
        self.idle_s = idle_s
        self.retry_s = retry_s
        self.probe_s = probe_s
        self.start = start
        self.offline_since = start
        self.hotspot_since = None
        self.last_station = None
        self.probing_since = None

    def decide(self, mode, stations, now):
        """Returns 'hotspot_up', 'probe' or None."""
        if mode == "client":
            self.offline_since = self.hotspot_since = self.probing_since = None
            return None
        if mode == "hotspot":
            self.offline_since = self.probing_since = None
            if self.hotspot_since is None:
                self.hotspot_since = now
            if stations > 0:
                self.last_station = now
            quiet_since = max(self.hotspot_since, self.last_station or self.hotspot_since)
            if now - self.hotspot_since >= self.retry_s and now - quiet_since >= self.idle_s:
                self.hotspot_since = None
                self.probing_since = now
                return "probe"
            return None
        if mode == "connecting":
            return None
        # mode == "none"
        if self.probing_since is not None:
            if now - self.probing_since < self.probe_s:
                return None          # give NetworkManager time to find a known network
            self.probing_since = None
            return "hotspot_up"
        if self.offline_since is None:
            self.offline_since = now
        grace = self.boot_grace_s if now - self.start < self.boot_grace_s else self.lost_s
        if now - self.offline_since >= grace:
            return "hotspot_up"
        return None


def write_status(path, status):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(status, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser(description="PiCrawler Wi-Fi hotspot fallback")
    parser.add_argument("--hotspot", default="picrawler-hotspot", help="NetworkManager profile of the hotspot")
    parser.add_argument("--interface", default="wlan0")
    parser.add_argument("--status", default="/run/picrawler/network.json")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--boot-grace", type=float, default=45)
    parser.add_argument("--retry", type=float, default=600)
    args = parser.parse_args()

    watch = Watch(boot_grace_s=args.boot_grace, retry_s=args.retry, start=time.monotonic())
    while True:
        state, connection = wifi_state(args.interface)
        mode = mode_of(state, connection, args.hotspot)
        stations = station_count(args.interface) if mode == "hotspot" else 0
        action = watch.decide(mode, stations, time.monotonic())
        if action == "hotspot_up":
            print("netwatch: no known Wi-Fi, starting the hotspot", flush=True)
            run("nmcli", "connection", "up", args.hotspot, timeout=40)
        elif action == "probe":
            print("netwatch: hotspot idle, looking for known Wi-Fi", flush=True)
            run("nmcli", "connection", "down", args.hotspot)
            # let NetworkManager pick the best known network (the hotspot
            # profile has autoconnect off, so it is never chosen here)
            run("nmcli", "device", "connect", args.interface, timeout=40)
        write_status(args.status, {
            "mode": mode, "connection": connection, "clients": stations,
            "ip": HOTSPOT_IP if mode == "hotspot" else None, "ts": time.time(),
        })
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())

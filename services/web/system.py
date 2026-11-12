"""Service and system information/actions for the admin page.

Only units from the configured allow-list can be touched. Restart and power
actions go through systemctl, which asks polkit; Ansible installs a rule that
allows exactly these for the web service user. No sudo, no shell.
"""
import asyncio
import random
import re
import time

from .metrics import CpuSampler, read_metrics

UNIT = re.compile(r"^picrawler-[a-z0-9_-]+(@[a-z0-9_-]+)?\.service$")


class SystemActionError(Exception):
    pass


async def _run(*args, timeout=10):
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise SystemActionError("%s timed out" % args[0])
    return proc.returncode, out.decode("utf-8", "replace")


def parse_show(text):
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value
    return values


def parse_throttled(text):
    """vcgencmd get_throttled -> flags (bits per Raspberry Pi docs)."""
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", text)
    if not m:
        return None
    value = int(m.group(1), 16)
    return {
        "raw": m.group(1),
        "under_voltage": bool(value & 0x1),
        "throttled": bool(value & 0x4),
        "under_voltage_since_boot": bool(value & 0x10000),
        "throttled_since_boot": bool(value & 0x40000),
    }


class System:

    def __init__(self, units, thermal_path="/sys/class/thermal/thermal_zone0/temp"):
        bad = [u for u in units if not UNIT.match(u)]
        if bad:
            raise ValueError("not a picrawler unit: %s" % ", ".join(bad))
        self.units = list(units)
        self.thermal_path = thermal_path
        self.cpu = CpuSampler()

    def _check(self, unit):
        if unit not in self.units:
            raise SystemActionError("unit not allowed")
        return unit

    async def services(self):
        result = []
        for unit in self.units:
            _, out = await _run("systemctl", "show", unit,
                                "--property=ActiveState,SubState,ActiveEnterTimestamp,MemoryCurrent")
            values = parse_show(out)
            memory = values.get("MemoryCurrent", "")
            result.append({"unit": unit, "active": values.get("ActiveState", "unknown"),
                           "sub": values.get("SubState", ""), "since": values.get("ActiveEnterTimestamp", ""),
                           "memory": int(memory) if memory.isdigit() else None})
        return result

    async def restart(self, unit):
        code, out = await _run("systemctl", "restart", self._check(unit), timeout=30)
        if code != 0:
            raise SystemActionError(out.strip() or "restart failed")
        return {"ok": True}

    async def journal(self, unit, lines=100):
        lines = max(10, min(int(lines), 500))
        _, out = await _run("journalctl", "-u", self._check(unit), "-n", str(lines), "--no-pager", "-o", "short-iso")
        return out

    async def info(self):
        info = {"time": time.time(), **read_metrics(self.cpu)}
        try:
            with open(self.thermal_path) as f:
                info["cpu_temp_c"] = round(int(f.read().strip()) / 1000, 1)
        except (OSError, ValueError):
            info["cpu_temp_c"] = None
        try:
            _, out = await _run("vcgencmd", "get_throttled", timeout=3)
            info["throttled"] = parse_throttled(out)
        except (OSError, SystemActionError):
            info["throttled"] = None
        try:
            with open("/proc/uptime") as f:
                info["uptime_s"] = int(float(f.read().split()[0]))
        except (OSError, ValueError):
            info["uptime_s"] = None
        return info

    async def power(self, action):
        if action not in ("poweroff", "reboot"):
            raise SystemActionError("unknown power action")
        code, out = await _run("systemctl", action, timeout=15)
        if code != 0:
            raise SystemActionError(out.strip() or "%s failed" % action)
        return {"ok": True}


class FakeSystem:
    def __init__(self, units):
        self.units = list(units)
        self.restarted = []
        self.power_actions = []

    async def services(self):
        return [{"unit": u, "active": "active", "sub": "running", "since": "fake",
                 "memory": random.randint(20, 90) * 2 ** 20} for u in self.units]

    async def restart(self, unit):
        if unit not in self.units:
            raise SystemActionError("unit not allowed")
        self.restarted.append(unit)
        return {"ok": True}

    async def journal(self, unit, lines=100):
        if unit not in self.units:
            raise SystemActionError("unit not allowed")
        return "2026-11-12T08:00:00 fake %s: running\n" % unit

    async def info(self):
        gib = 2 ** 30
        return {"time": time.time(), "cpu_temp_c": round(45 + random.random() * 8, 1), "uptime_s": 1234,
                "cpu_percent": round(15 + random.random() * 30, 1), "load": [0.8, 0.6, 0.5],
                "memory": {"total": 8 * gib, "used": int(1.9 * gib), "percent": 23.8},
                "disk": {"total": 32 * gib, "used": 9 * gib, "percent": 28.1},
                "throttled": {"raw": "0x0", "under_voltage": False, "throttled": False,
                              "under_voltage_since_boot": False, "throttled_since_boot": False}}

    async def power(self, action):
        if action not in ("poweroff", "reboot"):
            raise SystemActionError("unknown power action")
        self.power_actions.append(action)
        return {"ok": True}

"""Light system metrics straight from /proc, for the admin page.

No Prometheus on the Pi: it would cost RAM and SD card writes for one robot.
For history, run node_exporter here and Prometheus/Grafana elsewhere.
"""
import shutil


def parse_meminfo(text):
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024       # kB -> bytes
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    return {"total": total, "used": total - available, "percent": round(100 * (total - available) / total, 1)}


def parse_cpu_times(text):
    """First line of /proc/stat -> (idle, total) jiffies."""
    fields = text.splitlines()[0].split()
    if fields[0] != "cpu":
        raise ValueError("unexpected /proc/stat format")
    values = [int(v) for v in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)   # idle + iowait
    return idle, sum(values[:8])                                # without guest time (already in user)


class CpuSampler:
    """CPU usage in percent since the previous call."""

    def __init__(self, stat_path="/proc/stat"):
        self.stat_path = stat_path
        self._last = None

    def sample(self):
        try:
            with open(self.stat_path) as f:
                idle, total = parse_cpu_times(f.read())
        except (OSError, ValueError, IndexError):
            return None
        last, self._last = self._last, (idle, total)
        if last is None or total == last[1]:
            return None
        return round(100 * (1 - (idle - last[0]) / (total - last[1])), 1)


def read_metrics(cpu, root="/"):
    metrics = {"cpu_percent": cpu.sample()}
    try:
        with open("/proc/meminfo") as f:
            metrics["memory"] = parse_meminfo(f.read())
    except OSError:
        metrics["memory"] = None
    try:
        with open("/proc/loadavg") as f:
            metrics["load"] = [float(v) for v in f.read().split()[:3]]
    except (OSError, ValueError):
        metrics["load"] = None
    try:
        usage = shutil.disk_usage(root)
        metrics["disk"] = {"total": usage.total, "used": usage.used,
                           "percent": round(100 * usage.used / usage.total, 1)}
    except OSError:
        metrics["disk"] = None
    return metrics

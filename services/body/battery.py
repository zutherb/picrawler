"""Battery voltage of the Robot HAT, smoothed, with charge estimate and levels.

The HAT measures the battery on ADC channel A4 through a 20k/10k divider:
V = A4 / 4095 * 3.3 V * 3 (SunFounder Robot HAT docs). A4 is read directly
because robot_hat's get_battery_voltage() is broken in some versions.

The PiCrawler runs on two Li-ion cells in series (2S, 6.0-8.4 V). Walking
makes the voltage sag, so readings are smoothed and a level only changes
after it held for a while.
"""
import collections
import statistics

# 2S Li-ion, resting voltage -> charge (rough, per cell 3.2-4.2 V)
CURVE = ((8.40, 100), (8.10, 90), (7.90, 80), (7.70, 65), (7.55, 50), (7.45, 40),
         (7.35, 30), (7.25, 20), (7.10, 10), (6.90, 5), (6.40, 0))


def percent(volts):
    if volts >= CURVE[0][0]:
        return 100
    for (v_hi, p_hi), (v_lo, p_lo) in zip(CURVE, CURVE[1:]):
        if volts >= v_lo:
            return round(p_lo + (p_hi - p_lo) * (volts - v_lo) / (v_hi - v_lo))
    return 0


def adc_to_volts(raw):
    return raw / 4095 * 3.3 * 3


class BatteryMonitor:
    """Feed readings; tells the smoothed voltage, charge and level.

    level: "ok", "low" (warn) or "critical" (sit down). A level must hold
    for `hold_s` before it changes, so a sag while walking does not count.
    """

    def __init__(self, low_v=7.0, critical_v=6.6, hold_s=20.0, window=12, present_min_v=3.0):
        self.low_v = low_v
        self.critical_v = critical_v
        self.hold_s = hold_s
        self.present_min_v = present_min_v
        self._readings = collections.deque(maxlen=window)
        self.level = "ok"
        self._candidate = None      # (level, since)

    def feed(self, volts, now):
        """Returns the new level when it changed, else None."""
        if volts is None or volts < self.present_min_v:
            return None      # no battery on A4 (e.g. powered by USB only)
        self._readings.append(volts)
        raw_level = self._raw_level(self.voltage)
        if raw_level == self.level:
            self._candidate = None
            return None
        if self._candidate is None or self._candidate[0] != raw_level:
            self._candidate = (raw_level, now)
            return None
        if now - self._candidate[1] >= self.hold_s:
            self.level, self._candidate = raw_level, None
            return self.level
        return None

    def _raw_level(self, volts):
        if volts < self.critical_v:
            return "critical"
        # a little hysteresis on the way back up
        if volts < self.low_v or (self.level != "ok" and volts < self.low_v + 0.1):
            return "low"
        return "ok"

    @property
    def voltage(self):
        return round(statistics.median(self._readings), 2) if self._readings else None

    def state(self):
        v = self.voltage
        if v is None:
            return {"present": False}
        return {"present": True, "voltage": v, "percent": percent(v), "level": self.level}


class HatBattery:
    def __init__(self, channel="A4"):
        from robot_hat import ADC
        self._adc = ADC(channel)

    def read(self):
        return adc_to_volts(self._adc.read())


class FakeBattery:
    """Slowly discharging pack for --fake runs."""

    def __init__(self, clock):
        self._clock = clock
        self._t0 = clock()

    def read(self):
        return max(6.5, 8.2 - (self._clock() - self._t0) / 600)

"""Keep local flight detail while controlling samples sent to Marple."""
import math

SAMPLE_MODES = {'low': 1, 'high': 10}
LIVE_SIGNALS = frozenset({
    'airspeed_kias', 'altitude_msl_ft', 'roll_deg', 'pitch_deg',
    'heading_magnetic_deg', 'latitude_deg', 'longitude_deg', 'vertical_speed_fpm',
})


class LiveSampler:
    """Select the first received value per signal per session-relative time bucket.

    Preserve original receive timestamps; never fill forward missing values.
    Different UDP fragments may contain different signals in the same bucket.
    """
    def __init__(self, mode='low'):
        self.mode = mode
        self.hz = SAMPLE_MODES[mode]
        self.start = None
        self.last_bucket = {}

    def select(self, mono, values):
        if self.start is None:
            self.start = mono
        bucket = math.floor((mono - self.start) * self.hz + 1e-7)
        result = {}
        for name, value in values.items():
            if bucket > self.last_bucket.get(name, -1):
                result[name] = value
                self.last_bucket[name] = bucket
        return result


class ReportTelemetry:
    """Per-session derived channels; detection still receives all raw samples."""
    def __init__(self):
        self.latest = {}
        self.previous = None
        self.distance_m = 0.0

    def add(self, mono, values):
        result = dict(values)
        conversions = {'altitude_msl_m': ('altitude_msl_ft', 1 / .3048),
                       'altitude_agl_m': ('altitude_agl_ft', 1 / .3048),
                       'groundspeed_mps': ('groundspeed_kt', 3600 / 1852),
                       'vertical_speed_mps': ('vertical_speed_fpm', 60 / .3048)}
        for source, (name, factor) in conversions.items():
            if source in values:
                result[name] = values[source] * factor
        self.latest.update({k: (v, mono) for k, v in values.items()})
        def fresh(name):
            value, at = self.latest.get(name, (None, -math.inf))
            return value if mono - at < .5 else None
        if fresh('paused') != 0 or fresh('replay') != 0:
            self.previous = None
        elif 'groundspeed_mps' in values:
            now, speed = fresh('flight_time_s'), values['groundspeed_mps']
            if now is None or speed < 0:
                self.previous = None
            elif self.previous is None:
                self.previous = (now, speed)
            else:
                before, old_speed = self.previous
                dt = now - before
                if 0 < dt <= 2:
                    self.distance_m += .5 * (old_speed + speed) * dt
                if dt != 0:
                    self.previous = (now, speed)
            result['distance_covered_nm'] = self.distance_m / 1852
        return result

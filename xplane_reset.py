"""Pause and reposition once after landing; never unpause the operator's flight."""
import math
import struct

from xplane_live import command_packet

TARGET_ALTITUDE_M = 3000 * .3048
POSITION_FIELDS = ('latitude_deg', 'longitude_deg', 'heading_true_deg', 'true_airspeed_mps')


def fresh_values(latest, now, names, after=-math.inf):
    result = {}
    for name in names:
        value, at = latest.get(name, (None, -math.inf))
        if value is None or not math.isfinite(value) or not after < at <= now or now-at >= 1:
            return None
        result[name] = value
    return result


def reset_packet(target):
    """X-Plane 11 PREL, loc_specify_lle=6, user aircraft=0, packed 64-byte body."""
    lat, lon, heading, speed = (target[k] for k in POSITION_FIELDS)
    if not all(math.isfinite(v) for v in (lat, lon, heading, speed)):
        raise ValueError('Non-finite reset position')
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= heading <= 360 and 0 < speed < 400):
        raise ValueError('Invalid approach reset position or speed')
    return struct.pack('<4sxii8siiddddd', b'PREL', 6, 0, b'', 0, 0,
                       lat, lon, TARGET_ALTITUDE_M, heading, speed)


class LandingReset:
    """Independent of cloud workers. Each command is followed by fresh telemetry."""
    def __init__(self, receiver, log):
        self.receiver = receiver
        self.log = log
        self.state = 'IDLE'
        self.target = None
        self.sent_at = None
        self.deadline = None
        self.message = 'Pause and reset 10 seconds after touchdown'

    @property
    def active(self):
        return self.state in {'WAIT_PAUSE', 'WAIT_POSITION', 'WAIT_REPAUSE'}

    def fail(self, message):
        self.state = 'ERROR'
        self.message = message + ' Use pause/ISCS manually.'
        self.log(self.message)

    def cancel(self):
        if self.active:
            self.state = 'CANCELLED'
            self.message = 'Automatic reset cancelled.'
            self.log(self.message)

    def send(self, packet, now):
        try:
            self.receiver.sock.sendto(packet, self.receiver.target)
            self.sent_at = now
            return True
        except OSError:
            self.fail('Simulator command failed.')
            return False

    def begin(self, now, latest, target):
        if self.state != 'IDLE':
            return
        self.target = dict(target) if target else None
        status = fresh_values(latest, now, ('paused', 'replay'))
        if not status or status['replay'] != 0 or status['paused'] not in (0, 1):
            self.fail('Pause/reset skipped: no fresh non-replay simulator state.')
            return
        if status['paused'] == 1:
            self.reposition(now)
        elif self.send(command_packet('sim/operation/pause_toggle'), now):
            self.state = 'WAIT_PAUSE'
            self.deadline = now + 3
            self.message = 'Pause requested; waiting for confirmation before resetting.'
            self.log(self.message)

    def reposition(self, now):
        if not self.target:
            self.fail('Paused, but no valid approach starting position was captured.')
            return
        try:
            packet = reset_packet(self.target)
        except ValueError:
            self.fail('Paused, but the captured approach position is invalid.')
            return
        if self.send(packet, now):
            self.state = 'WAIT_POSITION'
            self.deadline = now + 20
            self.message = 'Reset requested to approach start at 3,000 ft MSL; waiting for telemetry.'
            self.log(self.message)

    def update(self, now, latest):
        if not self.active:
            return
        status = fresh_values(latest, now, ('paused', 'replay'), self.sent_at)
        if status and status['replay'] != 0:
            self.fail('Reset interrupted by replay mode.')
            return
        if self.state == 'WAIT_PAUSE' and status and status['paused'] == 1:
            self.reposition(now)
            return
        if self.state in {'WAIT_POSITION', 'WAIT_REPAUSE'} and status:
            position = fresh_values(latest, now, ('latitude_deg', 'longitude_deg', 'altitude_msl_m'), self.sent_at)
            if position and (abs(position['altitude_msl_m'] - TARGET_ALTITUDE_M) < 15.24
                             and abs(position['latitude_deg'] - self.target['latitude_deg']) < .001
                             and abs(position['longitude_deg'] - self.target['longitude_deg']) < .001):
                if status['paused'] == 1:
                    self.state = 'COMPLETE'
                    self.message = 'Reset confirmed at 3,000 ft and PAUSED. Set up ISCS, then unpause to record.'
                    self.log(self.message)
                    return
                if status['paused'] == 0 and self.state == 'WAIT_POSITION':
                    # PREL may resume the simulation. Toggle only after receiving
                    # a fresh post-reset unpaused state, never as a blind retry.
                    if self.send(command_packet('sim/operation/pause_toggle'), now):
                        self.state = 'WAIT_REPAUSE'
                        self.deadline = now + 3
                    return
        if now >= self.deadline:
            self.fail('Pause/reset was not confirmed in time.')

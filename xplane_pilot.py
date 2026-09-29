"""Pre-flight pilot entry while X-Plane is paused; cloud work never waits on UI."""
import subprocess
import sys
import uuid

from xplane_reset import fresh_values
from xplane_live import command_packet

PROMPT = 'New landing challenge, input pilot name'
SCRIPT = '''activate
try
set reply to display dialog "New landing challenge, input pilot name\n\nLeave X-Plane paused until the recorder is ready, then unpause manually." default answer "" with title "Marple Landing Challenge" buttons {"Cancel", "Ready"} default button "Ready" cancel button "Cancel"
set pilotName to text returned of reply
if length of pilotName > 80 then error "Name must be at most 80 characters"
return "NAME:" & pilotName
on error number -128
return "CANCEL:"
end try
'''


class PilotDialog:
    def __init__(self):
        self.process = None

    def open(self):
        if sys.platform != 'darwin':return False
        try:
            self.process = subprocess.Popen(['osascript', '-e', SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            return True
        except OSError:return False

    def poll(self):
        if self.process is None or self.process.poll() is None:return None
        output, _ = self.process.communicate()
        code = self.process.returncode
        self.process = None
        if code != 0:return ('error', '')
        if output.strip() == 'CANCEL:':return ('cancel', '')
        return ('name', output[5:].strip()) if output.startswith('NAME:') else ('error', '')

    def close(self):
        if self.process is None:return
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=.3)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=.3)
        if self.process.stdout:self.process.stdout.close()
        self.process = None


class PilotSetup:
    def __init__(self, receiver, native=False):
        self.receiver = receiver
        self.native = native
        self.id = uuid.uuid4().hex
        self.dialog = PilotDialog()
        self.dialog_attempted = False
        self.state = 'WAIT_PAUSE'
        self.name = None
        self.sent_at = None
        self.pause_failed = False
        self.message = 'Waiting for fresh simulator pause status.'

    def submit(self, target, name):
        if target != self.id or self.state != 'INPUT':return False
        name = name.strip()
        if not name or len(name) > 80 or not all(c.isprintable() for c in name):
            self.message = 'Enter a pilot name of 1–80 printable characters in Terminal.'
            return False
        self.name = name
        self.dialog.close()
        return True

    def update(self, now, latest):
        status = fresh_values(latest, now, ('paused', 'replay'))
        if not status or status['replay'] != 0:
            self.message = 'Waiting for fresh, non-replay simulator telemetry.'
            return
        acknowledged = self.sent_at is None or latest['paused'][1] > self.sent_at
        if status['paused'] == 1 and acknowledged:
            self.sent_at = None;self.pause_failed = False
            self.state = 'INPUT'
            if self.name:
                self.state = 'READY';return
            if self.native and not self.dialog_attempted:
                self.dialog_attempted = True
                self.dialog.open()
            result = self.dialog.poll()
            if result is not None:
                kind, name = result
                if kind == 'cancel':self.state = 'CANCELLED'
                elif kind == 'name' and self.submit(self.id, name):self.state = 'READY'
                else:self.message = 'Enter a pilot name of 1–80 printable characters in Terminal.'
                return
            self.message = PROMPT
        else:
            self.state = 'WAIT_PAUSE'
            if self.sent_at is None and not self.pause_failed and status['paused'] == 0:
                try:
                    self.receiver.sock.sendto(command_packet('sim/operation/pause_toggle'),self.receiver.target)
                    self.sent_at = now
                except OSError:self.pause_failed = True
            if self.sent_at is not None and now-self.sent_at >= 3:self.pause_failed = True
            self.message = ('Pause was not confirmed. Pause X-Plane manually to enter a name.' if self.pause_failed
                            else 'Pausing X-Plane before pilot name entry…')

    def close(self):
        self.dialog.close()

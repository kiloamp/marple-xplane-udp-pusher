"""Small, testable operator view and Unicode name entry; no network work here."""
import curses


class ConsoleInput:
    def __init__(self):
        self.seen = set()
        self.target = None
        self.text = ''
        self.details = False
        self.error = ''

    def observe(self, status):
        if status['exiting']:
            self.target = None
            return
        if self.target is None:
            entry = next((e for e in status['completed'] if e['id'] not in self.seen), None)
            if entry:
                self.seen.add(entry['id'])
                self.open(entry)

    def open(self, entry):
        self.target = dict(entry)  # The next flight cannot change this target.
        self.text = entry['participant']
        self.error = ''

    def handle(self, key, status):
        if key == '\x03':
            self.target = None
            return 'quit'
        if self.target is not None:
            if key == '\x1b':
                self.target = None
            elif key in ('\n', '\r', curses.KEY_ENTER):
                if self.text.strip():
                    result = ('participant', self.target['id'], self.text.strip())
                    self.target = None
                    return result
                self.error = 'Enter a name, or press Esc to skip.'
            elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
                self.text = self.text[:-1]
                self.error = ''
            elif isinstance(key, str) and key.isprintable() and len(self.text) < 80:
                self.text += key
                self.error = ''
            return None
        if not isinstance(key, str) or status['exiting']:
            return None
        key = key.lower()
        if key == 'd':
            self.details = not self.details
        elif key == 'n':
            available = [e for e in status['completed'] if e['name_status'] not in ('WAITING', 'SAVING')]
            if available:
                self.open(next((e for e in available if e['name_status'] == 'ERROR'), available[-1]))
        else:
            return {'s': 'start', 'x': 'stop', 'r': 'rate', 'q': 'quit'}.get(key)


def console_lines(s, editor):
    mode = {'landing': 'Airbus landing challenge', 'timed': '60-second flying challenge'}.get(s['mode'], 'Detecting aircraft')
    state = s['state']
    if s['exiting']:
        action = 'Finishing uploads. Please leave this window open.'
    elif s['reset_state'] == 'ERROR':
        action = 'Reset needs attention: ' + s['pause']
    elif state == 'RECORDING':
        action = (f"Touchdown! Reset in {s['remaining']:.0f}s." if s['touchdown'] else 'Recording — fly your approach.') if s['mode'] == 'landing' else f"Recording — {s['remaining']:.0f} seconds left."
    elif state == 'COUNTDOWN':
        action = f"Get ready — starting in {s['countdown']:.0f}s." if s['countdown'] else 'Preparing recording…'
    elif s['reset_state'] in ('WAIT_PAUSE', 'WAIT_POSITION', 'WAIT_REPAUSE'):
        action = 'Pausing and resetting to 3,000 ft…'
    elif state == 'WAITING':
        action = 'Finish simulator setup, then unpause to begin.'
    elif state == 'WAIT_RESET':
        action = 'Waiting for reset to 3,000 ft.'
    elif state == 'SAVING':
        action = 'Flight finished — saving to Marple.'
    elif state in ('WAIT_ID',) or not s['connected']:
        action = 'Waiting for X-Plane. Start the simulator and load a flight.'
    elif state == 'ERROR':
        action = s['message']
    else:
        action = 'Press S to start a new flight.'
    file_state = {'FINISHED': 'Ready in Marple', 'LOCAL_FILE_READY': 'Saved locally',
                  'ERROR': 'Upload not confirmed — local file kept',
                  'VERIFYING': 'Checking upload', 'IMPORTING': 'Processing in Marple',
                  'UPLOADING': 'Uploading', 'PREPARING': 'Preparing file'}.get(s['analysis_state'], 'Available after landing' if s['mode'] == 'landing' else 'Available after the flight')
    lines = ['MARPLE FLIGHT RECORDER', mode, action, '']
    if editor.target:
        lines += ['Flight finished: ' + editor.target['name'],
                  'Your name: ' + editor.text[-50:] + '▏',
                  editor.error or 'Enter: save name   Esc: skip   Ctrl+C: quit safely', '']
    lines += ['Live: ' + s['dataset'],
              'Analysis: ' + file_state,
              '  ' + s['analysis_name'] if s['analysis_name'] else '']
    if s['completed']:
        entry = next((e for e in s['completed'] if e['name_status'] == 'ERROR'), s['completed'][-1])
        label = {'WAITING': 'waiting for file upload', 'SAVING': 'saving to Marple',
                 'SAVED': 'saved to Marple', 'LOCAL': 'saved locally',
                 'ERROR': 'not saved to Marple — N to retry', 'UNNAMED': 'N to add name'}[entry['name_status']]
        if editor.target is None or editor.target['id'] != entry['id']:
            lines += ['Name: ' + label + (': ' + entry['participant'] if entry['participant'] else ''),
                      '  ' + entry['name']]
    if s['cloud_failed']:
        lines += ['Live preview failed. Analysis file status is shown above.']
    lines += ['', 'S Start   X Stop & save   N Name   D Details   Q Quit']
    if editor.details:
        lines += ['', f"X-Plane: {'connected' if s['connected'] else 'waiting'} | {s['aircraft']}",
                  f"Signals: {s['signals']} local / {s['live_signals']} live | Queue: {s['queue']}",
                  f"R: change live rate | current {s['active_sample_mode']} / next {s['sample_mode']}",
                  s['analysis'], s['pause'], *s['events'][-3:]]
    return lines

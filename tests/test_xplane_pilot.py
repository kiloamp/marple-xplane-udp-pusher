import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq

from xplane_pilot import PilotDialog, PilotSetup, SCRIPT
from xplane_session import SessionController, command_packet
from xplane_tui import ConsoleInput
from test_xplane_aircraft import FakeWorker, TOLISS


def state(at, paused=1, replay=0):
    return {'paused':(paused,at),'replay':(replay,at)}


class PilotSetupTests(unittest.TestCase):
    def test_pause_requires_new_ack_and_never_blindly_retries(self):
        receiver=MagicMock();p=PilotSetup(receiver)
        p.update(0,state(0,0))
        receiver.sock.sendto.assert_called_once_with(command_packet('sim/operation/pause_toggle'),receiver.target)
        p.update(.1,state(0,1));self.assertEqual(p.state,'WAIT_PAUSE')
        p.update(4,state(4,0));self.assertTrue(p.pause_failed)
        p.update(5,state(5,0));self.assertEqual(receiver.sock.sendto.call_count,1)
        p.update(6,state(6,1));self.assertEqual(p.state,'INPUT')

    def test_name_and_fresh_pause_required_no_previous_or_invalid_name(self):
        p=PilotSetup(MagicMock());p.update(0,state(0))
        for name in ('','  ','x'*81,'a\nb'):
            self.assertFalse(p.submit(p.id,name))
        self.assertFalse(p.submit('old-challenge','Old pilot'))
        self.assertTrue(p.submit(p.id,' José '))
        p.update(2,state(0));self.assertNotEqual(p.state,'READY')
        p.update(3,state(3,replay=1));self.assertNotEqual(p.state,'READY')
        p.update(4,state(4));self.assertEqual(p.state,'READY');self.assertEqual(p.name,'José')

    def test_unpause_before_entry_is_paused_again_and_gate_stays_closed(self):
        receiver=MagicMock();p=PilotSetup(receiver)
        p.update(0,state(0));p.update(.1,state(.1,0))
        self.assertEqual(p.state,'WAIT_PAUSE');self.assertFalse(p.submit(p.id,'Too soon'))
        receiver.sock.sendto.assert_called_once()
        p.update(.2,state(.2));self.assertEqual(p.state,'INPUT')

    def test_no_pause_command_on_stale_or_replay(self):
        receiver=MagicMock();p=PilotSetup(receiver)
        p.update(5,state(0,0));p.update(6,state(6,0,1))
        receiver.sock.sendto.assert_not_called()

    def test_native_dialog_once_cancel_and_fallback(self):
        for result,expected in [(('cancel',''),'CANCELLED'),(('error',''),'INPUT'),(('name','Alex'),'READY')]:
            p=PilotSetup(MagicMock(),native=True);p.dialog=MagicMock()
            p.dialog.poll.return_value=result
            p.update(0,state(0));self.assertEqual(p.state,expected)
            if expected=='INPUT':
                p.dialog.poll.return_value=None;p.update(.1,state(.1))
            p.dialog.open.assert_called_once()

    def test_dialog_passes_name_as_output_not_shell_code(self):
        with patch('xplane_pilot.sys.platform','darwin'),patch('xplane_pilot.subprocess.Popen') as popen:
            proc=popen.return_value;proc.poll.return_value=0;proc.returncode=0
            proc.communicate.return_value=('NAME:José "Q"\n',None)
            d=PilotDialog();self.assertTrue(d.open())
            self.assertEqual(d.poll(),('name','José "Q"'))
            self.assertEqual(popen.call_args.args[0],['osascript','-e',SCRIPT])
            self.assertNotIn('shell',popen.call_args.kwargs)


class PilotFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
        self.receiver=MagicMock();self.receiver.failure=None
        self.receiver.identity.snapshot.return_value=TOLISS
        self.c=SessionController(self.receiver,self.folder,auto_route=True)
        self.c.sequence_path=self.folder/'sequence.json'
        self.patch=patch('xplane_session.UploadWorker',FakeWorker);self.patch.start()

    def tearDown(self):
        self.c.close();self.patch.stop();self.tmp.cleanup()

    def feed(self,at,paused=1,ground=0,compression=0,alt=914.4):
        self.c.feed(int(at*1e9),at,{'paused':paused,'replay':0,'on_ground':ground,
            'gear_0_compression_m':compression,'altitude_msl_m':alt,
            'latitude_deg':39.55,'longitude_deg':2.73,'heading_true_deg':90,'true_airspeed_mps':75})
        self.c.tick(at)

    def test_first_flight_waits_for_name_then_manual_unpause(self):
        c=self.c;self.feed(100)
        self.assertEqual(c.state,'WAIT_PILOT');self.assertIsNone(c.worker)
        prompt=c.snapshot(100)['pilot_prompt'];self.assertEqual(prompt['name'],'New landing challenge, input pilot name')
        c.commands.put(('pilot',prompt['id'],'Alice'));c.tick(100.1)
        self.assertEqual(c.state,'COUNTDOWN');self.assertIsNone(c.timer.started)
        self.assertEqual(c.worker.metadata['Participant Name'],'Alice')
        self.feed(100.2);self.assertIsNone(c.record_file)
        self.feed(100.3,paused=0)
        self.assertEqual(c.state,'RECORDING');self.assertEqual(c.timer.started,100.3)
        self.receiver.sock.sendto.assert_not_called()

    def test_reset_prompts_during_previous_upload_and_next_name_is_isolated(self):
        c=self.c;self.feed(100)
        first_id=c.pilot_setup.id;c.commands.put(('pilot',first_id,'Alice'));c.tick(100.1)
        self.feed(100.2,paused=0);self.feed(100.3,paused=0);self.feed(101.5,paused=0)
        self.feed(102,paused=0,ground=1,compression=.1,alt=3)
        first=c.file_worker;first.join(5)
        self.assertEqual(pq.read_table(first.path).schema.metadata[b'Participant Name'],b'Alice')
        old=c.worker;old.finish=MagicMock()
        self.feed(111.9,paused=0,ground=1,compression=.1,alt=3);c.tick(112)
        self.assertEqual(c.state,'SAVING')
        self.feed(112.1,paused=1,ground=1,alt=3)
        self.feed(112.2);c.tick(112.3)
        self.assertEqual(c.resetter.state,'COMPLETE')
        self.assertEqual(c.state,'SAVING')
        prompt=c.snapshot(112.3)['pilot_prompt'];self.assertNotEqual(prompt['id'],first_id)
        c.commands.put(('pilot',first_id,'Wrong pilot'));c.tick(112.31)
        self.assertIsNone(c.pilot_setup.name)
        c.commands.put(('pilot',prompt['id'],'Bob'));c.tick(112.4)
        self.assertEqual(old.metadata['Participant Name'],'Alice')
        old.done.set();old.flight.manifest['state']='FINISHED';c.tick(112.5);c.tick(112.6)
        self.assertEqual(c.state,'COUNTDOWN');self.assertEqual(c.worker.metadata['Participant Name'],'Bob')
        self.assertIsNone(c.timer.started)
        self.feed(112.7,paused=0)
        self.assertEqual(c.timer.started,112.7)
        self.assertEqual(first.metadata['Participant Name'],'Alice')
        self.assertFalse(c.snapshot(112.7)['completed'][0]['auto_prompt'])

    def test_quit_cancels_entry_without_creating_dataset(self):
        self.feed(100);setup=self.c.pilot_setup
        setup.dialog=MagicMock();self.c.commands.put('quit');self.c.tick(100.1)
        self.assertTrue(self.c.closed);self.assertIsNone(self.c.worker)
        setup.dialog.close.assert_called_once()

    def test_pending_pause_ack_cannot_start_when_cloud_becomes_ready(self):
        c=self.c;self.feed(100)
        with patch.object(FakeWorker,'start',lambda worker:None):
            c.commands.put(('pilot',c.pilot_setup.id,'Alice'));c.tick(100.1)
        self.assertEqual(c.state,'COUNTDOWN')
        self.feed(100.2,paused=0)
        self.assertEqual(c.pilot_setup.state,'WAIT_PAUSE')
        c.worker.ready.set();c.tick(100.21)
        self.assertIsNone(c.timer.started)
        self.feed(100.3,paused=1);self.assertIsNone(c.timer.started)
        self.feed(100.4,paused=0);self.assertEqual(c.timer.started,100.4)
        self.receiver.sock.sendto.assert_called_once()

    def test_aircraft_change_discards_pending_dialog(self):
        self.feed(100);setup=self.c.pilot_setup;setup.dialog=MagicMock()
        self.receiver.identity.snapshot.return_value={'icao':'C172','author':'Laminar','description':'Cessna'}
        self.c.tick(100.1)
        setup.dialog.close.assert_called_once()
        self.assertIsNone(self.c.pilot_setup);self.assertIsNone(self.c.worker)

    def test_terminal_preflight_cannot_skip_into_recording(self):
        self.feed(100);s=self.c.snapshot(100);ui=ConsoleInput();ui.observe(s)
        for key in 'Alex Q':self.assertIsNone(ui.handle(key,s))
        self.assertEqual(ui.handle('\n',s),('pilot',self.c.pilot_setup.id,'Alex Q'))
        ui.open(s['pilot_prompt']);self.assertEqual(ui.handle('\x1b',s),'stop')
        s['completed']=[{'id':'old','auto_prompt':False,'name':'Old flight','participant':'Prior','name_status':'SAVED'}]
        s['pilot_prompt']=None;ui.observe(s);self.assertIsNone(ui.target)

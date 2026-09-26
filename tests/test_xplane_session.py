import json
import queue
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from xplane_session import SessionController, SessionTimer, UploadWorker, alert_packet, command_packet

class TimedSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
        self.receiver=MagicMock();self.receiver.failure=None;self.receiver.target=('127.0.0.1',49000)
        self.controller=SessionController(self.receiver,self.folder)
    def tearDown(self):self.controller.close();self.tmp.cleanup()
    def begin(self):
        c=self.controller;c.state='RECORDING';c.timer.start(100)
        c.worker=MagicMock();c.worker.done.is_set.return_value=False;c.worker.jobs=queue.Queue()
        c.record_file=(self.folder/'record.jsonl').open('w')
        c.feed(1,100,{'paused':0,'replay':0,'airspeed_kias':100})
        return c
    def test_alert_packet_matches_bundled_protocol(self):
        p=alert_packet('START\n60 seconds');self.assertEqual(len(p),965)
        self.assertEqual(struct.unpack('<240s240s240s240s',p[5:])[0].rstrip(b'\0'),b'START')
        self.assertEqual(command_packet('sim/operation/pause_toggle'),b'CMND\0sim/operation/pause_toggle\0')
        with self.assertRaises(ValueError):alert_packet('x'*240)
    def test_warning_once_and_pause_at_deadline_even_when_upload_stalled(self):
        c=self.begin();c.tick(149.9);self.receiver.sock.sendto.assert_not_called()
        c.tick(150);c.tick(151)
        self.assertEqual(self.receiver.sock.sendto.call_count,1)
        c.feed(2,159.9,{'paused':0});c.tick(160)
        self.assertEqual(c.state,'SAVING');c.worker.finish.assert_called_once_with('60-second session complete')
        self.assertEqual(self.receiver.sock.sendto.call_args.args[0],command_packet('sim/operation/pause_toggle'))
        c.feed(3,160.1,{'paused':1});self.assertTrue(c.pause_confirmed)
        self.assertEqual(c.worker.jobs.qsize(),2)
        self.assertEqual(len((self.folder/'record.jsonl').read_text().splitlines()),2)
    def test_no_pause_toggle_if_already_paused_or_status_stale(self):
        c=self.begin();c.feed(2,159.9,{'paused':1});c.tick(160)
        self.receiver.sock.sendto.assert_not_called();self.assertTrue(c.pause_confirmed)
    def test_disconnected_start_waits_and_cancel_creates_no_flight(self):
        c=self.controller;c.start(1);c.tick(10)
        self.assertEqual(c.state,'WAITING');self.assertIsNone(c.worker)
        c.commands.put('quit');c.tick(11);self.assertTrue(c.closed)
    def test_countdown_requires_unpaused_fresh_telemetry_and_setup(self):
        c=self.controller;c.state='COUNTDOWN';c.countdown_until=105;c.worker=MagicMock()
        c.worker.ready.is_set.return_value=True;c.worker.error=None;c.worker.name='test';c.worker.flight.manifest={}
        c.feed(1,104.9,{'paused':0,'replay':0});c.tick(104.9);self.assertEqual(c.state,'COUNTDOWN')
        c.tick(105);self.assertEqual(c.state,'RECORDING');self.assertEqual(c.timer.started,105)
        self.assertEqual(self.receiver.sock.sendto.call_args.args[0][:4],b'ALRT')
    def test_timer_does_not_send_late_warning_after_deadline(self):
        timer=SessionTimer();timer.start(0)
        self.assertEqual(timer.tick(61),['stop']);self.assertEqual(timer.tick(62),[])
    def test_worker_drains_all_samples_before_finalizing(self):
        with patch('xplane_session.Flight') as flight:
            worker=UploadWorker(self.folder,1,'test',None,1,lambda *a,**k:None)
            worker.jobs.put((1,{'pitch_deg':1}));worker.jobs.put((2,{'pitch_deg':2}))
            worker.finish('complete');worker.start();worker.join(2)
            self.assertTrue(worker.done.is_set());self.assertIsNone(worker.error)
            self.assertEqual(flight.return_value.add.call_count,2)
            flight.return_value.finish.assert_called_once_with('complete')

if __name__=='__main__':unittest.main()

import json
import queue
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from xplane_aircraft import AircraftIdentity, FIELDS, REQUESTS, aircraft_mode, packets
from xplane_session import SessionController
from xplane_live import Flight, command_packet

TOLISS={'icao':'A319','author':'Gliding Kiwi','description':'A319 with high fidelity system modelling'}
OTHER={'icao':'C172','author':'Laminar Research','description':'Cessna Skyhawk'}


def identity_packet(identity):
    raw={f:identity.get(f,'').encode().ljust(n,b'\0') for f,_,n in FIELDS}
    return b'RREF\0'+b''.join(struct.pack('<if',index,raw[field][offset]) for index,field,offset,ref in REQUESTS)


class IdentityTests(unittest.TestCase):
    def test_toliss_author_and_brand_but_not_other_airbus(self):
        self.assertEqual(aircraft_mode(TOLISS),'landing')
        self.assertEqual(aircraft_mode({'icao':'A320','author':'ToLiss','description':'Airbus'}),'landing')
        self.assertEqual(aircraft_mode({'icao':'A320','author':'Other company','description':'Airbus'}),'timed')
        self.assertEqual(aircraft_mode(OTHER),'timed');self.assertIsNone(aircraft_mode(None))
    def test_partial_out_of_order_and_two_confirmations(self):
        identity=AircraftIdentity();p=identity_packet(TOLISS);cut=5+100*8
        identity.observe(b'RREF\0'+p[cut:],0);self.assertIsNone(identity.snapshot(0))
        identity.observe(p[:cut],.1);self.assertIsNone(identity.snapshot(.1))
        identity.observe(p,1);self.assertEqual(identity.snapshot(1),TOLISS)
        self.assertIsNone(identity.snapshot(6))
        identity.observe(identity_packet(OTHER),7);self.assertIsNone(identity.snapshot(7))
        identity.observe(identity_packet(OTHER),8);self.assertEqual(identity.snapshot(8),OTHER)
    def test_subscriptions_do_not_collide_with_flight_signal_ids(self):
        self.assertEqual(len(packets()),296)
        for p in packets(0):
            hz,index,ref=struct.unpack('<ii400s',p[5:]);self.assertEqual(hz,0);self.assertGreaterEqual(index,10000)
    def test_blank_identity_does_not_default_to_other(self):
        identity=AircraftIdentity()
        for t in [0,1]:identity.observe(identity_packet({}),t)
        self.assertIsNone(identity.snapshot(1))


class FakeWorker:
    def __init__(self,folder,number,name,stream,interval,log,metadata=None):
        self.name=name;self.metadata=dict(metadata);self.jobs=queue.Queue()
        self.ready=threading.Event();self.done=threading.Event();self.error=None
        self.flight=SimpleNamespace(manifest={'name':name,'dataset_id':number,'state':'CAPTURING','metadata':self.metadata},failed=False)
    def start(self):self.ready.set()
    def finish(self,reason):
        self.reason=reason;self.flight.manifest['state']='FINISHED';self.done.set()


class CollectedPilot:
    """Keep legacy flight-timing tests focused; real pilot gating has separate tests."""
    def __init__(self,*args):
        self.state='READY';self.name='Test pilot';self.id='test'
        self.message='Test pilot collected';self.dialog=SimpleNamespace(process=None)
    def update(self,*args):pass
    def close(self):pass


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.receiver=MagicMock();self.receiver.failure=None;self.receiver.identity.snapshot.return_value=None
        self.controller=SessionController(self.receiver,self.folder,auto_route=True)
        self.controller.sequence_path=self.folder/'sequence.json'
        self.worker_patch=patch('xplane_session.UploadWorker',FakeWorker);self.worker_patch.start()
        self.pilot_patch=patch('xplane_session.PilotSetup',CollectedPilot);self.pilot_patch.start()
    def tearDown(self):
        self.worker_patch.stop();self.pilot_patch.stop();self.controller.close();self.temp.cleanup()
    def feed(self,now,ground=0,compression=0,alt=914.4):
        self.controller.feed(int(now*1e9),now,{'paused':0,'replay':0,'on_ground':ground,'altitude_msl_m':alt,'gear_0_compression_m':compression,'latitude_deg':39.55,'longitude_deg':2.73,'heading_true_deg':90,'true_airspeed_mps':75})
    def test_waits_for_identity_instead_of_mislabelling(self):
        c=self.controller;c.commands.put('start');self.feed(100);c.tick(100)
        self.assertIsNone(c.worker);self.assertEqual(c.state,'WAIT_ID')
    def test_toliss_uploads_at_touchdown_and_stops_live_ten_seconds_later(self):
        c=self.start_landed_capture()
        self.assertEqual(c.worker.metadata['Capture Type'],'Live')
        self.assertEqual(c.worker.metadata['A/C Model'],'Airbus A320')
        upload=c.file_worker
        self.assertEqual(c.completed_files,[])
        self.assertIsNotNone(upload)
        upload.join(5)
        self.assertEqual(upload.manifest['state'],'LOCAL_FILE_READY')
        self.assertEqual(upload.metadata['Capture Type'],'SDK upload')
        samples=[json.loads(line) for line in upload.journal.read_text().splitlines()]
        self.assertEqual(samples[-1]['gear_0_compression_m'],.1)
        self.assertEqual(samples[-1]['time'],102_000_000_000)
        self.assertEqual(c.session_manifest['cutoff_time_ns'],112_000_000_000)
        before=upload.path.read_bytes()
        self.feed(103,ground=1,compression=.2,alt=3)
        self.feed(111.99,ground=1,compression=.2,alt=3);c.tick(111.99)
        self.assertEqual(c.state,'RECORDING')
        self.assertAlmostEqual(c.snapshot(111.99)['remaining'],.01)
        old=c.worker;c.tick(112)
        self.assertIs(c.completed_files[0]['worker'],upload)
        self.assertEqual(c.state,'WAIT_RESET')
        self.assertEqual(old.reason,'10 seconds after first gear compression')
        self.assertIs(c.file_worker,upload)
        self.assertEqual(upload.path.read_bytes(),before)
        self.assertEqual(self.receiver.sock.sendto.call_args.args[0],command_packet('sim/operation/pause_toggle'))
        # Confirm pause before PREL, then verify the new location remains paused.
        c.feed(112_100_000_000,112.1,{'paused':1,'replay':0});c.tick(112.1)
        self.assertEqual(self.receiver.sock.sendto.call_args.args[0][:4],b'PREL')
        c.feed(112_200_000_000,112.2,{'paused':1,'replay':0,'altitude_msl_m':914.4,
            'latitude_deg':39.55,'longitude_deg':2.73})
        c.tick(112.2);self.assertEqual(c.state,'WAIT_PILOT')
        self.assertIn('ISCS',c.message)
        self.assertEqual(self.receiver.sock.sendto.call_count,2)
        self.feed(113);c.tick(113)
        self.assertEqual(c.state,'RECORDING')
        self.assertEqual(c.worker.name,'A320_Landing_Challenge_002')
        self.assertEqual(self.receiver.sock.sendto.call_count,2)

    def start_landed_capture(self):
        c=self.controller;self.receiver.identity.snapshot.return_value=TOLISS
        self.feed(100);c.tick(100)
        self.feed(100.1);self.feed(101.2);self.feed(102,ground=1,compression=.1,alt=3)
        return c

    def test_landing_cutoff_excludes_boundary_sample_before_pause(self):
        c=self.start_landed_capture();old=c.worker;count=old.jobs.qsize()
        self.feed(112,ground=1,compression=.1,alt=3);c.tick(112);c.tick(112.1)
        self.assertEqual(old.jobs.qsize(),count)
        self.assertEqual(old.reason,'10 seconds after first gear compression')
        self.receiver.sock.sendto.assert_called_once_with(command_packet('sim/operation/pause_toggle'),self.receiver.target)

    def test_cutoff_with_stale_telemetry_does_not_toggle_or_reset(self):
        c=self.start_landed_capture();c.tick(112)
        self.assertEqual(c.state,'WAIT_RESET')
        self.assertEqual(c.session_manifest['automatic_reset']['state'],'ERROR')
        self.receiver.sock.sendto.assert_not_called()

    def test_pause_reset_does_not_wait_for_cloud_or_brief_identity_gap(self):
        c=self.start_landed_capture();old=c.worker;old.finish=MagicMock()
        self.feed(111.9,ground=1,compression=.1,alt=3);c.tick(112)
        self.assertEqual(c.state,'SAVING')
        c.feed(112_100_000_000,112.1,{'paused':1,'replay':0});c.tick(112.1)
        self.assertEqual(c.resetter.state,'WAIT_POSITION')
        self.receiver.identity.snapshot.return_value=None
        c.feed(112_200_000_000,112.2,{'paused':1,'replay':0,'altitude_msl_m':914.4,
            'latitude_deg':39.55,'longitude_deg':2.73});c.tick(112.2)
        self.assertEqual(c.resetter.state,'COMPLETE')
        self.assertEqual(c.state,'SAVING')
        self.assertEqual(self.receiver.sock.sendto.call_count,2)
        self.receiver.identity.snapshot.return_value=TOLISS
        old.done.set();old.flight.manifest['state']='FINISHED';c.tick(112.3)
        self.assertEqual(c.state,'WAIT_PILOT')
        self.assertIn('ISCS',c.message)

    def test_file_ready_even_when_live_finalization_fails(self):
        c=self.start_landed_capture();old=c.worker
        old.finish=MagicMock()
        c.tick(112)
        self.assertEqual(c.state,'SAVING')
        old.finish.assert_called_once_with('10 seconds after first gear compression')
        c.file_worker.join(5)
        self.assertEqual(c.file_worker.manifest['state'],'LOCAL_FILE_READY')
        old.error='Live cooling failed';old.done.set();c.tick(123)
        self.assertEqual(c.state,'WAIT_RESET')
        self.assertIn('LOCAL_FILE_READY',c.snapshot(123)['analysis'])
        self.feed(124);c.tick(124);c.tick(124.01)
        self.assertEqual(c.state,'RECORDING')
        self.receiver.sock.sendto.assert_not_called()

    def test_quit_waits_for_file_worker_even_after_live_finished(self):
        c=self.controller
        pending=MagicMock();pending.done.is_set.return_value=False;pending.is_alive.return_value=False
        c.file_workers.append(pending)
        c.exit_requested=True;c.tick(100)
        self.assertFalse(c.closed)
        pending.done.is_set.return_value=True;c.tick(101)
        self.assertTrue(c.closed)

    def test_rate_toggle_applies_to_next_flight_and_metadata(self):
        c=self.start_landed_capture();old=c.worker
        self.assertEqual(c.sampler.hz,1)
        c.commands.put('rate');c.tick(102.1)
        self.assertEqual(c.sample_mode,'high');self.assertEqual(c.sampler.hz,1)
        self.feed(111.9,ground=1,compression=.1,alt=3);c.tick(112);c.tick(116)
        self.feed(123);c.tick(123);c.tick(123.01)
        self.assertEqual(c.state,'RECORDING');self.assertEqual(c.sampler.hz,10)
        self.assertEqual(c.worker.metadata['Live Sample Rate Hz'],10)
        self.assertEqual(old.metadata['Live Sample Rate Hz'],1)

    def test_low_rate_live_keeps_fast_local_touchdown_sample(self):
        c=self.controller;self.receiver.identity.snapshot.return_value=TOLISS
        self.feed(100);c.tick(100)
        for i in range(1,31):
            now=100+i/10
            c.feed(int(now*1e9),now,{'paused':0,'replay':0,'on_ground':0 if i<25 else 1,
                'gear_0_compression_m':.1 if i==25 else 0,'vertical_speed_mps':-2,
                'airspeed_kias':135,'pitch_deg':3})
        self.assertEqual(c.landing.touchdown,102.5)
        self.assertAlmostEqual(c.session_manifest['touchdown_snapshot']['vertical_speed_fpm'],-2*60/.3048)
        c.record_file.flush()
        self.assertEqual(len(c.record_path.read_text().splitlines()),30)
        self.assertEqual(c.worker.jobs.qsize(),3)
    def test_landing_is_not_cut_at_sixty_seconds(self):
        c=self.controller;self.receiver.identity.snapshot.return_value=TOLISS
        self.feed(100);c.tick(100);self.feed(161);c.tick(161)
        self.assertEqual(c.state,'RECORDING');self.receiver.sock.sendto.assert_not_called()
    def test_other_plane_metadata_and_timed_finish_without_pause(self):
        c=self.controller;self.receiver.identity.snapshot.return_value=OTHER
        self.feed(100);c.tick(100);self.assertEqual(c.state,'READY')
        c.commands.put('start');c.tick(100);self.assertEqual(c.state,'COUNTDOWN')
        self.feed(104.9);c.tick(105)
        self.assertEqual(c.worker.metadata['A/C Model'],'Marple Acrobatic')
        self.assertEqual(c.worker.name,'Marple_Acrobatic_001');self.assertEqual(c.state,'RECORDING')
        c.tick(155);self.feed(164.9);c.tick(165)
        sent=[x.args[0][:4] for x in self.receiver.sock.sendto.call_args_list]
        self.assertEqual(sent,[b'ALRT',b'ALRT']);self.assertEqual(c.state,'COMPLETE')
    def test_aircraft_change_finalizes_old_metadata_before_new_flight(self):
        c=self.controller;self.receiver.identity.snapshot.return_value=TOLISS
        self.feed(100);c.tick(100);old=c.worker
        self.receiver.identity.snapshot.return_value=OTHER;self.feed(101);c.tick(101);c.tick(101.1)
        self.assertEqual(old.metadata['A/C Model'],'Airbus A320');self.assertEqual(old.reason,'aircraft changed')
        self.assertEqual(c.mode,'timed');self.assertEqual(c.state,'READY')
        c.commands.put('start');c.tick(101.2)
        self.assertEqual(c.worker.metadata['A/C Model'],'Marple Acrobatic')
        self.assertNotEqual(c.worker.name,old.name)
    def test_operator_stop_disables_automatic_landing_restart(self):
        c=self.controller;self.receiver.identity.snapshot.return_value=TOLISS
        self.feed(100);c.tick(100);c.commands.put('stop');c.tick(101);c.tick(102)
        self.assertEqual(c.state,'COMPLETE');self.assertFalse(c.landing_cycle)
        self.receiver.sock.sendto.assert_not_called()
    def test_flight_forwards_profile_metadata_to_marple(self):
        stream=MagicMock();stream.add_dataset.return_value.id=1
        metadata={'A/C Model':'Marple Acrobatic','Departure Airport':'LEPA','Flight Type':'Simulator Session'}
        flight=Flight(self.folder,1,stream,'Marple_Acrobatic_001',metadata=metadata)
        stream.add_dataset.assert_called_once_with('Marple_Acrobatic_001',metadata=metadata)
        self.assertEqual(flight.manifest['metadata'],metadata);flight.file.close()

if __name__=='__main__':unittest.main()

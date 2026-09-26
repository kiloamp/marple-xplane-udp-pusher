import json
import re
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from xplane_signals import DATA, decode_data, signal_definitions
from xplane_live import Flight, Receiver, SIGNALS
from xplane_report_signals import TOLISS_NAMES, DERIVED


def packet(group, values):
    return b'DATA\0' + struct.pack('<i8f', group, *values)


class DataTests(unittest.TestCase):
    def test_wire_slots_preserved_including_pitch_before_roll(self):
        self.assertEqual(decode_data(packet(15,[1,2,3,-999,-999,-999,-999,-999])),
                         {'data_pitch_moment_ftlb':1,'data_roll_moment_ftlb':2,'data_yaw_moment_ftlb':3})
        self.assertEqual(decode_data(packet(4,[.5,-999,120,-999,1,0,0,-999]))['data_vertical_speed_fpm'],120)

    def test_unknown_retained_and_nonfinite_padding_skipped(self):
        values=decode_data(packet(999,[42,-999,float('nan'),float('inf'),0,-999,-999,-999]))
        self.assertEqual(values,{'data_group_999_slot_0':42,'data_group_999_slot_4':0})
        self.assertIn('require verification',signal_definitions(values,SIGNALS)[0]['description'])
        for p in [b'',b'DATA',packet(1,[0]*8)[:-1],b'RREF\0'+bytes(36)]:
            with self.assertRaises(ValueError): decode_data(p)

    def test_dictionary_unique_normalized_and_described(self):
        names=[d['signal'] for d in DATA.values()]
        self.assertEqual(len(names),len(set(names)))
        for d in signal_definitions(names+ [s[0] for s in SIGNALS],SIGNALS):
            self.assertRegex(d['signal'],r'^[a-z][a-z0-9_]+$')
            self.assertTrue(d['unit'])
            self.assertIn('https://',d['description'])

    def test_both_udp_protocols_and_raw_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            receiver=Receiver('127.0.0.1',9,10,Path(tmp),data_port=0,include_data=True)
            with patch.object(receiver,'subscribe'):
                receiver.thread.start()
                sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
                try:
                    sender.sendto(packet(41,[50]*8),('127.0.0.1',receiver.data_port))
                    _,_,values=receiver.events.get(timeout=3)
                    self.assertEqual(values['data_engine_1_n1_pct'],50)
                    sender.sendto(b'RREF\0'+struct.pack('<if',3,123),('127.0.0.1',receiver.sock.getsockname()[1]))
                    _,_,values=receiver.events.get(timeout=3)
                    self.assertEqual(values,{'airspeed_kias':123})
                finally:
                    receiver.close();sender.close()
            self.assertIsNone(receiver.failure)
            self.assertEqual(len((Path(tmp)/'packets.jsonl').read_text().splitlines()),2)

    def test_signals_added_midflight_get_metadata_before_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            stream=MagicMock();stream.add_dataset.return_value.id=1
            flight=Flight(Path(tmp),1,stream)
            ds=flight.dataset
            events=[]
            ds.upsert_signals.side_effect=lambda definitions: events.append(('define',[d['signal'] for d in definitions]))
            ds.append.side_effect=lambda *a,**kw: events.append(('append',None))
            flight.add(1,{'airspeed_kias':100});flight.flush()
            flight.add(2,decode_data(packet(41,[50]*8)));flight.flush()
            self.assertEqual([x[0] for x in events],['define','append','define','append'])
            self.assertEqual(len(events[2][1]),8)
            self.assertEqual(len(json.loads(flight.path.with_suffix('.signals.json').read_text())),9)
            flight.file.close()

    def test_programmatic_default_ignores_data_packets_and_does_not_set_output_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            receiver=Receiver('127.0.0.1',9,10,Path(tmp))
            self.assertIsNone(receiver.data_sock)
            with patch.object(receiver,'subscribe'):
                receiver.thread.start()
                sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
                try:
                    target=('127.0.0.1',receiver.sock.getsockname()[1])
                    sender.sendto(packet(41,[50]*8),target)
                    sender.sendto(b'RREF\0'+struct.pack('<if',3,123),target)
                    _,_,values=receiver.events.get(timeout=3)
                    self.assertEqual(values,{'airspeed_kias':123})
                    self.assertTrue(receiver.events.empty())
                finally:
                    receiver.close();sender.close()
            self.assertIsNone(receiver.failure)

    def test_toliss_subscriptions_are_gated_by_confirmed_aircraft_and_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            receiver=Receiver('127.0.0.1',9,10,Path(tmp))
            original_socket=receiver.sock
            receiver.sock=MagicMock()
            try:
                with patch.object(receiver.identity,'snapshot',return_value=None):receiver.subscribe(10)
                sent=[struct.unpack('<ii400s',call.args[0][5:]) for call in receiver.sock.sendto.call_args_list]
                self.assertFalse(any(ref.startswith(b'AirbusFBW/') for _,_,ref in sent))
                receiver.sock.reset_mock()
                with patch.object(receiver.identity,'snapshot',return_value={'icao':'A319','author':'Gliding Kiwi'}):receiver.subscribe(10)
                sent=[struct.unpack('<ii400s',call.args[0][5:]) for call in receiver.sock.sendto.call_args_list]
                self.assertEqual(sum(hz==10 and ref.startswith(b'AirbusFBW/') for hz,_,ref in sent),len(TOLISS_NAMES))
                receiver.sock.reset_mock()
                with patch.object(receiver.identity,'snapshot',return_value={'icao':'C172','author':'Laminar Research'}):receiver.subscribe(10)
                sent=[struct.unpack('<ii400s',call.args[0][5:]) for call in receiver.sock.sendto.call_args_list]
                self.assertEqual(sum(hz==0 and ref.startswith(b'AirbusFBW/') for hz,_,ref in sent),len(TOLISS_NAMES))
            finally:
                original_socket.close();receiver.journal.close()

    def test_subscription_pacing_does_not_block_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            receiver=Receiver('127.0.0.1',9,10,Path(tmp))
            entered=threading.Event();release=threading.Event()
            def slow_subscribe(hz):
                if hz:
                    entered.set();release.wait(3)
            with patch.object(receiver,'subscribe',side_effect=slow_subscribe):
                receiver.thread.start();sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
                try:
                    self.assertTrue(entered.wait(1))
                    sender.sendto(b'RREF\0'+struct.pack('<if',3,123),('127.0.0.1',receiver.sock.getsockname()[1]))
                    _,_,values=receiver.events.get(timeout=1)
                    self.assertFalse(release.is_set())
                    self.assertEqual(values,{'airspeed_kias':123})
                finally:
                    release.set();receiver.close();sender.close()

    def test_report_definitions_include_units_sources_and_raw_toliss_caveats(self):
        definitions={d['signal']:d for d in signal_definitions([n for n,_,_ in SIGNALS]+list(DERIVED),SIGNALS)}
        self.assertEqual(len(definitions),67)
        self.assertEqual(definitions['wind_speed_mps']['unit'],'m/s')
        self.assertEqual(definitions['distance_covered_nm']['protocol'],'derived')
        self.assertEqual(definitions['toliss_ils1_localizer_raw']['unit'],'raw')
        self.assertIn('do not score as dots',definitions['toliss_ils1_localizer_raw']['description'])
        for d in definitions.values():
            self.assertIn('https://',d['description'])
            self.assertTrue(d['unit'])


if __name__=='__main__': unittest.main()

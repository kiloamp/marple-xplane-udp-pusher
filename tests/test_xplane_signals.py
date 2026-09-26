import json
import re
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from xplane_signals import DATA, decode_data, signal_definitions
from xplane_live import Flight, Receiver, SIGNALS


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
            receiver=Receiver('127.0.0.1',9,10,Path(tmp),data_port=0)
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


if __name__=='__main__': unittest.main()

import struct
import unittest
from unittest.mock import MagicMock

from xplane_live import command_packet
from xplane_reset import LandingReset, reset_packet

TARGET = {'latitude_deg':39.55,'longitude_deg':2.73,'heading_true_deg':90,'true_airspeed_mps':75}


def samples(now, paused=0, replay=0, altitude=914.4):
    return {k:(v,now) for k,v in {**TARGET,'paused':paused,'replay':replay,'altitude_msl_m':altitude}.items()}


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.receiver=MagicMock()
        self.reset=LandingReset(self.receiver,MagicMock())

    def test_prel_matches_bundled_protocol_and_3000ft_is_msl_meters(self):
        packet=reset_packet(TARGET)
        self.assertEqual(len(packet),69)
        self.assertEqual(struct.unpack('<4sxii8siiddddd',packet),
                         (b'PREL',6,0,b'\0'*8,0,0,39.55,2.73,914.4000000000001,90,75))

    def test_pause_confirm_reposition_and_stay_paused(self):
        r=self.reset;r.begin(10,samples(9.9),TARGET)
        self.assertEqual(r.state,'WAIT_PAUSE')
        self.receiver.sock.sendto.assert_called_once_with(command_packet('sim/operation/pause_toggle'),self.receiver.target)
        r.update(10.1,samples(10.1,paused=1,altitude=3))
        self.assertEqual(r.state,'WAIT_POSITION')
        self.assertEqual(self.receiver.sock.sendto.call_args.args[0],reset_packet(TARGET))
        r.update(10.2,samples(10.2,paused=1))
        self.assertEqual(r.state,'COMPLETE')
        r.update(11,samples(11,paused=0))
        self.assertEqual(self.receiver.sock.sendto.call_count,2)

    def test_already_paused_never_toggles_until_fresh_reset_resumes(self):
        r=self.reset;r.begin(10,samples(9.9,paused=1),TARGET)
        self.assertEqual(self.receiver.sock.sendto.call_count,1)
        self.assertEqual(self.receiver.sock.sendto.call_args.args[0][:4],b'PREL')
        r.update(10.2,samples(9.99,paused=0))  # pre-reset status cannot toggle
        self.assertEqual(self.receiver.sock.sendto.call_count,1)
        r.update(10.3,samples(10.3,paused=0))
        self.assertEqual(r.state,'WAIT_REPAUSE')
        r.update(10.4,samples(10.4,paused=0))
        self.assertEqual(self.receiver.sock.sendto.call_count,2)
        r.update(10.5,samples(10.5,paused=1))
        self.assertEqual(r.state,'COMPLETE')

    def test_timeout_does_not_blindly_retry_pause(self):
        r=self.reset;r.begin(10,samples(9.9),TARGET)
        r.update(13,samples(13,paused=0));r.update(14,samples(14,paused=1))
        self.assertEqual(r.state,'ERROR')
        self.assertEqual(self.receiver.sock.sendto.call_count,1)

    def test_missing_target_still_pauses_but_does_not_reposition(self):
        r=self.reset;r.begin(10,samples(9.9),None)
        r.update(10.1,samples(10.1,paused=1))
        self.assertEqual(r.state,'ERROR')
        self.assertEqual(self.receiver.sock.sendto.call_count,1)

    def test_stale_status_or_replay_skips_all_commands(self):
        for latest in [samples(8),samples(9.9,replay=1)]:
            r=LandingReset(self.receiver,MagicMock());r.begin(10,latest,TARGET)
            self.assertEqual(r.state,'ERROR')
        self.receiver.sock.sendto.assert_not_called()

    def test_cancel_and_wrong_location_do_not_confirm_or_unpause(self):
        r=self.reset;r.begin(10,samples(9.9,paused=1),TARGET)
        wrong=samples(10.1,paused=1);wrong['latitude_deg']=(40,10.1)
        r.update(10.1,wrong)
        self.assertEqual(r.state,'WAIT_POSITION')
        r.cancel();r.update(10.2,samples(10.2,paused=0))
        self.assertEqual(r.state,'CANCELLED')
        self.assertEqual(self.receiver.sock.sendto.call_count,1)


if __name__=='__main__':unittest.main()

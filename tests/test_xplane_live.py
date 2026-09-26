import json
import queue
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from xplane_live import (AltitudeReset, Flight, LandingCut, SIGNALS, FLIGHT_METADATA,
                         decode, reset_detected, subscription, next_remote_number, reserve_challenge_name,
                         pause_simulator, main)


class NamingTests(unittest.TestCase):
    def test_numbering_survives_restart_and_existing_remote_names(self):
        from types import SimpleNamespace
        remote = [SimpleNamespace(path=p) for p in ["A320_Landing_Challenge_001", "A320_Landing_Challenge_012.parquet", "other"]]
        self.assertEqual(next_remote_number(remote), 13)
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "sequence.json"
            self.assertEqual(reserve_challenge_name(state, "stream:18", 13), "A320_Landing_Challenge_013")
            self.assertEqual(reserve_challenge_name(state, "stream:18", 1), "A320_Landing_Challenge_014")
            self.assertEqual(reserve_challenge_name(state, "local"), "A320_Landing_Challenge_001")

    def test_corrupt_counter_does_not_silently_reuse_names(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "sequence.json"
            state.write_text("broken")
            with self.assertRaises(json.JSONDecodeError):
                reserve_challenge_name(state, "stream:18")


class AltitudeResetTests(unittest.TestCase):
    def test_landed_jump_to_reset_altitude(self):
        watch = AltitudeReset()
        watch.landed = True
        self.assertFalse(watch.observe({"altitude_msl_m": 10}))
        self.assertTrue(watch.observe({"altitude_msl_m": 914.4, "on_ground": 0, "replay": 0}))
        self.assertFalse(watch.observe({"altitude_msl_m": 914.4}))

    def test_no_reset_before_landing_or_during_gradual_climb(self):
        watch = AltitudeReset()
        watch.observe({"altitude_msl_m": 10})
        self.assertFalse(watch.observe({"altitude_msl_m": 914.4}))
        watch.landed = True
        watch.observe({"altitude_msl_m": 10})
        for altitude in range(20, 930, 10):
            self.assertFalse(watch.observe({"altitude_msl_m": altitude}))

    def test_wrong_target_and_missing_altitude(self):
        watch = AltitudeReset()
        watch.landed = True
        watch.observe({"altitude_msl_m": 10})
        self.assertFalse(watch.observe({"altitude_msl_m": 2000}))
        self.assertFalse(watch.observe({"paused": 1}))

    def test_real_reset_transient_then_3000ft(self):
        watch = AltitudeReset()
        watch.landed = True
        watch.observe({"altitude_msl_m": 3.637, "on_ground": 1}, 0)
        self.assertFalse(watch.observe({"altitude_msl_m": 6925.174, "on_ground": 1}, 0.165))
        self.assertTrue(watch.observe({"altitude_msl_m": 925.1004, "on_ground": 0, "replay": 0}, 0.320))

    def test_expired_transient_is_not_a_reset(self):
        watch = AltitudeReset()
        watch.landed = True
        watch.observe({"altitude_msl_m": 3, "on_ground": 1}, 0)
        watch.observe({"altitude_msl_m": 7000, "on_ground": 1}, 1)
        self.assertFalse(watch.observe({"altitude_msl_m": 914.4, "on_ground": 0, "replay": 0}, 17))

    def test_recorded_reset_with_ground_flag_delayed_past_three_seconds(self):
        # September 26 capture: transient 22720 ft, then 3035 ft; ground
        # contact cleared 3.032 seconds after the initial teleport.
        watch = AltitudeReset()
        watch.landed = True
        watch.observe({'altitude_msl_m': 30.7 * .3048, 'on_ground': 1, 'replay': 0}, 0)
        self.assertFalse(watch.observe({'altitude_msl_m': 22720.4 * .3048, 'on_ground': 1, 'replay': 0}, 1))
        self.assertFalse(watch.observe({'altitude_msl_m': 3035.3 * .3048, 'on_ground': 1, 'replay': 0}, 3.920))
        self.assertTrue(watch.observe({'altitude_msl_m': 3034.8 * .3048, 'on_ground': 0, 'replay': 0}, 4.032))
        self.assertFalse(watch.observe({'altitude_msl_m': 914.4, 'on_ground': 0, 'replay': 0}, 4.1))

    def test_split_packets_and_stale_status(self):
        watch = AltitudeReset()
        watch.landed = True
        watch.observe({'altitude_msl_m': 3, 'on_ground': 0, 'replay': 0}, 0)
        self.assertFalse(watch.observe({'altitude_msl_m': 914.4}, 2))
        self.assertFalse(watch.observe({'on_ground': 0}, 2.1))
        self.assertTrue(watch.observe({'replay': 0}, 2.2))

    def test_stale_altitude_and_replay_cannot_trigger(self):
        watch = AltitudeReset()
        watch.landed = True
        watch.observe({'altitude_msl_m': 3}, 0)
        watch.observe({'altitude_msl_m': 914.4}, 1)
        self.assertFalse(watch.observe({'on_ground': 0, 'replay': 0}, 3))
        self.assertFalse(watch.observe({'altitude_msl_m': 914.4, 'on_ground': 0, 'replay': 1}, 3.1))
        self.assertFalse(watch.observe({'altitude_msl_m': 914.4, 'on_ground': 0, 'replay': 0}, 3.2))


class LandingTests(unittest.TestCase):
    def sample(self, ground=0, compression=0, **extra):
        return {"paused": 0, "replay": 0, "on_ground": ground, "gear_0_compression_m": compression, **extra}

    def test_ground_start_does_not_trigger(self):
        cut = LandingCut()
        for t in range(20):
            cut.observe(t, self.sample(ground=1, compression=0.1))
        self.assertFalse(cut.armed)
        self.assertFalse(cut.expired(100))

    def test_first_touchdown_latched_through_bounce(self):
        cut = LandingCut()
        cut.observe(0, self.sample())
        cut.observe(1, self.sample())
        self.assertTrue(cut.armed)
        cut.observe(3, self.sample(ground=1, compression=0.01))
        cut.observe(4, self.sample())
        cut.observe(6, self.sample(ground=1, compression=0.1))
        self.assertEqual(cut.touchdown, 3)
        self.assertFalse(cut.expired(7.99))
        self.assertTrue(cut.expired(8))

    def test_paused_replay_and_missing_status_do_not_arm(self):
        for sample in [{"on_ground": 0}, self.sample(paused=1), self.sample(replay=1)]:
            cut = LandingCut()
            cut.observe(0, sample)
            cut.observe(5, sample)
            self.assertFalse(cut.armed)

    def test_stale_status_does_not_trigger_touchdown(self):
        cut = LandingCut()
        cut.observe(0, self.sample())
        cut.observe(1, self.sample())
        cut.observe(3, {"gear_0_compression_m": 0.1})
        self.assertIsNone(cut.touchdown)


class PacketTests(unittest.TestCase):
    def test_pause_command_only_toggles_running_simulator(self):
        receiver = MagicMock(); receiver.target = ('127.0.0.1', 49000)
        log = MagicMock()
        self.assertEqual(pause_simulator(receiver, 1, log), 'already_paused')
        self.assertEqual(pause_simulator(receiver, None, log), 'unavailable')
        receiver.sock.sendto.assert_not_called()
        self.assertEqual(pause_simulator(receiver, 0, log), 'requested')
        receiver.sock.sendto.assert_called_once_with(
            b'CMND\0sim/operation/pause_toggle\0', receiver.target)

    def test_pause_send_failure_does_not_block_saving(self):
        receiver = MagicMock(); receiver.sock.sendto.side_effect = OSError('offline')
        self.assertEqual(pause_simulator(receiver, 0, MagicMock()), 'failed')

    def test_legacy_landing_pauses_before_finish_on_packet_or_timer_cutoff(self):
        for timer_cutoff in (False, True):
            with self.subTest(timer_cutoff=timer_cutoff), tempfile.TemporaryDirectory() as folder:
                receiver = MagicMock(); receiver.failure = None; receiver.target = ('127.0.0.1', 49000)
                samples = []
                for now, ground, compression in [(0, 0, 0), (1.1, 0, 0), (2, 1, .1), (6.9, 1, .1)]:
                    samples.append((int(now*1e9), now, {'paused': 0, 'replay': 0, 'on_ground': ground,
                                                       'gear_0_compression_m': compression}))
                samples.append(queue.Empty() if timer_cutoff else (7_000_000_000, 7, {'paused': 0}))
                receiver.events.get.side_effect = samples
                flight = MagicMock(); flight.last_flush = 7; flight.manifest = {}
                order = []
                receiver.sock.sendto.side_effect = lambda *a: order.append('pause')
                flight.finish.side_effect = lambda *a: order.append('finish')
                with patch('sys.argv', ['xplane_live.py', '--landing', '--seconds', '0']), \
                     patch('xplane_live.Path', side_effect=lambda p: Path(folder)/p), \
                     patch('xplane_live.Receiver', return_value=receiver), \
                     patch('xplane_live.Flight', return_value=flight), \
                     patch('xplane_live.time.monotonic', return_value=7), patch('builtins.print'):
                    main()
                self.assertEqual(order, ['pause', 'finish'])
                self.assertEqual(flight.add.call_count, 4)
                self.assertEqual(flight.manifest['cutoff_time_ns'], 7_000_000_000)
                flight.finish.assert_called_once_with('5 seconds after first gear compression')
                receiver.sock.sendto.assert_called_once_with(b'CMND\0sim/operation/pause_toggle\0', receiver.target)

    def test_decode_multiple_signals_ignores_unknown_and_nonfinite(self):
        packet = b"RREF\0" + b"".join(struct.pack("<if", i, v) for i, v in [(0, 123.5), (3, 91), (900, 2), (4, float("nan"))])
        self.assertEqual(decode(packet), {"flight_time_s": 123.5, "airspeed_kias": 91})

    def test_reject_truncated_or_different_protocol(self):
        for packet in [b"", b"RREF", b"RREF\0x", b"DATA\0" + bytes(36)]:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                decode(packet)

    def test_subscription_fixed_length_and_unsubscribe(self):
        packet = subscription(3, 0)
        self.assertEqual(len(packet), 413)
        hz, index, ref = struct.unpack("<ii400s", packet[5:])
        self.assertEqual((hz, index, ref.rstrip(b"\0").decode()), (0, 3, SIGNALS[3][1]))

    def test_reset_threshold_and_pause(self):
        self.assertTrue(reset_detected(180, 0))
        for a, b in [(10, 10), (10, 10.1), (10, 9.9), (None, 0), (180, None)]:
            self.assertFalse(reset_detected(a, b))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.verify_patch = patch("xplane_verify.verify_capture", return_value={"verified": True, "repaired_signals": []})
        self.verify_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.stream = MagicMock()
        self.dataset = self.stream.add_dataset.return_value
        self.dataset.id = 123
        self.dataset.import_status = "LIVE"
        self.dataset.cool.return_value = self.dataset
        self.dataset.wait_for_import.return_value = self.dataset

    def tearDown(self):
        self.verify_patch.stop()
        self.temp.cleanup()

    def test_final_batch_precedes_cooling_and_no_fill_forward(self):
        calls = []
        self.dataset.append.side_effect = lambda *a, **k: calls.append("append")
        def cool():
            calls.append("cool")
            self.dataset.import_status = "FINISHED"
            return self.dataset
        self.dataset.cool.side_effect = cool
        flight = Flight(self.folder, 1, self.stream)
        self.stream.add_dataset.assert_called_once_with("A320_Landing_Challenge_001", metadata=FLIGHT_METADATA)
        flight.add(100, {"airspeed_kias": 60})
        flight.add(200, {"pitch_deg": 5})
        flight.finish("operator stop")
        self.assertEqual(calls, ["append", "cool"])
        rows = self.dataset.append.call_args.args[0].to_dict("records")
        self.assertEqual(rows, [{"time": 100, "signal": "airspeed_kias", "value": 60}, {"time": 200, "signal": "pitch_deg", "value": 5}])
        self.assertEqual(json.loads(flight.manifest_path.read_text())["state"], "FINISHED")

    def test_uncertain_append_is_not_retried_and_local_capture_continues(self):
        for error in [TimeoutError(), KeyboardInterrupt()]:
            with self.subTest(error=type(error).__name__):
                number = 1 if isinstance(error, TimeoutError) else 2
                self.dataset.append.reset_mock()
                self.dataset.append.side_effect = error
                flight = Flight(self.folder, number, self.stream)
                flight.add(100, {"airspeed_kias": 60})
                if isinstance(error, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        flight.flush()
                else:
                    flight.flush()
                flight.add(200, {"airspeed_kias": 70})
                self.dataset.import_status = "FINISHED"
                flight.finish("error")
                self.dataset.append.assert_called_once()
                self.assertEqual(len(flight.path.read_text().splitlines()), 2)
                self.assertEqual(json.loads(flight.manifest_path.read_text())["state"], "FINISHED")
                self.assertTrue(json.loads(flight.manifest_path.read_text())["cold_storage_verified"])

    def test_final_append_respects_one_request_per_second(self):
        now = [100.0]
        starts = []
        def append(*args, **kwargs):
            if starts:
                self.assertGreaterEqual(now[0] - starts[-1], 1.0)
            starts.append(now[0])
        self.dataset.append.side_effect = append
        self.dataset.import_status = "FINISHED"
        with patch("xplane_live.time.monotonic", side_effect=lambda: now[0]), patch(
                "xplane_live.time.sleep", side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
            flight = Flight(self.folder, 1, self.stream)
            flight.add(1, {"pitch_deg": 1})
            flight.flush()
            now[0] += .1
            flight.add(2, {"pitch_deg": 2})
            flight.finish("operator stop")
        self.assertEqual(len(starts), 2)
        self.assertAlmostEqual(starts[1] - starts[0], 1.05)

    def test_failed_cool_not_automatically_repeated(self):
        self.dataset.cool.side_effect = TimeoutError()
        flight = Flight(self.folder, 1, self.stream)
        flight.add(100, {"pitch_deg": 1})
        with self.assertRaises(TimeoutError):
            flight.finish("reset")
        flight.finish("error")
        self.dataset.cool.assert_called_once()


if __name__ == "__main__":
    unittest.main()

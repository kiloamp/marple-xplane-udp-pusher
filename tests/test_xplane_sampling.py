import unittest

from xplane_sampling import LiveSampler, ReportTelemetry


class SamplingTests(unittest.TestCase):
    def test_low_high_rates_preserve_fragments_without_filling_missing_signals(self):
        low, high = LiveSampler('low'), LiveSampler('high')
        low_samples, high_samples = [], []
        for i in range(30):
            now = 100 + i / 10
            for sampler, captured in [(low, low_samples), (high, high_samples)]:
                selected = sampler.select(now, {'pitch_deg': i})
                if selected:
                    captured.append(selected)
        self.assertEqual(len(low_samples), 3)
        self.assertEqual(len(high_samples), 30)
        self.assertEqual(low_samples, [{'pitch_deg': 0}, {'pitch_deg': 10}, {'pitch_deg': 20}])
        self.assertEqual(low.select(102.91, {'pitch_deg': 99, 'roll_deg': 4}), {'roll_deg': 4})
        self.assertEqual(low.select(103.1, {'pitch_deg': 100}), {'pitch_deg': 100})
        self.assertEqual(low.select(102.1, {'pitch_deg': 101}), {})

    def test_new_sampler_resets_signal_buckets_for_next_flight(self):
        self.assertEqual(LiveSampler('low').select(100, {'a': 1}), {'a': 1})
        self.assertEqual(LiveSampler('high').select(1, {'a': 2}), {'a': 2})


class DerivedTelemetryTests(unittest.TestCase):
    def sample(self, t, speed=100, **extra):
        return dict(flight_time_s=t, groundspeed_mps=speed, paused=0, replay=0, **extra)

    def test_units_and_distance_use_simulator_time(self):
        report = ReportTelemetry()
        result = report.add(100, self.sample(0, altitude_msl_m=304.8, vertical_speed_mps=-3.048))
        self.assertAlmostEqual(result['altitude_msl_ft'], 1000)
        self.assertAlmostEqual(result['vertical_speed_fpm'], -600)
        self.assertAlmostEqual(result['groundspeed_kt'], 100 * 3600 / 1852)
        self.assertEqual(result['distance_covered_nm'], 0)
        # Only one wall-clock second passes, but two simulator seconds elapse.
        result = report.add(101, self.sample(2, speed=200))
        self.assertAlmostEqual(result['distance_covered_nm'], 300 / 1852)

    def test_pause_replay_reset_and_gaps_do_not_add_distance(self):
        r = ReportTelemetry(); r.add(0, self.sample(0));r.add(.1, self.sample(.1))
        self.assertAlmostEqual(r.distance_m, 10)
        r.add(.2, {'paused': 1});r.add(10, {**self.sample(10), 'paused': 1})
        r.add(11, self.sample(11));self.assertAlmostEqual(r.distance_m, 10)
        r.add(11.1, self.sample(11.1));self.assertAlmostEqual(r.distance_m, 20)
        r.add(12, self.sample(0));self.assertAlmostEqual(r.distance_m, 20)
        r.add(20, self.sample(8));self.assertAlmostEqual(r.distance_m, 20)
        r.add(21, {**self.sample(9), 'replay': 1})
        r.add(22, self.sample(10));self.assertAlmostEqual(r.distance_m, 20)
        self.assertEqual(ReportTelemetry().distance_m, 0)

    def test_stale_clock_or_missing_pause_state_cannot_integrate(self):
        r = ReportTelemetry()
        r.add(0, {'groundspeed_mps': 100, 'flight_time_s': 0})
        r.add(1, {'groundspeed_mps': 100, 'flight_time_s': 1})
        self.assertEqual(r.distance_m, 0)
        r.add(2, self.sample(2));r.add(3, {'groundspeed_mps': 100, 'paused': 0, 'replay': 0})
        self.assertEqual(r.distance_m, 0)

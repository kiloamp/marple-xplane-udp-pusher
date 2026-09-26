import json
import tempfile
import unittest
from pathlib import Path

from xplane_review import review_capture, REVIEW_TEMPLATES, ils_channel


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'raw.jsonl'
    def tearDown(self):self.tmp.cleanup()

    def flight(self, changes=None, count=30, touchdown=True, initial=False):
        rows=[]
        for i in range(-6 if initial else 0,count):
            row={'time':int((i+10)*1e9),'paused':0,'replay':0,'on_ground':0,
                 'airspeed_kias':145,'roll_deg':2,'altitude_agl_ft':1500 if i<0 else 1000-30*i,
                 'vertical_speed_fpm':-300,'gear_handle_down':1,
                 'nav1_frequency_raw':11030,'nav1_horizontal_valid':1,'nav1_vertical_valid':1,
                 'nav1_localizer_deviation_dots':.1,'nav1_glideslope_deviation_dots':.1}
            if changes:row.update(changes(i))
            rows.append(row)
        if touchdown:
            rows.append({'time':rows[-1]['time']+100_000_000,'on_ground':1,'gear_0_compression_m':.1})
        return rows

    def review(self,rows,landed=True):
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return review_capture(self.path,{'Capture End':'first gear compression' if landed else 'operator stop'})

    def test_all_ten_reviews_have_distinct_evidence_paths(self):
        cases={
            'insufficient_data':(self.flight(count=3),True),
            'incomplete_landing':(self.flight(touchdown=False),False),
            'brisk_descent':(self.flight(lambda i:{'vertical_speed_fpm':-700 if i==29 else -300}),True),
            'bank_at_touchdown':(self.flight(lambda i:{'roll_deg':7 if i==29 else 2}),True),
            'late_gear_command':(self.flight(lambda i:{'gear_handle_down':0 if i<27 else 1}),True),
            'speed_variation':(self.flight(lambda i:{'airspeed_kias':145 if i%2 else 180}),True),
            'roll_corrections':(self.flight(lambda i:{'roll_deg':12 if i<27 else 2}),True),
            'localizer_deviation':(self.flight(lambda i:{'nav1_localizer_deviation_dots':1.5},initial=True),True),
            'glideslope_deviation':(self.flight(lambda i:{'nav1_glideslope_deviation_dots':1.5}),True),
            'steady_approach':(self.flight(),True),
        }
        self.assertEqual(set(cases),set(REVIEW_TEMPLATES))
        for expected,(rows,landed) in cases.items():
            with self.subTest(expected=expected):
                result=self.review(rows,landed)
                self.assertEqual(result['category'],expected)
                self.assertNotIn('{',result['text'])
        text=self.review(cases['localizer_deviation'][0])['text']
        self.assertIn('Good initial speed and bank control',text)

    def test_no_false_ils_claim_from_invalid_flags_stale_frequency_or_raw_toliss(self):
        for changes in [
            {'nav1_horizontal_valid':0}, {'nav1_frequency_raw':11300},
            {'nav1_frequency_raw':11020}, {'nav1_glideslope_deviation_dots':-10},
        ]:
            rows=self.flight(lambda i:{'nav1_localizer_deviation_dots':2,
                'nav1_glideslope_deviation_dots':2,'toliss_ils1_localizer_raw':50,**changes})
            result=self.review(rows)
            if changes.get('nav1_glideslope_deviation_dots') == -10:
                # Valid localizer measurements can still produce their own review.
                self.assertEqual(result['category'],'localizer_deviation')
                self.assertIn('Glideslope tracking was not assessed',result['text'])
            else:
                self.assertEqual(result['category'],'steady_approach')
                self.assertIn('ILS tracking was not assessed',result['text'])
        rows=self.flight()
        for row in rows[2:]:row.pop('nav1_frequency_raw',None)
        self.assertIn('ILS tracking was not assessed',self.review(rows)['text'])

    def test_pause_replay_and_missing_data_do_not_become_good_reviews(self):
        for key in ['paused','replay']:
            self.assertEqual(self.review(self.flight(lambda i:{key:1}))['category'],'insufficient_data')
        rows=self.flight()
        for row in rows:row.pop('airspeed_kias',None)
        self.assertEqual(self.review(rows)['category'],'insufficient_data')

    def test_touchdown_is_not_inferred_from_metadata_alone(self):
        self.assertEqual(self.review(self.flight(touchdown=False))['category'],'incomplete_landing')

    def test_large_descent_rate_precedes_ils_comment(self):
        result=self.review(self.flight(lambda i:{'vertical_speed_fpm':-750,'nav1_localizer_deviation_dots':2}))
        self.assertEqual(result['category'],'brisk_descent')
        self.assertIn('750',result['text'])

    def test_ils_channel_filter(self):
        for f in [10810,10815,11030,11195]:self.assertTrue(ils_channel(f))
        for f in [None,0,10800,11020,11210,float('nan')]:self.assertFalse(ils_channel(f))

    def test_packet_rate_does_not_change_review_weighting(self):
        rows=self.flight(lambda i:{'nav1_localizer_deviation_dots':1.5 if i<15 else .1})
        slow=self.review(rows)
        fast=[]
        for row in rows[:-1]:
            for sub in range(10):fast.append({**row,'time':row['time']+sub*100_000_000})
        fast.append({**rows[-1],'time':fast[-1]['time']+100_000_000})
        high=self.review(fast)
        self.assertEqual(high['category'],slow['category'])
        self.assertEqual(high['metrics'],slow['metrics'])


if __name__=='__main__':unittest.main()

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow.parquet as pq

from xplane_approach import LEPA_06L as ref, DEFINITIONS, PLOT_SIGNALS
from xplane_sampling import ReportTelemetry, LiveSampler, LIVE_SIGNALS
from xplane_signals import signal_definitions
from xplane_file_upload import export_snapshot
from xplane_live import Flight, SIGNALS
from xplane_verify import verify_capture


def position(distance, cross=0):
    # Position on localizer course at a specified runway-along distance.
    theta = math.radians(ref.course_deg)
    le, ln = ref.xy(ref.localizer_lat, ref.localizer_lon)
    localizer_cross = le*math.cos(theta)-ln*math.sin(theta)
    cross += localizer_cross
    e = -distance*math.sin(theta)+cross*math.cos(theta)
    n = -distance*math.cos(theta)-cross*math.sin(theta)
    sx, sy = ref.scales()
    return ref.threshold_lat+n/sy, ref.threshold_lon+e/sx


class ApproachTests(unittest.TestCase):
    def test_shared_latitude_and_fixed_reference_do_not_follow_aircraft_longitude(self):
        lat, lon = position(5000)
        center = ref.signals(lat, lon, 300)
        off = ref.signals(lat, lon+.005, 300)
        self.assertAlmostEqual(center['ils_center_longitude_deg'], lon, places=9)
        for key in ('ils_center_longitude_deg','ils_left_longitude_deg','ils_right_longitude_deg'):
            self.assertEqual(center[key], off[key])
        self.assertLess(center['ils_left_longitude_deg'], lon)
        self.assertGreater(center['ils_right_longitude_deg'], lon)
        self.assertGreater(off['ils_lateral_error_m'], 0)
        self.assertEqual(center['ils_plot_latitude_deg'], lat)

    def test_cone_narrows_toward_runway_and_vertical_reference_is_three_degrees(self):
        far = ref.signals(*position(10000), 100)
        near = ref.signals(*position(1000), 100)
        self.assertGreater(far['ils_right_longitude_deg']-far['ils_left_longitude_deg'],
                           near['ils_right_longitude_deg']-near['ils_left_longitude_deg'])
        self.assertGreater(far['ils_upper_altitude_msl_m']-far['ils_lower_altitude_msl_m'],
                           near['ils_upper_altitude_msl_m']-near['ils_lower_altitude_msl_m'])
        self.assertAlmostEqual(far['ils_glidepath_altitude_msl_m']-near['ils_glidepath_altitude_msl_m'],9000*math.tan(math.radians(3)))
        self.assertAlmostEqual(far['ils_distance_to_threshold_m'],10000)
        on_path = ref.signals(*position(1000),near['ils_glidepath_altitude_msl_m'])
        self.assertAlmostEqual(on_path['ils_vertical_error_m'],0)
        self.assertAlmostEqual(on_path['ils_lateral_error_m'],0,places=6)

    def test_vertical_references_end_at_threshold_and_reject_other_airports(self):
        self.assertNotIn('ils_distance_to_threshold_m',ref.signals(*position(-500),10))
        self.assertEqual(ref.signals(50,10,1000),{})
        self.assertEqual(ref.signals(float('nan'),2,100),{})
        self.assertEqual(ref.signals(*position(26000),1000),{})

    def test_fragmented_telemetry_emits_only_fresh_complete_scatter_groups(self):
        r = ReportTelemetry(landing=True); lat,lon = position(5000)
        self.assertNotIn('ils_plot_latitude_deg',r.add(0,{'latitude_deg':lat}))
        self.assertNotIn('ils_plot_latitude_deg',r.add(.1,{'longitude_deg':lon}))
        frame = r.add(.2,{'altitude_msl_m':300})
        self.assertTrue(PLOT_SIGNALS <= frame.keys())
        self.assertNotIn('ils_plot_latitude_deg',r.add(.3,{'roll_deg':0}))
        self.assertNotIn('ils_plot_latitude_deg',r.add(.4,{'longitude_deg':lon+.001}))
        self.assertNotIn('ils_plot_latitude_deg',r.add(1,{'altitude_msl_m':300}))
        self.assertNotIn('ils_plot_latitude_deg',ReportTelemetry().add(0,{'latitude_deg':lat,'longitude_deg':lon,'altitude_msl_m':300}))

    def test_live_rates_preserve_shared_scatter_samples(self):
        for mode,expected in [('low',2),('high',20)]:
            r=ReportTelemetry(landing=True);sampler=LiveSampler(mode);count=0
            for i in range(20):
                lat,lon=position(5000-i*10)
                frame=r.add(i/10,{'latitude_deg':lat,'longitude_deg':lon,'altitude_msl_m':300})
                frame=sampler.select(i/10,{k:v for k,v in frame.items() if k in LIVE_SIGNALS})
                if PLOT_SIGNALS & frame.keys():
                    self.assertTrue(PLOT_SIGNALS <= frame.keys());count+=1
            self.assertEqual(count,expected)

    def test_text_flaps_only_label_valid_toliss_detents(self):
        r=ReportTelemetry(landing=True)
        for i,label in enumerate(['Flap 0','Flap 1','Flap 2','Flap 3','Flap Full']):
            self.assertEqual(r.add(i,{'toliss_flap_lever_ratio':i/4})['flap_configuration'],label)
        for value in [-1,1.5,.4,float('nan')]:
            self.assertNotIn('flap_configuration',r.add(10,{'toliss_flap_lever_ratio':value}))
        self.assertNotIn('flap_configuration',r.add(11,{'pitch_deg':2}))
        self.assertNotIn('flap_configuration',ReportTelemetry().add(0,{'toliss_flap_lever_ratio':.5}))

    def test_all_new_signals_have_descriptions_and_shared_units(self):
        definitions={d['signal']:d for d in signal_definitions(set(DEFINITIONS)|{'flap_configuration'},SIGNALS)}
        for name,d in definitions.items():
            self.assertTrue(d['description']);self.assertTrue(d['source_url'])
            self.assertEqual(d['unit'],'text' if name=='flap_configuration' else 'deg' if name.endswith('_deg') else 'm')


class TextUploadTests(unittest.TestCase):
    def test_parquet_and_live_preserve_text_without_stringifying_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);source=folder/'source.jsonl'
            samples=[{'time':100,'pitch_deg':3.0,'flap_configuration':'Flap 1'},
                     {'time':200,'flap_configuration':'Flap Full'}]
            source.write_text(''.join(json.dumps(s)+'\n' for s in samples))
            names,count=export_snapshot(source,source.stat().st_size,folder/'raw.jsonl',folder/'file.parquet',{})
            rows=pq.read_table(folder/'file.parquet').to_pylist()
            self.assertEqual(count,3)
            self.assertEqual([r['value_text'] for r in rows],[None,'Flap 1','Flap Full'])
            self.assertEqual([r['value'] for r in rows],[3.0,None,None])
            stream=MagicMock();stream.add_dataset.return_value.id=123
            flight=Flight(folder,1,stream,log=lambda *a,**k:None)
            try:
                for sample in samples:flight.add(sample['time'],{k:v for k,v in sample.items() if k!='time'})
                flight.flush()
                df=stream.add_dataset.return_value.append.call_args.args[0]
                self.assertEqual(list(df.value_text),[None,'Flap 1','Flap Full'])
                self.assertEqual(df.value.iloc[0],3.0)
                self.assertTrue(df.value.iloc[1:].isna().all())
            finally:flight.file.close()

    def test_verification_detects_lost_text_and_repairs_as_text(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'raw.jsonl';path.write_text(json.dumps({'time':100,'flap_configuration':'Flap Full'})+'\n')
            dataset=MagicMock();dataset.id=42
            dataset.get_signals.return_value=[SimpleNamespace(name='flap_configuration',id=1,storage_status='COLD')]
            c=MagicMock();c.config.cold_catalog='cold';c.config.datapool='pool'
            counts=pd.DataFrame([{'signal':1,'n':1,'first_time':100,'last_time':100}])
            lost=pd.DataFrame([{'signal':1,'value_text':None,'n':1}])
            good=pd.DataFrame([{'signal':1,'value_text':'Flap Full','n':1}])
            c.execute.side_effect=[SimpleNamespace(dataframe=f) for f in (counts,lost,counts,good)]
            with patch('xplane_verify.MarpleTrinoClient',return_value=c),patch('xplane_verify.load_local_env'):
                self.assertTrue(verify_capture(dataset,path,repair=True,log=lambda *a,**k:None)['verified'])
            upload=dataset.add_signals.call_args.args[0][0]['data']
            self.assertEqual(upload.value_text.iloc[0],'Flap Full')
            self.assertIsNone(upload.value.iloc[0])

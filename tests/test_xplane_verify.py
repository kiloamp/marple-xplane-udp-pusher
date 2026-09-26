import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pandas as pd
from xplane_verify import verify_capture

class VerificationTests(unittest.TestCase):
    def test_file_timestamp_rounding_tolerance_does_not_hide_missing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'raw.jsonl'
            timestamp=1790451200614471000
            p.write_text(json.dumps({'time':timestamp,'a':1})+'\n')
            dataset=MagicMock();dataset.id=42
            dataset.get_signals.return_value=[SimpleNamespace(name='a',id=1)]
            client=MagicMock();client.config.cold_catalog='cold';client.config.datapool='pool'
            with patch('xplane_verify.load_local_env'),patch('xplane_verify.MarpleTrinoClient',return_value=client):
                for count,drift,ok in [(1,128,True),(1,129,False),(2,0,False),(0,0,False)]:
                    client.execute.return_value=SimpleNamespace(dataframe=pd.DataFrame([
                        {'signal':1,'n':count,'first_time':timestamp+drift,'last_time':timestamp+drift}]))
                    if ok:
                        self.assertTrue(verify_capture(dataset,p,timestamp_tolerance_ns=128)['verified'])
                        with self.assertRaises(RuntimeError):verify_capture(dataset,p)
                    else:
                        with self.assertRaises(RuntimeError):verify_capture(dataset,p,timestamp_tolerance_ns=128)
            dataset.add_signals.assert_not_called()

    def test_bulk_repair_preserves_signal_times_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'flight.jsonl'
            p.write_text(json.dumps({'time':100,'a':1})+'\n'+json.dumps({'time':200,'a':2,'b':3})+'\n')
            p.with_suffix('.signals.json').write_text(json.dumps([{'signal':'a','unit':'m','description':'Altitude'}]))
            dataset=MagicMock();dataset.id=42
            dataset.get_signals.return_value=[SimpleNamespace(name=n,id=i,storage_status='COLD') for i,n in [(1,'a'),(2,'b')]]
            client=MagicMock();client.config.cold_catalog='cold';client.config.datapool='pool'
            before=pd.DataFrame([{'signal':1,'n':1,'first_time':100,'last_time':100}])
            after=pd.DataFrame([{'signal':1,'n':2,'first_time':100,'last_time':200},{'signal':2,'n':1,'first_time':200,'last_time':200}])
            client.execute.side_effect=[SimpleNamespace(dataframe=before),SimpleNamespace(dataframe=after)]
            with patch('xplane_verify.load_local_env'),patch('xplane_verify.MarpleTrinoClient',return_value=client):
                result=verify_capture(dataset,p,repair=True)
            self.assertTrue(result['verified'])
            dataset.add_signals.assert_called_once()
            args=dataset.add_signals.call_args
            self.assertTrue(args.kwargs['overwrite'])
            uploads={u['name']:u for u in args.args[0]}
            self.assertEqual(uploads['a']['metadata']['unit'],'m')
            self.assertEqual(uploads['b']['data'].to_dict('records'),[{'time':200,'value':3}])
            self.assertEqual(result['expected_datapoints'],3)

if __name__=='__main__': unittest.main()

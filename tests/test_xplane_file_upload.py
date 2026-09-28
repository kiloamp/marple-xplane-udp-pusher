import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq
import pyarrow as pa

from xplane_file_upload import FileUploadWorker, export_snapshot, file_stream, wait_for_file_import
from xplane_sampling import LIVE_SIGNALS
from xplane_session import SessionController


class FileUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'session-001.jsonl'
        self.samples = [
            {'time': 1790451200614471000, 'airspeed_kias': 132, 'toliss_fd1_engaged': 1},
            {'time': 1790451200614471123, 'gear_0_compression_m': .13, 'pitch_deg': 3.1},
        ]
        self.source.write_text(''.join(json.dumps(s)+'\n' for s in self.samples))
        self.boundary = self.source.stat().st_size

    def tearDown(self):
        self.temp.cleanup()

    def worker(self, stream):
        return FileUploadWorker(self.source, self.boundary, 1, 'A320_Landing_Challenge_001',
                                stream, {'Capture Type': 'Live'}, lambda *a, **k: None)

    def test_snapshot_preserves_sparse_full_rate_values_and_exact_ns(self):
        with self.source.open('a') as f:
            f.write(json.dumps({'time': 1790451201614471000, 'pitch_deg': 9})+'\n')
        worker = self.worker(None)
        worker.run()
        self.assertEqual(worker.manifest['state'], 'LOCAL_FILE_READY')
        table = pq.read_table(worker.path)
        self.assertEqual(table.schema.field('time').type, pa.int64())
        expected = [{'time': s['time'], 'signal': k, 'value': v}
                    for s in self.samples for k, v in s.items() if k != 'time']
        self.assertEqual(table.to_pylist(), expected)
        self.assertEqual(table.schema.metadata[b'Capture Type'], b'SDK upload')
        self.assertEqual(table.schema.metadata[b'Flight Review'].decode(),worker.metadata['Flight Review'])
        self.assertEqual(json.loads(worker.journal.with_suffix('.review.json').read_text())['text'],worker.metadata['Flight Review'])
        self.assertEqual(worker.manifest['name'],'A320_Landing_Challenge_001')
        self.assertEqual(worker.manifest['signal_count'], 4)

    def test_file_upload_uses_files_sdk_and_verifies_without_live_cooling(self):
        stream = MagicMock()
        dataset = stream.push_file.return_value
        dataset.id = 123
        dataset.wait_for_import.return_value = dataset
        dataset.import_status = 'FINISHED'
        worker = self.worker(stream)
        with patch('xplane_verify.verify_capture', return_value={'verified': True}) as verify:
            worker.run()
        stream.push_file.assert_called_once_with(str(worker.path), file_name='A320_Landing_Challenge_001', metadata=worker.metadata)
        self.assertIn('Flight Review',worker.metadata)
        dataset.cool.assert_not_called()
        verify.assert_called_once_with(dataset, worker.journal, repair=False, log=worker.log, timestamp_tolerance_ns=128)
        self.assertEqual(worker.manifest['state'], 'FINISHED')
        self.assertEqual(worker.manifest['dataset_id'], 123)
        self.assertTrue(worker.done.is_set())

    def test_waits_through_upload_to_import_handoff(self):
        dataset = MagicMock()
        pending = MagicMock(); pending.import_status = 'UPLOADED'
        finished = MagicMock(); finished.import_status = 'FINISHED'
        dataset.wait_for_import.return_value = pending
        pending.wait_for_import.return_value = finished
        with patch('xplane_file_upload.time.sleep'):
            self.assertIs(wait_for_file_import(dataset), finished)
        self.assertTrue(dataset.wait_for_import.call_args.kwargs['force_fetch'])

    def test_failed_import_is_not_reported_ready_and_keeps_file(self):
        stream = MagicMock()
        dataset = stream.push_file.return_value
        dataset.id = 123
        dataset.wait_for_import.return_value = dataset
        dataset.import_status = 'FAILED'
        worker = self.worker(stream)
        worker.run()
        self.assertEqual(worker.manifest['state'], 'ERROR')
        self.assertTrue(worker.path.exists())
        self.assertTrue(worker.journal.exists())
        stream.push_file.assert_called_once()
        dataset.upsert_signals.assert_not_called()

    def test_existing_realtime_stream_cannot_be_used_for_analysis(self):
        db = MagicMock()
        db.get_streams.return_value = [type('Stream', (), {
            'name': 'X-Plane Flight Files', 'type': 'realtime'})()]
        with self.assertRaises(ValueError):
            file_stream(db)
        db.create_stream.assert_not_called()

    def test_realtime_allowlist_keeps_raw_journal_complete(self):
        receiver = MagicMock(); receiver.failure = None
        c = SessionController(receiver, self.folder)
        c.state = 'RECORDING'; c.timer.start(100)
        c.worker = MagicMock(); c.worker.done.is_set.return_value = False
        c.record_file = (self.folder / 'test.jsonl').open('w')
        values = {name: 1 for name in LIVE_SIGNALS}
        values.update({'gear_0_compression_m': .1, 'toliss_fd1_engaged': 1, 'paused': 0})
        try:
            c.feed(1000, 100, values)
            c.record_file.flush()
            sent = c.worker.jobs.put_nowait.call_args.args[0][1]
            self.assertEqual(set(sent), LIVE_SIGNALS)
            self.assertEqual(len(sent), 8)
            saved = json.loads((self.folder / 'test.jsonl').read_text())
            self.assertEqual(saved['toliss_fd1_engaged'], 1)
            self.assertEqual(saved['gear_0_compression_m'], .1)
        finally:
            c.close()


if __name__ == '__main__':
    unittest.main()

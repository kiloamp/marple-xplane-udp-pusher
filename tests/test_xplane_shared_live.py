import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from xplane_live import Flight
from xplane_session import UploadWorker, SessionController
from xplane_shared_live import DEFAULT_LIVE_NAME, SharedLiveError, open_shared_preview


class SharedPreviewTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.stream = MagicMock()
        self.stream.name = DEFAULT_LIVE_NAME
        self.stream.type = 'realtime'
        self.stream.id = 123
        self.dataset = MagicMock()
        self.dataset.id = 'stable-dataset-id'
        self.dataset.path = DEFAULT_LIVE_NAME
        self.dataset.import_status = 'LIVE'
        self.db.get_streams.return_value = [self.stream]
        self.stream.get_datasets.return_value = [self.dataset]
        self.stream.get_dataset.return_value = self.dataset
        self.stream.add_dataset.return_value = self.dataset
        self.db.create_stream.return_value = self.stream

    def test_create_once_and_reuse_after_restart(self):
        self.db.get_streams.return_value = []
        self.stream.get_datasets.return_value = []
        first = open_shared_preview(self.db)
        self.db.create_stream.assert_called_once()
        self.stream.add_dataset.assert_called_once_with(DEFAULT_LIVE_NAME, metadata={'Capture Type': 'Live'})
        self.db.get_streams.return_value = [self.stream]
        self.stream.get_datasets.return_value = [self.dataset]
        restarted = open_shared_preview(self.db)
        self.assertEqual(first.dataset.id, restarted.dataset.id)
        self.assertEqual(self.stream.add_dataset.call_count, 1)

    def test_two_flights_and_restart_keep_id_without_cooling_or_repair(self):
        with tempfile.TemporaryDirectory() as folder, patch('xplane_live.time.sleep'), \
             patch('xplane_verify.verify_capture') as verify:
            previous_deadline = None
            preview = open_shared_preview(self.db)
            for number, pilot in [(1, 'Alice'), (2, 'Bob'), (3, 'Claire')]:
                if number == 3:
                    preview = open_shared_preview(self.db)
                worker = UploadWorker(Path(folder), number, f'A320_Landing_Challenge_{number:03d}',
                                      preview, 1, MagicMock(), {'Participant Name': pilot})
                worker.jobs.put((number * 10**9, {'airspeed_kias': 130, 'flap_configuration': 'Flap Full'}))
                worker.finish('touchdown'); worker.run()
                self.assertIsNone(worker.error)
                self.assertEqual(worker.flight.manifest['dataset_id'], 'stable-dataset-id')
                self.assertEqual(worker.flight.manifest['state'], 'LIVE_SEGMENT_COMPLETE')
                self.assertEqual(worker.flight.manifest['name'], DEFAULT_LIVE_NAME)
                self.assertEqual(worker.name, f'A320_Landing_Challenge_{number:03d}')
                self.assertEqual(worker.metadata['Participant Name'], pilot)
                self.assertTrue(worker.flight.file.closed)
                if number == 2:
                    self.assertGreaterEqual(preview.next_append, previous_deadline)
                previous_deadline = preview.next_append
            self.assertEqual(self.dataset.append.call_count, 3)
            self.dataset.cool.assert_not_called()
            self.dataset.update_metadata.assert_not_called()
            self.stream.add_dataset.assert_not_called()
            verify.assert_not_called()

    def test_uncertain_append_not_replayed_or_repaired(self):
        self.dataset.append.side_effect = TimeoutError()
        with tempfile.TemporaryDirectory() as folder, patch('xplane_live.time.sleep'), \
             patch('xplane_verify.verify_capture') as verify:
            flight = Flight(Path(folder), 1, open_shared_preview(self.db), DEFAULT_LIVE_NAME, log=MagicMock())
            flight.add(10**9, {'pitch_deg': 2})
            flight.flush()
            flight.add(2 * 10**9, {'pitch_deg': 3})
            flight.finish('stop'); flight.finish('quit')
            self.assertEqual(flight.manifest['upload_status'], 'APPEND_UNCONFIRMED')
            self.assertEqual(len(flight.path.read_text().splitlines()), 2)
            self.dataset.append.assert_called_once()
            self.dataset.cool.assert_not_called()
            verify.assert_not_called()

    def test_closed_or_duplicate_preview_never_silently_replaced(self):
        self.dataset.import_status = 'FINISHED'
        with self.assertRaises(SharedLiveError): open_shared_preview(self.db)
        self.dataset.import_status = 'LIVE'
        self.stream.get_datasets.return_value = [self.dataset, self.dataset]
        with self.assertRaises(SharedLiveError): open_shared_preview(self.db)
        self.db.get_streams.return_value = [self.stream, self.stream]
        with self.assertRaises(SharedLiveError): open_shared_preview(self.db)
        self.stream.add_dataset.assert_not_called()
        self.db.create_stream.assert_not_called()

    def test_external_cooling_blocks_next_segment(self):
        preview = open_shared_preview(self.db)
        self.dataset.import_status = 'FINISHED'
        with self.assertRaises(SharedLiveError): preview.add_dataset('ignored')
        self.stream.add_dataset.assert_not_called()

    def test_completed_segment_is_success_and_quit_does_not_cool(self):
        with tempfile.TemporaryDirectory() as folder:
            receiver = MagicMock(); receiver.failure = None
            controller = SessionController(receiver, Path(folder), stream=open_shared_preview(self.db))
            controller.state = 'SAVING'; controller.exit_requested = True
            controller.worker = MagicMock(); controller.worker.error = None
            controller.worker.done.is_set.return_value = True
            controller.worker.flight.manifest = {'state': 'LIVE_SEGMENT_COMPLETE'}
            controller.tick(1)
            self.assertEqual(controller.state, 'COMPLETE')
            self.assertTrue(controller.closed)
            controller.close()
            self.dataset.cool.assert_not_called()


if __name__ == '__main__':
    unittest.main()

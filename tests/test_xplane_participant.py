import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from xplane_file_upload import FileUploadWorker, ParticipantUploadWorker
from xplane_session import SessionController
from xplane_tui import ConsoleInput, console_lines


class ParticipantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.stream = MagicMock()
        self.worker = FileUploadWorker(self.folder/'session-001.jsonl', 0, 1, 'Flight_001',
                                       self.stream, {'Flight Review': 'Steady approach'}, lambda *a: None)
        self.worker.manifest['dataset_id'] = 123
        self.stream.get_dataset.return_value.metadata = {'Participant Name': 'José'}

    def tearDown(self):
        self.temp.cleanup()

    def test_waits_for_upload_and_keeps_fixed_target(self):
        job = ParticipantUploadWorker(self.worker, ' José ')
        waiting = threading.Event()
        wait = self.worker.done.wait
        def block():
            waiting.set()
            wait()
        with patch.object(self.worker.done, 'wait', side_effect=block):
            job.start()
            try:
                self.assertTrue(waiting.wait(2))
                self.stream.get_dataset.assert_not_called()
                self.assertEqual(json.loads(job.path.read_text())['name'], 'José')
            finally:
                self.worker.done.set()
                job.join(2)
        self.assertTrue(job.done.is_set())
        self.assertEqual(job.state, 'SAVED')
        self.assertEqual(self.stream.get_dataset.call_args_list[0].args, (123,))
        self.stream.get_dataset.return_value.update_metadata.assert_called_once_with({'Participant Name': 'José'})
        self.assertEqual(self.worker.metadata['Flight Review'], 'Steady approach')
        self.assertEqual(json.loads(self.worker.manifest_path.read_text())['metadata']['Participant Name'], 'José')

    def test_failure_retains_name_and_retry_updates_same_dataset(self):
        self.worker.done.set()
        self.stream.get_dataset.side_effect = ConnectionError
        first = ParticipantUploadWorker(self.worker, 'José'); first.run()
        self.assertEqual(first.state, 'ERROR')
        self.assertEqual(json.loads(first.path.read_text())['name'], 'José')
        self.stream.get_dataset.side_effect = None
        retry = ParticipantUploadWorker(self.worker, 'José'); retry.run()
        self.assertEqual(retry.state, 'SAVED')
        self.stream.push_file.assert_not_called()

    def test_unconfirmed_metadata_is_not_reported_saved(self):
        self.worker.done.set()
        self.stream.get_dataset.return_value.metadata = {}
        job = ParticipantUploadWorker(self.worker, 'José'); job.run()
        self.assertEqual(job.state, 'ERROR')

    def test_failed_upload_without_id_never_updates_another_dataset(self):
        self.worker.done.set(); self.worker.manifest.pop('dataset_id')
        job = ParticipantUploadWorker(self.worker, 'José'); job.run()
        self.assertEqual(job.state, 'ERROR')
        self.stream.get_dataset.assert_not_called()

    def test_local_only_saves_manifest_without_cloud(self):
        self.worker.stream = None; self.worker.done.set()
        job = ParticipantUploadWorker(self.worker, 'José'); job.run()
        self.assertEqual(job.state, 'LOCAL')
        self.assertEqual(self.worker.metadata['Participant Name'], 'José')
        self.stream.get_dataset.assert_not_called()

    def test_name_validation(self):
        for name in (' ', 'a'*81, 'a\nb', '\x1b[2J'):
            with self.assertRaises(ValueError):ParticipantUploadWorker(self.worker, name)

    def test_controller_names_previous_flight_and_waits_on_quit(self):
        receiver = MagicMock(); receiver.failure = None
        c = SessionController(receiver, self.folder)
        target = str(self.worker.manifest_path)
        c.completed_files.append({'id': target, 'worker': self.worker, 'job': None})
        # start() clears the current file pointer but must retain completed targets.
        c.start(1)
        c.commands.put(('participant', target, 'José'))
        c.tick(1)
        job = c.participant_jobs[0]
        c.set_participant(target, 'Different name')
        self.assertEqual(len(c.participant_jobs), 1)
        c.commands.put('quit'); c.tick(2)
        self.assertFalse(c.closed)
        self.worker.done.set(); job.join(2)
        c.tick(3)
        self.assertTrue(c.closed)
        self.assertEqual(self.worker.metadata['Participant Name'], 'José')
        c.close()


class ConsoleTests(unittest.TestCase):
    def status(self):
        return {'exiting': False, 'completed': [{'id': 'old-file', 'name': 'Flight_001',
                 'participant': '', 'name_status': 'UNNAMED'}]}

    def test_unicode_editing_does_not_trigger_shortcuts_or_change_target(self):
        s = self.status(); editor = ConsoleInput(); editor.observe(s)
        for key in 'José QRXS':self.assertIsNone(editor.handle(key, s))
        editor.handle('\x7f', s)
        s['completed'].append({'id': 'next-file', 'name': 'Flight_002', 'participant': '', 'name_status': 'UNNAMED'})
        editor.observe(s)
        self.assertEqual(editor.handle('\n', s), ('participant', 'old-file', 'José QRX'))
        editor.observe(s)
        self.assertEqual(editor.target['id'], 'next-file')

    def test_skip_and_reopen_name_without_repeated_prompt(self):
        s = self.status(); editor = ConsoleInput(); editor.observe(s)
        self.assertIsNone(editor.handle('\n', s)); self.assertTrue(editor.error)
        editor.handle('\x1b', s); editor.observe(s)
        self.assertIsNone(editor.target)
        editor.handle('n', s); self.assertEqual(editor.target['id'], 'old-file')
        self.assertEqual(editor.handle('\x03', s), 'quit')

    def test_compact_view_fits_standard_terminal_with_name_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            receiver = MagicMock(); receiver.failure = None
            c = SessionController(receiver, Path(folder))
            s = c.snapshot(1); s.update(self.status())
            editor = ConsoleInput(); editor.observe(s)
            lines = console_lines(s, editor)
            self.assertLess(len(lines), 23)
            self.assertTrue(any('Enter: save name' in line for line in lines))
            self.assertFalse(any('Queue:' in line for line in lines))
            editor.handle('\x1b', s); editor.handle('d', s)
            self.assertTrue(any('Queue:' in line for line in console_lines(s, editor)))
            c.close()


if __name__ == '__main__':unittest.main()

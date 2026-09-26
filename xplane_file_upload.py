"""Independent, immutable full-rate file uploads for post-flight analysis."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from xplane_signals import signal_definitions

FILE_STREAM_NAME = 'X-Plane Flight Files'
FILE_PLUGIN_ARGS = '--shape long --time-factor 1'


def wait_for_file_import(dataset, timeout=180):
    """The SDK can return during the handoff from upload to its import queue."""
    deadline = time.monotonic() + timeout
    while True:
        dataset = dataset.wait_for_import(timeout=max(.1, deadline-time.monotonic()), force_fetch=True)
        if dataset.import_status == 'FINISHED':
            return dataset
        if dataset.import_status in {'FAILED', 'ERROR', 'POSTPROCESSING_FAILED', 'COOLING_FAILED'}:
            raise RuntimeError('File import failed')
        if time.monotonic() >= deadline:
            raise TimeoutError('File import not confirmed before timeout')
        time.sleep(1)


def file_stream(db):
    matches = [s for s in db.get_streams() if s.name == FILE_STREAM_NAME]
    stream = matches[0] if matches else db.create_stream(
        FILE_STREAM_NAME, type='files', plugin='parquet',
        plugin_args=FILE_PLUGIN_ARGS,
        description='Full-rate X-Plane approach and touchdown files for analysis')
    if (stream.type != 'files' or stream.plugin != 'parquet'
            or stream.plugin_args != FILE_PLUGIN_ARGS):
        raise ValueError(f'{FILE_STREAM_NAME} must use files/parquet with {FILE_PLUGIN_ARGS}')
    return stream


def export_snapshot(source, byte_limit, journal, parquet, metadata):
    """Copy only complete records present at the trigger; preserve ns and sparse samples."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    with source.open('rb') as src, journal.open('xb') as dst:
        remaining = byte_limit
        while remaining:
            block = src.read(min(remaining, 1024 * 1024))
            if not block:
                raise ValueError('Capture shorter than frozen upload boundary')
            dst.write(block)
            remaining -= len(block)
    schema = pa.schema([('time', pa.int64()), ('signal', pa.string()), ('value', pa.float64())],
                       metadata={k: str(v) for k, v in metadata.items()})
    names = set()
    count = 0
    rows = []
    with pq.ParquetWriter(parquet, schema, compression='zstd') as writer:
        with journal.open() as capture:
            for line in capture:
                sample = json.loads(line)
                for name, value in sample.items():
                    if name == 'time':
                        continue
                    rows.append({'time': sample['time'], 'signal': name, 'value': value})
                    names.add(name)
                    count += 1
                if len(rows) >= 50000:
                    writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                    rows.clear()
        if rows:
            writer.write_table(pa.Table.from_pylist(rows, schema=schema))
    if not count:
        raise ValueError('No telemetry in file snapshot')
    return names, count


class FileUploadWorker(threading.Thread):
    def __init__(self, source, byte_limit, number, name, stream, metadata, log):
        super().__init__(daemon=True)
        self.source = Path(source)
        self.byte_limit = byte_limit
        self.stream = stream
        self.log = log
        self.done = threading.Event()
        self.error = None
        self.journal = self.source.parent / f'raw-{number:03d}.jsonl'
        self.path = self.source.parent / f'{name}.parquet'
        self.manifest_path = self.source.parent / f'raw-{number:03d}.json'
        self.metadata = {**metadata, 'Capture Type': 'SDK upload'}
        self.manifest = {'name': name, 'state': 'PREPARING', 'metadata': self.metadata,
                         'source': str(self.source), 'source_bytes': byte_limit,
                         'file': str(self.path), 'stream': FILE_STREAM_NAME}

    def save(self):
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2) + '\n')

    def run(self):
        try:
            from xplane_live import SIGNALS
            self.save()
            names, count = export_snapshot(self.source, self.byte_limit, self.journal,
                                           self.path, self.metadata)
            definitions = signal_definitions(names, SIGNALS)
            self.journal.with_suffix('.signals.json').write_text(json.dumps(definitions, indent=2) + '\n')
            self.manifest.update(signal_count=len(names), datapoints=count)
            if self.stream is None:
                self.manifest['state'] = 'LOCAL_FILE_READY'
            else:
                self.manifest['state'] = 'UPLOADING'
                self.save()
                # A files stream never acquires the realtime lifecycle. No cooling
                # or modification of the live dataset is needed to analyze this file.
                dataset = self.stream.push_file(str(self.path), metadata=self.metadata)
                self.manifest.update(dataset_id=dataset.id, state='IMPORTING', initial_import_status=dataset.import_status)
                self.save()
                dataset = wait_for_file_import(dataset)
                self.manifest['state'] = 'VERIFYING'
                self.save()
                dataset.upsert_signals(definitions)
                from xplane_verify import verify_capture
                # The file importer rounds Unix ns via float64 (up to 128 ns
                # at current dates). Keep exact source times; never tolerate
                # missing samples or alter the live verifier's zero tolerance.
                result = verify_capture(dataset, self.journal, repair=False, log=self.log,
                                        timestamp_tolerance_ns=128)
                self.manifest.update(state='FINISHED', cold_storage_verified=result['verified'])
            self.save()
            self.log(f"Analysis file ready: {self.manifest['name']} | SDK upload | dataset={self.manifest.get('dataset_id', 'local only')}")
        except Exception as exc:
            self.error = type(exc).__name__
            self.manifest.update(failed_stage=self.manifest['state'], state='ERROR', error=self.error)
            self.save()
            self.log('SDK file upload not confirmed; local file retained. See ' + str(self.manifest_path))
        finally:
            self.done.set()

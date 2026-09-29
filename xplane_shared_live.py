"""One persistent Marple preview, with separate local segments for each flight."""
import time

DEFAULT_LIVE_NAME = "Toulouse Live Fair Day 1"


class SharedLiveError(RuntimeError):
    """Safe operator-facing error; never contains SDK response bodies."""


class SharedLiveStream:
    shared_live = True

    def __init__(self, stream, dataset):
        self.stream = stream
        self.dataset = dataset
        self.id = stream.id
        self.preview_name = dataset.path
        # Also leave a pacing gap on service restart.
        self.next_append = time.monotonic() + 1.05

    def get_datasets(self):
        return self.stream.get_datasets()

    def add_dataset(self, name, metadata=None):
        # Flight metadata belongs to its Parquet file, not the whole fair day.
        dataset = self.stream.get_dataset(id=self.dataset.id)
        if dataset.import_status != "LIVE":
            raise SharedLiveError("The shared preview is no longer LIVE. Choose a new --live-name for a new day.")
        self.dataset = dataset
        return dataset


def open_shared_preview(db, name=DEFAULT_LIVE_NAME):
    name = name.strip()
    if not name:
        raise SharedLiveError("The live preview name must not be empty.")
    streams = [stream for stream in db.get_streams() if stream.name == name]
    if len(streams) > 1:
        raise SharedLiveError("Multiple datastreams match the live name; resolve duplicates before starting.")
    stream = streams[0] if streams else db.create_stream(
        name, type="realtime", description="Persistent fair-day flight preview; individual analysis files are uploaded separately.")
    if stream.type != "realtime":
        raise SharedLiveError("The live name belongs to a non-realtime datastream.")
    datasets = [dataset for dataset in stream.get_datasets() if dataset.path == name]
    if len(datasets) > 1:
        raise SharedLiveError("Multiple datasets match the live name; resolve duplicates before starting.")
    dataset = datasets[0] if datasets else stream.add_dataset(name, metadata={"Capture Type": "Live"})
    if dataset.import_status != "LIVE":
        raise SharedLiveError("The shared preview is no longer LIVE. Choose a new --live-name for a new day.")
    return SharedLiveStream(stream, dataset)

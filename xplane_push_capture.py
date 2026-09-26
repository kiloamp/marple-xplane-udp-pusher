"""Push a locally captured flight to an existing live dataset, optionally following it."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from xplane_marple import get_sdk_db
from xplane_live import SIGNALS
from xplane_signals import signal_definitions, BY_NAME


def read_complete_samples(file, limit=500):
    rows = []
    while len(rows) < limit:
        position = file.tell()
        line = file.readline()
        if not line or not line.endswith("\n"):
            file.seek(position)
            break
        rows.append(json.loads(line))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--dataset-id", type=int, required=True)
    parser.add_argument("--stream", default="X-Plane Fair Live")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--manifest", type=Path, help="Explicit upload journal for a separate fresh destination dataset")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    progress_path = args.manifest or args.capture.with_suffix(".upload.json")
    if progress_path.exists():
        parser.error("Upload manifest already exists; inspect its state before resuming (no blind retries).")
    capture_manifest = args.capture.with_suffix(".json")
    state = {"dataset_id": args.dataset_id, "state": "STARTING", "confirmed_packets": 0}
    def save():
        progress_path.write_text(json.dumps(state, indent=2) + "\n")
    save()
    db = get_sdk_db()
    stream = db.get_stream(args.stream)
    dataset = stream.get_dataset(args.dataset_id)
    if dataset.import_status != "LIVE":
        parser.error("Destination dataset must be LIVE.")
    # Prevent accidentally appending a capture to an unrelated populated dataset.
    if dataset.get_signals():
        parser.error("Destination dataset must be empty; choose a fresh live dataset.")
    defined = set()
    definitions_path = args.capture.with_suffix(".signals.json")
    if definitions_path.exists():
        BY_NAME.update({d["signal"]: d for d in json.loads(definitions_path.read_text())})
    state["state"] = "LIVE"
    save()
    print(f"Following {args.capture} into Marple dataset {dataset.id}", flush=True)
    try:
        with args.capture.open() as file:
            while True:
                start_offset = file.tell()
                samples = read_complete_samples(file, args.batch_size)
                if samples:
                    rows = [{"time": sample["time"], "signal": name, "value": value}
                            for sample in samples for name, value in sample.items() if name != "time"]
                    state.update(state="APPENDING", pending_start_offset=start_offset, pending_end_offset=file.tell())
                    save()
                    try:
                        new_names = {row["signal"] for row in rows} - defined
                        if new_names:
                            dataset.upsert_signals(signal_definitions(new_names, SIGNALS))
                            defined.update(new_names)
                        dataset.append(pd.DataFrame(rows), shape="long")
                    except BaseException as exc:
                        state["state"] = "APPEND_UNCONFIRMED"
                        response = getattr(exc, "response", None)
                        state["http_status"] = getattr(response, "status_code", None)
                        save()
                        raise
                    state.update(state="LIVE", confirmed_packets=state["confirmed_packets"] + len(samples), confirmed_offset=file.tell())
                    save()
                    print(f"Appended {len(samples)} packets; total {state['confirmed_packets']}", flush=True)
                    time.sleep(2)
                    continue
                # The capture manifest can briefly be rewritten by the recorder.
                try:
                    manifest = json.loads(capture_manifest.read_text())
                except json.JSONDecodeError:
                    time.sleep(0.1)
                    continue
                if manifest["state"] == "LOCAL_CAPTURE_COMPLETE":
                    # Recheck EOF after seeing completion to avoid racing the last write.
                    if file.tell() < args.capture.stat().st_size:
                        continue
                    state["capture_end_reason"] = manifest.get("end_reason")
                    state["state"] = "COOLING_REQUESTED"
                    save()
                    dataset = dataset.cool()
                    state["state"] = dataset.import_status
                    save()
                    dataset = dataset.wait_for_import(timeout=60)
                    state["state"] = dataset.import_status
                    save()
                    if dataset.import_status != "FINISHED":
                        raise RuntimeError("Finalization not confirmed")
                    state["state"] = "VERIFYING_COLD_STORAGE"
                    save()
                    from xplane_verify import verify_capture
                    result = verify_capture(dataset, args.capture, repair=True)
                    dataset.upsert_signals(signal_definitions(defined, SIGNALS))
                    state.update(state="FINISHED", cold_storage_verified=result["verified"], repaired_signals=result["repaired_signals"])
                    save()
                    print(f"Dataset {dataset.id} FINISHED; {state['confirmed_packets']} captured packets uploaded.", flush=True)
                    return
                if not args.follow:
                    raise RuntimeError("Capture is still running; use --follow to await its end")
                time.sleep(1)
    except BaseException as exc:
        print(f"Upload stopped ({type(exc).__name__}); inspect {progress_path}", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

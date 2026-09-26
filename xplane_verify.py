"""Verify completed X-Plane captures against Marple cold storage; repair from the journal."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from env_loader import load_local_env
from xplane_marple import MarpleTrinoClient


def verify_capture(dataset, capture: Path, *, repair=False, log=print):
    load_local_env()
    expected = {}
    for line in capture.read_text().splitlines():
        sample = json.loads(line)
        for name, value in sample.items():
            if name != "time":
                expected.setdefault(name, []).append({"time": sample["time"], "value": value})
    client = MarpleTrinoClient()
    catalog = client.config.cold_catalog
    pool = client.config.datapool
    sql = f"SELECT signal,count(*) AS n,min(time) AS first_time,max(time) AS last_time FROM {catalog}.{pool}.data WHERE dataset={int(dataset.id)} GROUP BY signal"

    def mismatches():
        actual = client.execute(sql).dataframe
        lookup = {int(row.signal): row for row in actual.itertuples()}
        signals = {s.name: s for s in dataset.get_signals(refresh=True)}
        bad = []
        for name, rows in expected.items():
            signal = signals.get(name)
            found = lookup.get(signal.id) if signal else None
            if (found is None or found.n != len(rows)
                    or found.first_time != rows[0]["time"] or found.last_time != rows[-1]["time"]):
                bad.append(name)
        return bad, actual

    bad, actual = mismatches()
    repaired = []
    if bad and repair:
        # Submit one SDK batch; per-signal HTTP polling would overwhelm rate limits
        # with the hundreds of fields in X-Plane's selected DATA groups.
        definitions_path = capture.with_suffix(".signals.json")
        definitions = ({d["signal"]: {k: v for k, v in d.items() if k != "signal"}
                        for d in json.loads(definitions_path.read_text())}
                       if definitions_path.exists() else {})
        log(f"Restoring {len(bad)} signals from the local capture", flush=True)
        dataset.add_signals([
            {"name": name, "data": pd.DataFrame(expected[name]),
             "metadata": definitions.get(name, {})}
            for name in bad
        ], overwrite=True, concurrency=4)
        repaired = list(bad)
        deadline = time.monotonic() + 60
        while True:
            pending = [s for s in dataset.get_signals(refresh=True)
                       if s.name in repaired and s.storage_status == "FROZEN_TO_COLD"]
            if not pending:
                break
            if time.monotonic() >= deadline:
                break  # The final count/timestamp check reports incomplete imports.
            time.sleep(2)
        for attempt in range(3):
            bad, actual = mismatches()
            if not bad:
                break
            time.sleep(1)
    result = {"dataset_id": dataset.id, "verified": not bad,
              "verification": "per-signal count and first/last timestamp in cold storage",
              "expected_datapoints": sum(map(len, expected.values())),
              "actual_datapoints": int(actual.n.sum()), "signals": len(expected),
              "repaired_signals": repaired, "mismatched_signals": bad}
    capture.with_suffix(".verification.json").write_text(json.dumps(result, indent=2) + "\n")
    if bad:
        raise RuntimeError("Cold-storage verification failed; inspect verification manifest")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--dataset-id", type=int, required=True)
    parser.add_argument("--stream", default="X-Plane Fair Live")
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    from xplane_marple import get_sdk_db
    dataset = get_sdk_db().get_stream(args.stream).get_dataset(args.dataset_id)
    if dataset.import_status != "FINISHED":
        parser.error("Dataset must be FINISHED before cold-storage verification")
    print(json.dumps(verify_capture(dataset, args.capture, repair=args.repair)))


if __name__ == "__main__":
    main()

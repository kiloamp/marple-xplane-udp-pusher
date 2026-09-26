# X-Plane UDP → Marple Live Recorder

Stream X-Plane telemetry to Marple DB, with one dataset per flight. The Python
service receives native UDP RREF/DATA packets, keeps a local journal, batches
live uploads, and finalizes and verifies each completed dataset.

## Flight modes

| Aircraft | Recording flow | Dataset name |
| --- | --- | --- |
| ToLiss Airbus | Automatic recording; ends 15 seconds after first landing-gear compression; resets to a new dataset at 3000 ft | `A320_Landing_Challenge_XXX` |
| Any other aircraft | Press S; 5-second preparation; 60-second flight; warning at 10 seconds remaining; pause request and finalize | `Marple_Acrobatic_XXX` |

Both modes use the `X-Plane Fair Live` datastream. The console shows aircraft,
mode, recording state, upload queue and recent activity. FlyWithLua is not required.

## Quick start

Tested on macOS, Python 3.13 and X-Plane 11.55r2. Requires Python 3.11+.
The service uses `fcntl` and `curses`; native Windows support is not implemented.

```sh
git clone https://github.com/kiloamp/marple-xplane-udp-pusher.git
cd marple-xplane-udp-pusher
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env.local
```

Set your Marple credentials and query catalog settings in `.env.local`. Keep
`MARPLE_API_TOKEN` available for cold-storage verification; optionally use a separate
`MARPLE_DB_API_TOKEN` for realtime SDK operations. The tested SDK version is pinned.

Start X-Plane manually, then run:

```sh
python start_xplane_service.py
```

On macOS you can instead double-click **Start A320 Landing Challenge.command**;
its historical filename launches both aircraft modes and prefers the local `.venv`.
Use `--host SIMULATOR_IP` if X-Plane runs on another computer, or `--local-only`
to record without Marple writes.

- **S:** start a timed session.
- **X:** stop and save the current session.
- **Q:** stop safely; wait for finalization before closing Terminal.

## Select fewer signals

Uncheck unwanted **network/UDP output** groups in X-Plane's Data Output settings.
Only received DATA fields are uploaded. The recorder separately subscribes to
26 core signals, preserving landing/reset and pause detection. Aircraft identity
is also independent of these checkboxes.

Stop and restart the recorder after changing selections for a clean signal count.
Fewer signals reduce payloads and restoration work; they do not guarantee one-second
updates. Upload pacing waits at least 1.05 seconds after the previous response.

## Completion and recovery

After the cutoff the service flushes remaining samples, calls `cool()`, waits for
`FINISHED`, then checks per-signal sample counts and first/last timestamps in Marple
cold storage through Trino. Incomplete signals are restored from the local journal.
Captures and manifests are stored under `outputs/xplane/`, excluded from Git.

## Code layout

- `start_xplane_service.py`: launcher and single-instance lock.
- `xplane_session.py`: terminal console, aircraft routing and session controller.
- `xplane_live.py`, `xplane_aircraft.py`, `xplane_signals.py`: UDP capture,
  aircraft identification, landing/reset detection and signal descriptions.
- `xplane_marple.py`, `env_loader.py`: SDK and Trino connections, local credentials.
- `xplane_verify.py`, `xplane_push_capture.py`: verification and recovery upload.
- `xplane_benchmark*.py`: optional recorded-data replay throughput diagnostics;
  these create explicitly named benchmark datasets when run.
- `tests/test_xplane*.py`: recorder tests without simulator/cloud access.

[Operating details](XPLANE_LIVE.md) · [Colleague setup and development](CONTRIBUTING.md)

# X-Plane UDP → Marple Live Recorder

Stream X-Plane telemetry to Marple DB, with one dataset per flight. The Python
service receives native UDP RREF/DATA packets, keeps a local journal, batches
live uploads, and finalizes and verifies each completed dataset.

## Flight modes

| Aircraft | Recording flow | Dataset name |
| --- | --- | --- |
| ToLiss Airbus | Automatic recording; ends and requests a pause 5 seconds after first landing-gear compression; reset to 3000 ft and unpause for a new dataset | `A320_Landing_Challenge_XXX` |
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
- **R:** toggle LOW (1 Hz) / HIGH (10 Hz) live sampling for the next flight.
- **Q:** stop safely; wait for finalization before closing Terminal.

## Programmatic signals and sample rate

Leave X-Plane's Data Output UDP checkboxes off. The service requests a curated
landing-report set directly: GPS position, speed, altitude, heading, attitude,
vertical speed, throttle requests, N1, flaps, speedbrakes, guidance and landing gear.
It derives distance covered and report-friendly units. Confirmed ToLiss aircraft
also supply ten custom flight-director, autopilot, autothrust and ILS indications.
Up to 57 signals are recorded for other aircraft, or 67 for ToLiss.

The default **LOW** mode sends at most one sample per signal per second to Marple;
**HIGH** sends up to ten. Select `--sample-mode low|high` or press **R** in the console
for the next flight. Local capture and touchdown detection stay at 10 Hz in both
modes. Each dataset keeps a single selected rate. HTTP batch pacing remains at
least 1.05 seconds after the previous response; sample rate is not UI refresh rate.

The full-rate local journal and touchdown snapshot prepare a future landing PDF.
Automatic PDF generation/scoring is not included yet. ToLiss ILS values with
unverified scales remain explicitly raw, with separate display flags.
See [operating details](XPLANE_LIVE.md) for the signal groups and interpretation.
Legacy checkbox capture requires explicit `--include-data` and is normally disabled.

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
- `xplane_report_signals.py`, `xplane_sampling.py`: report channels, distance derivation
  and low/high live sampling.
- `xplane_verify.py`, `xplane_push_capture.py`: verification and recovery upload.
- `xplane_benchmark*.py`: optional recorded-data replay throughput diagnostics;
  these create explicitly named benchmark datasets when run.
- `tests/test_xplane*.py`: recorder tests without simulator/cloud access.

[Operating details](XPLANE_LIVE.md) · [Colleague setup and development](CONTRIBUTING.md)

# X-Plane UDP → Marple Live Recorder

Stream X-Plane telemetry to Marple DB, with separate live and analysis datasets per flight. The Python
service receives native UDP RREF/DATA packets, keeps a local journal, batches
live uploads, and finalizes and verifies each completed dataset.

## Flight modes

| Aircraft | Recording flow | Dataset name |
| --- | --- | --- |
| ToLiss Airbus | Automatic recording; full file upload at first gear compression; live ends after 10 seconds; pause and reset to approach start at 3000 ft; manual ISCS/unpause | `A320_Landing_Challenge_XXX` |
| Any other aircraft | Press S; 5-second preparation; 60-second flight; warning at 10 seconds remaining; stop and upload full file | `Marple_Acrobatic_XXX` |

Both modes use `X-Plane Fair Live` for the preview and `X-Plane Flight Files` for
analysis. Metadata `Capture Type` is `Live` or `SDK upload`; the original aircraft,
airport and flight-type metadata is retained. **Landing mode pauses and resets after 10 seconds; timed mode does not pause.** The console shows the current challenge and analysis upload status; press D for diagnostics. Open the
`SDK upload` dataset in Insight for analysis. FlyWithLua is not required.

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
- **N:** add/edit the last completed flight’s participant name, or retry a failed name update.
- **D:** show/hide diagnostics (signal counts, queue, rate and recent activity).
- **Q:** stop safely; wait for finalization and submitted names before closing Terminal.

## Programmatic signals and sample rate

Leave X-Plane's Data Output UDP checkboxes off. The service requests a curated
landing-report set directly: GPS position, speed, altitude, heading, attitude,
vertical speed, throttle requests, N1, flaps, speedbrakes, guidance and landing gear.
It derives distance covered and report-friendly units. Confirmed ToLiss aircraft
also supply ten custom flight-director, autopilot, autothrust and ILS indications.
Up to 57 signals are recorded for other aircraft, or 67 for ToLiss.

Realtime sends only eight signals: airspeed KIAS, altitude MSL ft, roll, pitch,
magnetic heading, latitude, longitude and vertical speed FPM. All other received
channels stay in the full-rate file.

The default **LOW** mode sends at most one sample per preview signal per second;
**HIGH** sends up to ten. Select `--sample-mode low|high` or press **R** in the console
for the next flight. Local capture and touchdown detection stay at 10 Hz in both
modes. Each dataset keeps a single selected rate. HTTP batch pacing remains at
least 1.05 seconds after the previous response; sample rate is not UI refresh rate.

The SDK analysis file and touchdown snapshot prepare a future landing PDF.
Automatic PDF generation/scoring is not included yet. ToLiss ILS values with
unverified scales remain explicitly raw, with separate display flags.
See [operating details](XPLANE_LIVE.md) for the signal groups and interpretation.
Legacy checkbox capture requires explicit `--include-data` and is normally disabled.

## Completion and recovery

At first landing-gear compression, the service freezes a full-rate snapshot,
including the touchdown packet, and uploads it through SDK `push_file()` as a
Parquet file. It preserves every received numeric signal and original nanosecond
timestamp. The analysis file ends at first touchdown; the 10-second taxi tail
continues in realtime and the local session journal. Upload duration depends on
network/import time and does not change the realtime deadline.

At the landing cutoff, the service requests pause, waits for confirmation, then
sends native UDP `PREL` to return to the approach's first captured airborne
position, heading and true airspeed at **3000 ft MSL**. It confirms the reset and
paused state (re-pausing only if fresh telemetry shows PREL resumed the sim).
Finish the ToLiss ISCS setup yourself and **unpause manually** to start the next
recording. This does not load a ToLiss scenario or restore its system configuration.
Missing/stale telemetry or a failed reset is logged for manual handling; commands
are not blindly retried. X/Q and early manual resets do not trigger repositioning.

The file uses a **files** datastream, never a realtime dataset. Its import and
cold-storage verification run independently of live cooling. The console reports
`FINISHED` only after checking sample counts and time bounds through Trino. A live
cooling failure does not invalidate this analysis dataset. The small live preview
still drains and calls `cool()` at its cutoff.
The original file retains exact timestamps; file verification allows up to 128 ns
of observed importer rounding, with exact per-signal sample counts.

## Dataset names and generated reviews

Analysis datasets have a clean name: `A320_Landing_Challenge_XXX` or
`Marple_Acrobatic_XXX`, with **no suffix** in Marple. Realtime previews use the
same base name with **`.live`**, for example `A320_Landing_Challenge_001.live`.
The local analysis payload retains its normal `.parquet` extension. These names
apply to new flights; existing datasets are unchanged.

When recording finishes, the console asks for the participant's name. **Enter**
saves it as **Participant Name** metadata on that flight's SDK analysis dataset;
**Esc** skips. Names can contain accents and spaces (up to 80 characters). While
entering a name, shortcut letters are treated as text; **Ctrl+C** still quits safely.
Press **N** afterward to edit the latest completed flight or retry a failed update.
The prompt identifies its flight, even if the next recording starts meanwhile.

The file upload still starts immediately at touchdown. Name entry and its metadata
update run independently of recording/reset; if the file is still uploading, the
name waits for it. The console says “saved to Marple” only after a read-back confirms
it. Names are added to **dataset metadata**, without re-uploading or rewriting the
original Parquet binary. They are also retained in the local analysis manifest and
`raw-NNN.participant.json`; local-only mode saves them locally. A failed update
shows a retry notice. Pending/failed names survive on disk but are not automatically
resubmitted after restarting the service. Unanswered prompts do not block quitting.

Each new SDK file receives a **Flight Review** metadata field. One of ten
templates is selected from measured speed variation, bank, gear-handle timing,
pre-touchdown descent and valid standard NAV1 localizer/glideslope samples.
Insufficient data and incomplete landings have their own reviews. There is no
random assignment or external AI call. The chosen rule and measurements are
saved in `raw-NNN.review.json`; thresholds are documented in [operating details](XPLANE_LIVE.md).

Example: “Good initial speed and bank control, but the recorded ILS localizer
was more than one dot from centre for 35% of valid final-approach samples.”
The positive opening is included only when the recorded initial speed and bank
support it. Unverified ToLiss raw ILS values do not contribute to the review.

X/Q or a flight change before touchdown uploads the capture collected so far;
timed sessions upload their full file when recording stops. Q waits for both
workers. Unconfirmed uploads retain the file and an error manifest; do not blindly
retry an uncertain upload, since it may already exist in Marple.
Captures and manifests are stored under `outputs/xplane/`, excluded from Git.

## Code layout

- `start_xplane_service.py`: launcher and single-instance lock.
- `xplane_session.py`, `xplane_tui.py`: terminal console, aircraft routing and session controller.
- `xplane_live.py`, `xplane_aircraft.py`, `xplane_signals.py`: UDP capture,
  aircraft identification, landing/reset detection and signal descriptions.
- `xplane_marple.py`, `env_loader.py`: SDK and Trino connections, local credentials.
- `xplane_report_signals.py`, `xplane_sampling.py`: report channels, distance derivation
  and low/high live sampling.
- `xplane_reset.py`: telemetry-confirmed landing pause and UDP repositioning.
- `xplane_file_upload.py`: immutable Parquet export and independent SDK file import.
- `xplane_review.py`: ten evidence-based simulator review templates and criteria.
- `xplane_verify.py`, `xplane_push_capture.py`: verification and recovery upload.
- `xplane_benchmark*.py`: optional recorded-data replay throughput diagnostics;
  these create explicitly named benchmark datasets when run.
- `tests/test_xplane*.py`: recorder tests without simulator/cloud access.

[Operating details](XPLANE_LIVE.md) · [Colleague setup and development](CONTRIBUTING.md)

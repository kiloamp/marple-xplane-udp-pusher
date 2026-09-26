# Flight Session Recorder

## Start and controls

Start X-Plane manually and double-click **Start A320 Landing Challenge.command**,
which launches both aircraft modes. Alternatively run:

```sh
python3 start_xplane_service.py --sample-mode low
```

The default uses **programmatic RREF subscriptions only**. Leave all X-Plane Data
Output network checkboxes off. The simulator's UDP command/RREF interface must
still be reachable (normally `127.0.0.1:49000`). `--host` is the simulator computer's
address, not an output destination. No FlyWithLua or AviTab plugin is required.
The recorder does not enable DATA output or change its destination in this mode.

| Control | Action |
| --- | --- |
| S | Start a timed session on a non-ToLiss aircraft |
| X | Stop and save early |
| R | Toggle LOW / HIGH for the **next flight** |
| Q / Ctrl+C | Stop safely and wait for finalization |

The console displays active Marple sample rate and next-flight selection. A flight
keeps its original rate so low/high comparisons use separate datasets. You can
also select `--sample-mode high` at startup. Metadata records the selected live
sample rate and the requested local capture rate.

## Rates and data paths

| Mode | Local RREF capture / detection | Samples sent to the eight-signal live preview |
| --- | --- | --- |
| LOW (default) | Requested 10 Hz | At most one sample per signal per 1-second bucket |
| HIGH | Requested 10 Hz | At most one sample per signal per 0.1-second bucket |

Actual simulator delivery can be lower than requested (for example about 9 Hz).
Sampling retains each selected value's original receiver timestamp and never fills
missing signals with old values. Different signals arriving in separate packets
are sampled independently. HTTP uploads batch those selected samples; they are
paced at least 1.05 seconds **after the previous response**, so 1 Hz signal sampling
does not promise one HTTP call or screen update per second.

The live preview contains only `airspeed_kias`, `altitude_msl_ft`, `roll_deg`,
`pitch_deg`, `heading_magnetic_deg`, `latitude_deg`, `longitude_deg` and
`vertical_speed_fpm`. Heading is magnetic; no extra control/status signals are
sent live. Full-rate file capture still includes all returned/derived channels.

The main console writes the full received, enriched telemetry to `session-NNN.jsonl`
and the selected upload data to `flight-NNN.jsonl`. Thus LOW preserves fine local
landing detail while reducing cloud volume. `packets.jsonl` contains original UDP
packets including identity replies. The legacy console keeps full detail in this
raw packet journal rather than a separate decoded session journal.

Subscription requests are paced on their own thread. They do not block the
receiver's timestamps or touchdown processing. Aircraft identity uses separate
1 Hz character subscriptions and is never expanded into numeric Marple signals.

## Curated landing-report signals

The set contains **52 standard RREF channels**, **10 additional ToLiss channels**
when that aircraft is confirmed, and up to **5 derived channels**: normally up to
57 signals for another aircraft or 67 for ToLiss. Only returned/derived values are
uploaded. Engine 2 and jet N1 fields are meaningful only on appropriate aircraft.

| Report purpose | Signals |
| --- | --- |
| GPS ground track | Latitude, longitude, true ground track, ground speed (m/s and kt), distance covered |
| Primary flight display | IAS, TAS, pitch, roll, true/magnetic heading, indicated altitude, geometric MSL altitude (m/ft), AGL (m/ft), indicated and true vertical speed (fpm), altimeter setting |
| Requested power and engine response | Throttle levers 1/2, flight-model engine 1 throttle setting, actual N1 1/2 |
| Configuration | Requested flap handle, actual flap deployment, speedbrake handle and actual deployment, gear handle |
| Landing event | Ground contact, normal G, ten gear compression slots, simulator pause/replay/time |
| Standard guidance | NAV1 lateral/vertical deviation in dots, horizontal/vertical validity, tuned frequency, FD mode and pitch/roll cues |
| Conditions | Effective wind direction/speed and aircraft mass |
| ToLiss-specific | FD1 and AP1/AP2 engagement, raw autothrust mode, raw ILS1 LOC/GS, captain LOC/GS/LS display flags, flap lever ratio |

Names, units, descriptions and dataref paths are in `xplane_live.py` and
`xplane_report_signals.py`. Every dataset receives signal descriptions and each
flight saves `flight-NNN.signals.json` for live signals and `raw-NNN.signals.json`
for the full analysis file.

Important distinctions for a future PDF report:

- A throttle lever request is not delivered thrust. N1 is engine response, not
  requested power. ToLiss autothrust can separate lever position from engine output.
- Geometric altitude and barometric indicated altitude have different meanings.
  True sink rate is derived from vertical velocity, separately from indicated VVI.
- `distance_covered_nm` integrates ground speed against simulator flight time from
  the recording's start. Pauses, replay, backwards time and gaps over two seconds
  are excluded. It is estimated horizontal distance, not runway distance or
  distance to threshold, and resets with every session.
- RREF returns float32 even for latitude/longitude. This is adequate for a demo map,
  but do not promise precision runway-centerline or touchdown-position measurements.
- NAV1 deviations require valid guidance and an actual ILS tuning. A zero does not
  prove a centered ILS. A custom aircraft may use its own guidance implementation.
- ToLiss raw ILS scale/sign and invalid-value rules are not yet verified. An observed
  `-10` must not be interpreted as a dot deviation. Its display flags are preserved
  separately; selecting LS display alone does not prove reception. Do not compute
  an ILS score until these semantics have been checked against the cockpit.
- Flap ratios are not universal Airbus CONF numbers. Autothrust enum codes also
  remain raw until their mapping is verified.

At touchdown the main console stores a fresh speed, sink-rate, attitude, position
and configuration snapshot in the session manifest, even in LOW mode. This data
collection prepares a future landing PDF; the service does **not** yet generate or
score a PDF report automatically. Such a report should also identify the landing
runway/threshold, account for aircraft limits, and label missing or invalid inputs.

Standard definitions were checked against the installed X-Plane 11.55
`Resources/plugins/DataRefs.txt` and [Laminar's dataref documentation](https://developer.x-plane.com/datarefs/).
ToLiss custom names were checked against the installed A319 v1.11 plugin and
read-only UDP responses. These references are aircraft/version-specific; see
[ToLiss support](https://toliss.com/pages/support). No aircraft files are distributed.

## Aircraft routing and flight lifecycle

The recorder requires two complete matching ICAO/author/description reads. Missing
identity never defaults to the non-ToLiss mode. ToLiss is identified by its brand,
or `Gliding Kiwi` author plus Airbus ICAO. Custom subscriptions are enabled only
for a confirmed ToLiss and cancelled when that identity is lost or changed.

| Aircraft | Flow | Metadata A/C Model | Dataset name |
| --- | --- | --- | --- |
| ToLiss Airbus | Full file upload at compression; live +20 s; wait for reset | Airbus A320 | A320_Landing_Challenge_XXX |
| Any other aircraft | S → 5 s preparation → 60 s flight → full file upload | Marple Acrobatic | Marple_Acrobatic_XXX |

Both use `X-Plane Fair Live` previews and `X-Plane Flight Files` analysis files,
with metadata `Capture Type: Live` / `Capture Type: SDK upload`, separate dataset
IDs and matching session names. They retain independent sequence counters,
Departure Airport `LEPA`, and Flight Type `Simulator Session`. The installed A319
retains the requested `Airbus A320` metadata label; actual ICAO and description
are recorded separately. Names are reserved locally before network creation;
gaps can occur on failed attempts. Do not run multiple writers on different
computers against this shared stream at once.

Landing detection arms after one observed second airborne with fresh running,
non-replay telemetry. The **first gear compression over 0.1 mm** latches touchdown;
bounces do not restart the timer. The first compression packet is included in an
immutable full-rate file snapshot, uploaded immediately on a separate worker.
**Twenty wall-clock seconds later** realtime recording stops and the preview
is cooled. The simulator is never automatically paused by the main console.
The user can taxi during upload; the file ends at first touchdown, while the
local session journal and live preview include the following 20 seconds.

After a landing, an upward altitude jump of at least 1000 ft arms a 15-second
reset window so situation loading and delayed ground-contact updates can settle.
Fresh altitude, airborne and non-replay signals may arrive in separate packets.
An airborne sample at 3000 ±150 ft MSL marks the next flight ready and is logged;
the main console waits for fresh unpaused telemetry. Reset and unpause manually.
An aircraft change ends the old capture before applying new metadata.

Timed mode sends `START` via ALRT, then `10 seconds to go` at 50 seconds. Its
60-second timer includes simulator pauses and dialogs: dismiss alerts promptly.
It stops at the deadline, starts its SDK file upload and finalizes realtime, then
waits for S again. It does not pause the simulator.

## Finalization and recovery

The realtime uploader drains the eight selected signals, appends the final batch, calls `cool()`,
waits for `FINISHED`, and compares cold-storage signal counts and first/last
receiver timestamps with `flight-NNN.jsonl` through Trino. Missing/incomplete
signals are restored from **that sampled upload journal**, not the full-rate
session journal, so repair does not silently turn LOW into HIGH.

The independent SDK worker copies only journal bytes committed at the trigger to
`raw-NNN.jsonl`, writes `<session-name>.parquet`, and calls `push_file()` on the
**files** stream `X-Plane Flight Files`. Its Parquet plugin uses `--shape long
--time-factor 1`, preserving sparse per-signal samples and integer nanosecond
timestamps. The worker waits through the upload/import queue handoff, adds signal
descriptions, and verifies all signal counts and time bounds through Trino without
repair. Its own `raw-NNN.json` manifest records the analysis dataset ID and status.
File verification permits at most 128 ns of timestamp rounding observed in the
Marple Parquet importer; all sample counts must match exactly. Local Parquet and
JSONL timestamps remain exact, and realtime verification retains zero tolerance.

Open the **SDK upload** dataset for analysis once it is `FINISHED`; it has no live
lifecycle and does not depend on the live preview cooling successfully. The console
shows its status independently. A failed or uncertain file upload is not blindly
retried; the local file remains available. X/Q, aircraft changes or an early reset
before touchdown upload the capture collected so far. Q waits for file workers as
well as live finalization. Credentials and all `outputs/` recordings stay out of Git.

The format and file-stream configuration follow the
[Marple file plugin documentation](https://docs.marpledata.com/docs/marple-db/datastreams/supported-file-types).

Recovery: `xplane_verify.py CAPTURE --dataset-id ID --repair` must be used with that
dataset's own `flight-NNN.jsonl`. `xplane_push_capture.py` supports uploading an
existing capture to a new empty dataset. The benchmark scripts replay recorded
captures into a separate `X-Plane Throughput Tests` stream and do not control the sim.

## Legacy options and compatibility

`--include-data` explicitly opts into the old checkbox-selected DATA feed and
configures its destination to this receiver on port 49005. It can add hundreds of
signals and is unnecessary for the curated report set. The old DATA dictionary is
retained so historical recordings and recovery remain usable.

`--landing-mode` selects the old legacy console, which still has its 5-second pause rule
and does not implement this split-file flow. Use the default `.command` for the new flow.
The legacy console supports startup `--sample-mode low|high`, but no interactive R key. Its cloud calls are
synchronous; use the default console for timing independent of upload latency.
`--landing-mode --wait-for-reset` attaches after a completed landing.

Tested with X-Plane 11.55r2 on macOS and Python 3.13; X-Plane 10 is unverified.
The project uses `fcntl` and `curses`; native Windows support is not implemented.

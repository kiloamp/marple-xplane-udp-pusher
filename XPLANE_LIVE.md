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
| ToLiss Airbus | Full file upload at compression; live +10 s; pause/reset; manual ISCS/unpause | Airbus A320 | A320_Landing_Challenge_XXX |
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
**Ten wall-clock seconds later** realtime recording stops and the preview
is cooled. Landing mode requests pause and then a reset to 3000 ft MSL.
The user can taxi during upload; the file ends at first touchdown, while the
local session journal and live preview include the following 10 seconds.

The reset destination is the first fresh airborne latitude/longitude, true heading
and true airspeed captured during this recording. `PREL` uses `loc_specify_lle=6`,
user aircraft 0 and elevation 914.4 metres. Pause is confirmed before sending PREL;
completion requires fresh position near the target, altitude within 50 ft and a
paused state. If PREL resumes the simulator, one new pause request is permitted
only after fresh post-reset telemetry confirms it is running. Pause confirmations
have a three-second timeout; repositioning has a twenty-second timeout. No blind
retries or automatic unpausing. The native packet matches X-Plane 11's bundled
`Exchanging Data with X-Plane` specification.

The operator handles ISCS configuration and unpauses. A ToLiss `.qps` scenario is
not loaded. Timed mode remains unchanged and does not pause or reposition.
X/Q cancels pending automatic reset commands. Aircraft changes cancel them too.
The automatic reset is independent of live finalization and SDK file upload.
If a command cannot be confirmed, use X-Plane/ISCS manually; inspect the session
manifest's `automatic_reset` status. A confirmed reset waits for manual unpause
before a new recording begins.

For manual reset detection, an upward altitude jump of at least 1000 ft arms a 15-second
reset window so situation loading and delayed ground-contact updates can settle.
Fresh altitude, airborne and non-replay signals may arrive in separate packets.
An airborne sample at 3000 ±150 ft MSL marks the next flight ready and is logged;
the main console waits for fresh unpaused telemetry. Unpause manually after ISCS setup.
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

## Analysis names and review criteria

Local files remain `<session-name>.parquet`; SDK `push_file(file_name=...)` gives
the uploaded analysis dataset the clean `<session-name>` name, without a suffix.
It remains type `files`, with `Capture Type: SDK upload`. Realtime preview names
are `<session-name>.live`. Sequence numbering recognises clean names and historic
`.parquet`/`.live` names. Existing datasets are not renamed.

After recording ends, the compact console asks for a participant name (Enter saves,
Esc skips). It updates **Participant Name** on the SDK file dataset via
`Dataset.update_metadata`, preserving existing metadata, then reads it back to
confirm. The worker waits for file upload completion and retains that flight's
identity even during the next recording. The original Parquet binary is not
rewritten; local manifests and `raw-NNN.participant.json` retain the name/status.
N edits the last completed flight or retries a failed update; D toggles diagnostic
information. Submitted updates finish before Q exits; unanswered prompts do not
block exit. Failed/pending updates are retained locally, but not automatically
replayed after a service restart.

Each SDK upload contains one `Flight Review` metadata string, selected from ten
templates. The review is computed from the immutable raw snapshot before upload,
also embedded in Parquet metadata and saved with supporting measurements in
`raw-NNN.review.json`. It requires fresh, unpaused, non-replay, airborne samples.
Observations are limited to one per second to avoid weighting high packet rates
more heavily. Final approach means recorded height 100–1000 ft above local terrain.

Rules below are evaluated in priority order. They are configurable code thresholds
for this simulator challenge, not aircraft-specific operating limits or VREF checks.

| Review | Evidence |
| --- | --- |
| Incomplete landing | At least 10 seconds recorded, but no confirmed first-compression ending (includes timed sessions) |
| Insufficient data | Otherwise, fewer than 10 usable final-approach seconds or no fresh pre-contact bank/descent sample |
| Brisk descent | Pre-contact descent greater than 600 ft/min |
| Bank at touchdown | Absolute pre-contact bank greater than 5 degrees |
| Late gear command | Gear handle up in more than 20% of at least five observations below 500 ft; does not infer gear lock |
| Speed variation | Final-approach airspeed 90th–10th percentile spread greater than 20 kt |
| Roll corrections | Absolute bank 90th percentile greater than 10 degrees |
| Localizer deviation | More than 20% of at least 10 valid observations beyond one dot |
| Glideslope deviation | More than 20% of at least 10 valid observations beyond one dot |
| Steady approach | Enough measured data and none of the preceding thresholds exceeded |

Initial-control praise requires at least five observations above 1000 ft in the
first 20 seconds, bank 90th percentile at most 8 degrees and airspeed percentile
spread at most 15 kt. Otherwise the review uses a neutral opening.

ILS observations require a fresh ILS-channel NAV1 frequency, horizontal and vertical
validity flags, and finite deviations within ±5 dots. ILS channel selection follows
the [FAA AIM channel table](https://www.faa.gov/air_traffic/publications/atpubs/aim_html/chap1_section_1.html).
Missing guidance is explicitly unassessed. Custom ToLiss raw ILS/display flags are
never scored because their scaling/validity mapping has not been confirmed.
Reviews cover recorded approach through first contact; the snapshot cannot assess
bounces, rollout or peak post-contact loads. No new realtime signals or SDK calls
are added for reviews.

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

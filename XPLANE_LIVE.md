# Flight Session Recorder

Double-click **Start A320 Landing Challenge.command** as before. It now opens a
terminal console that chooses the flow from the loaded aircraft; FlyWithLua is not needed. X-Plane can be started
before or after the console.

The recorder reads ICAO, author and description over UDP RREF byte arrays at 1 Hz.
It requires two complete matching identity reads; unknown identity never defaults to
an acrobatic session. The console shows the detected aircraft and selected mode.

| Detected aircraft | Flow | A/C Model | Dataset names |
| --- | --- | --- | --- |
| ToLiss Airbus | Automatic landing capture, first compression +15 seconds, then wait for a reset to 3000 ft MSL | Airbus A320 | A320_Landing_Challenge_NNN |
| Any other aircraft | Operator-started 5-second countdown, 60-second flight, warning, pause, finalize | Marple Acrobatic | Marple_Acrobatic_NNN |

ToLiss is matched by its brand in the author/description, or the installed aircraft's
`Gliding Kiwi` author plus an Airbus ICAO code. The installed A319 identifies itself
this way; it retains the user-requested **Airbus A320** metadata label for the challenge.
ICAO A320 alone does not incorrectly classify other vendors as ToLiss.

Both modes use the existing `X-Plane Fair Live` stream, separated by dataset names and
metadata. Their counters are independent. Departure Airport remains LEPA and Flight
Type remains Simulator Session. An aircraft change or unavailable identity ends the
current capture without pausing the newly loaded aircraft; its dataset finishes before
the next aircraft's metadata is used. Identity detection runs separately from numeric
telemetry, so identity bytes do not become hundreds of extra Marple signals.

For **any non-ToLiss aircraft**:

- Press **S** to start recording and pushing data. The console waits for fresh,
  unpaused, non-replay flight telemetry, then counts down five seconds while preparing
  the Marple dataset. Slow cloud setup may extend preparation.
- X-Plane receives **START** via UDP ALRT, then the 60-second recording begins.
- At 50 seconds it receives **10 seconds to go**.
- At 60 seconds recording stops. The recorder requests a pause only if fresh
  telemetry says the simulator is not already paused, then checks for confirmation.
- Pending samples finish uploading. The dataset is finalized and cold storage is
  verified/repaired from the local journal. The console shows completion or an error.
- Set up and unpause the next flight, then press **S** again. There is no automatic
  3000-foot reset trigger in timed mode.
- **X** stops and saves early. **Q** (or Ctrl+C) stops safely, waits for finalization,
  and exits. The automatic pause applies to the timed end; X/Q do not pause the sim.

The timer measures **60 wall-clock seconds**, including manual pauses and dialogs.
Dismiss alerts promptly. ALRT and pause packet construction have been checked against
the installed simulator protocol/commands and unit tested; their actual presentation
and behavior still need a flight test because X-Plane was closed during development.
UDP commands are not acknowledged directly. A missing pause confirmation is reported;
the recorder never blindly toggles pause again. Recording stops regardless.

The last full selection produced 480 telemetry signals. The selection is controlled
by X-Plane's network-output checkboxes plus the recorder's 26 core RREF signals.
The table above defines the two profiles.

## Reducing signals

Uncheck unwanted groups in **X-Plane Settings → Data Output → network/UDP output**.
The recorder automatically uploads only the DATA fields it actually receives; it
does not re-enable groups or fill missing fields with old values. Its signal
dictionary describes possible fields and is not a fixed upload list.

The 26 core signals are separately requested through RREF at 10 Hz, independently
of those checkboxes. They include altitude, gear compression, ground state, pause
and replay state. Aircraft identity is also requested separately and is not sent
to Marple as numeric signals. Landing detection, the 3000-ft reset watcher and the
timed session flow therefore continue to work when optional DATA groups are disabled.

The previous selection contained 454 optional DATA fields plus 26 core signals.
Keeping 20% of those optional fields gives approximately 117 total signals. For
roughly 100 total, keep about 74 optional fields. Each checkbox selects a group with
up to eight populated fields, so percentages of checkboxes are only an approximation.

For a clean comparison, stop/save with **Q**, wait for finalization, change the
checkboxes, then restart the recorder and begin a new flight. Changes also take
effect while running, but previously captured signals stay in that flight and the
console's **Signals seen** count is cumulative for the service run. Restarting makes
the count reflect the reduced selection. Existing datasets are preserved.

Fewer fields reduce upload size, storage work and potential restoration uploads.
This can improve responsiveness, but does not guarantee one-second updates: the
recorder still applies request pacing, and HTTP latency contributes to the interval.

## Viewing status inside X-Plane

The current console runs in Terminal. Standard AviTab does not embed that terminal.
The separate [AviTab Browser add-on](https://github.com/rswilem/avitab-browser)
documents X-Plane 11 and 12 support and could display a local web status/control
page. This would require adding a web adapter to the recorder and checking the
add-on with the installed aircraft/platform; it is not implemented here.

Another option is a FlyWithLua floating window that reads recorder status and sends
start/stop commands. The installed FlyWithLua includes floating-window examples.
That integration also requires implementation; ordinary UDP recording needs no plugin.

## Upload pacing and records

The server returned `Rate limit exceeded: 1 per 1 second` even with 50 signals.
The recorder therefore waits at least **1.05 seconds after an append response** before
sending another append, including the final batch. It batches intervening samples
without reducing their sampling frequency. Real upload intervals include HTTP latency
and can exceed one second. Changing signal count alone does not remove this limit.

Timers, simulator commands and raw UDP capture run independently from blocking cloud
requests. Each session saves both a controller journal (`session-NNN.jsonl`) and the
uploader journal (`flight-NNN.jsonl`), with manifests beside them. `session.log` records
operator/connection messages. Local journals remain available on cloud failures.

Benchmarks are explicitly labeled recorded-flight replays under the separate Marple
stream **X-Plane Throughput Tests**, with reports under `outputs/xplane/benchmarks/`.
They measure ingestion responses, pacing and cold-storage counts; they do not measure
Marple Insight browser rendering or simulator FPS.

## Original landing behavior

Run `python3 start_xplane_service.py --landing-mode` for the original 15-second
post-compression cutoff and automatic 3000-foot reset watcher.
The historical setup and validation details below apply to that mode.

# Legacy landing mode and previous flight validation

One Marple **realtime datastream**, `X-Plane Fair Live`, contains one **dataset per flight**.
Ending a flight flushes its last batch, calls `dataset.cool()`, and verifies `FINISHED`.
The next flight gets a new dataset; finished datasets are never appended again.

Every flight dataset has these metadata fields:

| Field | Value |
| --- | --- |
| A/C Model | Airbus A320 |
| Departure Airport | LEPA |
| Flight Type | Simulator Session |

Names are `A320_Landing_Challenge_001`, `A320_Landing_Challenge_002`, and so on.
Numbering continues across recorder restarts using both existing Marple names and
`outputs/xplane/challenge-sequence.json`. A reserved number is not reused after a
failed creation attempt, so failures can leave a gap. Local-only tests have a separate
counter. The two completed test flights are now 001 (1163) and 002 (1165).

## Start on this Mac

**FlyWithLua is not required.** The recorder is a separate Python process using
X-Plane's built-in RREF and DATA UDP interfaces. Existing FlyWithLua scripts can remain installed.

1. In Finder, double-click **Start A320 Landing Challenge.command** in this project.
2. Start X-Plane yourself and load the A320 at LEPA. Either startup order works:
   the launcher waits for telemetry if the simulator is not yet ready.
3. Keep the Terminal window open while flying. The recorder captures the landing,
   ends 15 seconds after initial gear compression, and waits for your reset to 3,000 ft.
4. To stop, press **Ctrl+C** in that Terminal and wait for finalization to complete.

The launcher runs one instance at a time and loads this project's existing `.env.local`.
It does not install a login/background service or start X-Plane automatically.
To launch from Terminal instead, run this from the project folder:

```sh
python3 start_xplane_service.py
```

If the aircraft is already on the ground after a demonstration and the next action
will be a reset, use `python3 start_xplane_service.py --landing-mode --wait-for-reset`.

## Verified connection

The local simulator is X-Plane **11.55r2**, reachable at `127.0.0.1:49000`.
Its RREF UDP interface successfully returned all 26 requested signals at approximately
10 Hz, including ten landing-gear compression channels. The first sample was paused
and airborne. RREF needs no output checkboxes. The additional DATA signals use the network output checkboxes and a destination configured by the recorder; no plugins are needed.

`192.168.0.1` was the configured **output destination**, not the simulator address.
For a remote simulator, `--host` must be the IP of its computer, and UDP replies
must be able to reach the recorder. On this Mac, use the loopback address above.

## Landing rule

1. Start recording when telemetry first arrives.
2. Arm landing detection after observing at least one second airborne, with fresh
   unpaused/non-replay status. Initial runway compression does not trigger it.
3. The first measured compression above **0.1 mm** on any gear latches touchdown.
   This small threshold excludes numeric noise; verify it with the chosen aircraft.
4. End **15 wall-clock seconds** after that first compression. Bounces do not restart
   the countdown. At 10 Hz, touchdown detection is limited by approximately 0.1-second
   sampling plus UDP/network delay. The tail also expires if the sim is paused.
5. In continuous mode, discard intervening samples from flight datasets while waiting
   for the next simulator reset. Raw UDP packets continue to be journaled locally.
6. After landing, an altitude jump of at least 1,000 ft arms a three-second reset
   window. An airborne sample within **3,000 +/-150 ft MSL** starts the next dataset.
   This accommodates an actual observed reset: a transient 22,720-ft packet followed
   by 3,035 ft. A gradual climb through 3,000 ft does not trigger it. Set another target
   with `--reset-altitude-ft`. If resetting before the landing tail completes, the old
   flight ends immediately with that reason, so the flights remain separate.
7. `--auto-reset` optionally also watches backwards jumps in the flight timer. The
   tested repositioning did **not** reset that timer, so altitude is the default fair trigger.

## Run

Local connection check (no Marple writes):

```sh
python3 xplane_live.py --seconds 15
```

One short live-ingestion/finalization test:

```sh
python3 xplane_live.py --live --seconds 20
```

Fair mode, landing cutoff and repeated flights:

```sh
python3 xplane_live.py --live --landing --continuous --seconds 0
```

Omit `--live` to validate the complete flight sequence locally first. Ctrl+C ends
the active recording and attempts finalization. If reset detection is unavailable,
run one flight at a time with `--live --landing --seconds 0`, then rerun after resetting.
The program never moves the aircraft or triggers a simulator reset. If starting the
recorder while already landed after a demonstration, add `--wait-for-reset`.

## Credentials and tested results

The existing `xplane_marple.get_sdk_db()` loads `.env.local`. `MARPLE_DB_API_TOKEN` takes
precedence over `MARPLE_API_TOKEN`; an already-exported environment value takes precedence
over the file. Keep tokens out of tracked source and chat.

Realtime access was enabled during testing. Stream **18**, `X-Plane Fair Live`, is ready.
Both test flights have been uploaded and finalized:

- **1163**, `A320_Landing_Challenge_001`: 3,299 samples per signal,
  26 signals, 85,774 data points verified in cold storage. Last sample was 14.951 seconds
  after first gear compression, just before the 15-second boundary.
- **1165**, `A320_Landing_Challenge_002`: recovered from the real raw
  journal after the initial altitude watcher missed the transient. The corrected watcher
  recognizes the reset at 3,035 ft when replayed against those packets. It contains
  296 captured sample packets and ends 14.979 seconds after first compression.
- **1161**, `xplane-landing-validation`, is a finalized **partial connectivity test**,
  not the complete landing. Its metadata identifies it as incomplete diagnostic data.

The initial watcher bug was fixed and tested against the recorded reset; a subsequent
fresh simulator reset has not yet been tested. These results describe the initial test; subsequent runs are recorded in the per-flight manifests.

## Data and failure handling

All artifacts live in `outputs/xplane/<UTC-run-id>/`:

- `signals.json`: signal names, exact datarefs, and units.
- `packets.jsonl`: raw UDP packets with receiver UTC and monotonic timestamps.
- `flight-NNN.jsonl`: decoded samples assigned to each flight.
- `flight-NNN.json`: dataset ID, state, confirmed upload count, touchdown/cutoff timestamps,
  and end reason where available.

RREF requests have a five-byte header, followed by little-endian 32-bit frequency,
32-bit request index, and a 400-byte null-padded dataref name. Replies have the same
header length, followed by pairs of 32-bit index and float. An index belongs to this
recorder's signal list, avoiding X-Plane's version-dependent DATA group mappings.
Subscriptions are canceled on clean exit. RREF returns float32 values even for double
datarefs, so latitude/longitude are suitable for this demo rather than precision survey work.

Each signal retains its receive timestamp; missing values are not forward-filled.
Live uploads are batched every two seconds to reduce API request load.
Raw capture runs separately from HTTP uploads. UDP delivery itself is not guaranteed.
An uncertain append is marked `APPEND_UNCONFIRMED` and is not blindly retried. The
recorder continues writing locally. At flight end it cools the dataset and verifies
each signal's sample count and first/last timestamps through Trino cold storage.
Missing or incomplete signals are replaced from the authoritative local journal using
the SDK, then checked again. This also handles the two missing channels observed in
the first completed import. A cooling timeout leaves the dataset ID and last known
state in the manifest; do not treat it as confirmed completion. If initial Marple
dataset setup fails, the complete local capture remains available for a later upload.

`xplane_push_capture.py` can upload a captured flight to a fresh, empty live dataset.
Use `--follow` to follow a local recording until its landing cutoff. Its journal guards
against accidental repeat uploads; inspect any uncertain batch before resuming.
`xplane_verify.py CAPTURE --dataset-id ID --repair` verifies a finalized dataset and
restores incomplete signals from that capture when needed. Use it only with that
capture's own dataset.

Protocol and units were checked against the simulator's bundled
`Instructions/X-Plane SPECS from Austin/Exchanging Data with X-Plane.rtfd`
and `Resources/plugins/DataRefs.txt`. SDK lifecycle was checked against installed
`marpledata` 3.4.0.dev1. This implementation has not yet been verified on X-Plane 10.

## Expanded checkbox-selected UDP capture (X-Plane 11.55)

The service receives both RREF (the original 26 signals, including landing detection)
and DATA (the groups selected for network output in X-Plane). It sets the DATA
destination to this computer on UDP port 49005 every five seconds. Keep the network
output checkboxes enabled; Disk output and FlyWithLua are not required.

The inspected selection contains 66 groups and 454 populated DATA fields. Together
with RREF this produces 480 distinct signals. Additional selections are captured
automatically. Unknown groups/slots receive a stable raw name and an explicit
unverified description until a mapping is added. Blank -999 slots and nonfinite
values are omitted; original packet bytes remain in the journal.

`xplane_signals.py` contains the dictionary. DATA names use `data_` plus descriptive
snake_case names, such as `data_engine_1_n1_pct`. The prefix distinguishes their
independent sample times from RREF channels. Gear indices remain zero-based; engine
indices are one-based. All eight engine/battery slots are retained, including unused
slots with simulator defaults. These do not imply the aircraft has eight engines.

Each signal receives its description and unit in Marple before its first append.
Every flight also saves `flight-NNN.signals.json` with descriptions and provenance.
Descriptions were researched in Laminar's online DATA table and developer articles,
then slot order was checked against the output labels bundled in the installed
11.55 executable. The online table itself describes 10.30 and is incomplete.
Aircraft-configured fuel quantities/flow, torque and temperature scales are labeled
`aircraft-configured`; undocumented thrust-vector, cyclic and wing-force units are
`unspecified`. Raw values are preserved without speculative conversion.

Sources:
- https://www.x-plane.com/kb/data-set-output-table/
- https://developer.x-plane.com/datarefs/
- https://developer.x-plane.com/article/movingtheplane/
- https://developer.x-plane.com/article/using-the-correct-wing-datarefs/
- https://developer.x-plane.com/article/vacuum-systems/
- https://developer.x-plane.com/article/preconfigured-autopilots-and-other-autopilot-changes-in-11-30/

Start as before by double-clicking `Start A320 Landing Challenge.command`. Stop with
Ctrl+C and wait for finalization. The 15-second first-compression cutoff, 3000-ft
reset watcher, numbering and flight metadata are unchanged.

Expanded-capture validation on 2026-09-22 UTC: flight 004 (dataset 1169)
received 454 additional channels from 2,992 inspection packets, totaling 1,358,368
additional samples. Every channel's count and first/last timestamp matched cold
storage. These extra channels begin partway through flight 004 when inspection
started; earlier measurements were not fabricated. Its original 26 channels also
passed verification. The maintenance restart began flight 005 (dataset 1170), with
480 signals live and all 480 units/descriptions confirmed through the Marple API.
The packet, landing/reset, metadata, naming and bulk recovery suite has 24 passing tests.
Bulk recovery avoids issuing hundreds of individual signal-poll requests.

Aircraft-routing validation: 43 automated tests pass, covering both profiles,
fragmented/out-of-order identity packets, missing/stale identity, metadata forwarding,
landing cutoff/reset, no timed pause for ToLiss, and switching aircraft without
relabeling the previous capture. The console was smoke tested while X-Plane was
closed. Live aircraft identity and ALRT/pause presentation still need simulator validation.

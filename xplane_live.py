"""X-Plane RREF telemetry capture, optionally streamed to Marple.

Protocol: X-Plane 11/Instructions/X-Plane SPECS from Austin/
Exchanging Data with X-Plane.rtfd. Units: Resources/plugins/DataRefs.txt.
Landing completion requests a pause over UDP; no simulator reset commands are sent.
Default capture uses programmatic RREF subscriptions only. Legacy DATA capture is opt-in.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import queue
import select
import re
import socket
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from xplane_signals import decode_data, signal_definitions
from xplane_aircraft import AircraftIdentity, aircraft_mode, subscribe as subscribe_identity
from xplane_report_signals import REPORT_RREFS, TOLISS_RREFS, TOLISS_NAMES
from xplane_sampling import LiveSampler, ReportTelemetry

FLIGHT_METADATA = {
    "A/C Model": "Airbus A320",
    "Departure Airport": "LEPA",
    "Flight Type": "Simulator Session",
}
CHALLENGE_PREFIX = "A320_Landing_Challenge_"
LANDING_TAIL_SECONDS = 5.0
LANDING_END_REASON = f"{LANDING_TAIL_SECONDS:g} seconds after first gear compression"


def command_packet(command):
    return b"CMND\0" + command.encode("ascii") + b"\0"


def pause_simulator(receiver, paused, log=print):
    """Toggle once only when the caller has fresh, unpaused telemetry."""
    if paused == 1:
        return "already_paused"
    if paused != 0:
        log("Pause not sent: no fresh pause state. Pause X-Plane manually.", flush=True)
        return "unavailable"
    try:
        receiver.sock.sendto(command_packet("sim/operation/pause_toggle"), receiver.target)
    except OSError:
        log("Pause request failed. Pause X-Plane manually; recording has stopped.", flush=True)
        return "failed"
    log("Pause requested; awaiting telemetry confirmation.", flush=True)
    return "requested"


def next_remote_number(datasets, prefix=CHALLENGE_PREFIX):
    numbers = []
    for dataset in datasets:
        match = re.fullmatch(re.escape(prefix) + r"(\d+)(?:\.(?:parquet|live))?", getattr(dataset, "path", "") or "")
        if match:
            numbers.append(int(match[1]))
    return max(numbers, default=0) + 1


def reserve_challenge_name(state_path: Path, namespace: str, minimum=1, prefix=CHALLENGE_PREFIX):
    """Reserve before network mutation, so restarts cannot reuse an attempted name."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("a+") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.seek(0)
        raw = file.read()
        state = json.loads(raw) if raw else {}
        number = max(minimum, state.get(namespace, 1))
        state[namespace] = number + 1
        file.seek(0)
        file.truncate()
        json.dump(state, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    return f"{prefix}{number:03d}"

# name, dataref, unit. Indices are assigned by us, not X-Plane's DATA table.
SIGNALS = [
    ("flight_time_s", "sim/time/total_flight_time_sec", "s"),
    ("paused", "sim/time/paused", "boolean"),
    ("replay", "sim/time/is_in_replay", "boolean"),
    ("airspeed_kias", "sim/flightmodel/position/indicated_airspeed", "kt"),
    ("true_airspeed_mps", "sim/flightmodel/position/true_airspeed", "m/s"),
    ("groundspeed_mps", "sim/flightmodel/position/groundspeed", "m/s"),
    ("altitude_msl_m", "sim/flightmodel/position/elevation", "m"),
    ("altitude_agl_m", "sim/flightmodel/position/y_agl", "m"),
    ("latitude_deg", "sim/flightmodel/position/latitude", "deg"),
    ("longitude_deg", "sim/flightmodel/position/longitude", "deg"),
    ("pitch_deg", "sim/flightmodel/position/theta", "deg"),
    ("roll_deg", "sim/flightmodel/position/phi", "deg"),
    ("heading_true_deg", "sim/flightmodel/position/psi", "deg"),
    ("normal_g", "sim/flightmodel/forces/g_nrml", "g"),
    ("throttle_ratio", "sim/flightmodel/engine/ENGN_thro[0]", "ratio"),
    ("on_ground", "sim/flightmodel/failures/onground_any", "boolean"),
]
SIGNALS += [(f"gear_{i}_compression_m", f"sim/flightmodel2/gear/tire_vertical_deflection_mtr[{i}]", "m") for i in range(10)]
SIGNALS += [(name, ref, unit) for name, ref, unit, _ in REPORT_RREFS + TOLISS_RREFS]


class LandingCut:
    """Arm after observed flight; latch FIRST compression, including a bounced landing.

    Uses receiver monotonic seconds, so the default tail is 5 wall-clock seconds.
    A 0.1 mm threshold excludes floating-point noise; no landing debounce shifts
    the touchdown instant. Missing/old airborne or pause samples cannot arm it.
    """

    def __init__(self, tail=LANDING_TAIL_SECONDS):
        self.tail = tail
        self.latest = {}
        self.airborne_since = None
        self.armed = False
        self.touchdown = None

    def observe(self, now, values):
        self.latest.update({name: (value, now) for name, value in values.items()})
        def fresh(name):
            value, when = self.latest.get(name, (None, -math.inf))
            return value if now - when <= 1 else None
        if fresh("paused") != 0 or fresh("replay") != 0:
            self.airborne_since = None
            return
        if not self.armed:
            if fresh("on_ground") == 0:
                if self.airborne_since is None:
                    self.airborne_since = now
                if now - self.airborne_since >= 1:
                    self.armed = True
            else:
                self.airborne_since = None
        if self.armed and self.touchdown is None:
            if any(name.startswith("gear_") and name.endswith("_compression_m") and value > 0.0001 for name, value in values.items()):
                self.touchdown = now

    def expired(self, now):
        return self.touchdown is not None and now >= self.touchdown + self.tail


class AltitudeReset:
    """Recognize a reset teleport after landing, not a normal climb through 3000 ft."""

    def __init__(self, target_ft=3000.0, tolerance_ft=150.0, jump_ft=1000.0, settle_seconds=15.0):
        self.target_ft = target_ft
        self.tolerance_ft = tolerance_ft
        self.jump_ft = jump_ft
        self.settle_seconds = settle_seconds
        self.latest = {}
        self.previous_ft = None
        self.landed = False
        self.pending_until = None

    def observe(self, values, now=None):
        now = time.monotonic() if now is None else now
        self.latest.update({name: (value, now) for name, value in values.items()
                            if name in {'altitude_msl_m', 'on_ground', 'replay'}})
        def fresh(name):
            value, at = self.latest.get(name, (None, -math.inf))
            return value if now - at <= 1 else None
        if fresh('replay') == 1:
            self.previous_ft = None
            self.pending_until = None
            return False
        altitude_m = values.get("altitude_msl_m")
        if altitude_m is not None:
            altitude_ft = altitude_m / 0.3048
            if (self.landed and self.previous_ft is not None
                    and altitude_ft - self.previous_ft >= self.jump_ft):
                # Loading can emit an impossible altitude, then update ground
                # contact later. Allow settling without requiring one UDP packet.
                self.pending_until = now + self.settle_seconds
            self.previous_ft = altitude_ft
        altitude_m = fresh('altitude_msl_m')
        if altitude_m is None:
            return False
        altitude_ft = altitude_m / 0.3048
        reset = (self.landed and self.pending_until is not None and now <= self.pending_until
                 and abs(altitude_ft - self.target_ft) <= self.tolerance_ft
                 and fresh('on_ground') == 0 and fresh('replay') == 0)
        if reset:
            self.landed = False
            self.pending_until = None
        return reset


def subscription(index: int, frequency: int) -> bytes:
    return b"RREF\0" + struct.pack("<ii400s", frequency, index, SIGNALS[index][1].encode())


def decode(packet: bytes) -> dict[str, float]:
    if len(packet) < 5 or packet[:4] != b"RREF" or (len(packet) - 5) % 8:
        raise ValueError("Not a complete RREF packet")
    return {
        SIGNALS[index][0]: value
        for index, value in struct.iter_unpack("<if", packet[5:])
        if 0 <= index < len(SIGNALS) and math.isfinite(value)
    }


def reset_detected(previous: float | None, current: float | None) -> bool:
    # Candidate only: must be verified against the operator's actual reset action.
    return previous is not None and current is not None and current < previous - 1.0


class Receiver:
    """Keep draining UDP while an HTTP upload is in progress; journal raw packets."""

    def __init__(self, host: str, port: int, hz: int, folder: Path, data_port=49005, include_data=False):
        self.target = (socket.gethostbyname(host), port)
        self.hz = hz
        self.events = queue.Queue(maxsize=20000)
        self.stop = threading.Event()
        self.failure = None
        self.identity = AircraftIdentity()
        self.toliss_active = False
        self.include_data = include_data
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.data_sock = None
        self.data_port = data_port
        if include_data:
            self.data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.data_sock.bind(("0.0.0.0", data_port))
            route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            route.connect(self.target)
            self.data_address = route.getsockname()[0]
            route.close()
            self.data_port = self.data_sock.getsockname()[1]
        self.journal = (folder / "packets.jsonl").open("x", buffering=1)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.subscription_thread = threading.Thread(target=self.maintain_subscriptions, daemon=True)

    def subscribe(self, hz):
        toliss = bool(hz and aircraft_mode(self.identity.snapshot(time.monotonic())) == 'landing')
        for index, (name, _, _) in enumerate(SIGNALS):
            if name not in TOLISS_NAMES or toliss:
                self.sock.sendto(subscription(index, hz), self.target)
            elif self.toliss_active:
                self.sock.sendto(subscription(index, 0), self.target)
            time.sleep(.003)
        subscribe_identity(self.sock, self.target, 1 if hz else 0)
        self.toliss_active = toliss

    def maintain_subscriptions(self):
        """Pace requests without delaying receive timestamps or touchdown detection."""
        try:
            last_subscribe = 0.0
            while not self.stop.is_set():
                toliss = aircraft_mode(self.identity.snapshot(time.monotonic())) == 'landing'
                if time.monotonic() - last_subscribe > 5 or toliss != self.toliss_active:
                    self.subscribe(self.hz)
                    if self.include_data:
                        self.sock.sendto(b"ISE4\0" + struct.pack("<i16s8si", 64,
                            self.data_address.encode(), str(self.data_port).encode(), 1), self.target)
                    last_subscribe = time.monotonic()
                self.stop.wait(.1)
        except Exception as exc:
            self.failure = type(exc).__name__

    def run(self):
        try:
            self.subscription_thread.start()
            while not self.stop.is_set():
                sockets = [self.sock] + ([self.data_sock] if self.data_sock else [])
                readable, _, _ = select.select(sockets, [], [], 0.25)
                if not readable:
                    continue
                packet, source = readable[0].recvfrom(65535)
                if source[0] != self.target[0]:
                    continue
                now = time.time_ns()
                mono = time.monotonic()
                self.identity.observe(packet, mono)
                self.journal.write(json.dumps({"time": now, "monotonic": mono, "packet_hex": packet.hex()}) + "\n")
                try:
                    if packet[:4] == b"DATA":
                        if not self.include_data:
                            continue
                        values = decode_data(packet)
                    else:
                        values = decode(packet)
                        if not self.toliss_active:
                            values = {k: v for k, v in values.items() if k not in TOLISS_NAMES}
                except ValueError:
                    continue
                if values:
                    self.events.put_nowait((now, mono, values))
        except Exception as exc:
            self.failure = type(exc).__name__

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        if self.subscription_thread.ident is not None:
            self.subscription_thread.join(timeout=3)
        try:
            self.subscribe(0)
        finally:
            self.sock.close()
            if self.data_sock:
                self.data_sock.close()
            self.journal.close()


class Flight:
    def __init__(self, folder: Path, number: int, stream=None, dataset_name=None, log=print, metadata=None):
        self.log = log
        self.shared_stream = stream if getattr(stream, "shared_live", False) is True else None
        self.metadata = dict(FLIGHT_METADATA if metadata is None else metadata)
        self.path = folder / f"flight-{number:03d}.jsonl"
        self.file = self.path.open("x", buffering=1)
        self.manifest_path = folder / f"flight-{number:03d}.json"
        self.manifest = {"name": dataset_name or f"{CHALLENGE_PREFIX}{number:03d}",
                         "metadata": dict(self.metadata), "state": "CAPTURING", "packets": 0}
        self.pending = []
        self.dataset = None
        self.failed = False
        self.setup_failed = False
        self.finish_attempted = False
        self.last_flush = time.monotonic()
        self.next_append = 0.0
        self.defined_signals = set()
        self.observed_signals = set()
        self.save()
        if stream is not None:
            try:
                self.dataset = stream.add_dataset(self.manifest["name"], metadata=dict(self.metadata))
                self.manifest["dataset_id"] = self.dataset.id
                self.save()

            except BaseException as exc:
                self.failed = True
                self.setup_failed = True
                self.manifest["state"] = "SETUP_FAILED"
                self.manifest["upload_error"] = type(exc).__name__
                self.save()
                if not isinstance(exc, Exception):
                    self.file.close()
                    raise
                self.log("Marple setup failed; continuing local flight capture.", flush=True)
        self.log(f"Started {self.manifest['name']} | dataset={self.manifest.get('dataset_id', 'local only')}", flush=True)

    def save(self):
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2) + "\n")

    def add(self, timestamp, values):
        sample = {"time": timestamp, **values}
        new_names = set(values) - self.observed_signals
        if new_names:
            self.observed_signals.update(new_names)
            self.path.with_suffix(".signals.json").write_text(json.dumps(
                signal_definitions(self.observed_signals, SIGNALS), indent=2) + "\n")
            self.manifest["signal_count"] = len(self.observed_signals)
        self.file.write(json.dumps(sample) + "\n")
        if not self.failed:
            self.pending.append(sample)
        self.manifest["packets"] += 1
        self.manifest.setdefault("first_time_ns", timestamp)
        self.manifest["last_time_ns"] = timestamp

    def flush(self):
        from xplane_values import signal_row
        if self.failed:
            self.pending.clear()
            self.last_flush = time.monotonic()
            self.save()
            return
        if not self.pending:
            return
        if self.dataset is not None:
            import pandas as pd
            # Long format preserves each signal's own arrival time; no fill-forward.
            rows = [signal_row(sample['time'], name, value)
                    for sample in self.pending for name, value in sample.items() if name != "time"]
            self.manifest["state"] = "APPENDING"
            self.save()
            try:
                new_names = {row["signal"] for row in rows} - self.defined_signals
                if new_names:
                    self.dataset.upsert_signals(signal_definitions(new_names, SIGNALS))
                    self.defined_signals.update(new_names)
                # Server confirmed: at most one append per second. Pace from the
                # previous response so network jitter/final flush cannot cause bursts.
                pacing = self.shared_stream if self.shared_stream is not None else self
                time.sleep(max(0, pacing.next_append - time.monotonic()))
                self.dataset.append(pd.DataFrame(rows), shape="long")
                pacing.next_append = time.monotonic() + 1.05
            except BaseException as exc:
                # An HTTP timeout can mean the batch was accepted. Do not blindly retry.
                self.failed = True
                self.manifest["state"] = "APPEND_UNCONFIRMED"
                self.manifest["upload_error"] = type(exc).__name__
                self.manifest["http_status"] = getattr(getattr(exc, "response", None), "status_code", None)
                self.pending.clear()
                self.save()
                if not isinstance(exc, Exception):
                    raise
                self.log("Live append interrupted; local capture continues. " +
                         ("The separate SDK file remains available for analysis." if self.shared_stream is not None else
                          "Cold storage will be reconciled at flight end."), flush=True)
                if self.shared_stream is not None:
                    self.shared_stream.next_append = time.monotonic() + 1.05
                return
            self.manifest["confirmed_uploaded_packets"] = self.manifest.get("confirmed_uploaded_packets", 0) + len(self.pending)
        self.pending.clear()
        self.manifest["state"] = "CAPTURING"
        self.save()
        self.last_flush = time.monotonic()

    def finish(self, reason):
        if self.finish_attempted:
            return
        self.finish_attempted = True
        try:
            self.manifest["end_reason"] = reason
            self.manifest["capture_complete"] = True
            if self.setup_failed:
                self.manifest["state"] = "LOCAL_CAPTURE_COMPLETE"
                self.manifest["upload_status"] = "SETUP_FAILED"
                self.save()
                self.log("Local flight saved; Marple setup requires recovery.", flush=True)
                return
            self.flush()
            if self.shared_stream is not None:
                # Cooling or repairing from this segment would close or overwrite
                # the preview used by every other flight in the fair day.
                self.manifest["state"] = "LIVE_SEGMENT_COMPLETE"
                if self.failed:
                    self.manifest["upload_status"] = "APPEND_UNCONFIRMED"
                self.save()
                self.log("Flight segment saved; shared live preview stays open.", flush=True)
                return
            if self.dataset is not None:
                self.manifest["state"] = "COOLING_REQUESTED"
                self.save()
                self.dataset = self.dataset.cool()
                self.manifest["state"] = self.dataset.import_status
                self.save()
                self.dataset = self.dataset.wait_for_import(timeout=60)
                if self.dataset.import_status != "FINISHED":
                    raise RuntimeError("Cooling did not finish")
                self.manifest["state"] = "VERIFYING_COLD_STORAGE"
                self.save()
                from xplane_verify import verify_capture
                result = verify_capture(self.dataset, self.path, repair=True, log=self.log)
                self.dataset.upsert_signals(signal_definitions(self.observed_signals, SIGNALS))
                self.manifest["cold_storage_verified"] = result["verified"]
                self.manifest["repaired_signals"] = result["repaired_signals"]
            self.manifest["state"] = "FINISHED" if self.dataset is not None else "LOCAL_CAPTURE_COMPLETE"
            self.save()
            self.log(f"Ended: {self.manifest['state']} | {self.manifest['packets']} packets | {reason}", flush=True)
        finally:
            self.file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=49000)
    parser.add_argument("--data-port", type=int, default=49005, help="Local port for selected DATA output groups")
    parser.add_argument("--include-data", action="store_true", help="Opt in to legacy checkbox-selected DATA capture; normally leave disabled")
    parser.add_argument("--sample-mode", choices=['low', 'high'], default='low', help="Marple sampling: low 1 Hz / high 10 Hz; local RREF detection remains at --hz")
    parser.add_argument("--flush-seconds", type=float, default=2, help="Live upload interval; keeps request rate below per-minute limits")
    parser.add_argument("--hz", type=int, default=10)
    parser.add_argument("--seconds", type=float, default=30, help="Capture duration from first packet; 0 means until Ctrl+C")
    parser.add_argument("--wait-seconds", type=float, default=30, help="Timeout waiting for first UDP data; 0 waits until X-Plane is ready")
    parser.add_argument("--live", action="store_true", help="Create a Marple dataset and append real telemetry")
    parser.add_argument("--stream", default="X-Plane Fair Live")
    parser.add_argument("--auto-reset", action="store_true", help="Experimental: split when flight timer goes backwards by >1s")
    parser.add_argument("--landing", action="store_true", help="End and request a pause 5 seconds after first gear compression following observed airborne flight")
    parser.add_argument("--continuous", action="store_true", help="After landing end, wait for reset to 3000 ft MSL before starting a new dataset; requires --landing --seconds 0")
    parser.add_argument("--reset-altitude-ft", type=float, default=3000, help="MSL reset target; watcher uses +/-150 ft tolerance and >=1000 ft jump after landing")
    parser.add_argument("--wait-for-reset", action="store_true", help="Start in landed/waiting state; use when attaching after a completed landing")
    args = parser.parse_args()
    if not 1 <= args.hz <= 100 or args.seconds < 0 or args.wait_seconds < 0 or args.flush_seconds <= 0:
        parser.error("Use hz 1..100, seconds >=0, wait-seconds >=0, flush-seconds >0")
    if args.continuous and not (args.landing and args.seconds == 0):
        parser.error("--continuous requires --landing --seconds 0")
    if args.wait_for_reset and not args.continuous:
        parser.error("--wait-for-reset requires --continuous")
    folder = Path("outputs/xplane") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    folder.mkdir(parents=True)
    (folder / "signals.json").write_text(json.dumps(SIGNALS, indent=2) + "\n")
    stream = None
    minimum_number = 1
    namespace = "local"
    if args.live:
        from xplane_marple import get_sdk_db
        db = get_sdk_db()
        matches = [s for s in db.get_streams() if s.name == args.stream]
        stream = matches[0] if matches else db.create_stream(args.stream, type="realtime", description="Short X-Plane flight demos; one dataset per flight")
        if stream.type != "realtime":
            raise ValueError("Selected Marple stream must be realtime")
        minimum_number = next_remote_number(stream.get_datasets())
        namespace = f"stream:{stream.id}"
    receiver = Receiver(args.host, args.port, args.hz, folder, args.data_port, include_data=args.include_data)
    receiver.thread.start()
    print(f"Listening to {args.host}:{args.port} at {args.hz} Hz. Logs: {folder}. Ctrl+C ends and finalizes.", flush=True)
    flight = None
    number = 0
    first = None
    previous_timer = None
    latest_pause = (None, -math.inf)
    sampler = LiveSampler(args.sample_mode)
    report_telemetry = ReportTelemetry()
    started = time.monotonic()
    last_status = started
    reason = "duration"
    landing = LandingCut()
    altitude_reset = AltitudeReset(args.reset_altitude_ft)
    waiting_reset = args.wait_for_reset
    altitude_reset.landed = args.wait_for_reset
    if waiting_reset:
        print(f"Waiting for the landed aircraft to reset to {args.reset_altitude_ft:g} ft MSL.", flush=True)
    try:
        while True:
            if receiver.failure:
                raise RuntimeError(f"Receiver failed ({receiver.failure}); inspect packet journal")
            try:
                timestamp, mono, values = receiver.events.get(timeout=0.25)
            except queue.Empty:
                if first is None and args.wait_seconds and time.monotonic() - started > args.wait_seconds:
                    reason = "no telemetry"
                    print("No UDP telemetry received. Load a flight and check host/UDP port.", flush=True)
                    break
                if first is not None and args.seconds and time.monotonic() - first >= args.seconds:
                    break
                if flight and args.landing and landing.expired(time.monotonic()):
                    paused, at = latest_pause
                    pause_simulator(receiver, paused if time.monotonic() - at < 1 else None)
                    flight.finish(LANDING_END_REASON)
                    flight = None
                    if not args.continuous:
                        break
                    waiting_reset = True
                    print(f"Flight saved. Waiting for reset to {args.reset_altitude_ft:g} ft MSL.", flush=True)
                if flight and time.monotonic() - flight.last_flush >= args.flush_seconds:
                    flight.flush()
                continue
            if first is None:
                first = mono
            if "paused" in values:
                latest_pause = (values["paused"], mono)
            if args.seconds and mono - first >= args.seconds:
                break
            timer = values.get("flight_time_s")
            timer_reset = reset_detected(previous_timer, timer)
            altitude_reset_seen = altitude_reset.observe(values, mono)
            if timer_reset:
                print(f"RESET CANDIDATE: flight timer {previous_timer:.3f} -> {timer:.3f}", flush=True)
            if altitude_reset_seen or (args.auto_reset and timer_reset):
                reset_reason = f"reset altitude jump to {args.reset_altitude_ft:g} ft MSL" if altitude_reset_seen else "flight timer reset (experimental)"
                print(f"NEW FLIGHT: {reset_reason}", flush=True)
                if flight:
                    flight.finish(reset_reason + " before landing cutoff")
                    flight = None
                waiting_reset = False
                landing = LandingCut()
                altitude_reset.landed = False
            if timer is not None:
                previous_timer = timer
            if waiting_reset:
                continue
            if args.landing and flight and landing.expired(mono):
                paused, at = latest_pause
                pause_simulator(receiver, paused if time.monotonic() - at < 1 else None)
                flight.finish(LANDING_END_REASON)
                flight = None
                if not args.continuous:
                    break
                waiting_reset = True
                print(f"Flight saved. Waiting for reset to {args.reset_altitude_ft:g} ft MSL.", flush=True)
                continue
            if flight is None:
                number += 1
                name = reserve_challenge_name(Path("outputs/xplane/challenge-sequence.json"), namespace, minimum_number)
                flight = Flight(folder, number, stream, dataset_name=name,
                                metadata={**FLIGHT_METADATA, 'Live Sample Rate Hz': sampler.hz})
                sampler = LiveSampler(args.sample_mode)
                report_telemetry = ReportTelemetry()
            enriched = report_telemetry.add(mono, values)
            selected = sampler.select(mono, enriched)
            if selected:
                flight.add(timestamp, selected)
            if args.landing:
                was_armed, previous_touchdown = landing.armed, landing.touchdown
                landing.observe(mono, values)
                if landing.armed and not was_armed:
                    print("Airborne confirmed. Landing detection armed.", flush=True)
                if landing.touchdown is not None and previous_touchdown is None:
                    altitude_reset.landed = True
                    flight.manifest["touchdown_time_ns"] = timestamp
                    flight.manifest["cutoff_time_ns"] = timestamp + int(landing.tail * 1e9)
                    flight.save()
                    print(f"FIRST GEAR COMPRESSION: recording {landing.tail:g} more seconds, then pausing and ending this flight.", flush=True)
            if time.monotonic() - flight.last_flush >= args.flush_seconds:
                flight.flush()
            if time.monotonic() - last_status >= 5:
                summary = {name: round(value, 3) for name, value in values.items()
                           if name in {"paused", "airspeed_kias", "altitude_agl_m", "on_ground"}
                           or (name.startswith("gear_") and value > 0)}
                print(f"Packets {flight.manifest['packets']}; latest {json.dumps(summary)}", flush=True)
                last_status = time.monotonic()
    except KeyboardInterrupt:
        reason = "operator stop"
    except Exception as exc:
        reason = "error"
        # Avoid printing arbitrary SDK response text that might include secrets.
        print(f"Stopped due to {type(exc).__name__}. Inspect local manifests before resuming uploads.", flush=True)
        raise SystemExit(1) from None
    finally:
        receiver.close()
        if flight:
            try:
                flight.finish(reason)
            except Exception as exc:
                print(f"Finalization not confirmed ({type(exc).__name__}); inspect {flight.manifest_path}", flush=True)
                raise SystemExit(1) from None
    if first is None:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

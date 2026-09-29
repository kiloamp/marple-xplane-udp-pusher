"""Launch the aircraft-aware flight console. Legacy landing mode is available explicitly."""
from pathlib import Path
import argparse
import fcntl
import os
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=49000)
    parser.add_argument("--landing-mode", action="store_true", help="Use the original continuous landing challenge instead of automatic aircraft routing")
    parser.add_argument("--local-only", action="store_true", help="Console without Marple uploads")
    parser.add_argument("--live-name", help="Persistent live preview name (default: Toulouse Live Fair Day 1)")
    parser.add_argument("--sample-mode", choices=['low', 'high'], default='low', help="Live samples: low 1 Hz / high 10 Hz")
    parser.add_argument("--include-data", action="store_true", help="Opt in to legacy DATA checkbox capture")
    parser.add_argument("--wait-for-reset", action="store_true", help="Use if the plane has already completed a landing")
    args = parser.parse_args()
    if args.landing_mode and args.live_name:
        parser.error("--live-name is supported by the default aircraft-aware console only")
    os.chdir(ROOT)
    output = ROOT / "outputs/xplane"
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / "service.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("The A320 recorder is already running. Use its existing Terminal window.")
    # Keep the process lock across exec; it is released automatically on exit.
    os.set_inheritable(lock.fileno(), True)
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()) + "\n")
    lock.flush()
    if args.landing_mode:
        command = [sys.executable, "-u", str(ROOT / "xplane_live.py"), "--landing",
                   "--continuous", "--seconds", "0", "--wait-seconds", "0",
                   "--host", args.host, "--port", str(args.port)]
        if args.wait_for_reset:
            command.append("--wait-for-reset")
        if not args.local_only:
            command.append("--live")
    else:
        if args.wait_for_reset:
            parser.error("--wait-for-reset requires --landing-mode")
        command = [sys.executable, "-u", str(ROOT / "xplane_session.py"),
                   "--host", args.host, "--port", str(args.port)]
        if args.local_only:
            command.append("--local-only")
        if args.live_name:
            command += ["--live-name", args.live_name]
    command += ['--sample-mode', args.sample_mode]
    if args.include_data:
        command.append('--include-data')
    print("Opening Flight Session Recorder…", flush=True)
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()

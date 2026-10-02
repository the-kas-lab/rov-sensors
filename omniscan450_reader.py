#!/usr/bin/env python3
"""Read profiles from a Cerulean Omniscan 450 FS sonar over TCP.

The sonar sits on the BlueROV2's ethernet switch with a static IP
(default 192.168.2.92) and speaks the Blue Robotics Ping protocol on
TCP port 51200.

Examples:
    python omniscan450_reader.py                       # ping, print summaries
    python omniscan450_reader.py --range 20 --csv out.csv
    python omniscan450_reader.py --listen-only         # don't (re)configure the sonar
    python omniscan450_reader.py --svlog logs/         # also record a SonarView .svlog
"""

import argparse
import builtins
import csv
import time

import numpy as np
from brping import Omniscan450, definitions

# The sonar periodically sends message id 0, which brping doesn't know and
# reports with a print() on every occurrence. Drop just that line.
_print = builtins.print
builtins.print = lambda *a, **k: None if a[:2] == ("Unknown message: ", 0) else _print(*a, **k)


def scale_power_db(msg):
    """Convert the raw u16 pwr_results into dB using the message's min/max."""
    raw = np.asarray(msg.pwr_results, dtype=np.float64)
    return msg.min_pwr_db + (raw / 65535.0) * (msg.max_pwr_db - msg.min_pwr_db)


def range_axis_m(msg):
    """Distance (m) from the transducer for each sample in pwr_results."""
    start = msg.start_mm / 1000.0
    length = msg.length_mm / 1000.0
    return start + np.arange(msg.num_results) * (length / msg.num_results)


def main():
    parser = argparse.ArgumentParser(description="Cerulean Omniscan 450 FS reader")
    parser.add_argument("--host", default="192.168.2.92", help="sonar IP address")
    parser.add_argument("--port", type=int, default=51200, help="sonar TCP port")
    parser.add_argument("--start", type=float, default=0.0, help="scan start (m)")
    parser.add_argument("--range", type=float, default=10.0, help="scan length (m)")
    parser.add_argument("--num-results", type=int, default=600, help="samples per profile")
    parser.add_argument("--ping-rate", type=float, default=0,
                        help="pings per second (0 = as fast as possible)")
    parser.add_argument("--gain", type=int, default=-1, help="gain index 0-7, -1 = auto")
    parser.add_argument("--listen-only", action="store_true",
                        help="don't send ping parameters; just read whatever the sonar outputs")
    parser.add_argument("--csv", help="write one row per profile to this CSV file")
    parser.add_argument("--svlog", metavar="DIR", help="record a SonarView-compatible .svlog in DIR")
    parser.add_argument("--count", type=int, default=0, help="stop after N profiles (0 = forever)")
    args = parser.parse_args()

    sonar = Omniscan450(logging=args.svlog is not None, log_directory=args.svlog)
    sonar.connect_tcp(args.host, args.port)
    if not sonar.initialize():
        raise SystemExit(f"No reply from Omniscan at {args.host}:{args.port}")

    print(f"Connected: device_type={sonar._device_type} rev={sonar._device_revision} "
          f"fw={sonar._firmware_version_major}.{sonar._firmware_version_minor}"
          f".{sonar._firmware_version_patch}")

    if not args.listen_only:
        msec_per_ping = Omniscan450.calc_msec_per_ping(args.ping_rate) if args.ping_rate > 0 else 0
        sonar.control_os_ping_params(
            start_mm=int(args.start * 1000),
            length_mm=int(args.range * 1000),
            msec_per_ping=msec_per_ping,
            gain_index=args.gain,
            num_results=args.num_results,
            enable=True,
        )

    csv_file = writer = None
    if args.csv:
        csv_file = open(args.csv, "w", newline="")
        writer = csv.writer(csv_file)

    received = 0
    last_rx = time.time()
    try:
        while args.count == 0 or received < args.count:
            msg = sonar.wait_message([definitions.OMNISCAN450_OS_MONO_PROFILE], timeout=1.0)
            if msg is None:
                if time.time() - last_rx > 3:
                    print("Waiting for profiles... (is the sonar enabled / pinging?)")
                    last_rx = time.time()
                continue
            last_rx = time.time()
            received += 1

            power_db = scale_power_db(msg)
            distances = range_axis_m(msg)
            peak = int(np.argmax(power_db))

            print(f"#{msg.ping_number:6d}  {msg.ping_hz / 1000:.0f} kHz  "
                  f"range {msg.start_mm / 1000:.1f}-{(msg.start_mm + msg.length_mm) / 1000:.1f} m  "
                  f"gain {msg.gain_index}  samples {msg.num_results}  "
                  f"peak {power_db[peak]:.1f} dB @ {distances[peak]:.2f} m")

            if writer:
                if received == 1:
                    # One column per sample, labelled with its distance from the transducer.
                    writer.writerow(["host_time", "ping_number", "timestamp_ms", "ping_hz",
                                     "start_mm", "length_mm", "sos_dmps", "gain_index",
                                     "num_results", *(f"pwr_db_{d:.3f}m" for d in distances)])
                writer.writerow([f"{time.time():.3f}", msg.ping_number, msg.timestamp_ms,
                                 msg.ping_hz, msg.start_mm, msg.length_mm, msg.sos_dmps,
                                 msg.gain_index, msg.num_results,
                                 *np.round(power_db, 2)])
    except KeyboardInterrupt:
        pass
    finally:
        if not args.listen_only:
            # Stop pinging so the sonar isn't left transmitting.
            sonar.control_os_ping_params(enable=False)
        if csv_file:
            csv_file.close()
        sonar.iodev.close()
        print(f"\nReceived {received} profiles.")


if __name__ == "__main__":
    main()

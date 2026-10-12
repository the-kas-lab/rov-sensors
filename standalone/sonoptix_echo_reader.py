#!/usr/bin/env python3
"""Read sonar frames from a Sonoptix ECHO multibeam imaging sonar.

The sonar streams its raw data as H.264 video over RTSP
(rtsp://<ip>:8554/raw) once its stream type is set to 2 (RTSP). Each decoded
frame is a matrix of 8-bit intensities: one column per beam (256) and one row
per range sample, nearest at the top. The video is yuvj420p (full-range), so
the luma (Y) plane is the intensity matrix as sent. Settings are changed
through the REST API (see sonoptix_echo_api.py).

Needs GStreamer's Python bindings: the rov-sensors conda env has them
(conda-forge pygobject, gstreamer, gst-plugins-*, gst-libav).

Examples:
    python sonoptix_echo_reader.py --count 10              # 6 m range, print 10 frames
    python sonoptix_echo_reader.py --range 15 --save echo.npz
    python sonoptix_echo_reader.py --count 5 --csv echo.csv   # one CSV row per range sample
    python sonoptix_echo_reader.py --range 50 --mode 2     # 400 kHz (LF), 90 deg sector
    python sonoptix_echo_reader.py --listen-only           # don't change the sonar's settings

On exit the stream type, range, operation mode and transponder state are restored to what they were,
unless --listen-only is set.
"""

import argparse
import collections
import csv
import time

import gi
import numpy as np

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstApp, GstVideo  # noqa: E402,F401  (GstApp adds the appsink methods)

from sonoptix_echo_api import OPERATION_MODES, VALID_RANGES_M, EchoApi  # noqa: E402

STREAM_TYPE_RTSP = 2
HF_MAX_RANGE_M = 30  # 700 kHz is used up to 30 m, 400 kHz beyond (Blue Robotics spec)


def sector_deg(operation_mode, range_m):
    """Horizontal sector of the 256 beams: 120 deg at 700 kHz (HF), 90 deg at 400 kHz (LF)."""
    if operation_mode == 1:
        return 120.0
    if operation_mode in (2, 3):
        return 90.0
    return 120.0 if range_m <= HF_MAX_RANGE_M else 90.0  # AUTO: frequency follows the range
PULL_TIMEOUT_NS = 200 * Gst.MSECOND


def frame_from_sample(sample):
    """Copy the luma (Y) plane of a decoded I420 sample into a (samples, beams) uint8 array."""
    info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
    buf = sample.get_buffer()
    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    if not ok:
        return None
    try:
        stride = info.stride[0]
        plane = np.frombuffer(mapinfo.data, dtype=np.uint8, count=stride * info.height,
                              offset=info.offset[0]).reshape(info.height, stride)
        # Rows may be padded beyond the frame width; drop the padding.
        return plane[:, :info.width].copy()
    finally:
        buf.unmap(mapinfo)


def main():
    parser = argparse.ArgumentParser(description="Sonoptix ECHO frame reader")
    parser.add_argument("--host", default="192.168.2.42", help="sonar IP address")
    parser.add_argument("--api-port", type=int, default=8000, help="REST API port")
    parser.add_argument("--rtsp-port", type=int, default=8554, help="RTSP port")
    parser.add_argument("--range", type=float, default=6.0, choices=VALID_RANGES_M, metavar="M",
                        help="sonar range (m): " + ", ".join(map(str, VALID_RANGES_M)))
    parser.add_argument("--listen-only", action="store_true",
                        help="don't change any settings; just read the stream if it's running")
    parser.add_argument("--mode", type=int, choices=OPERATION_MODES,
                        help="set operation_mode: " + ", ".join(f"{k} = {v}" for k, v in OPERATION_MODES.items()))
    parser.add_argument("--fov", type=float,
                        help="horizontal sector (deg) for labelling beam angles "
                             "(default: 120 at 700 kHz, 90 at 400 kHz, from the operation mode)")
    parser.add_argument("--blank", type=float, default=0.0,
                        help="ignore this much near range (m) when finding the strongest return")
    parser.add_argument("--save", metavar="FILE", help="save all frames to a compressed .npz")
    parser.add_argument("--csv", metavar="FILE",
                        help="write every frame to CSV: one row per range sample, one column per beam")
    parser.add_argument("--count", type=int, default=0, help="stop after N frames (0 = forever)")
    args = parser.parse_args()

    api = EchoApi(args.host, args.api_port, timeout=3.0)
    url = f"rtsp://{args.host}:{args.rtsp_port}/raw"

    # Remember the current state so it can be put back on exit.
    old_transponder = api.request("GET", "/transponder")
    old_stream_type = api.request("GET", "/streamtype")["value"]
    if not args.listen_only:
        settings = {"enable": True, "sonar_range": args.range}
        if args.mode is not None:
            settings["operation_mode"] = args.mode
        api.request("PATCH", "/transponder", settings)
        if old_stream_type != STREAM_TYPE_RTSP:
            api.request("PUT", "/streamtype", {"value": STREAM_TYPE_RTSP})
    transponder = api.request("GET", "/transponder")
    range_m = float(transponder["sonar_range"])
    mode = transponder["operation_mode"]
    fov = args.fov or sector_deg(mode, range_m)
    print(f"Sonoptix ECHO at {args.host}: enable={transponder['enable']} range={range_m} m "
          f"mode={OPERATION_MODES.get(mode, mode)} sector={fov} deg")
    if mode == 1 and range_m > HF_MAX_RANGE_M:
        print(f"Warning: HF (700 kHz) is specified only up to {HF_MAX_RANGE_M} m.")

    Gst.init(None)
    # TCP transport (as with ffprobe); I420 keeps the decoder's planes as-is, so Y is untouched.
    # A bounded queue without drop: every frame is kept, for recording.
    pipeline = Gst.parse_launch(
        # Wait for a keyframe and drop corrupt frames: otherwise the decoder emits flat grey
        # (128) frames until the first keyframe, about 2 s after connecting.
        f'rtspsrc location="{url}" latency=50 protocols=tcp ! rtph264depay wait-for-keyframe=true ! '
        'h264parse ! avdec_h264 output-corrupt=false ! videoconvert ! video/x-raw,format=I420 ! '
        'appsink name=sink max-buffers=30 drop=false sync=false')
    sink = pipeline.get_by_name("sink")
    bus = pipeline.get_bus()

    frames, host_times = [], []
    received = 0
    csv_file = writer = csv_header = None
    if args.csv:
        csv_file = open(args.csv, "w", newline="")
        writer = csv.writer(csv_file)
    height = settings = None
    last_settings = 0.0
    recent = collections.deque(maxlen=16)  # arrival times, for the frame rate
    last_rx = time.time()
    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise SystemExit(f"Could not start the GStreamer pipeline for {url}")
        while args.count == 0 or received < args.count:
            msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg:
                if msg.type == Gst.MessageType.EOS:
                    print("Stream ended.")
                else:
                    err, _ = msg.parse_error()
                    print(f"Stream error: {err.message}")
                break

            sample = sink.try_pull_sample(PULL_TIMEOUT_NS)
            if sample is None:
                if time.time() - last_rx > 3:
                    print(f"Waiting for frames on {url}... (is the stream type RTSP and the "
                          "transponder enabled?)")
                    last_rx = time.time()
                continue
            frame = frame_from_sample(sample)
            if frame is None:
                continue
            last_rx = time.time()
            received += 1

            # Re-read range and mode when the frame height changes (the number of samples
            # follows the range) and every 2 s, since e.g. 30 m HF -> 50 m LF keeps 1024 rows.
            if frame.shape[0] != height or time.time() - last_settings > 2.0:
                transponder = api.request("GET", "/transponder")
                last_settings = time.time()
                new = (float(transponder["sonar_range"]), transponder["operation_mode"])
                if frame.shape[0] != height or new != settings:
                    height, settings = frame.shape[0], new
                    range_m = new[0]
                    fov = args.fov or sector_deg(new[1], range_m)
                    print(f"Frames: {frame.shape[1]} beams x {height} samples for {range_m} m, "
                          f"{OPERATION_MODES.get(new[1], new[1])}, {fov} deg")
            n_beams = frame.shape[1]
            m_per_row = range_m / height
            blank_rows = min(int(args.blank / m_per_row), height - 1)

            # Strongest return beyond the blanked near range.
            row, beam = np.unravel_index(np.argmax(frame[blank_rows:]), frame[blank_rows:].shape)
            row += blank_rows
            # Assumes evenly spaced beams, beam 0 on the left of the image (not yet verified).
            angle = -fov / 2 + (beam + 0.5) * fov / n_beams
            recent.append(time.time())
            # Frames buffered while connecting arrive in a burst, so wait for a full window.
            fps = (f"{(len(recent) - 1) / (recent[-1] - recent[0]):4.1f} fps"
                   if len(recent) == recent.maxlen else "")
            print(f"#{received:5d}  {n_beams}x{height}  mean {frame.mean():5.1f}  "
                  f"peak {frame[row, beam]:3d} @ {row * m_per_row:5.2f} m, "
                  f"beam {beam:3d} ({angle:+5.1f} deg)  {fps}")

            if args.save:
                frames.append(frame)
                host_times.append(time.time())
            if writer:
                # Columns are labelled with each beam's angle, so they change if the sector does.
                angles = -fov / 2 + (np.arange(n_beams) + 0.5) * fov / n_beams
                header = ["host_time", "frame", "range_m", "sector_deg", "sample", "distance_m",
                          *(f"beam{b:03d}_{a:+.2f}deg" for b, a in enumerate(angles))]
                if header != csv_header:
                    writer.writerow(header)
                    csv_header = header
                t = f"{time.time():.3f}"
                for i, row_values in enumerate(frame):
                    writer.writerow([t, received, range_m, fov, i, f"{i * m_per_row:.4f}",
                                     *row_values.tolist()])
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.set_state(Gst.State.NULL)
        if not args.listen_only:
            # Put the sonar back the way we found it (WebRTC video for the web viewer, etc).
            if old_stream_type != STREAM_TYPE_RTSP:
                api.request("PUT", "/streamtype", {"value": old_stream_type})
            api.request("PATCH", "/transponder", {
                key: old_transponder[key] for key in ("enable", "sonar_range", "operation_mode")})
        if csv_file:
            csv_file.close()
            print(f"Wrote {received} frames to {args.csv}")
        if args.save and frames:
            # Frames of different sizes (range changed mid-run) can't be stacked: keep the last size.
            keep = [i for i, f in enumerate(frames) if f.shape == frames[-1].shape]
            np.savez_compressed(args.save, frames=np.stack([frames[i] for i in keep]),
                                host_time=np.array([host_times[i] for i in keep]),
                                range_m=range_m, fov_deg=fov)
            print(f"Saved {len(keep)} frames to {args.save}")
        print(f"\nReceived {received} frames.")


if __name__ == "__main__":
    main()

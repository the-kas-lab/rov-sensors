#!/usr/bin/env python3
"""Receive a BlueOS camera on the topside computer.

RTSP: python bluerov_camera_reader.py --url 'rtsp://URL-COPIED-FROM-BLUEOS'
UDP H264: python bluerov_camera_reader.py --udp-port 5602
UDP requires an OpenCV build with GStreamer support and GStreamer plugins.
"""
import argparse
import time
import cv2


def open_camera(args):
    if args.url:
        return cv2.VideoCapture(args.url, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
        ])
    if not any('GStreamer:' in line and 'YES' in line
               for line in cv2.getBuildInformation().splitlines()):
        raise SystemExit('This OpenCV build lacks GStreamer. Use RTSP, or install an OpenCV build with GStreamer support.')
    pipeline = (
        f'udpsrc port={args.udp_port} timeout=5000000000 ! '
        'application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=96 ! '
        'rtpjitterbuffer latency=50 drop-on-latency=true ! '
        'rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! '
        'video/x-raw,format=BGR ! appsink max-buffers=1 drop=true sync=false'
    )
    return cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)


def main():
    parser = argparse.ArgumentParser(description='BlueROV camera reader')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--url', help='exact RTSP URL from BlueOS Video Streams')
    source.add_argument('--udp-port', type=int, help='local port receiving RTP/H264')
    parser.add_argument('--count', type=int, default=0, help='stop after N frames; 0 = forever')
    parser.add_argument('--no-display', action='store_true', help='run without a GUI')
    args = parser.parse_args()
    if args.count < 0 or (args.udp_port is not None and not 1 <= args.udp_port <= 65535):
        parser.error('count must be nonnegative and port must be 1..65535')
    cap = open_camera(args)
    received = 0
    started = time.monotonic()
    last_report = started
    try:
        if not cap.isOpened():
            raise SystemExit('Cannot open stream. Check BlueOS endpoint, network, and decoder backend.')
        while args.count == 0 or received < args.count:
            ok, frame = cap.read()
            if not ok:
                raise SystemExit('No decoded frame received. Check the stream and network, then restart this reader.')
            host_time = time.time()  # host decode/read time, NOT camera exposure time
            received += 1
            # YOUR PROCESSING HERE: frame is a uint8 BGR NumPy array (H, W, 3).
            # Example: rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            now = time.monotonic()
            if now - last_report >= 1:
                print(f'#{received} host_time={host_time:.3f} '
                      f'shape={frame.shape} average_fps={received / (now - started):.1f}')
                last_report = now
            if not args.no_display:
                cv2.imshow('BlueROV camera - q to quit', frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if not args.no_display:
            cv2.destroyAllWindows()
        print(f'Received {received} frames.')


if __name__ == '__main__':
    main()

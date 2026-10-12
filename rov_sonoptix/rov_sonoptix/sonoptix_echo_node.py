#!/usr/bin/env python3
"""ROS 2 driver for the Sonoptix ECHO multibeam imaging sonar.

Configures the sonar over its REST API (same calls as standalone/sonoptix_echo_api.py),
switches it to its raw RTSP stream, decodes that in GStreamer (same pipeline as
standalone/sonoptix_echo_reader.py) and publishes every frame.

Publishes:
    sonoptix_echo/frame  rov_interfaces/SonoptixFrame  every frame: intensities + range and beam axes
    sonoptix_echo/image  sensor_msgs/Image (mono8)     the same frame as an image: rows = range
                                                       (nearest at the top), columns = beams (port left)

Parameters (range_m and operation_mode can be changed live, e.g. from Foxglove's Parameters panel):
    host, api_port, rtsp_port  sonar address (default 192.168.2.42, REST 8000, RTSP 8554)
    range_m                    3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, 175
                               or 200; the sonar silently changes any other value to 3
    operation_mode             0 = AUTO, 1 = HF (700 kHz), 2 = LF (400 kHz), 3 = LFLR
    listen_only                don't configure the sonar, just publish what it streams
    frame_id                   TF frame of the transducer
    rtsp_latency_ms            RTSP jitter buffer
    stall_timeout_s            restart the stream if no frame arrives for this long

The connection is retried every 2 s, so the node can start before the sonar (or the
mock) is up and survives the link dropping. On shutdown the sonar is disabled and put
back on its WebRTC video stream for the web viewer, unless listen_only is set.
"""

import array
import json
import signal
import threading
import time
import urllib.error
import urllib.request

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstApp, GstVideo  # noqa: E402,F401  (GstApp adds AppSink.try_pull_sample)

import numpy as np  # noqa: E402
import rclpy  # noqa: E402
from rcl_interfaces.msg import SetParametersResult  # noqa: E402
from rclpy.executors import ExternalShutdownException  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import qos_profile_sensor_data  # noqa: E402
from sensor_msgs.msg import Image  # noqa: E402

from rov_interfaces.msg import SonoptixFrame  # noqa: E402

# sonar_range values the sonar keeps, the same in every operation mode (measured on firmware 1.5.0).
VALID_RANGES_M = (3, 6, 9, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, 175, 200)
OPERATION_MODES = {0: 'AUTO', 1: 'HF', 2: 'LF', 3: 'LFLR'}
HF_MAX_RANGE_M = 30          # 700 kHz is used up to 30 m, 400 kHz beyond (Blue Robotics spec)
STREAM_TYPE_VIDEO = 0        # WebRTC video, what the web viewer shows
STREAM_TYPE_RTSP = 2         # raw frames on rtsp://<host>:<rtsp_port>/raw
SONAR_PARAM_NAMES = ('range_m', 'operation_mode')
RECONNECT_DELAY_S = 2.0
SETTINGS_POLL_S = 2.0        # re-read range/mode this often, in case they're changed elsewhere
HTTP_TIMEOUT_S = 3.0
PULL_TIMEOUT_NS = 200 * Gst.MSECOND


def is_high_frequency(operation_mode, range_m):
    """700 kHz in HF mode, or in AUTO up to 30 m; 400 kHz otherwise (LF, LFLR)."""
    return operation_mode == 1 or (operation_mode == 0 and range_m <= HF_MAX_RANGE_M)


def sector_deg(operation_mode, range_m):
    """Horizontal sector of the beams: 120 deg at 700 kHz, 90 deg at 400 kHz."""
    return 120.0 if is_high_frequency(operation_mode, range_m) else 90.0


def float32_array(values):
    return array.array('f', np.asarray(values, dtype=np.float32).tobytes())


def frame_from_sample(sample):
    """Copy the luma (Y) plane of a decoded I420 sample into a (samples, beams) uint8 array.

    The stream is full-range 4:2:0 (yuvj420p), so luma is the intensity matrix as sent.
    """
    info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
    buf = sample.get_buffer()
    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    if not ok:
        return None
    try:
        stride = info.stride[0]
        plane = np.frombuffer(mapinfo.data, dtype=np.uint8, count=stride * info.height,
                              offset=info.offset[0]).reshape(info.height, stride)
        return plane[:, :info.width].copy()  # rows may be padded beyond the frame width
    finally:
        buf.unmap(mapinfo)


class EchoApi:
    """The few REST calls the node needs (http://<host>:<port>/api/v1)."""

    def __init__(self, host, port):
        self.root = f'http://{host}:{port}/api/v1'

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.root + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                text = resp.read().decode()
        except urllib.error.HTTPError as e:
            raise ConnectionError(f'{method} {path} -> HTTP {e.code}: {e.read().decode(errors="replace")}')
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionError(f'no reply from {self.root}: {getattr(e, "reason", e)}')
        return json.loads(text) if text else None


class SonoptixEchoNode(Node):
    def __init__(self):
        super().__init__('sonoptix_echo')
        self.declare_parameter('host', '192.168.2.42')
        self.declare_parameter('api_port', 8000)
        self.declare_parameter('rtsp_port', 8554)
        self.declare_parameter('range_m', 6.0)
        self.declare_parameter('operation_mode', 1)
        self.declare_parameter('listen_only', False)
        self.declare_parameter('frame_id', 'sonoptix_echo_link')
        self.declare_parameter('rtsp_latency_ms', 50)
        self.declare_parameter('stall_timeout_s', 5.0)
        self.add_on_set_parameters_callback(self._on_set_parameters)

        self.frame_pub = self.create_publisher(SonoptixFrame, 'sonoptix_echo/frame', qos_profile_sensor_data)
        self.image_pub = self.create_publisher(Image, 'sonoptix_echo/image', qos_profile_sensor_data)

        self._api = None             # EchoApi while connected
        self._settings = None        # (range_m, operation_mode) the sonar reports
        self._lock = threading.Lock()  # guards _api and _settings against reconnects
        self._frame_number = 0

        Gst.init(None)
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._run, daemon=True)
        self._reader.start()

    # -- sonar connection ----------------------------------------------------

    def _run(self):
        """Reader thread: configure the sonar, stream frames, reconnect on any failure."""
        while not self._stop.is_set() and rclpy.ok():
            host = self.get_parameter('host').value
            pipeline = None
            try:
                api = EchoApi(host, self.get_parameter('api_port').value)
                self._configure(api)
                with self._lock:
                    self._api = api
                pipeline = Gst.parse_launch(self._pipeline_description())
                self._stream(api, pipeline)
            except Exception as e:
                if self._stop.is_set() or not rclpy.ok():
                    break
                self.get_logger().warn(f'Sonoptix ECHO {host}: {e}; retrying in {RECONNECT_DELAY_S:.0f} s',
                                       throttle_duration_sec=10.0)
            finally:
                if pipeline is not None:
                    pipeline.set_state(Gst.State.NULL)
                with self._lock:
                    self._api = None
            self._stop.wait(RECONNECT_DELAY_S)

    def _configure(self, api):
        version = api.request('GET', '/version')['value']
        serial = api.request('GET', '/serial')['value']
        if not self.get_parameter('listen_only').value:
            api.request('PATCH', '/transponder', {'enable': True, **self._sonar_params()})
            if api.request('GET', '/streamtype')['value'] != STREAM_TYPE_RTSP:
                api.request('PUT', '/streamtype', {'value': STREAM_TYPE_RTSP})
        range_m, mode = self._read_settings(api)
        self.get_logger().info(f'Connected to Sonoptix ECHO at {self.get_parameter("host").value}: '
                               f'serial {serial}, firmware {version}, {range_m:g} m, '
                               f'{OPERATION_MODES.get(mode, mode)}')

    def _read_settings(self, api):
        t = api.request('GET', '/transponder')
        settings = (float(t['sonar_range']), int(t['operation_mode']))
        with self._lock:
            self._settings = settings
        return settings

    def _sonar_params(self, overrides=None):
        values = {name: self.get_parameter(name).value for name in SONAR_PARAM_NAMES}
        values.update(overrides or {})
        return {'sonar_range': float(values['range_m']), 'operation_mode': int(values['operation_mode'])}

    def _pipeline_description(self):
        url = (f'rtsp://{self.get_parameter("host").value}:{self.get_parameter("rtsp_port").value}/raw')
        # Wait for a keyframe and drop corrupt frames: otherwise the decoder emits flat grey
        # frames until the first keyframe, about 2 s after connecting. I420 keeps the decoder's
        # planes as they are, so the luma (intensity) values are untouched.
        # 1-frame drop=true appsink: always deliver the newest frame, never back up.
        return (
            f'rtspsrc location="{url}" latency={self.get_parameter("rtsp_latency_ms").value} protocols=tcp ! '
            'rtph264depay wait-for-keyframe=true ! h264parse ! avdec_h264 output-corrupt=false ! '
            'videoconvert ! video/x-raw,format=I420 ! appsink name=sink max-buffers=1 drop=true sync=false'
        )

    def _stream(self, api, pipeline):
        sink = pipeline.get_by_name('sink')
        bus = pipeline.get_bus()
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('pipeline failed to start')

        stall_timeout = self.get_parameter('stall_timeout_s').value
        last_frame = last_poll = time.monotonic()
        shape = None
        while not self._stop.is_set() and rclpy.ok():
            msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg is not None:
                if msg.type == Gst.MessageType.EOS:
                    raise RuntimeError('stream ended')
                err, _debug = msg.parse_error()
                raise RuntimeError(err.message)

            if time.monotonic() - last_poll > SETTINGS_POLL_S:
                self._read_settings(api)
                last_poll = time.monotonic()

            sample = sink.try_pull_sample(PULL_TIMEOUT_NS)
            if sample is None:
                if time.monotonic() - last_frame > stall_timeout:
                    raise TimeoutError(f'no frames for {stall_timeout:.0f} s '
                                       '(is the sonar enabled and its stream type RTSP?)')
                continue
            last_frame = time.monotonic()
            frame = frame_from_sample(sample)
            if frame is None:
                continue

            with self._lock:
                range_m, mode = self._settings
            if frame.shape != shape:
                # The number of samples follows the range, so this also logs range changes.
                shape = frame.shape
                self.get_logger().info(f'Receiving frames: {shape[1]} beams x {shape[0]} samples, '
                                       f'{range_m:g} m, {OPERATION_MODES.get(mode, mode)}, '
                                       f'{sector_deg(mode, range_m):g} deg')
            self._publish(frame, range_m, mode)

    def _on_set_parameters(self, params):
        changed = {p.name: p.value for p in params if p.name in SONAR_PARAM_NAMES}
        if not changed:
            return SetParametersResult(successful=True)
        p = self._sonar_params(changed)
        if p['sonar_range'] not in VALID_RANGES_M:
            return SetParametersResult(successful=False,
                                       reason=f'range_m must be one of {", ".join(map(str, VALID_RANGES_M))}')
        if p['operation_mode'] not in OPERATION_MODES:
            return SetParametersResult(successful=False,
                                       reason='operation_mode must be 0 (AUTO), 1 (HF), 2 (LF) or 3 (LFLR)')
        with self._lock:
            api = self._api
        if api is not None and not self.get_parameter('listen_only').value:
            try:
                api.request('PATCH', '/transponder', p)
            except ConnectionError as e:
                return SetParametersResult(successful=False, reason=str(e))
            with self._lock:
                self._settings = (p['sonar_range'], p['operation_mode'])
            self.get_logger().info(f'Reconfigured sonar: {changed}')
        return SetParametersResult(successful=True)

    # -- publishing ----------------------------------------------------------

    def _publish(self, frame, range_m, mode):
        rows, beams = frame.shape
        sector = sector_deg(mode, range_m)

        msg = SonoptixFrame()
        msg.header.stamp = self.get_clock().now().to_msg()  # host receive time
        msg.header.frame_id = self.get_parameter('frame_id').value
        msg.frame_number = self._frame_number
        msg.sonar_range_m = range_m
        msg.operation_mode = mode
        msg.frequency_hz = 700000 if is_high_frequency(mode, range_m) else 400000
        msg.sector_deg = sector
        msg.num_samples = rows
        msg.num_beams = beams
        msg.range_m = float32_array(np.arange(rows) * (range_m / rows))
        # Assumes evenly spaced beams (not yet verified on the real sonar).
        msg.beam_angle_deg = float32_array(-sector / 2 + (np.arange(beams) + 0.5) * sector / beams)
        msg.intensity = frame.tobytes()
        self.frame_pub.publish(msg)
        self._frame_number = (self._frame_number + 1) & 0xFFFFFFFF

        img = Image()
        img.header = msg.header
        img.height, img.width = rows, beams
        img.encoding = 'mono8'
        img.is_bigendian = 0
        img.step = beams
        img.data = msg.intensity
        self.image_pub.publish(img)

    def shutdown(self):
        self._stop.set()
        with self._lock:
            api = self._api
        if api is not None and not self.get_parameter('listen_only').value:
            try:
                # Stop pinging so the sonar isn't left transmitting, and give the web viewer
                # its video back.
                api.request('PATCH', '/transponder', {'enable': False})
                api.request('PUT', '/streamtype', {'value': STREAM_TYPE_VIDEO})
            except ConnectionError:
                pass
        self._reader.join(timeout=3.0)


def main(args=None):
    rclpy.init(args=args)
    node = SonoptixEchoNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        # Ctrl-C reaches the node twice under ros2 launch (terminal + launch forwarding);
        # don't let the second one interrupt a clean shutdown.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        node.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # try_shutdown restores rclpy's previous handler


if __name__ == '__main__':
    main()

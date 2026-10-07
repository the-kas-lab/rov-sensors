#!/usr/bin/env python3
"""ROS 2 node for the BlueROV camera, as streamed by BlueOS.

BlueOS (Mavlink Camera Manager) sends each USB camera as H.264. This node
receives either form and publishes JPEG frames, which Foxglove shows directly:

    RTSP  (recommended; works whatever the topside IP is):
          url:=rtsp://192.168.2.2:8554/<name>   (copy it from BlueOS > Video Streams)
    UDP   (BlueOS's default; BlueOS must send to THIS computer's IP):
          udp_port:=5600   (used when url is empty)

Decoding and JPEG encoding run inside GStreamer (C), not Python, so 1080p30
is cheap. No OpenCV / numpy needed.

Publishes:
    camera/image_raw/compressed  sensor_msgs/CompressedImage (jpeg)
    camera/image_raw             sensor_msgs/Image (rgb8), only if publish_raw:=true

Parameters:
    url, udp_port       stream source (see above)
    frame_id            TF frame of the camera
    jpeg_quality        1-100
    max_width           downscale to this width (keeps aspect); 0 = full resolution
    publish_raw         also publish uncompressed frames (for local vision nodes; ~6 MB/frame at 1080p)
    rtsp_latency_ms     RTSP jitter buffer
    stall_timeout_s     restart the stream if no frame arrives for this long

The stream is restarted automatically after errors or stalls, so the node can
start before the robot is reachable.
"""

import signal
import threading
import time

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp  # noqa: E402,F401  (GstApp adds AppSink.try_pull_sample)

import rclpy  # noqa: E402
from rclpy.executors import ExternalShutdownException  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import qos_profile_sensor_data  # noqa: E402
from sensor_msgs.msg import CompressedImage, Image  # noqa: E402

RESTART_DELAY_S = 2.0
PULL_TIMEOUT_NS = 200 * Gst.MSECOND


def read_buffer(sample):
    buf = sample.get_buffer()
    ok, info = buf.map(Gst.MapFlags.READ)
    if not ok:
        return None
    try:
        return bytes(info.data)
    finally:
        buf.unmap(info)


class BlueRovCameraNode(Node):
    def __init__(self):
        super().__init__('bluerov_camera')
        self.declare_parameter('url', '')
        self.declare_parameter('udp_port', 5600)
        self.declare_parameter('frame_id', 'camera_link')
        self.declare_parameter('jpeg_quality', 80)
        self.declare_parameter('max_width', 0)
        self.declare_parameter('publish_raw', False)
        self.declare_parameter('rtsp_latency_ms', 50)
        self.declare_parameter('stall_timeout_s', 5.0)

        self.jpeg_pub = self.create_publisher(CompressedImage, 'camera/image_raw/compressed',
                                              qos_profile_sensor_data)
        self.raw_pub = None
        if self.get_parameter('publish_raw').value:
            self.raw_pub = self.create_publisher(Image, 'camera/image_raw', qos_profile_sensor_data)

        Gst.init(None)
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._run, daemon=True)
        self._reader.start()

    def _pipeline_description(self):
        p = {name: self.get_parameter(name).value for name in
             ('url', 'udp_port', 'jpeg_quality', 'max_width', 'rtsp_latency_ms')}
        if p['url']:
            source = f'rtspsrc location="{p["url"]}" latency={p["rtsp_latency_ms"]} ! decodebin ! '
        else:
            source = (
                f'udpsrc port={p["udp_port"]} '
                'caps="application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=96" ! '
                'rtpjitterbuffer latency=50 drop-on-latency=true ! '
                'rtph264depay ! h264parse ! avdec_h264 ! '
            )
        scale = (f'videoscale ! video/x-raw,width={p["max_width"]},pixel-aspect-ratio=1/1 ! '
                 if p['max_width'] > 0 else '')
        # One decoded stream, split into a JPEG branch and (optionally) a raw RGB branch.
        # Leaky 1-frame queues + drop=true appsinks: always deliver the newest frame, never back up.
        desc = (
            source + 'videoconvert ! ' + scale + 'tee name=t '
            't. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! '
            f'jpegenc quality={p["jpeg_quality"]} ! '
            'appsink name=jpeg max-buffers=1 drop=true sync=false'
        )
        if self.raw_pub:
            desc += (' t. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! '
                     'video/x-raw,format=RGB ! appsink name=raw max-buffers=1 drop=true sync=false')
        return desc

    def _source_name(self):
        url = self.get_parameter('url').value
        return url if url else f'udp port {self.get_parameter("udp_port").value}'

    def _run(self):
        """Reader thread: run the pipeline, publish frames, restart on error or stall."""
        while not self._stop.is_set() and rclpy.ok():
            pipeline = Gst.parse_launch(self._pipeline_description())
            try:
                self._stream(pipeline)
            except Exception as e:
                if self._stop.is_set() or not rclpy.ok():
                    break
                self.get_logger().warn(f'Camera ({self._source_name()}): {e}; restarting in '
                                       f'{RESTART_DELAY_S:.0f} s', throttle_duration_sec=10.0)
            finally:
                pipeline.set_state(Gst.State.NULL)
            self._stop.wait(RESTART_DELAY_S)

    def _stream(self, pipeline):
        jpeg_sink = pipeline.get_by_name('jpeg')
        raw_sink = pipeline.get_by_name('raw')
        bus = pipeline.get_bus()
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('pipeline failed to start')

        stall_timeout = self.get_parameter('stall_timeout_s').value
        last_frame = time.monotonic()
        frames = 0
        while not self._stop.is_set() and rclpy.ok():
            msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg is not None:
                if msg.type == Gst.MessageType.EOS:
                    raise RuntimeError('stream ended')
                err, _debug = msg.parse_error()
                raise RuntimeError(err.message)

            sample = jpeg_sink.try_pull_sample(PULL_TIMEOUT_NS)
            if sample is None:
                if time.monotonic() - last_frame > stall_timeout:
                    raise TimeoutError(f'no frames for {stall_timeout:.0f} s '
                                       '(is the stream running and sent to this computer?)')
                continue
            last_frame = time.monotonic()
            stamp = self.get_clock().now().to_msg()  # host receive time, not exposure time
            frame_id = self.get_parameter('frame_id').value

            data = read_buffer(sample)
            if data is None:
                continue
            msg = CompressedImage()
            msg.header.stamp = stamp
            msg.header.frame_id = frame_id
            msg.format = 'jpeg'
            msg.data = data
            self.jpeg_pub.publish(msg)

            if raw_sink is not None:
                self._publish_raw(raw_sink, stamp, frame_id)

            frames += 1
            if frames == 1:
                s = sample.get_caps().get_structure(0)
                self.get_logger().info(f'Receiving camera from {self._source_name()}: '
                                       f'{s.get_value("width")}x{s.get_value("height")}')

    def _publish_raw(self, raw_sink, stamp, frame_id):
        sample = raw_sink.try_pull_sample(0)
        if sample is None:
            return
        s = sample.get_caps().get_structure(0)
        data = read_buffer(sample)
        if data is None:
            return
        img = Image()
        img.header.stamp = stamp
        img.header.frame_id = frame_id
        img.width = s.get_value('width')
        img.height = s.get_value('height')
        img.encoding = 'rgb8'
        img.is_bigendian = 0
        img.step = len(data) // img.height  # GStreamer may pad rows to 4 bytes
        img.data = data
        self.raw_pub.publish(img)

    def shutdown(self):
        self._stop.set()
        self._reader.join(timeout=3.0)


def main(args=None):
    rclpy.init(args=args)
    node = BlueRovCameraNode()
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

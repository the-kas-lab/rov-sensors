#!/usr/bin/env python3
"""Turns multibeam frames into a fan-shaped, top-down sonar image.

The sonar's frames are polar: rows are range and columns are beam angle. This node
redraws them the way the sonar sees the scene: the transducer at the bottom centre,
straight ahead up, starboard to the right, colour = echo strength. Each output pixel
takes the value of the sample and beam it falls in (nearest neighbour).

Subscribes:
    sonoptix_echo/frame  rov_interfaces/SonoptixFrame
Publishes:
    sonoptix_echo/fan    sensor_msgs/Image (rgb8), reliable QoS: subscribe with reliable QoS too

Parameters:
    image_height  pixels from the transducer to the end of the range; the width follows
                  the sector (about 1.7 x height at 120 deg, 1.4 x at 90 deg)
    publish_rate  images per second; limits bandwidth (about 830 kB per image at 400 px)
    colormap      any matplotlib colormap name
    min_intensity, max_intensity  colour scale (0-255); lower max_intensity to brighten weak echoes
"""

import math
import signal

import matplotlib
import numpy as np
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Image

from rov_interfaces.msg import SonoptixFrame

BACKGROUND_RGB = (32, 32, 32)  # outside the fan, so its edges stay visible
# Reliable, newest only: with best effort, most images this large (~830 kB) are lost in
# transport (measured: 7 of 40 delivered at 400 px; queue depth made no difference).
FAN_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)


class SonoptixFanNode(Node):
    def __init__(self):
        super().__init__('sonoptix_fan')
        self.declare_parameter('image_height', 400)
        self.declare_parameter('publish_rate', 10.0)
        self.declare_parameter('colormap', 'inferno')
        self.declare_parameter('min_intensity', 0.0)
        self.declare_parameter('max_intensity', 255.0)

        cmap = matplotlib.colormaps[self.get_parameter('colormap').value]
        self._lut = (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)

        self._latest = None     # newest frame
        self._dirty = False
        self._geometry = None   # key of the cached pixel -> sample mapping
        self._index = None      # (height, width) flat index into the frame's intensities
        self._inside = None     # (height, width) True inside the fan

        self.create_subscription(SonoptixFrame, 'sonoptix_echo/frame', self._on_frame, qos_profile_sensor_data)
        self.image_pub = self.create_publisher(Image, 'sonoptix_echo/fan', FAN_QOS)
        self.create_timer(1.0 / self.get_parameter('publish_rate').value, self._publish)

    def _on_frame(self, msg):
        self._latest = msg
        self._dirty = True

    def _update_geometry(self, msg):
        """Work out, once per range/sector/size, which sample each output pixel shows."""
        height = self.get_parameter('image_height').value
        key = (msg.num_samples, msg.num_beams, msg.sonar_range_m, msg.sector_deg, height)
        if key == self._geometry:
            return
        half = math.radians(msg.sector_deg) / 2
        width = 2 * math.ceil(height * math.sin(min(half, math.pi / 2)))
        px_per_m = height / msg.sonar_range_m

        x = (np.arange(width) + 0.5 - width / 2) / px_per_m        # metres to starboard
        y = (height - np.arange(height) - 0.5) / px_per_m          # metres ahead
        xx, yy = np.meshgrid(x, y)
        r = np.hypot(xx, yy)
        bearing = np.arctan2(xx, yy)                                # 0 ahead, positive starboard

        angles = np.radians(np.frombuffer(msg.beam_angle_deg, dtype=np.float32))
        row = np.floor(r / msg.sonar_range_m * msg.num_samples).astype(np.int64)
        col = np.searchsorted(angles[:-1] + np.diff(angles) / 2, bearing)  # nearest beam
        self._inside = (r < msg.sonar_range_m) & (np.abs(bearing) <= half)
        self._index = np.where(self._inside, row * msg.num_beams + col, 0)
        self._geometry = key

    def _publish(self):
        if not self._dirty or not rclpy.ok():
            return
        self._dirty = False
        msg = self._latest
        intensity = np.frombuffer(msg.intensity, dtype=np.uint8)
        if intensity.size != msg.num_samples * msg.num_beams or msg.sonar_range_m <= 0:
            return
        self._update_geometry(msg)

        lo = self.get_parameter('min_intensity').value
        hi = self.get_parameter('max_intensity').value
        values = intensity[self._index].astype(np.float32)
        index = np.clip((values - lo) / max(hi - lo, 1e-6) * 255.0, 0, 255).astype(np.uint8)
        rgb = self._lut[index]
        rgb[~self._inside] = BACKGROUND_RGB

        img = Image()
        img.header = msg.header
        img.height, img.width = index.shape
        img.encoding = 'rgb8'
        img.is_bigendian = 0
        img.step = img.width * 3
        img.data = rgb.tobytes()
        self.image_pub.publish(img)


def main(args=None):
    rclpy.init(args=args)
    node = SonoptixFanNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception:
        # Ctrl-C can invalidate the context mid-publish; only that case is expected here.
        if rclpy.ok():
            raise
    finally:
        # Ctrl-C reaches the node twice under ros2 launch (terminal + launch forwarding).
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        node.destroy_node()
        rclpy.try_shutdown()
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # try_shutdown restores rclpy's previous handler


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Turns the stream of sonar profiles into a scrolling waterfall image.

Each incoming ping becomes one row of the image (newest at the top), with
colour = echo strength. Columns are distance from the transducer, left to right.

Subscribes:
    omniscan450/profile    rov_interfaces/OmniscanProfile
Publishes:
    omniscan450/waterfall  sensor_msgs/Image (rgb8)

Parameters:
    history       pings (rows) kept in the image
    publish_rate  images per second; every ping still gets a row, this only
                  limits bandwidth (a 600x300 rgb8 image is ~540 kB)
    colormap      any matplotlib colormap name
    min_db, max_db  colour scale; NaN = use the scale the sonar reports
"""

import math
import signal

import matplotlib
import numpy as np
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

from rov_interfaces.msg import OmniscanProfile


class SonarWaterfallNode(Node):
    def __init__(self):
        super().__init__('sonar_waterfall')
        self.declare_parameter('history', 300)
        self.declare_parameter('publish_rate', 10.0)
        self.declare_parameter('colormap', 'inferno')
        self.declare_parameter('min_db', math.nan)
        self.declare_parameter('max_db', math.nan)

        cmap = matplotlib.colormaps[self.get_parameter('colormap').value]
        self._lut = (cmap(np.linspace(0.0, 1.0, 256))[:, :3] * 255).astype(np.uint8)

        self._rows = None      # (history, num_results) dB, row 0 = newest ping
        self._latest = None    # newest profile, for the header and colour scale
        self._dirty = False

        self.create_subscription(OmniscanProfile, 'omniscan450/profile', self._on_profile,
                                 qos_profile_sensor_data)
        self.image_pub = self.create_publisher(Image, 'omniscan450/waterfall', qos_profile_sensor_data)
        self.create_timer(1.0 / self.get_parameter('publish_rate').value, self._publish)

    def _on_profile(self, msg):
        power = np.frombuffer(msg.power_db, dtype=np.float32)
        history = self.get_parameter('history').value
        if self._rows is None or self._rows.shape != (history, power.size):
            # First ping, or the range/sample settings changed: start a fresh image.
            self._rows = np.full((history, power.size), msg.min_pwr_db, dtype=np.float32)
        self._rows[1:] = self._rows[:-1]
        self._rows[0] = power
        self._latest = msg
        self._dirty = True

    def _publish(self):
        if not self._dirty or not rclpy.ok():
            return
        self._dirty = False
        msg = self._latest
        lo = self.get_parameter('min_db').value
        hi = self.get_parameter('max_db').value
        lo = msg.min_pwr_db if math.isnan(lo) else lo
        hi = msg.max_pwr_db if math.isnan(hi) else hi

        index = np.clip((self._rows - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
        rgb = self._lut[index]

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
    node = SonarWaterfallNode()
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

#!/usr/bin/env python3
"""ROS 2 driver for the Cerulean Omniscan 450 FS sonar.

Connects to the sonar over TCP (Ping protocol, same as standalone/omniscan450_reader.py),
configures it, and publishes every ping.

Publishes:
    omniscan450/profile  rov_interfaces/OmniscanProfile  every ping, power in dB
    omniscan450/peak     sensor_msgs/Range  strongest echo beyond the ring-down zone

Parameters (range settings can be changed live, e.g. from Foxglove's Parameters panel):
    host, port              sonar address (default 192.168.2.92:51200)
    start_m, range_m        scan window (m)
    num_results             samples per ping
    gain_index              0-7, -1 = auto
    ping_rate_hz            0 = as fast as the range allows
    listen_only             don't configure the sonar, just publish what it sends
    frame_id                TF frame of the transducer
    peak_blank_m            ignore this much ring-down near the transducer when finding the peak

The sonar connection is retried every 2 s, so the node can start before the
sonar (or the mock) is up and survives the link dropping.
"""

import array
import builtins
import signal
import threading
import time

import numpy as np
import rclpy

try:
    from brping import Omniscan450, definitions
except ImportError:
    raise SystemExit('brping not found: run `source ~/ros2_ws/src/rov-sensors/env.sh` first '
                     '(it puts the project .venv on PYTHONPATH)')
from rcl_interfaces.msg import SetParametersResult
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Range

from rov_interfaces.msg import OmniscanProfile

# The sonar periodically sends message id 0, which brping doesn't know and
# reports with a print() on every occurrence. Drop just that line.
_print = builtins.print
builtins.print = lambda *a, **k: None if a[:2] == ("Unknown message: ", 0) else _print(*a, **k)

PING_PARAM_NAMES = ('start_m', 'range_m', 'num_results', 'gain_index', 'ping_rate_hz')
RECONNECT_DELAY_S = 2.0


def scale_power_db(msg):
    """Convert the raw u16 pwr_results into dB using the message's min/max."""
    raw = np.asarray(msg.pwr_results, dtype=np.float64)
    return msg.min_pwr_db + (raw / 65535.0) * (msg.max_pwr_db - msg.min_pwr_db)


def range_axis_m(msg):
    """Distance (m) from the transducer for each sample in pwr_results."""
    start = msg.start_mm / 1000.0
    length = msg.length_mm / 1000.0
    return start + np.arange(msg.num_results) * (length / msg.num_results)


def float32_array(values):
    return array.array('f', np.asarray(values, dtype=np.float32).tobytes())


class Omniscan450Node(Node):
    def __init__(self):
        super().__init__('omniscan450')
        self.declare_parameter('host', '192.168.2.92')
        self.declare_parameter('port', 51200)
        self.declare_parameter('start_m', 0.0)
        self.declare_parameter('range_m', 10.0)
        self.declare_parameter('num_results', 600)
        self.declare_parameter('gain_index', -1)
        self.declare_parameter('ping_rate_hz', 0.0)
        self.declare_parameter('listen_only', False)
        self.declare_parameter('frame_id', 'omniscan450_link')
        self.declare_parameter('peak_blank_m', 0.2)
        self.add_on_set_parameters_callback(self._on_set_parameters)

        self.profile_pub = self.create_publisher(OmniscanProfile, 'omniscan450/profile',
                                                 qos_profile_sensor_data)
        self.peak_pub = self.create_publisher(Range, 'omniscan450/peak', qos_profile_sensor_data)

        self._sonar = None
        self._sonar_lock = threading.Lock()  # guards self._sonar against reconnects
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._run, daemon=True)
        self._reader.start()

    # -- sonar connection ----------------------------------------------------

    def _run(self):
        """Reader thread: connect, stream pings, reconnect on any failure."""
        while not self._stop.is_set():
            host = self.get_parameter('host').value
            port = self.get_parameter('port').value
            try:
                sonar = Omniscan450()
                sonar.connect_tcp(host, port)
                if not sonar.initialize():
                    raise ConnectionError('no reply to device_information request')
                self.get_logger().info(
                    f'Connected to Omniscan at {host}:{port}: device_type={sonar._device_type} '
                    f'fw={sonar._firmware_version_major}.{sonar._firmware_version_minor}'
                    f'.{sonar._firmware_version_patch}')
                with self._sonar_lock:
                    self._sonar = sonar
                if not self.get_parameter('listen_only').value:
                    self._send_ping_params(sonar, self._ping_params())
                self._read_loop(sonar)
            except Exception as e:  # brping raises plain Exception on connect failures
                if self._stop.is_set() or not rclpy.ok():
                    break
                self.get_logger().warn(f'Sonar {host}:{port}: {e}; retrying in {RECONNECT_DELAY_S:.0f} s',
                                       throttle_duration_sec=10.0)
            finally:
                with self._sonar_lock:
                    if self._sonar is not None and self._sonar.iodev is not None:
                        self._sonar.iodev.close()
                    self._sonar = None
            self._stop.wait(RECONNECT_DELAY_S)

    def _read_loop(self, sonar):
        last_rx = time.monotonic()
        while not self._stop.is_set() and rclpy.ok():
            msg = sonar.wait_message([definitions.OMNISCAN450_OS_MONO_PROFILE], timeout=1.0)
            if msg is None:
                if time.monotonic() - last_rx > 3.0:
                    self.get_logger().warn('No profiles from the sonar (is it enabled / pinging?)',
                                           throttle_duration_sec=10.0)
                continue
            last_rx = time.monotonic()
            self._publish(msg)

    def _ping_params(self, overrides=None):
        values = {name: self.get_parameter(name).value for name in PING_PARAM_NAMES}
        values.update(overrides or {})
        return values

    def _send_ping_params(self, sonar, p, enable=True):
        rate = p['ping_rate_hz']
        sonar.control_os_ping_params(
            start_mm=int(p['start_m'] * 1000),
            length_mm=int(p['range_m'] * 1000),
            msec_per_ping=Omniscan450.calc_msec_per_ping(rate) if rate > 0 else 0,
            gain_index=int(p['gain_index']),
            num_results=int(p['num_results']),
            enable=enable,
        )

    def _on_set_parameters(self, params):
        changed = {p.name: p.value for p in params if p.name in PING_PARAM_NAMES}
        if not changed:
            return SetParametersResult(successful=True)
        p = self._ping_params(changed)
        if p['range_m'] <= 0 or p['start_m'] < 0:
            return SetParametersResult(successful=False, reason='start_m must be >= 0 and range_m > 0')
        if not 1 <= p['num_results'] <= 32000:
            return SetParametersResult(successful=False, reason='num_results must be 1-32000')
        if not -1 <= p['gain_index'] <= 7:
            return SetParametersResult(successful=False, reason='gain_index must be -1 (auto) or 0-7')
        if p['ping_rate_hz'] < 0:
            return SetParametersResult(successful=False, reason='ping_rate_hz must be >= 0')
        with self._sonar_lock:
            if self._sonar is not None and not self.get_parameter('listen_only').value:
                self._send_ping_params(self._sonar, p)
                self.get_logger().info(f'Reconfigured sonar: {changed}')
        return SetParametersResult(successful=True)

    # -- publishing ----------------------------------------------------------

    def _publish(self, msg):
        power_db = scale_power_db(msg)
        distances = range_axis_m(msg)
        stamp = self.get_clock().now().to_msg()
        frame_id = self.get_parameter('frame_id').value

        profile = OmniscanProfile()
        profile.header.stamp = stamp
        profile.header.frame_id = frame_id
        profile.ping_number = msg.ping_number
        profile.timestamp_ms = msg.timestamp_ms
        profile.ping_hz = msg.ping_hz
        profile.gain_index = msg.gain_index
        profile.speed_of_sound = msg.sos_dmps / 10.0
        profile.start_m = msg.start_mm / 1000.0
        profile.length_m = msg.length_mm / 1000.0
        profile.pulse_duration_sec = msg.pulse_duration_sec
        profile.analog_gain = msg.analog_gain
        profile.min_pwr_db = msg.min_pwr_db
        profile.max_pwr_db = msg.max_pwr_db
        profile.transducer_heading_deg = msg.transducer_heading_deg
        profile.vehicle_heading_deg = msg.vehicle_heading_deg
        profile.range_m = float32_array(distances)
        profile.power_db = float32_array(power_db)
        self.profile_pub.publish(profile)

        # Strongest echo, skipping the transducer ring-down right after the pulse.
        blank_end = profile.start_m + self.get_parameter('peak_blank_m').value
        search = np.flatnonzero(distances >= blank_end)
        if search.size == 0:
            return
        peak = search[np.argmax(power_db[search])]
        rng = Range()
        rng.header = profile.header
        rng.radiation_type = Range.ULTRASOUND
        rng.min_range = float(blank_end)
        rng.max_range = float(profile.start_m + profile.length_m)
        rng.range = float(distances[peak])
        self.peak_pub.publish(rng)

    def shutdown(self):
        self._stop.set()
        with self._sonar_lock:
            if self._sonar is not None and not self.get_parameter('listen_only').value:
                try:
                    # Stop pinging so the sonar isn't left transmitting.
                    self._sonar.control_os_ping_params(enable=False)
                except Exception:
                    pass
        self._reader.join(timeout=3.0)


def main(args=None):
    rclpy.init(args=args)
    node = Omniscan450Node()
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
